import time
import unicodedata
from functools import wraps
from secrets import token_hex

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    filters,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler)

from telegram_ecommerce.filters.decorators import execute_if_user_exist
from telegram_ecommerce.tamplates.buttons import tamplate_for_show_a_list_of_products
from telegram_ecommerce.tamplates.buy_callbacks import send_a_shipping_message
from telegram_ecommerce.database.query import get_available_product, search_catalog
from telegram_ecommerce.language import get_text
from telegram_ecommerce.utils.consts import currency
from telegram_ecommerce.tamplates.products import (
    send_a_product,
    send_a_detailed_product,
    send_product_card,
    get_text_for_product,
    ListProductIterator)


(END                            ,
ASK_FOR_TERM_TO_SEARCH          ,
GET_LIST_OF_PRODUCTS            ,
SHOW_LIST_OF_PRODUCT_THAT_MATCH ,
BUY_PROCESS                     ,
SELECT_SEARCH_CHOICE             ) = range(-1, 5)


products_data_key = "list_of_products"
pattern_identifier = "search_product:"
PRODUCT_CARD_PATTERN = r"^search_product:[0-9a-f]{12}:[1-9][0-9]{0,18}:"
PATTERN_TO_CATCH_THE_PREVIUS_PRODUCT = 'previus_product'
PATTERN_TO_CATCH_THE_NEXT_PRODUCT = 'next_product'
PATTERN_TO_CATCH_THE_VIEW_DETAILS = 'product_details'
PATTERN_TO_CATCH_THE_BUY_BUTTON = 'buy_product'
SEARCH_CHOICE_PREFIX = "search_choice:"
SEARCH_CHOICES_KEY = "search_choices"
SEARCH_CHATS_KEY = "search_chats"
PRODUCT_SELECTION_KEY = "shopping_product_selection"


def _search_data(update, context):
    """Keep search state per chat while retaining user ownership in groups."""
    return context.user_data.setdefault(SEARCH_CHATS_KEY, {}).setdefault(update.effective_chat.id, {})


def _product_prefix(data, product):
    return f"{pattern_identifier}{data['generation']}:{product.id}:"


async def reject_search_card(update, context):
    await update.callback_query.answer(get_text("search_choice_stale", context), show_alert=True)
    return None


def _current_product_card(action):
    """Bind every card action to the search and product that displayed it."""
    def decorate(callback):
        @wraps(callback)
        async def checked(update, context):
            data = _search_data(update, context)
            products = data.get(products_data_key, {}).get("products")
            if (not data.get("generation") or not isinstance(products, ListProductIterator)
                    or products.is_empty()
                    or update.callback_query.data != _product_prefix(data, products.actual()) + action):
                return await reject_search_card(update, context)
            return await callback(update, context)
        return checked
    return decorate


def put_products_data_in_user_data(update, context):
    data = _search_data(update, context)
    data["generation"] = token_hex(6)
    data[products_data_key] = {
        "products": []
    }


def save_products_in_user_data(update, context, string_to_search):
    result = search_catalog(string_to_search)
    products = ListProductIterator(*(hit.product for hit in result.hits))
    _search_data(update, context)[products_data_key]["products"] = products
    return result


def _choice_label(product):
    name = " ".join(product.name.split())
    name = "".join(char for char in name if not unicodedata.category(char).startswith("C"))
    price = f" · {currency} {product.price}"
    return name[:max(1, 64 - len(price))] + price


async def show_search_choices(update, context, products, *, activity=None, notice=None, related=True):
    """Offer a small set of related products without selecting or buying one."""
    data = _search_data(update, context)
    data["generation"] = token_hex(6)
    products = list({product.id: product for product in products}.values())[:5]
    if not products:
        data.pop(SEARCH_CHOICES_KEY, None)
        await update.effective_message.reply_text(get_text("without_product_in_this_search", context))
        return END
    options = {f"{SEARCH_CHOICE_PREFIX}{product.id}": product.id for product in products}
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(_choice_label(product), callback_data=callback)]
        for product, callback in zip(products, options)
    ])
    text = get_text("search_closest_matches" if related else "search_matching_choices", context)
    if notice:
        text = notice + '\n\n' + text
    if activity is None:
        message = await update.effective_message.reply_text(text, reply_markup=markup)
    else:
        if not await activity.finish(text, reply_markup=markup):
            data.pop(SEARCH_CHOICES_KEY, None)
            return END
        message = activity.message
    data[SEARCH_CHOICES_KEY] = {
        "chat_id": update.effective_chat.id,
        "message_id": message.message_id,
        "options": options,
    }
    return SELECT_SEARCH_CHOICE


async def choose_search_product(update, context):
    query = update.callback_query
    data = _search_data(update, context)
    choices = data.get(SEARCH_CHOICES_KEY, {})
    product_id = choices.get("options", {}).get(query.data) if isinstance(query.data, str) else None
    if (product_id is None or choices.get("chat_id") != update.effective_chat.id
            or choices.get("message_id") != update.effective_message.message_id):
        await query.answer(get_text("search_choice_stale", context), show_alert=True)
        return None
    product = get_available_product(product_id)
    if product is None:
        await query.answer(get_text("search_choice_stale", context), show_alert=True)
        return None
    products = ListProductIterator(product)
    products.next()
    data["generation"] = token_hex(6)
    data[products_data_key] = {"products": products}
    await send_a_detailed_product(update, context, product, _product_prefix(data, product))
    context.chat_data[PRODUCT_SELECTION_KEY] = {
        "id": product.id, "name": product.name, "selected_at": time.time(),
    }
    data.pop(SEARCH_CHOICES_KEY, None)
    return BUY_PROCESS


async def ask_for_term_to_search(update, context):
    put_products_data_in_user_data(update, context)
    _search_data(update, context).pop(SEARCH_CHOICES_KEY, None)
    text = get_text("ask_for_term_to_search", context)
    await update.message.reply_text(text)
    return GET_LIST_OF_PRODUCTS


async def get_list_of_products_that_match(update, context):
    string_to_search = update.message.text
    result = save_products_in_user_data(update, context, string_to_search)
    if result.suggestions_only and result.hits:
        return await show_search_choices(update, context, [hit.product for hit in result.hits])
    if not _search_data(update, context)[products_data_key]["products"].is_empty():
        text = get_text("OK", context)
        await update.message.reply_text(text)
        await show_list_of_product_that_match(update, context)
        return SHOW_LIST_OF_PRODUCT_THAT_MATCH
    else:
        text = get_text("without_product_in_this_search", context)
        await update.message.reply_text(text)
        await cancel_search(update, context)
        return END


async def show_list_of_product_that_match(update, context):
    data = _search_data(update, context)
    product = data[products_data_key]["products"].next()
    markup = tamplate_for_show_a_list_of_products(
        _product_prefix(data, product), context)
    text = get_text_for_product(product, context)
    await send_product_card(update, product, text, markup)
    return SHOW_LIST_OF_PRODUCT_THAT_MATCH


@_current_product_card(PATTERN_TO_CATCH_THE_PREVIUS_PRODUCT)
async def catch_previus(update, context):
    data = _search_data(update, context)
    product = data[products_data_key]["products"].previus()
    await send_a_product(update, context, product, _product_prefix(data, product))
    return SHOW_LIST_OF_PRODUCT_THAT_MATCH


@_current_product_card(PATTERN_TO_CATCH_THE_NEXT_PRODUCT)
async def catch_next(update, context):
    data = _search_data(update, context)
    product = data[products_data_key]["products"].next()
    await send_a_product(update, context, product, _product_prefix(data, product))
    return SHOW_LIST_OF_PRODUCT_THAT_MATCH


@_current_product_card(PATTERN_TO_CATCH_THE_VIEW_DETAILS)
async def catch_details(update, context):
    data = _search_data(update, context)
    product = data[products_data_key]["products"].actual()
    await send_a_detailed_product(update, context, product, _product_prefix(data, product))
    return BUY_PROCESS 


@_current_product_card(PATTERN_TO_CATCH_THE_BUY_BUTTON)
@execute_if_user_exist
async def send_a_shipping_message_callback(update, context):
    data = _search_data(update, context)
    product = data[products_data_key]["products"].actual()
    await send_a_shipping_message(update, context, product, _product_prefix(data, product))
    return END


async def cancel_search(update, context):
    context.user_data.get(SEARCH_CHATS_KEY, {}).pop(update.effective_chat.id, None)
    text = get_text("canceled_operation", context)
    await update.message.reply_text(text) 
    return END


search_command = (
    CommandHandler("search", ask_for_term_to_search))
search_choice_callback = CallbackQueryHandler(choose_search_product, pattern=r"^search_choice:")
stale_card_callback = CallbackQueryHandler(reject_search_card, pattern=r"^search_product:")


search = ConversationHandler(
    entry_points = [search_command, search_choice_callback, stale_card_callback],
    states = {
        SELECT_SEARCH_CHOICE: [search_choice_callback],
        ASK_FOR_TERM_TO_SEARCH : [
            MessageHandler(
                filters.TEXT,
                ask_for_term_to_search)
            ],
        GET_LIST_OF_PRODUCTS : [
            MessageHandler(
                filters.TEXT, 
                get_list_of_products_that_match)
            ],
        SHOW_LIST_OF_PRODUCT_THAT_MATCH : [
            CallbackQueryHandler(
                catch_next, 
                pattern = PRODUCT_CARD_PATTERN +
                PATTERN_TO_CATCH_THE_NEXT_PRODUCT + "$"),
            CallbackQueryHandler(
                catch_previus, 
                pattern = PRODUCT_CARD_PATTERN +
                PATTERN_TO_CATCH_THE_PREVIUS_PRODUCT + "$"),
            CallbackQueryHandler(
                catch_details,
                pattern = PRODUCT_CARD_PATTERN +
                PATTERN_TO_CATCH_THE_VIEW_DETAILS + "$")
            ],
        BUY_PROCESS : [
            CallbackQueryHandler(
                catch_previus, 
                pattern = PRODUCT_CARD_PATTERN +
                PATTERN_TO_CATCH_THE_PREVIUS_PRODUCT + "$"),
            CallbackQueryHandler(
                send_a_shipping_message_callback, 
                pattern = PRODUCT_CARD_PATTERN +
                PATTERN_TO_CATCH_THE_BUY_BUTTON + "$")
            ]
        },
    fallbacks = [CommandHandler("cancel", cancel_search), search_choice_callback, stale_card_callback]
    )


