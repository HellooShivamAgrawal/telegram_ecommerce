"""Read-only owner demos backed by isolated catalog snapshots."""

import asyncio
import time
import re
from os import environ
from pathlib import Path

from sqlalchemy import func, select
from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, InputMediaPhoto, WebAppInfo
from telegram.error import BadRequest, TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from telegram_ecommerce.database.models import DemoProduct, Session
from telegram_ecommerce.database.search import search_demo_catalog
from telegram_ecommerce.agent.routing import answer_shopping_request
from telegram_ecommerce.agent.progress import ActivityMessage
from telegram_ecommerce.utils.log import logger
from telegram_ecommerce.miniapp.links import mini_app_url
from telegram_ecommerce.database.product_facts import product_facts


BRANDS = ('dailyobjects', 'blue_tokai', 'nicobar', 'ellementry', 'jaipur_rugs')
NAMES = ('DailyObjects', 'Blue Tokai', 'Nicobar', 'ellementry', 'Jaipur Rugs')
ACTIVE_BRAND = environ.get('DEMO_BRAND', '').strip().lower()
if ACTIVE_BRAND and ACTIVE_BRAND not in BRANDS:
    raise ValueError(f'DEMO_BRAND must be one of: {", ".join(BRANDS)}')
ROOT = Path(__file__).resolve().parents[2] / 'data' / 'demo_catalogs'
STATE_KEY = 'demo_catalog_state'
PAGE_SIZE = 10
PICKER_SIZE = 9
NO_PHOTO = Path(__file__).resolve().parents[2] / 'assets' / 'product-photo-unavailable.png'


def _delivery_error(error):
    """Keep useful provider errors without logging request URLs or bot tokens."""
    message = re.sub(r'https?://\S+', '[url]', str(error))
    return re.sub(r'\d{5,}:[A-Za-z0-9_-]+', '[token]', message)[:250]


def _state(update, context):
    return context.user_data.setdefault(STATE_KEY, {}).setdefault(update.effective_chat.id, {})


def _categories(brand):
    with Session() as session:
        return session.execute(
            select(DemoProduct.category, func.count(DemoProduct.id))
            .where(DemoProduct.brand == brand)
            .group_by(DemoProduct.category)
            .order_by(func.count(DemoProduct.id).desc(), DemoProduct.category)
        ).all()


def _products(brand, *, category=None, query=None):
    statement = select(DemoProduct.id).where(DemoProduct.brand == brand)
    if category:
        statement = statement.where(DemoProduct.category == category)
    if query:
        return [hit.product.id for hit in search_demo_catalog(query, brand).hits]
    statement = statement.order_by(DemoProduct.name)
    with Session() as session:
        return list(session.scalars(statement))


def _product(product_id, brand):
    with Session() as session:
        return session.scalar(select(DemoProduct).where(
            DemoProduct.id == product_id, DemoProduct.brand == brand,
        ))


def _money(value):
    return f'₹{value:,.0f}' if value is not None else 'Price on request'


def _card_text(product, brand_number, detailed=False):
    display_name = product.name
    subtitle = product.details.get('subtitle')
    if subtitle and subtitle.casefold() != display_name.casefold():
        display_name += f' · {subtitle}'
    lines = [display_name, _money(product.price)]
    if product.mrp and product.price and product.mrp > product.price:
        lines[-1] += f' · MRP {_money(product.mrp)}'
    if product.category:
        lines.append(product.category)
    if product.available is False:
        lines.append('Currently unavailable on source store')
    if detailed:
        custom = product.details.get('custom') or {}
        for key in ('material', 'color', 'size_options', 'weaving', 'construction', 'shape',
                    'texture', 'pile thickness', 'option_values'):
            value = custom.get(key)
            if isinstance(value, dict):
                value = ', '.join(f'{k}: {", ".join(map(str, v))}' for k, v in value.items() if isinstance(v, list))
            elif isinstance(value, list):
                value = ', '.join(map(str, value[:8]))
            if value:
                lines.append(f'{key.replace("_", " ").title()}: {str(value)[:120]}')
    if detailed:
        facts = product_facts(product)
        for key, value in facts['specifications'].items():
            if key.lower() in {'dimensions', 'capacity', 'weight', 'compatibility'}:
                lines.append(f'{key}: {value}')
        if facts['fit_note']:
            lines.append(facts['fit_note'])
    if detailed and product.description:
        lines.append('')
        lines.append(product.description[:430 if detailed else 170].strip())
    text = '\n'.join(lines)
    return text[:940] + f'\n\n{NAMES[brand_number - 1]} · Catalog preview'


def _catalog_buttons(number, state):
    categories = state['categories']
    page = state.get('category_page', 0)
    start = page * PAGE_SIZE
    buttons = [[InlineKeyboardButton(f'{name or "Other"} ({count})',
                                     callback_data=f'demo:{number}:cat:{start + offset}')]
               for offset, (name, count) in enumerate(categories[start:start + PAGE_SIZE])]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton('‹ Categories', callback_data=f'demo:{number}:cats:{page - 1}'))
    if start + PAGE_SIZE < len(categories):
        navigation.append(InlineKeyboardButton('More categories ›', callback_data=f'demo:{number}:cats:{page + 1}'))
    if navigation:
        buttons.append(navigation)
    if mini_app_url():
        buttons.insert(0, [InlineKeyboardButton('Open shop · photos & search', web_app=WebAppInfo(mini_app_url()))])
    return InlineKeyboardMarkup(buttons)


async def _show_categories(update, context, number, *, edit=False):
    state = _state(update, context)
    brand = BRANDS[number - 1]
    categories = await asyncio.to_thread(_categories, brand)
    if not categories:
        await update.effective_message.reply_text('This demo catalog has not been imported yet.')
        return
    state.update(brand=brand, categories=categories)
    page = state.get('category_page', 0)
    state['category_page'] = min(page, (len(categories) - 1) // PAGE_SIZE)
    if mini_app_url():
        deliver = update.effective_message.edit_text if edit else update.effective_message.reply_text
        await deliver(f'{NAMES[number - 1]}\nFind your everyday essentials. Browse the shop or tell me what you’re looking for.\n\nCatalog preview · checkout is unavailable.', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Open shop', web_app=WebAppInfo(mini_app_url()))]]))
        return
    overview = ', '.join((name or 'Other')[:60] for name, _ in categories[:6])
    deliver = update.effective_message.edit_text if edit else update.effective_message.reply_text
    await deliver(
        f'{NAMES[number - 1]}\nExplore {overview}.\n\nChoose a category below, or use /search to find a product.\nCatalog preview · checkout is unavailable.',
        reply_markup=_catalog_buttons(number, state),
    )


async def _show_product(update, context, number, detailed=False, *, edit=False):
    state = _state(update, context)
    ids = state.get('product_ids') or []
    if not ids:
        await update.effective_message.reply_text('No products found. Try another category or search.')
        return
    index = state.get('index', 0) % len(ids)
    state['index'] = index
    product = await asyncio.to_thread(_product, ids[index], BRANDS[number - 1])
    if product is None:
        await update.effective_message.reply_text('This product is no longer in the demo catalog.')
        return
    state.update(opened_product_id=product.id, opened_at=time.time())
    buttons = [
        [InlineKeyboardButton('‹ Previous', callback_data=f'demo:{number}:prev'),
         InlineKeyboardButton(f'{index + 1}/{len(ids)}', callback_data=f'demo:{number}:noop'),
         InlineKeyboardButton('Next ›', callback_data=f'demo:{number}:next')],
        [InlineKeyboardButton('Details' if not detailed else 'Summary',
                              callback_data=f'demo:{number}:{"summary" if detailed else "detail"}'),
         InlineKeyboardButton('Browse 9', callback_data=f'demo:{number}:picker:{index // PICKER_SIZE}')],
        [InlineKeyboardButton('Categories', callback_data=f'demo:{number}:back')],
    ]
    if mini_app_url():
        buttons.insert(0, [InlineKeyboardButton('View photo grid', web_app=WebAppInfo(mini_app_url(q=state.get('search_query', ''), category=state.get('web_category', ''))))])
    markup = InlineKeyboardMarkup(buttons)
    caption = _card_text(product, number, detailed)
    path = ROOT / product.demo_image if product.demo_image else None
    photo = None
    if path and path.is_file() and path.resolve().is_relative_to(ROOT.resolve()):
        try:
            from telegram_ecommerce.miniapp.images import thumbnail
            photo = InputFile(await asyncio.to_thread(thumbnail, getattr(product, 'original_image', None) or product.demo_image, mark=bool(getattr(product, 'original_image', None))), filename='product.jpg')
        except OSError as error:
            logger.warning('Preview image file unavailable (%s)', type(error).__name__)
    if photo and mini_app_url():
        # Telegram fetches the small derivative itself, avoiding multipart uploads
        # over the connection that has been failing TLS record validation.
        photo = mini_app_url().rstrip('/') + f'/api/image/{product.id}'
    message = update.effective_message
    if edit:
        # Details only change the caption. Moving to another product replaces the
        # image and caption together; failures leave the prior card in place.
        if not message.photo:
            # A text fallback stays text; some Telegram servers reject adding
            # media to an existing text message. Never abandon its navigation.
            delivered = await message.edit_text(caption, reply_markup=markup)
        elif update.callback_query.data.rsplit(':', 1)[-1] in {'detail', 'summary'}:
            delivered = await message.edit_caption(caption=caption, reply_markup=markup)
        elif photo:
            delivered = await message.edit_media(InputMediaPhoto(photo, caption=caption), reply_markup=markup)
        elif message.photo:
            placeholder = InputFile(await asyncio.to_thread(NO_PHOTO.read_bytes), filename=NO_PHOTO.name)
            delivered = await message.edit_media(
                InputMediaPhoto(placeholder, caption=caption + '\nPhoto unavailable.'), reply_markup=markup)
        else:
            delivered = await message.edit_text(caption, reply_markup=markup)
    else:
        delivered = None
        if photo:
            try:
                delivered = await message.reply_photo(photo, caption=caption, reply_markup=markup)
            except TelegramError as error:
                logger.warning('Preview photo unavailable; sending text card (%s): %s',
                               type(error).__name__, _delivery_error(error))
        if delivered is None:
            delivered = await message.reply_text(caption, reply_markup=markup)
    state['card_message_id'] = delivered.message_id
    state.pop('picker_ids', None)


def _picker_products(brand, ids):
    with Session() as session:
        products = session.scalars(select(DemoProduct).where(
            DemoProduct.brand == brand, DemoProduct.id.in_(ids))).all()
        by_id = {product.id: product for product in products}
        return [by_id[product_id] for product_id in ids if product_id in by_id]


async def _show_picker(update, context, number, page):
    state = _state(update, context)
    ids = state.get('product_ids') or []
    page = max(0, min(page, (len(ids) - 1) // PICKER_SIZE))
    start = page * PICKER_SIZE
    products = await asyncio.to_thread(_picker_products, BRANDS[number - 1], ids[start:start + PICKER_SIZE])
    buttons = [[InlineKeyboardButton(
        f'{product.name[:42]} · {_money(product.price)}', callback_data=f'demo:{number}:pick:{product.id}')]
        for product in products]
    navigation = []
    if page:
        navigation.append(InlineKeyboardButton('‹ Previous 9', callback_data=f'demo:{number}:picker:{page - 1}'))
    if start + PICKER_SIZE < len(ids):
        navigation.append(InlineKeyboardButton('Next 9 ›', callback_data=f'demo:{number}:picker:{page + 1}'))
    if navigation:
        buttons.append(navigation)
    buttons.append([InlineKeyboardButton('Back to product', callback_data=f'demo:{number}:summary'),
                    InlineKeyboardButton('Categories', callback_data=f'demo:{number}:back')])
    await update.effective_message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(buttons))
    state['picker_ids'] = [product.id for product in products]


async def _show_search(update, context, number, query):
    state = _state(update, context)
    result = await asyncio.to_thread(search_demo_catalog, query, BRANDS[number - 1])
    state.pop('card_message_id', None)
    state.pop('picker_ids', None)
    state.update(search_query=query, web_category='')
    state.update(product_ids=[hit.product.id for hit in result.hits], index=0,
                 choice_message_id=None)
    if result.suggestions_only:
        choices = result.hits[:5]
        state['product_ids'] = [hit.product.id for hit in choices]
        message = await update.effective_message.reply_text(
            'These are the closest matches. Which one would you like to see?',
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(hit.product.name[:60],
                                      callback_data=f'demo:{number}:choose:{hit.product.id}')]
                for hit in choices
            ]),
        )
        state['choice_message_id'] = message.message_id
        return
    await _show_product(update, context, number)


async def demo_command(update, context):
    number = BRANDS.index(ACTIVE_BRAND) + 1
    state = _state(update, context)
    state.clear()
    state['brand'] = BRANDS[number - 1]
    if context.args and len(context.args) == 1 and re.fullmatch(r'product_[0-9]+', context.args[0]):
        product_id = int(context.args[0][8:])
        if await asyncio.to_thread(_product, product_id, state['brand']):
            state.update(categories=_categories(state['brand']), category_page=0, product_ids=[product_id], index=0)
            await _show_product(update, context, number, detailed=True)
        else:
            await _show_categories(update, context, number)
        return
    if context.args:
        query = ' '.join(context.args).strip()[:100]
        state.update(categories=_categories(state['brand']), category_page=0)
        await _show_search(update, context, number, query)
    else:
        await _show_categories(update, context, number)


async def demo_search_command(update, context):
    """Explicit search stays usable even when model APIs are unavailable."""
    state = _state(update, context)
    state['brand'] = ACTIVE_BRAND
    state['search_requested_at'] = time.time()
    if context.args:
        await _explicit_search(update, context, ' '.join(context.args))
        return
    await update.effective_message.reply_text(
        'What are you looking for? Send a product name or keywords, such as “blue backpack”.',
        reply_markup=ForceReply(selective=True, input_field_placeholder='Product name or keywords'),
    )


async def _explicit_search(update, context, query):
    if not query.strip() or len(query) > 100:
        await update.effective_message.reply_text('Please send product keywords in 100 characters or fewer.')
        return
    _state(update, context).pop('search_requested_at', None)
    try:
        await _show_search(update, context, BRANDS.index(ACTIVE_BRAND) + 1, query)
    except ValueError:
        await update.effective_message.reply_text('Please enter a valid item-price limit, such as blue backpack under 2500.')


async def demo_cancel_command(update, context):
    _state(update, context).pop('search_requested_at', None)
    await update.effective_message.reply_text('Search cancelled. Use /start to browse the catalog.')


async def _retire_buttons(query):
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except TelegramError as error:
        logger.warning('Could not retire old preview buttons (%s)', type(error).__name__)


async def demo_callback(update, context):
    query = update.callback_query
    parts = query.data.split(':')
    number = int(parts[1])
    if not ACTIVE_BRAND or BRANDS[number - 1] != ACTIVE_BRAND:
        await query.answer('This brand is not active on this bot.', show_alert=True)
        return
    action = parts[2]
    state = _state(update, context)
    if state.get('brand') != BRANDS[number - 1]:
        await query.answer('Use /start to browse this catalog again.', show_alert=True)
        return
    card_actions = {'next', 'prev', 'detail', 'summary', 'picker', 'pick'}
    if action in card_actions and state.get('card_message_id') != update.effective_message.message_id:
        await query.answer('This card is out of date. Open a product from your latest search.', show_alert=True)
        return
    before = dict(state)
    if action == 'choose':
        product_id = int(parts[3])
        if (state.get('choice_message_id') != update.effective_message.message_id
                or product_id not in state.get('product_ids', [])):
            await query.answer('Please search again for current matches.', show_alert=True)
            return
        state.update(index=state['product_ids'].index(product_id), choice_message_id=None)
    state.pop('search_requested_at', None)
    await query.answer()
    if action == 'noop':
        return
    if action == 'cats':
        state['category_page'] = int(parts[3])
        await _show_categories(update, context, number, edit=True)
        return
    if action == 'picker':
        try:
            await _show_picker(update, context, number, int(parts[3]))
        except TelegramError:
            await query.answer('Couldn’t open the product list. Please try again.', show_alert=True)
        return
    if action == 'pick':
        product_id = int(parts[3])
        if product_id not in state.get('picker_ids', []):
            await query.answer('Please choose a product from the current list.', show_alert=True)
            return
        state['index'] = state['product_ids'].index(product_id)
    if action == 'cat':
        categories = state.get('categories') or []
        index = int(parts[3])
        if index >= len(categories):
            return
        state.update(product_ids=_products(state['brand'], category=categories[index][0]), index=0,
                     search_query='', web_category=categories[index][0] or '')
    elif action == 'back':
        await _show_categories(update, context, number)
        await _retire_buttons(query)
        return
    elif action == 'next':
        state['index'] = state.get('index', 0) + 1
    elif action == 'prev':
        state['index'] = state.get('index', 0) - 1
    try:
        await _show_product(update, context, number, detailed=action == 'detail', edit=action in card_actions)
    except BadRequest as error:
        if 'message is not modified' in str(error).lower():
            return
        state.clear()
        state.update(before)
        logger.warning('Product card edit rejected (%s): %s', type(error).__name__, _delivery_error(error))
        await query.answer('Couldn’t update this card. Please try again.', show_alert=True)
        return
    except TelegramError as error:
        state.clear()
        state.update(before)
        logger.warning('Product card delivery failed (%s): %s', type(error).__name__, _delivery_error(error))
        await query.answer('Couldn’t update this card. Please try again.', show_alert=True)
        return
    if action not in card_actions:
        await _retire_buttons(query)


async def demo_text_search(update, context):
    number = BRANDS.index(ACTIVE_BRAND) + 1
    message = update.effective_message
    if len(message.text) > 1000:
        await message.reply_text('Please keep your request under 1,000 characters.')
        return
    state = _state(update, context)
    state.update(brand=ACTIVE_BRAND)
    now = time.time()
    if now - state.get('search_requested_at', 0) < 300:
        await _explicit_search(update, context, message.text)
        return
    state.pop('search_requested_at', None)
    # Known product facts should remain accessible during provider outages.
    # This reports source data, never infers device fit or a delivered total.
    fact_question = re.search(r'\b(?:fit|dimensions|capacity|material|weight|delivery|shipping|price)\b|how much', message.text, re.I)
    complex_question = re.search(r'\b(?:compare|recommend|better|another|instead|cart|buy|checkout|order|cancel)\b', message.text, re.I)
    if (fact_question and not complex_question and state.get('opened_at', 0) > now - 1800
            and state.get('opened_product_id')):
        product = await asyncio.to_thread(_product, state['opened_product_id'], ACTIVE_BRAND)
        if product:
            facts = product_facts(product)
            lines = [f'Source catalog: {product.name}', f'Item price: {_money(product.price)}']
            lines += [f'{key}: {value}' for key, value in facts['specifications'].items()]
            if facts['fit_note']:
                lines.append(facts['fit_note'])
            if not facts['specifications']:
                lines.append('No detailed specifications are listed in this catalog snapshot.')
            lines.append('Delivery charges, delivery dates and the checkout total are not available in this preview.')
            markup = (InlineKeyboardMarkup([[InlineKeyboardButton('View product photos', web_app=WebAppInfo(mini_app_url(q=product.name)))]])) if mini_app_url() else None
            await message.reply_text('\n'.join(lines)[:3900], reply_markup=markup)
            return
    history = state.get('history', []) if now - state.get('history_at', 0) < 1800 else []
    if state.get('opened_at', 0) > now - 1800 and state.get('opened_product_id'):
        history = history + [{'role': 'assistant', 'content':
                              f"Product opened by shopper: ID {state['opened_product_id']}."}]
    activity = await ActivityMessage(message, context).start()
    try:
        reply = await answer_shopping_request(
            message.text, catalog_brand=ACTIVE_BRAND, history=history[-6:],
            on_progress=activity.update,
        )
    except Exception as error:
        logger.warning('Preview assistant failed (%s)', type(error).__name__)
        await activity.finish('I couldn’t complete that request. You can still browse with /start or search product keywords with /search.')
        return
    if reply.categories:
        await activity.finish('Choose a category below.')
        state['category_page'] = 0
        await _show_categories(update, context, number)
        answer = 'Showing store categories.'
    elif reply.products:
        state.pop('card_message_id', None)
        state.pop('picker_ids', None)
        state.update(product_ids=[product['id'] for product in reply.products], index=0, choice_message_id=None,
                     search_query=reply.query, web_category='')
        label = ('These are the closest matches. Which one would you like to see?' if reply.related
                 else 'Which product would you like to see?')
        buttons = [[InlineKeyboardButton(
            product['name'][:48] + (' · ' + _money(float(product['price'])) if product.get('price') is not None else ''),
            callback_data=f"demo:{number}:choose:{product['id']}")] for product in reply.products]
        if mini_app_url():
            buttons.insert(0, [InlineKeyboardButton('View matching photos' if reply.query else 'Browse shop',
                web_app=WebAppInfo(mini_app_url(q=reply.query)))])
        markup = InlineKeyboardMarkup(buttons)
        if await activity.finish(label, reply_markup=markup):
            state['choice_message_id'] = activity.message.message_id
        answer = 'Products found: ' + '; '.join(f"{p['name']} (ID {p['id']})" for p in reply.products)
    else:
        markup = (InlineKeyboardMarkup([[InlineKeyboardButton('Browse shop', web_app=WebAppInfo(mini_app_url()))]])
                  if mini_app_url() else None)
        await activity.finish(reply.text, reply_markup=markup)
        answer = reply.text
    state['history'] = (history + [{'role': 'user', 'content': message.text},
                                  {'role': 'assistant', 'content': answer}])[-6:]
    state['history_at'] = now


demo_handlers = [
    CommandHandler('start', demo_command),
    CommandHandler('demo', demo_command),
    CommandHandler('search', demo_search_command),
    CommandHandler('cancel', demo_cancel_command),
    CallbackQueryHandler(demo_callback, pattern=r'^demo:[1-5]:(?:picker:\d+|pick:\d+|choose:\d+|cat:\d+|cats:\d+|next|prev|detail|summary|back|noop)$'),
    MessageHandler(filters.TEXT & ~filters.COMMAND, demo_text_search),
]
