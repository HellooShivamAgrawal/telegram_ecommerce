"""Advisory classification of one message; never authorize or execute tools.

Thresholds are provisional operating points, not calibrated guarantees. Tune them
with labeled shop messages. Context, task and difficulty uncertainty are handled
independently. Every cart mutation still needs the existing authorization checks.
See https://developers.openai.com/api/docs/guides/decisions.
"""

from copy import deepcopy
from dataclasses import dataclass
from math import isclose, isfinite
from typing import Literal, Mapping

from openai import APIError

ROUTING_MODEL = "gpt-6-luna"
ROUTING_CONFIDENCE_THRESHOLD = 0.8
HISTORY_PROBABILITY_THRESHOLD = 0.15
DIFFICULTY_CONFIDENCE_THRESHOLD = 0.8
ROUTING_TIMEOUT_SECONDS = 6.0
Route = Literal["categories", "catalog", "cart_read", "cart_change", "comparison", "other"]
ROUTES = ("categories", "catalog", "cart_read", "cart_change", "comparison", "other")

DIFFICULTY_LEVELS = (
    {"label": "easy", "description": (
        "One lookup or simple operation: find notebooks, list categories, show my cart, add two notebooks."
    )},
    {"label": "medium", "description": (
        "Average/medium are the same level: compare two notebooks, or recommend a notebook under $10."
    )},
    {"label": "difficult", "description": (
        "Several interacting requirements: choose stationery for five people within a budget, comparing quality and size."
    )},
    {"label": "very_difficult", "description": (
        "Complex planning: optimize supplies for several classrooms with different needs, quantities, stock limits and a shared budget."
    )},
)

ROUTING_QUESTIONS = (
    {"type": "predicate", "name": "needs_history", "instructions": (
        "Does understanding this message require earlier chat messages to resolve "
        "references such as 'that', 'make it three', 'again', 'which one', or 'yes'? "
        "'Show my cart' and 'clear my cart' are self-contained: database state is not chat history."
    )},
    {"type": "choice", "name": "route", "instructions": (
        "What does the shopper want? Choose cart_change for a request to alter "
        "the cart, even when product references need history."
    ), "choices": (
        {"value": "categories", "description": "List the kinds of products sold."},
        {"value": "catalog", "description": "Find products or check details, price or stock."},
        {"value": "cart_read", "description": "View cart contents, quantities or total."},
        {"value": "cart_change", "description": "Add, remove, clear or change quantities; 'make it three'."},
        {"value": "comparison", "description": "Compare products or recommend a choice."},
        {"value": "other", "description": "Greeting, unrelated or unclear request."},
    )},
    {"type": "score", "name": "difficulty", "instructions": (
        "How much reasoning does this shopping request require? Use the ordered "
        "examples. Missing references need history but do not make reasoning harder."
    ), "levels": DIFFICULTY_LEVELS},
)


@dataclass(frozen=True)
class RoutingDecision:
    needs_history: bool
    route: Route
    difficulty: float  # Probability-weighted level index: easy=0 .. very_difficult=3.
    confidence: float  # Task confidence: minimum of reported confidence and selected probability.


class RoutingUnavailable(RuntimeError):
    """The Decisions endpoint could not be used; callers may use their fallback."""


def _field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _number(value, maximum=1.0) -> float:
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= maximum:
        raise ValueError("Invalid decision number")
    return float(value)


def _distribution(answer, values, *, labels=None):
    entries = _field(answer, "probabilities")
    if not isinstance(entries, (list, tuple)) or len(entries) != len(values):
        raise ValueError("Invalid decision distribution")
    probabilities = {}
    for entry in entries:
        value = _field(entry, "value")
        expected_type = str if labels is None else int
        if type(value) is not expected_type or value not in values or value in probabilities:
            raise ValueError("Invalid decision value")
        if labels is not None and _field(entry, "label") != labels[value]:
            raise ValueError("Invalid decision label")
        probabilities[value] = _number(_field(entry, "probability"))
    if not isclose(sum(probabilities.values()), 1.0, abs_tol=0.01):
        raise ValueError("Invalid decision probability sum")
    return probabilities


def parse_routing_decision(response) -> RoutingDecision:
    """Validate advisory answers, or request the existing assistant fallback."""
    answers = _field(response, "answers")
    if not isinstance(answers, (list, tuple)) or len(answers) != len(ROUTING_QUESTIONS):
        raise RoutingUnavailable("Decision routing is unavailable")
    expected = {question["name"]: question["type"] for question in ROUTING_QUESTIONS}
    indexed = {}
    try:
        for answer in answers:
            name = _field(answer, "name")
            if type(name) is not str or name not in expected or name in indexed:
                raise ValueError("Invalid decision name")
            if _field(answer, "type") != expected[name]:
                raise ValueError("Invalid or refused decision type")
            indexed[name] = answer

        history = _number(_field(indexed["needs_history"], "probability"))
        route = _field(indexed["route"], "choice")
        if type(route) is not str or route not in ROUTES:
            raise ValueError("Invalid route")
        route_probabilities = _distribution(indexed["route"], ROUTES)
        route_confidence = min(_number(_field(indexed["route"], "confidence")), route_probabilities[route])
        difficulty = _number(_field(indexed["difficulty"], "score"), 3.0)
        scores = _distribution(indexed["difficulty"], range(4), labels=[level["label"] for level in DIFFICULTY_LEVELS])
        if not isclose(difficulty, sum(value * probability for value, probability in scores.items()), abs_tol=0.02):
            raise ValueError("Invalid difficulty score")
        difficulty_confidence = _number(_field(indexed["difficulty"], "confidence"))
    except ValueError:
        raise RoutingUnavailable("Decision routing is unavailable") from None

    return RoutingDecision(
        needs_history=history > HISTORY_PROBABILITY_THRESHOLD,
        route=route if route_confidence >= ROUTING_CONFIDENCE_THRESHOLD else "other",
        difficulty=difficulty if difficulty_confidence >= DIFFICULTY_CONFIDENCE_THRESHOLD else 3.0,
        confidence=route_confidence,
    )


async def classify_request(client, message: str) -> RoutingDecision:
    """Ask once about the current message, without history, catalog, IDs or tools.

    Missing endpoint, API errors, refusals and malformed answers raise a sanitized
    RoutingUnavailable so the caller can retain its existing assistant path.
    """
    if not isinstance(message, str) or not message.strip():
        raise RoutingUnavailable("Decision routing is unavailable")
    # Keep routing latency bounded independently of the main Responses policy.
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        client = with_options(timeout=ROUTING_TIMEOUT_SECONDS, max_retries=0)
    create = getattr(getattr(client, "decisions", None), "create", None)
    if not callable(create):
        raise RoutingUnavailable("Decision routing is unavailable")
    try:
        response = await create(model=ROUTING_MODEL, input=message, questions=deepcopy(ROUTING_QUESTIONS))
    except APIError:
        raise RoutingUnavailable("Decision routing is unavailable") from None
    return parse_routing_decision(response)
