
import os
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext

# Function to start the bot
def start(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Привет! Я ваш тестовый бот.')

# Function to handle messages
def echo(update: Update, context: CallbackContext) -> None:
    update.message.reply_text('Привет!')

def main():
    # Get the bot token from environment variables
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("Ошибка: BOT_TOKEN не найден. Добавьте его в переменные окружения.")
        return

    updater = Updater(token)

    # Register handlers
    dispatcher = updater.dispatcher
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, echo))

    # Start the bot
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
