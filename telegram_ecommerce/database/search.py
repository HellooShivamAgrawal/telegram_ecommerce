"""Rank catalog candidates using field importance, word forms and typo similarity.

Scores are ordering signals, never confidence probabilities. Every query term
must match; approximate matches are labeled so callers can offer a choice.
"""

from dataclasses import dataclass
import re
from unicodedata import category as unicode_category, normalize

from sqlalchemy import and_, case, func, or_, select, text

from telegram_ecommerce.database.models import Product, Session


MAX_SEARCH_LENGTH = 100
MAX_SEARCH_TERMS = 8
MIN_FUZZY_LENGTH = 4
MIN_PREFIX_LENGTH = 3
MIN_WORD_SIMILARITY = 0.5
FIELD_WEIGHTS = {
    'name': 10.0,
    'brand': 6.0,
    'tags': 5.0,
    'short_description': 3.0,
    'description': 1.0,
}


@dataclass(frozen=True)
class SearchHit:
    product: Product
    score: float
    match_kind: str
    matched_fields: tuple[str, ...]


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit]
    suggestions_only: bool = False


def _search_words(phrase):
    words, word = [], ''
    for character in phrase:
        if character.isalnum() or (word and unicode_category(character).startswith('M')):
            word += character
        elif word:
            words.append(word)
            word = ''
    if word:
        words.append(word)
    return words


def _lexical(field, word):
    return or_(
        func.to_tsvector(text("'simple'"), field).op('@@')(
            func.plainto_tsquery(text("'simple'"), word)),
        func.to_tsvector(text("'english'"), field).op('@@')(
            func.plainto_tsquery(text("'english'"), word)),
    )


def _partial(field, variants):
    matches = []
    for variant in variants:
        if len(variant) >= MIN_FUZZY_LENGTH and variant.isalpha():
            matches.append(field.contains(variant, autoescape=True))
        elif len(variant) >= MIN_PREFIX_LENGTH and variant.isalpha():
            matches.append(field.op('~')(r'\m' + re.escape(variant)))
    return or_(*matches) if matches else None


def _strength(field, word, variants):
    """One contribution per field/term: repetition cannot inflate relevance."""
    lexical = _lexical(field, word)
    branches = [(lexical, 1.0)]
    partial = _partial(field, variants)
    if partial is not None:
        branches.append((partial, 0.65))
    if len(word) >= MIN_FUZZY_LENGTH and word.isalpha():
        similarity = func.strict_word_similarity(word, field)
        branches.append((similarity >= MIN_WORD_SIMILARITY, 0.5 + 0.3 * similarity))
    return lexical, case(*branches, else_=0.0)


def _candidate(word, variants):
    # These predicates can use the combined full-text/trigram indexes. The
    # stronger, field-specific checks below reject loose trigram candidates.
    candidates = [_lexical(Product.search_text, word)]
    for variant in variants:
        if len(variant) >= MIN_PREFIX_LENGTH and variant.isalpha():
            candidates.append(Product.search_text.contains(variant, autoescape=True))
    if len(word) >= MIN_FUZZY_LENGTH and word.isalpha():
        candidates.append(Product.search_text.op('%>>')(word))
    return or_(*candidates)


def _ranked_statement(phrase, terms, limit):
    fields = {name: func.lower(func.coalesce(getattr(Product, name), ''))
              for name in FIELD_WEIGHTS}
    signal_columns, candidates = [], []
    for index, (word, variants) in enumerate(terms):
        for name, field in fields.items():
            _, strength = _strength(field, word, variants)
            signal_columns.append(strength.label(f't{index}_{name}'))
        candidates.append(_candidate(word, variants))
    # Score each field once per candidate. Without materialization PostgreSQL
    # repeats costly text parsing for SELECT, coverage filters and ORDER BY.
    signals = (
        select(Product.id.label('product_id'),
               (fields['name'] == phrase).label('exact_name'), *signal_columns)
        .where(Product.quantity_in_stock > 0, *candidates)
        .cte('search_signals').prefix_with('MATERIALIZED')
    )
    contributions = {name: [] for name in fields}
    lexical_terms, coverage, weighted_terms = [], [], []
    for index in range(len(terms)):
        strengths, lexical_fields = [], []
        for name in fields:
            strength = signals.c[f't{index}_{name}']
            contributions[name].append(strength)
            strengths.append(strength * FIELD_WEIGHTS[name])
            lexical_fields.append(strength == 1.0)
        lexical_terms.append(or_(*lexical_fields))
        weighted = func.greatest(*strengths)
        coverage.append(weighted > 0)
        weighted_terms.append(weighted)

    all_lexical = and_(*lexical_terms)
    exact_name = signals.c.exact_name
    score = sum(weighted_terms) + case((exact_name, 40.0), else_=0.0)
    matched = [func.greatest(*values).label(name + '_strength')
               for name, values in contributions.items()]
    return (
        select(Product, all_lexical.label('all_lexical'), score.label('score'), *matched)
        .join(signals, Product.id == signals.c.product_id)
        .where(*coverage)
        .order_by(exact_name.desc(), all_lexical.desc(), score.desc(), Product.id)
        .limit(limit)
    )


def search_catalog(query, limit=10):
    """Return ranked available products and explicit approximate-match metadata.

    Names > brands > tags > short descriptions > descriptions. Full lexical
    matches rank above approximate matches, then field score and stable ID.
    Short model/size codes stay exact. Related hits remain available even when
    an exact result exists, and callers should show at most five weak options.
    """
    if not isinstance(query, str) or len(query) > MAX_SEARCH_LENGTH or '\x00' in query:
        return SearchResult([])
    phrase = normalize('NFC', ' '.join(query.lower().split()))
    words = _search_words(phrase)
    if not words or len(words) > MAX_SEARCH_TERMS:
        return SearchResult([])
    words = list(dict.fromkeys(words))
    limit = max(1, min(int(limit), 50))
    with Session() as session:
        # Stems also make partial 'books' match 'Notebook'. Ask PostgreSQL's
        # dictionary once for all terms; no English-only prompt substitutions.
        stems = session.execute(select(*[
            func.ts_lexize(text("'english_stem'"), word) for word in words
        ])).one()
        terms = []
        for word, variants in zip(words, stems):
            terms.append((word, tuple(dict.fromkeys([word, *(variants or [])]))))
        # Transaction-local: never leak a threshold into pooled connections.
        session.execute(text("SELECT set_config('pg_trgm.strict_word_similarity_threshold', '0.49', true), "
                             "set_config('plan_cache_mode', 'force_custom_plan', true)"))
        rows = session.execute(_ranked_statement(phrase, terms, limit)).all()
        hits = [SearchHit(
            product=row[0], score=round(float(row[2]), 4),
            match_kind='exact' if row[1] else 'related',
            matched_fields=tuple(name for name, strength in zip(FIELD_WEIGHTS, row[3:]) if strength > 0),
        ) for row in rows]
        return SearchResult(hits, bool(hits) and hits[0].match_kind == 'related')


def search_products(query, limit=10):
    """Compatibility API for existing catalog callers."""
    return [hit.product for hit in search_catalog(query, limit).hits]
