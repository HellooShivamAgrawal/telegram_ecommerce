import asyncio
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from telegram_ecommerce.agent import routing
from telegram_ecommerce.agent.catalog import answer_catalog_question, CartActionRejectedError
from tests.test_catalog_agent import FakeResponses, call, fake_response


def decision(route, confidence=.99, actions=None):
    answers = [SimpleNamespace(type='choice', name='route', choice=route, confidence=confidence)]
    if actions is not None:
        answers += [SimpleNamespace(type='predicate', name=name, probability=float(name in actions))
                    for name in routing.OPERATIONS]
    return SimpleNamespace(answers=answers)


def client(route='keywords', responses=(), *, actions=None):
    return SimpleNamespace(decisions=FakeResponses([decision(route, actions=actions)]),
                           responses=FakeResponses(responses))


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize('message', ['hi', 'hello!', 'categories', 'what do you sell?'])
def test_local_routes_need_no_client(message):
    reply = run(routing.answer_shopping_request(message, client=object()))
    assert reply.text or reply.categories


def test_keywords_use_no_api_calls_and_preserve_modifiers():
    c = client(actions=set())
    product = {'id': 1, 'name': 'Blue A5 Notebook'}
    with patch.object(routing, 'execute_tool', return_value={'products': [product], 'suggestions_only': False}) as tool:
        reply = run(routing.answer_shopping_request('blue A5 notebook', client=c, chat_id=42))
    assert reply.products == (product,)
    assert not c.responses.requests
    tool.assert_called_once_with('search_products', {'query': 'blue A5 notebook'})
    assert not c.decisions.requests


def test_simple_search_returns_cards_after_one_luna_response():
    c = client('simple_read', [fake_response('r1', [call('search_products', {'query': 'notebook'})])], actions=set())
    product = {'id': 1, 'name': 'Notebook', 'match_kind': 'exact'}
    with patch('telegram_ecommerce.agent.catalog.execute_tool', return_value={'products': [product], 'suggestions_only': False}), \
         patch('telegram_ecommerce.agent.catalog._requested_cart_actions', new_callable=AsyncMock) as intent:
        reply = run(routing.answer_shopping_request('Can you find notebooks?', client=c, chat_id=42))
    assert reply.products == (product,)
    assert c.responses.requests[0]['model'] == routing.READ_MODEL
    assert len(c.responses.requests) == 1
    intent.assert_not_called()
    assert not {'clear_cart', 'add_to_cart'} & {t['name'] for t in c.responses.requests[0]['tools']}


def test_cart_operation_reuses_batched_intent_and_keeps_receipt():
    c = client('cart_change', [fake_response('r1', [call('clear_cart', {})]),
                               fake_response('r2', [], 'Cart cleared.')], actions={'clear'})
    with patch('telegram_ecommerce.agent.catalog.execute_tool', return_value={'cart': {'items': []}}) as tool, \
         patch('telegram_ecommerce.agent.catalog._requested_cart_actions', new_callable=AsyncMock) as intent:
        reply = run(routing.answer_shopping_request('Clear my cart', client=c, chat_id=42, request_id='telegram:1'))
    assert reply.text == 'Cart cleared.'
    intent.assert_not_called()
    assert tool.call_args.kwargs['operation_key'].startswith('assistant:')
    assert c.responses.requests[0]['parallel_tool_calls'] is False


def test_batched_intent_does_not_authorize_a_different_write():
    c = client('cart_change', [fake_response('r1', [call('clear_cart', {})])], actions={'add'})
    with patch('telegram_ecommerce.agent.catalog.execute_tool') as tool, pytest.raises(CartActionRejectedError):
        run(routing.answer_shopping_request('Add notebook', client=c, chat_id=42, request_id='telegram:1'))
    tool.assert_not_called()


@pytest.mark.parametrize('response', [decision('keywords', .5), decision('keywords', float('nan')),
                                      SimpleNamespace(answers=[]), RuntimeError('unavailable')])
def test_decision_failure_or_uncertainty_falls_back(response):
    c = SimpleNamespace(decisions=FakeResponses([response]))
    assert run(routing.decide_route(c, 'some request')).route == 'other'


def test_uncertain_intent_is_not_treated_as_permission():
    d = decision('cart_change', actions={'add'})
    d.answers[1].probability = .8
    c = SimpleNamespace(decisions=FakeResponses([d]))
    assert run(routing.decide_route(c, 'add notebook', check_actions=True)).actions is None


def test_conflicting_route_and_intent_go_to_guarded_workflow():
    c = SimpleNamespace(decisions=FakeResponses([decision('keywords', actions={'clear'})]))
    assert run(routing.decide_route(c, 'clear cart', check_actions=True)).route == 'other'


def test_preview_cannot_access_cart_even_on_unknown_route():
    c = client('other', [fake_response('r1', [call('clear_cart', {})])])
    with patch('telegram_ecommerce.agent.catalog.execute_tool') as live, \
         patch('telegram_ecommerce.agent.catalog.execute_preview_tool') as preview, pytest.raises(CartActionRejectedError):
        run(routing.answer_shopping_request('Tell me about the products', client=c, catalog_brand='dailyobjects'))
    live.assert_not_called()
    preview.assert_not_called()
    assert {t['name'] for t in c.responses.requests[0]['tools']} == {'list_categories', 'search_products', 'get_product'}


def test_preview_cart_request_explains_limit_without_generation():
    c = client('cart_change')
    reply = run(routing.answer_shopping_request('buy that', client=c, catalog_brand='dailyobjects'))
    assert 'preview' in reply.text
    assert not c.responses.requests


def test_independent_reads_run_concurrently_and_keep_call_ids():
    c = SimpleNamespace(responses=FakeResponses([
        fake_response('r1', [call('search_products', {'query': 'backpack'}, 'bag'),
                             call('search_products', {'query': 'tote'}, 'tote')]),
        fake_response('r2', [], 'Comparison based on the catalog.'),
    ]))
    barrier = Barrier(2, timeout=2)
    def read(brand, name, args):
        barrier.wait()  # Fails if execution is sequential.
        return {'products': [], 'suggestions_only': False, 'query': args['query']}
    with patch('telegram_ecommerce.agent.catalog.execute_preview_tool', side_effect=read):
        run(answer_catalog_question('Compare bags', client=c, catalog_brand='dailyobjects'))
    assert c.responses.requests[0]['parallel_tool_calls'] is True
    assert [item['call_id'] for item in c.responses.requests[1]['input']] == ['bag', 'tote']


def test_read_only_tool_injection_cannot_write():
    c = SimpleNamespace(responses=FakeResponses([fake_response('r1', [call('clear_cart', {})])]))
    with patch('telegram_ecommerce.agent.catalog.execute_tool') as tool, pytest.raises(CartActionRejectedError):
        run(answer_catalog_question('What is the price?', client=c, read_only=True, chat_id=42, request_id='telegram:1'))
    tool.assert_not_called()


@pytest.mark.parametrize('message', ['find blue backpack', 'not red notebook', 'under 500',
                                      'clear cart', 'which one?', 'make it three', 'hello and add notebook'])
def test_sentence_and_constraint_shortcuts_are_rejected(message):
    assert not routing.is_keyword_query(message)


def test_live_decision_batches_questions_without_history():
    c = client('simple_read', actions=set())
    result = run(routing.decide_route(c, 'How much is a notebook?', check_actions=True))
    assert result.route == 'simple_read'
    assert len(c.decisions.requests[0]['questions']) == 5
    assert c.decisions.requests[0]['input'] == 'How much is a notebook?'


def test_preview_handler_renders_choices_and_remembers_context(monkeypatch):
    from telegram_ecommerce.handlers import demo
    from telegram_ecommerce.agent.routing import ShoppingReply
    monkeypatch.setattr(demo, 'ACTIVE_BRAND', 'dailyobjects')
    activity = SimpleNamespace(update=AsyncMock(), finish=AsyncMock(return_value=True),
                               message=SimpleNamespace(message_id=30))
    source = SimpleNamespace(text='Find a backpack')
    update = SimpleNamespace(effective_message=source, effective_chat=SimpleNamespace(id=1))
    context = SimpleNamespace(user_data={})
    with patch.object(demo, 'ActivityMessage') as activity_class, \
         patch.object(demo, 'answer_shopping_request', new_callable=AsyncMock,
                      return_value=ShoppingReply(products=({'id': 12, 'name': 'Blue Backpack'},))) as answer:
        activity_class.return_value.start = AsyncMock(return_value=activity)
        run(demo.demo_text_search(update, context))
    assert answer.call_args.kwargs['catalog_brand'] == 'dailyobjects'
    state = demo._state(update, context)
    assert state['choice_message_id'] == 30
    assert state['product_ids'] == [12]
    assert 'Blue Backpack' in state['history'][-1]['content']
    assert activity.finish.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data == 'demo:1:choose:12'


def test_rate_limited_router_does_not_cascade_to_more_models(monkeypatch):
    monkeypatch.setattr(routing, '_PROVIDER_BLOCKED_UNTIL', 0.0)
    import httpx
    from openai import RateLimitError
    error = RateLimitError('Busy', response=httpx.Response(429, request=httpx.Request('POST', 'https://api.openai.com/v1/decisions')),
                           body={'code': 'rate_limit_exceeded'})
    c = SimpleNamespace(decisions=FakeResponses([error]), responses=FakeResponses([]))
    reply = run(routing.answer_shopping_request('Find me something for work', client=c, catalog_brand='dailyobjects'))
    assert '/search' in reply.text
    assert not c.responses.requests
    second = run(routing.answer_shopping_request('Find something else', client=c, catalog_brand='dailyobjects'))
    assert '/search' in second.text
    assert len(c.decisions.requests) == 1


def test_sdk_typed_decision_is_accepted():
    from openai.types.decision import Decision
    response = Decision.model_validate({
        'model': 'gpt-6-luna',
        'answers': [{'type': 'choice', 'name': 'route', 'choice': 'simple_read', 'confidence': .98,
                     'probabilities': [{'value': 'simple_read', 'probability': .98},
                                       {'value': 'other', 'probability': .02}]}],
        'usage': {'input_tokens': 100, 'output_tokens': 0, 'total_tokens': 100,
                  'input_tokens_details': {'cache_write_tokens': 0, 'cached_tokens': 0},
                  'output_tokens_details': {'reasoning_tokens': 0}},
    })
    c = SimpleNamespace(decisions=FakeResponses([response]))
    assert run(routing.decide_route(c, 'Find notebooks')).route == 'simple_read'


@pytest.mark.parametrize('message', ['what do we have', 'What do you have?', 'show me the catalog'])
def test_catalog_requests_work_without_an_api_client(message):
    assert run(routing.answer_shopping_request(message, client=object())).categories


def test_explicit_search_prompts_then_searches_without_ai(monkeypatch):
    from telegram_ecommerce.handlers import demo
    from telegram import ForceReply
    monkeypatch.setattr(demo, 'ACTIVE_BRAND', 'dailyobjects')
    message = SimpleNamespace(text='/search', reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=10))
    context = SimpleNamespace(user_data={}, args=[])
    run(demo.demo_search_command(update, context))
    assert 'What are you looking for?' in message.reply_text.call_args.args[0]
    assert isinstance(message.reply_text.call_args.kwargs['reply_markup'], ForceReply)
    message.text = 'blue backpack'
    with patch.object(demo, '_show_search', new_callable=AsyncMock) as search, \
         patch.object(demo, 'answer_shopping_request', new_callable=AsyncMock) as ai:
        run(demo.demo_text_search(update, context))
    search.assert_awaited_once_with(update, context, 1, 'blue backpack')
    ai.assert_not_called()
    assert 'search_requested_at' not in demo._state(update, context)


def test_explicit_search_state_is_chat_scoped_and_cancelable(monkeypatch):
    from telegram_ecommerce.handlers import demo
    monkeypatch.setattr(demo, 'ACTIVE_BRAND', 'dailyobjects')
    message = SimpleNamespace(text='/search', reply_text=AsyncMock())
    update = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=10))
    other = SimpleNamespace(effective_message=message, effective_chat=SimpleNamespace(id=11))
    context = SimpleNamespace(user_data={}, args=[])
    run(demo.demo_search_command(update, context))
    assert not demo._state(other, context).get('search_requested_at')
    run(demo.demo_cancel_command(update, context))
    assert not demo._state(update, context).get('search_requested_at')


def test_search_command_arguments_do_not_need_ai(monkeypatch):
    from telegram_ecommerce.handlers import demo
    monkeypatch.setattr(demo, 'ACTIVE_BRAND', 'dailyobjects')
    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()), effective_chat=SimpleNamespace(id=10))
    context = SimpleNamespace(user_data={}, args=['blue', 'backpack'])
    with patch.object(demo, '_show_search', new_callable=AsyncMock) as search:
        run(demo.demo_search_command(update, context))
    search.assert_awaited_once_with(update, context, 1, 'blue backpack')










@pytest.mark.parametrize('message', ['Can I buy one?', 'Make it one in my cart', 'How do I track an order?',
                                     'How can I cancel an order?', 'Where is checkout?'])
def test_preview_capabilities_do_not_depend_on_provider(message):
    reply=run(routing.answer_shopping_request(message, catalog_brand='dailyobjects', client=object()))
    assert 'preview' in reply.text and 'No order or cart change' in reply.text


def test_keyword_miss_does_not_spend_api_request():
    with patch.object(routing, 'execute_preview_tool', return_value={'products': [],'suggestions_only':False}):
        reply=run(routing.answer_shopping_request('unfindablething', catalog_brand='dailyobjects', client=object()))
    assert 'No matching' in reply.text
