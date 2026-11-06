"""Weak search matches offer explicit choices before the existing checkout flow."""

import asyncio
import importlib
import json
import os
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "123:test")
for key in ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME"):
    os.environ.setdefault(key, "test")

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from telegram.request import BaseRequest

search = importlib.import_module("telegram_ecommerce.handlers.search")


def product(identifier, name="Notebook", price="9.99"):
    return SimpleNamespace(id=identifier, name=name, price=Decimal(price), image_id=None,
                           description="Product details", quantity_purchased=0)


def result(products, suggestions_only):
    return SimpleNamespace(hits=[SimpleNamespace(product=item, score=0.5, match_kind="related",
                                                matched_fields=("name",)) for item in products],
                           suggestions_only=suggestions_only)


def context():
    return SimpleNamespace(user_data={}, chat_data={})


def update(*, chat_id=700, message_id=100, data=None):
    message = SimpleNamespace(text="notbook", message_id=message_id,
                              reply_text=AsyncMock(return_value=SimpleNamespace(message_id=message_id)))
    query = SimpleNamespace(data=data, answer=AsyncMock()) if data is not None else None
    return SimpleNamespace(message=message, effective_message=message, callback_query=query,
                           effective_chat=SimpleNamespace(id=chat_id), effective_user=SimpleNamespace(id=700))


def card_update(event, ctx, action):
    data = ctx.user_data[search.SEARCH_CHATS_KEY][event.effective_chat.id]
    current = data[search.products_data_key]["products"].actual()
    event.callback_query = SimpleNamespace(data=search._product_prefix(data, current) + action, answer=AsyncMock())
    return event


class SearchChoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_details_and_buy_cannot_target_a_new_selection(self):
        for new_item in (product(8, "Pen"), product(7, "Notebook")):
            with self.subTest(new_id=new_item.id):
                event, ctx = update(), context()
                old_item = product(7, "Notebook")
                with patch.object(search, "get_available_product", return_value=old_item), patch.object(
                    search, "send_a_detailed_product", new_callable=AsyncMock,
                ):
                    await search.show_search_choices(event, ctx, [old_item])
                    await search.choose_search_product(update(data="search_choice:7"), ctx)
                data = ctx.user_data[search.SEARCH_CHATS_KEY][700]
                old_prefix = search._product_prefix(data, old_item)
                await search.show_search_choices(event, ctx, [new_item])
                with patch.object(search, "send_a_shipping_message", new_callable=AsyncMock) as checkout:
                    self.assertIsNone(await search.send_a_shipping_message_callback(
                        update(data=old_prefix + "buy_product"), ctx))
                    checkout.assert_not_awaited()
                with patch.object(search, "get_available_product", return_value=new_item), patch.object(
                    search, "send_a_detailed_product", new_callable=AsyncMock,
                ):
                    await search.choose_search_product(update(data=f"search_choice:{new_item.id}"), ctx)
                current_prefix = search._product_prefix(data, new_item)
                self.assertNotEqual(old_prefix, current_prefix)
                with patch.object(search, "send_a_detailed_product", new_callable=AsyncMock) as details, patch.object(
                    search, "send_a_shipping_message", new_callable=AsyncMock,
                ) as checkout, patch("telegram_ecommerce.filters.decorators.user_exist", return_value=True) as registered:
                    old_details = update(data=old_prefix + "product_details")
                    old_buy = update(data=old_prefix + "buy_product")
                    self.assertIsNone(await search.catch_details(old_details, ctx))
                    self.assertIsNone(await search.send_a_shipping_message_callback(old_buy, ctx))
                    old_details.callback_query.answer.assert_awaited_once()
                    old_buy.callback_query.answer.assert_awaited_once()
                    details.assert_not_awaited()
                    checkout.assert_not_awaited()
                    registered.assert_not_called()
                    await search.catch_details(update(data=current_prefix + "product_details"), ctx)
                    await search.send_a_shipping_message_callback(update(data=current_prefix + "buy_product"), ctx)
                self.assertIs(details.await_args.args[2], new_item)
                self.assertIs(checkout.await_args.args[2], new_item)

    async def test_old_navigation_and_details_are_rejected_after_current_product_changes(self):
        event, ctx = update(), context()
        search.put_products_data_in_user_data(event, ctx)
        products = [product(7), product(8)]
        with patch.object(search, "search_catalog", return_value=result(products, False)), patch.object(
            search, "send_product_card", new_callable=AsyncMock,
        ):
            await search.get_list_of_products_that_match(event, ctx)
        data = ctx.user_data[search.SEARCH_CHATS_KEY][700]
        old_prefix = search._product_prefix(data, products[0])
        with patch.object(search, "send_a_product", new_callable=AsyncMock) as card, patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ) as details:
            await search.catch_next(update(data=old_prefix + "next_product"), ctx)
            card.reset_mock()
            for callback, action in ((search.catch_next, "next_product"),
                                     (search.catch_previus, "previus_product"),
                                     (search.catch_details, "product_details")):
                self.assertIsNone(await callback(update(data=old_prefix + action), ctx))
            card.assert_not_awaited()
            details.assert_not_awaited()
            self.assertIs(data[search.products_data_key]["products"].actual(), products[1])

    def test_card_callbacks_fit_telegram_byte_limit_for_largest_database_id(self):
        from telegram_ecommerce.tamplates.buttons import tamplate_for_show_a_detailed_product

        item = product(9223372036854775807)
        prefix = search._product_prefix({"generation": "a" * 12}, item)
        for markup in (search.tamplate_for_show_a_list_of_products(prefix, context()),
                       tamplate_for_show_a_detailed_product(prefix, context())):
            for row in markup.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode("utf-8")), 64)
                    self.assertRegex(button.callback_data, search.PRODUCT_CARD_PATTERN)

    async def test_same_user_keeps_independent_choices_and_selections_in_two_chats(self):
        shared_user_data = {}
        first_ctx = SimpleNamespace(user_data=shared_user_data, chat_data={})
        second_ctx = SimpleNamespace(user_data=shared_user_data, chat_data={})
        first, second = update(chat_id=700), update(chat_id=701)
        first_product, second_product = product(7, "Notebook"), product(8, "Pen")
        await asyncio.gather(
            search.show_search_choices(first, first_ctx, [first_product]),
            search.show_search_choices(second, second_ctx, [second_product]),
        )
        buckets = shared_user_data[search.SEARCH_CHATS_KEY]
        self.assertEqual(buckets[700][search.SEARCH_CHOICES_KEY]["options"], {"search_choice:7": 7})
        self.assertEqual(buckets[701][search.SEARCH_CHOICES_KEY]["options"], {"search_choice:8": 8})
        # Message IDs may coincide across chats; membership must still be local.
        with patch.object(search, "get_available_product") as lookup:
            self.assertIsNone(await search.choose_search_product(update(chat_id=700, data="search_choice:8"), first_ctx))
            self.assertIsNone(await search.choose_search_product(update(chat_id=701, data="search_choice:7"), second_ctx))
        lookup.assert_not_called()
        available = {7: first_product, 8: second_product}
        with patch.object(search, "get_available_product", side_effect=available.get), patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ):
            states = await asyncio.gather(
                search.choose_search_product(update(chat_id=700, data="search_choice:7"), first_ctx),
                search.choose_search_product(update(chat_id=701, data="search_choice:8"), second_ctx),
            )
        self.assertEqual(states, [search.BUY_PROCESS, search.BUY_PROCESS])
        self.assertEqual(buckets[700][search.products_data_key]["products"].actual().id, 7)
        self.assertEqual(buckets[701][search.products_data_key]["products"].actual().id, 8)
        self.assertEqual(first_ctx.chat_data[search.PRODUCT_SELECTION_KEY]["id"], 7)
        self.assertEqual(second_ctx.chat_data[search.PRODUCT_SELECTION_KEY]["id"], 8)

    async def test_navigation_checkout_restart_and_cancel_only_affect_current_chat(self):
        unrelated_flow = {"products": "another browsing flow"}
        shared_user_data = {search.products_data_key: unrelated_flow}
        first_ctx = SimpleNamespace(user_data=shared_user_data, chat_data={})
        second_ctx = SimpleNamespace(user_data=shared_user_data, chat_data={})
        first, second = update(chat_id=700), update(chat_id=701)
        first_products = [product(7), product(8)]
        second_products = [product(9), product(10)]
        await search.ask_for_term_to_search(first, first_ctx)
        await search.ask_for_term_to_search(second, second_ctx)
        with patch.object(search, "search_catalog", side_effect=[result(first_products, False), result(second_products, False)]), patch.object(
            search, "send_product_card", new_callable=AsyncMock,
        ):
            await search.get_list_of_products_that_match(first, first_ctx)
            await search.get_list_of_products_that_match(second, second_ctx)
        buckets = shared_user_data[search.SEARCH_CHATS_KEY]
        with patch.object(search, "send_a_product", new_callable=AsyncMock) as card, patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ) as details:
            await search.catch_next(card_update(first, first_ctx, "next_product"), first_ctx)
            self.assertEqual(card.await_args.args[2].id, 8)
            self.assertEqual(buckets[701][search.products_data_key]["products"].actual().id, 9)
            await search.catch_previus(card_update(first, first_ctx, "previus_product"), first_ctx)
            self.assertEqual(card.await_args.args[2].id, 7)
            await search.catch_details(card_update(second, second_ctx, "product_details"), second_ctx)
            self.assertEqual(details.await_args.args[2].id, 9)
        with patch("telegram_ecommerce.filters.decorators.user_exist", return_value=True), patch.object(
            search, "send_a_shipping_message", new_callable=AsyncMock,
        ) as checkout:
            await search.send_a_shipping_message_callback(card_update(first, first_ctx, "buy_product"), first_ctx)
            await search.send_a_shipping_message_callback(card_update(second, second_ctx, "buy_product"), second_ctx)
        self.assertEqual([call.args[2].id for call in checkout.await_args_list], [7, 9])
        await search.show_search_choices(first, first_ctx, first_products)
        await search.show_search_choices(second, second_ctx, second_products)
        await search.cancel_search(first, first_ctx)
        self.assertNotIn(700, buckets)
        self.assertIn(search.SEARCH_CHOICES_KEY, buckets[701])
        self.assertEqual(buckets[701][search.products_data_key]["products"].actual().id, 9)
        await search.ask_for_term_to_search(first, first_ctx)
        self.assertIn(search.SEARCH_CHOICES_KEY, buckets[701])
        self.assertEqual(buckets[701][search.products_data_key]["products"].actual().id, 9)
        self.assertIs(shared_user_data[search.products_data_key], unrelated_flow)

    async def test_related_results_show_five_named_choices_without_selecting(self):
        event, ctx = update(), context()
        search.put_products_data_in_user_data(event, ctx)
        products = [product(index, f"Notebook {index}") for index in range(1, 8)]
        with patch.object(search, "search_catalog", return_value=result(products, True)) as lookup, patch.object(
            search, "send_product_card", new_callable=AsyncMock,
        ) as card:
            state = await search.get_list_of_products_that_match(event, ctx)
        self.assertEqual(state, search.SELECT_SEARCH_CHOICE)
        lookup.assert_called_once_with("notbook")
        card.assert_not_awaited()
        sent = event.message.reply_text.await_args
        self.assertEqual(sent.args[0], "Closest matches — choose a product:")
        buttons = sent.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(buttons), 5)
        self.assertEqual(buttons[0][0].text, "Notebook 1 · USD 9.99")
        self.assertEqual(buttons[-1][0].callback_data, "search_choice:5")
        self.assertEqual(ctx.user_data[search.SEARCH_CHATS_KEY][700][search.SEARCH_CHOICES_KEY]["message_id"], 100)

    async def test_exact_search_keeps_existing_product_list_navigation(self):
        event, ctx = update(), context()
        search.put_products_data_in_user_data(event, ctx)
        products = [product(7), product(8, "Sketchbook")]
        with patch.object(search, "search_catalog", return_value=result(products, False)), patch.object(
            search, "send_product_card", new_callable=AsyncMock,
        ) as card, patch.object(search, "send_a_product", new_callable=AsyncMock) as next_card:
            state = await search.get_list_of_products_that_match(event, ctx)
            self.assertEqual(card.await_args.args[1], products[0])
            await search.catch_next(card_update(event, ctx, "next_product"), ctx)
        self.assertEqual(state, search.SHOW_LIST_OF_PRODUCT_THAT_MATCH)
        self.assertEqual(next_card.await_args.args[2], products[1])
        self.assertNotIn(search.SEARCH_CHOICES_KEY, ctx.user_data.get(search.SEARCH_CHATS_KEY, {}).get(700, {}))

    async def test_no_results_keep_existing_empty_search_response(self):
        event, ctx = update(), context()
        search.put_products_data_in_user_data(event, ctx)
        with patch.object(search, "search_catalog", return_value=result([], False)):
            self.assertEqual(await search.get_list_of_products_that_match(event, ctx), search.END)
        self.assertIn("no products", event.message.reply_text.await_args_list[0].args[0])

    async def test_selection_loads_fresh_product_and_opens_details_only(self):
        event, ctx = update(), context()
        await search.show_search_choices(event, ctx, [product(7)])
        selected = update(data="search_choice:7")
        refreshed = product(7, "Updated notebook", "12.50")
        with patch.object(search.time, "time", return_value=1000.0), patch.object(
            search, "get_available_product", return_value=refreshed,
        ) as lookup, patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ) as details, patch.object(search, "send_a_shipping_message", new_callable=AsyncMock) as checkout:
            state = await search.choose_search_product(selected, ctx)
        lookup.assert_called_once_with(7)
        data = ctx.user_data[search.SEARCH_CHATS_KEY][700]
        details.assert_awaited_once_with(selected, ctx, refreshed, search._product_prefix(data, refreshed))
        checkout.assert_not_awaited()
        self.assertEqual(state, search.BUY_PROCESS)
        self.assertIs(ctx.user_data[search.SEARCH_CHATS_KEY][700][search.products_data_key]["products"].actual(), refreshed)
        self.assertNotIn(search.SEARCH_CHOICES_KEY, ctx.user_data.get(search.SEARCH_CHATS_KEY, {}).get(700, {}))
        self.assertEqual(ctx.chat_data[search.PRODUCT_SELECTION_KEY],
                         {"id": 7, "name": "Updated notebook", "selected_at": 1000.0})

    async def test_unknown_malformed_cross_chat_and_old_message_choices_never_query_database(self):
        for selected in (update(data="search_choice:999"), update(data="search_choice:7 OR 1=1"),
                         update(data="search_choice:7", chat_id=701),
                         update(data="search_choice:7", message_id=99), update(data="unrelated:7")):
            ctx = context()
            await search.show_search_choices(update(), ctx, [product(7)])
            with patch.object(search, "get_available_product") as lookup:
                self.assertIsNone(await search.choose_search_product(selected, ctx))
            lookup.assert_not_called()
            self.assertTrue(selected.callback_query.answer.await_args.kwargs["show_alert"])
            self.assertNotIn(search.PRODUCT_SELECTION_KEY, ctx.chat_data)

    async def test_another_user_cannot_use_someone_elses_stored_options(self):
        selected = update(data="search_choice:7")
        with patch.object(search, "get_available_product") as lookup:
            self.assertIsNone(await search.choose_search_product(selected, context()))
        lookup.assert_not_called()

    async def test_sold_out_or_removed_option_leaves_other_choices_available(self):
        event, ctx = update(), context()
        await search.show_search_choices(event, ctx, [product(7), product(8)])
        selected = update(data="search_choice:7")
        with patch.object(search, "get_available_product", return_value=None), patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ) as details:
            self.assertIsNone(await search.choose_search_product(selected, ctx))
        details.assert_not_awaited()
        self.assertIn("search_choice:8", ctx.user_data[search.SEARCH_CHATS_KEY][700][search.SEARCH_CHOICES_KEY]["options"])
        self.assertIn("no longer available", selected.callback_query.answer.await_args.args[0])
        self.assertNotIn(search.PRODUCT_SELECTION_KEY, ctx.chat_data)

    async def test_new_search_and_cancel_invalidate_previous_options(self):
        for callback in (search.ask_for_term_to_search, search.cancel_search):
            ctx = context()
            await search.show_search_choices(update(), ctx, [product(7)])
            await callback(update(), ctx)
            self.assertNotIn(search.SEARCH_CHOICES_KEY, ctx.user_data.get(search.SEARCH_CHATS_KEY, {}).get(700, {}))

    async def test_helper_accepts_lightweight_products_and_localizes_plain_short_labels(self):
        event, ctx = update(), context()
        ctx.user_data["language"] = "pt"
        item = SimpleNamespace(id=7, name="  <Notebook>\n\u202e " + "A" * 100, price="9.99")
        await search.show_search_choices(event, ctx, [item, item])
        sent = event.message.reply_text.await_args
        self.assertIn("escolha um produto", sent.args[0])
        buttons = sent.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(len(buttons), 1)
        self.assertLessEqual(len(buttons[0][0].text), 64)
        self.assertTrue(buttons[0][0].text.startswith("<Notebook> "))
        self.assertNotIn("\u202e", buttons[0][0].text)


class TelegramStub(BaseRequest):
    def __init__(self):
        self.messages = []
        self.alerts = []

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        bot = {"id": 123, "is_bot": True, "first_name": "Test", "username": "TestBot"}
        if endpoint == "getMe":
            value = bot
        elif endpoint == "answerCallbackQuery":
            if request_data.parameters.get("text"):
                self.alerts.append(request_data.parameters["text"])
            value = True
        elif endpoint == "editMessageReplyMarkup":
            value = True
        elif endpoint in {"sendMessage", "editMessageText"}:
            params = request_data.parameters
            self.messages.append(params["text"])
            value = {"message_id": params.get("message_id", len(self.messages)), "date": 1, "from": bot,
                     "chat": {"id": int(params["chat_id"]), "type": "private"}, "text": params["text"]}
        else:
            raise AssertionError(endpoint)
        return 200, json.dumps({"ok": True, "result": value}).encode()


def inbound(bot, chat_id, text, *, data=None, message_id=1):
    user = {"id": chat_id, "is_bot": False, "first_name": "Shopper"}
    message = {"message_id": message_id, "date": 1, "from": user,
               "chat": {"id": chat_id, "type": "private"}, "text": text}
    if data:
        payload = {"callback_query": {"id": "test-callback", "from": user,
                                      "chat_instance": "test-chat", "data": data, "message": message}}
    else:
        if text.startswith("/"):
            message["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        payload = {"message": message}
    return Update.de_json({"update_id": message_id, **payload}, bot)


class SearchChoiceRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_card_callbacks_after_a_new_search_cannot_checkout_the_new_product(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token("123:test").request(transport).build()
        app.add_handler(search.search)
        notebook, pen = product(7), product(8, "Pen")

        async def assistant(update, context):
            item = pen if "pen" in update.message.text else notebook
            await search.show_search_choices(update, context, [item])

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, assistant))
        with patch.object(search, "get_available_product", side_effect={7: notebook, 8: pen}.get), patch(
            "telegram_ecommerce.tamplates.products.count_occurrence_of_specified_rating", return_value=0,
        ), patch("telegram_ecommerce.filters.decorators.user_exist", return_value=True), patch.object(
            search, "send_a_shipping_message", new_callable=AsyncMock,
        ) as checkout:
            async with app:
                await app.process_update(inbound(app.bot, 8003, "Find notebook"))
                await app.process_update(inbound(app.bot, 8003, transport.messages[-1], data="search_choice:7"))
                data = app.user_data[8003][search.SEARCH_CHATS_KEY][8003]
                old_prefix = search._product_prefix(data, notebook)
                await app.process_update(inbound(app.bot, 8003, "Find pen"))
                choice_message_id = data[search.SEARCH_CHOICES_KEY]["message_id"]
                await app.process_update(inbound(app.bot, 8003, transport.messages[-1], data="search_choice:8",
                                                message_id=choice_message_id))
                current_prefix = search._product_prefix(data, pen)
                message_count = len(transport.messages)
                for action in ("product_details", "buy_product"):
                    await app.process_update(inbound(app.bot, 8003, "Old notebook card", data=old_prefix + action))
                checkout.assert_not_awaited()
                self.assertEqual(len(transport.messages), message_count)
                self.assertEqual(len(transport.alerts), 2)
                await app.process_update(inbound(app.bot, 8003, "Current pen card",
                                                data=current_prefix + "buy_product", message_id=choice_message_id))
        checkout.assert_awaited_once()
        self.assertIs(checkout.await_args.args[2], pen)

    async def test_choices_from_plain_assistant_flow_enter_existing_details_and_checkout(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token("123:test").request(transport).build()
        app.add_handler(search.search)
        item = product(7)

        async def assistant(update, context):
            await search.show_search_choices(update, context, [item])

        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, assistant))
        with patch.object(search, "get_available_product", return_value=item), patch(
            "telegram_ecommerce.tamplates.products.count_occurrence_of_specified_rating", return_value=0,
        ), patch("telegram_ecommerce.filters.decorators.user_exist", return_value=True), patch.object(
            search, "send_a_shipping_message", new_callable=AsyncMock,
        ) as checkout:
            async with app:
                await app.process_update(inbound(app.bot, 8001, "Find a notbook"))
                await app.process_update(inbound(app.bot, 8001, transport.messages[-1], data="search_choice:7"))
                checkout.assert_not_awaited()
                self.assertIn("Product details", transport.messages[-1])
                self.assertIn("9.99", transport.messages[-1])
                data = app.user_data[8001][search.SEARCH_CHATS_KEY][8001]
                await app.process_update(inbound(app.bot, 8001, transport.messages[-1],
                                                data=search._product_prefix(data, item) + "buy_product"))
        checkout.assert_awaited_once()
        self.assertEqual(checkout.await_args.args[2].id, 7)

    async def test_normal_text_after_weak_matches_reaches_assistant(self):
        transport = TelegramStub()
        app = ApplicationBuilder().token("123:test").request(transport).build()
        assistant = AsyncMock()
        app.add_handler(search.search)
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, assistant))
        with patch.object(search, "search_catalog", return_value=result([product(7)], True)):
            async with app:
                await app.process_update(inbound(app.bot, 8002, "/search"))
                await app.process_update(inbound(app.bot, 8002, "notbook", message_id=2))
                await app.process_update(inbound(app.bot, 8002, "Add two notebooks", message_id=3))
        assistant.assert_awaited_once()
        self.assertEqual(assistant.await_args.args[0].message.text, "Add two notebooks")

    async def test_choice_checkout_still_requires_registration(self):
        event, ctx = update(), context()
        await search.show_search_choices(event, ctx, [product(7)])
        selected = update(data="search_choice:7")
        with patch.object(search, "get_available_product", return_value=product(7)), patch.object(
            search, "send_a_detailed_product", new_callable=AsyncMock,
        ):
            await search.choose_search_product(selected, ctx)
        with patch("telegram_ecommerce.filters.decorators.user_exist", return_value=False), patch.object(
            search, "send_a_shipping_message", new_callable=AsyncMock,
        ) as checkout:
            state = await search.send_a_shipping_message_callback(card_update(selected, ctx, "buy_product"), ctx)
        checkout.assert_not_awaited()
        self.assertEqual(state, search.END)
        selected.effective_message.reply_text.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
