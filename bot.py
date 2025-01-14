import os
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Функция для обработки команды /start
async def start(update: Update, context) -> None:
    await update.message.reply_text("Привет! Я ваш тестовый бот.")

# Функция для обработки всех текстовых сообщений
async def echo(update: Update, context) -> None:
    await update.message.reply_text(update.message.text)

def main():
    # Получаем токен из переменных окружения
    token = os.getenv("BOT_TOKEN")
    if not token:
        print("Ошибка: BOT_TOKEN не найден. Добавьте его в переменные окружения.")
        return

    # Создаём приложение
    app = ApplicationBuilder().token(token).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # Запускаем бота
    app.run_polling()

if __name__ == "__main__":
    main()
