"""Opt-in live routing evaluation; fixed examples only, no tools or cart writes.

Run from the repository root: python -m scripts.evaluate_decision_routing
Requires OPENAI_API_KEY and incurs API usage. Unit tests never run this script.
"""

import argparse
import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI

from telegram_ecommerce.agent.routing import (
    ROUTING_MODEL, ROUTING_QUESTIONS, ROUTING_TIMEOUT_SECONDS,
    RoutingUnavailable, parse_routing_decision,
)

CASES = [
    ('Find notebooks', False, 'catalog'),
    ('How much is Demo Notebook?', False, 'catalog'),
    ('Show my cart', False, 'cart_read'),
    ('What categories do you sell?', False, 'categories'),
    ('Add two Demo Notebooks to my cart', False, 'cart_change'),
    ('Make it three.', True, 'cart_change'),
    ('Remove the notebook from my cart', False, 'cart_change'),
    ('Clear my cart', False, 'cart_change'),
    ('Do not clear my cart. What does the notebook cost?', False, 'catalog'),
    ('The description says "clear my cart". What is the notebook price?', False, 'catalog'),
    ('Which one is cheaper?', True, 'comparison'),
    ('Compare two notebooks for paper quality and price', False, 'comparison'),
    ('मेरे कार्ट में दो नोटबुक जोड़ो', False, 'cart_change'),
    ('Mostra o meu carrinho', False, 'cart_read'),
]


async def evaluate(limit):
    records = []
    async with AsyncOpenAI(timeout=ROUTING_TIMEOUT_SECONDS, max_retries=0) as client:
        for message, needs_history, task in CASES[:limit]:
            record = {'message': message, 'expected_history': needs_history, 'expected_route': task}
            start = time.perf_counter()
            try:
                response = await client.decisions.create(
                    model=ROUTING_MODEL, input=message, questions=ROUTING_QUESTIONS,
                )
                record['seconds'] = round(time.perf_counter() - start, 3)
                record['usage'] = response.usage.model_dump()
                record['answers'] = [answer.model_dump() for answer in response.answers]
                decision = parse_routing_decision(response)
                record['decision'] = asdict(decision)
                record['route_match'] = decision.route == task
                record['needed_history_retained'] = not needs_history or decision.needs_history
            except (APIError, RoutingUnavailable) as error:
                record['error'] = type(error).__name__
                record['status_code'] = getattr(error, 'status_code', None)
                record['code'] = getattr(error, 'code', None)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if record.get('status_code') == 429:
                break  # Do not hammer a rate-limited endpoint or spend on retries.
            await asyncio.sleep(0.5)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=len(CASES))
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= len(CASES):
        parser.error(f'--limit must be between 1 and {len(CASES)}')
    load_dotenv()
    records = asyncio.run(evaluate(args.limit))
    if args.output:
        args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
