
from os import environ
from dotenv import load_dotenv

load_dotenv()
if not environ.get("BOT_TOKEN"):
    raise SystemExit("Set BOT_TOKEN in .env before starting the Telegram bot.")
from telegram.ext import ApplicationBuilder
from telegram.error import InvalidToken

from telegram_ecommerce.utils.consts import bot_token 
from telegram_ecommerce.utils.log import logger
from telegram_ecommerce.handlers import (
    all_public_commands_descriptions, 
    all_handlers, demo_handlers, demo_public_commands_descriptions)


async def post_init(app):
    commands = demo_public_commands_descriptions if environ.get('DEMO_BRAND') else all_public_commands_descriptions
    await app.bot.set_my_commands(commands)

def main():
    app = ApplicationBuilder().token(bot_token).post_init(post_init).build()

    for handler in (demo_handlers if environ.get('DEMO_BRAND') else all_handlers):
        app.add_handler(handler)


    logger.info("bot started")
    app.run_polling()
    logger.info("bot closed")


if __name__ == "__main__":
    try:
        main()
    except InvalidToken:
        raise SystemExit("Telegram rejected BOT_TOKEN. Check the token saved in .env.") from None


