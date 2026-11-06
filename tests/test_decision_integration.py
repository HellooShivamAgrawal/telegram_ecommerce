"""Decision routing must reduce context without weakening cart boundaries."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('BOT_TOKEN', 'test-token')

from telegram_ecommerce.agent.catalog import CartActionRejectedError, answer_catalog_question
from telegram_ecommerce.agent.routing import RoutingDecision, RoutingUnavailable
from tests.test_catalog_agent import FakeResponses, call, fake_response

CATALOG = 'telegram_ecommerce.agent.catalog.'


def route(*, history=False, task='catalog', difficulty=0.0, confidence=0.99):
    return RoutingDecision(history, task, difficulty, confidence)


class DecisionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enable = patch(CATALOG + 'DECISIONS_ROUTING_ENABLED', True)
        self.enable.start()
        self.addCleanup(self.enable.stop)
        self.legacy = patch(CATALOG + '_requested_cart_actions', new_callable=AsyncMock, return_value=set())
        self.intent = self.legacy.start()
        self.addCleanup(self.legacy.stop)

    async def run_answer(self, text, responses, **kwargs):
        return await answer_catalog_question(text, chat_id=42, request_id='telegram:20',
                                             client=SimpleNamespace(responses=responses),
                                             model='main-model', fast_model='fast-model', **kwargs)

    async def test_independent_search_omits_history_and_uses_fast_read_tools(self):
        responses = FakeResponses([
            fake_response('r1', [call('search_products', {'query': 'notebooks'})]),
            fake_response('r2', [], 'Notebook costs $9.99.'),
        ])
        with patch(CATALOG + 'classify_request', return_value=route()), patch(
            CATALOG + 'execute_tool', return_value={'products': [{'id': 7, 'name': 'Notebook'}]},
        ):
            await self.run_answer('Find notebooks', responses, history=[{'role': 'user', 'content': 'Previous topic'}])
        self.assertEqual(responses.requests[0]['input'], [{'role': 'user', 'content': 'Find notebooks'}])
        self.assertTrue(all(r['model'] == 'fast-model' for r in responses.requests))
        names = {tool['name'] for tool in responses.requests[0]['tools']}
        self.assertEqual(names, {'list_categories', 'search_products', 'get_product', 'get_cart'})
        self.intent.assert_awaited_once()

    async def test_followup_write_keeps_history_and_skips_advisory_router(self):
        self.intent.return_value = {'set'}
        responses = FakeResponses([
            fake_response('r1', [call('get_cart', {})]),
            fake_response('r2', [call('update_cart_item', {'product_id': 7, 'quantity': 3})]),
            fake_response('r3', [], 'Quantity is now three.'),
        ])
        history = [{'role': 'user', 'content': 'Add two notebooks'}]
        with patch(CATALOG + 'classify_request') as classify, patch(CATALOG + 'execute_tool', side_effect=[
            {'items': [{'product_id': 7, 'name': 'Notebook', 'quantity': 2}]},
            {'cart': {'items': [{'product_id': 7, 'name': 'Notebook', 'quantity': 3}]}},
        ]) as execute:
            await self.run_answer('Make it three', responses, history=history)
        self.assertEqual(responses.requests[0]['input'][0], history[0])
        self.assertTrue(all(r['model'] == 'main-model' for r in responses.requests))
        self.assertEqual(execute.call_args.args[:2], ('update_cart_item', {'product_id': 7, 'quantity': 3}))
        self.intent.assert_awaited_once()
        classify.assert_not_awaited()

    async def test_uncertain_read_followup_keeps_context_and_main_model(self):
        responses = FakeResponses([fake_response('r1', [], 'Which two products should I compare?')])
        history = [{'role': 'user', 'content': 'Show two notebooks'}]
        with patch(CATALOG + 'classify_request', return_value=route(
            history=True, task='other', difficulty=3, confidence=0.7,
        )):
            await self.run_answer('Which one is cheaper?', responses, history=history)
        self.assertEqual(responses.requests[0]['input'][0], history[0])
        self.assertEqual(responses.requests[0]['model'], 'main-model')

    async def test_direct_write_always_uses_main_model(self):
        self.intent.return_value = {'clear'}
        responses = FakeResponses([fake_response('r1', [call('clear_cart', {})]), fake_response('r2', [], 'Cleared.')])
        with patch(CATALOG + 'classify_request') as classify, patch(
            CATALOG + 'execute_tool', return_value={'cart': {'items': []}},
        ):
            await self.run_answer('Clear my cart', responses)
        self.assertTrue(all(r['model'] == 'main-model' for r in responses.requests))
        self.intent.assert_awaited_once()
        classify.assert_not_awaited()

    async def test_read_classification_cannot_execute_an_invented_write(self):
        responses = FakeResponses([fake_response('r1', [call('clear_cart', {})])])
        with patch(CATALOG + 'classify_request', return_value=route()), patch(
            CATALOG + 'execute_tool',
        ) as execute, self.assertRaises(CartActionRejectedError):
            await self.run_answer('What does a notebook cost?', responses)
        execute.assert_not_called()
        self.intent.assert_awaited_once()

    async def test_disagreement_about_a_cart_change_cannot_claim_success(self):
        responses = FakeResponses([fake_response('r1', [], 'Added it to your cart.')])
        with patch(CATALOG + 'classify_request', return_value=route(task='cart_change')), patch(
            CATALOG + 'execute_tool',
        ) as execute, self.assertRaises(CartActionRejectedError):
            await self.run_answer('Add a notebook', responses)
        self.assertEqual(responses.requests, [])
        execute.assert_not_called()

    async def test_removal_only_cannot_set_a_positive_quantity(self):
        self.intent.return_value = {'remove'}
        for quantity in (0, 3):
            with self.subTest(quantity=quantity):
                responses = FakeResponses([
                    fake_response('r1', [call('get_cart', {})]),
                    fake_response('r2', [call('update_cart_item', {'product_id': 7, 'quantity': quantity})]),
                    fake_response('r3', [], 'Removed.'),
                ])
                with patch(CATALOG + 'classify_request', return_value=route(task='cart_change')), patch(
                    CATALOG + 'execute_tool', side_effect=[
                        {'items': [{'product_id': 7, 'name': 'Notebook'}]}, {'cart': {'items': []}},
                    ],
                ) as execute:
                    if quantity == 0:
                        await self.run_answer('Remove notebook', responses)
                        self.assertEqual(execute.call_count, 2)
                    else:
                        with self.assertRaises(CartActionRejectedError):
                            await self.run_answer('Remove notebook', responses)
                        self.assertEqual(execute.call_count, 1)

    async def test_endpoint_outage_retains_existing_intent_gate_and_history(self):
        responses = FakeResponses([fake_response('r1', [call('get_cart', {})]), fake_response('r2', [], 'Empty.')])
        history = [{'role': 'user', 'content': 'Earlier request'}]
        with patch(CATALOG + 'classify_request', side_effect=RoutingUnavailable()) as classify, patch(
            CATALOG + 'execute_tool', return_value={'items': []},
        ):
            await self.run_answer('Show my cart', responses, history=history)
        self.intent.assert_awaited_once()
        classify.assert_awaited_once()
        self.assertEqual(responses.requests[0]['input'][0], history[0])
        self.assertEqual(responses.requests[0]['model'], 'main-model')

    async def test_failed_advisory_assessment_does_not_authorize_a_write(self):
        responses = FakeResponses([fake_response('r1', [call('clear_cart', {})])])
        with patch(CATALOG + 'classify_request', side_effect=RoutingUnavailable()), patch(
            CATALOG + 'execute_tool',
        ) as execute, self.assertRaises(CartActionRejectedError):
            await self.run_answer('Find notebooks', responses)
        execute.assert_not_called()
        self.intent.assert_awaited_once()

    async def test_request_to_write_needs_a_committed_receipt(self):
        self.intent.return_value = {'add'}
        responses = FakeResponses([fake_response('r1', [], 'Added a notebook.')])
        with patch(CATALOG + 'classify_request', return_value=route()), self.assertRaises(CartActionRejectedError):
            await self.run_answer('Add a notebook', responses)
        self.intent.assert_awaited_once()

    async def test_routing_can_be_disabled_without_disabling_intent_check(self):
        self.intent.return_value = {'clear'}
        responses = FakeResponses([fake_response('r1', [call('clear_cart', {})]), fake_response('r2', [], 'Cleared.')])
        with patch(CATALOG + 'DECISIONS_ROUTING_ENABLED', False), patch(
            CATALOG + 'classify_request',
        ) as classify, patch(CATALOG + 'execute_tool', return_value={'cart': {'items': []}}):
            await self.run_answer('Clear my cart', responses)
        classify.assert_not_called()
        self.intent.assert_awaited_once()
