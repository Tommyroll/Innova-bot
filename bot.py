import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import openai

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Установите ваш OpenAI API-ключ
openai.api_key = os.getenv("OPENAI_API_KEY")

# Функция для обработки команды /start
async def start(update: Update, context) -> None:
    logger.info("Команда /start вызвана")
    await update.message.reply_text("Привет! Я ваш бот, готов помочь!")

# Функция для обработки текстовых сообщений
async def handle_message(update: Update, context) -> None:
    user_message = update.message.text
    logger.info(f"Получено сообщение: {user_message}")

    try:
        # Отправляем запрос к OpenAI API
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": user_message}
            ]
        )
        reply = response['choices'][0]['message']['content']
        await update.message.reply_text(reply)

    except Exception as e:
        logger.error(f"Ошибка при запросе к OpenAI API: {e}")
        await update.message.reply_text("Извините, произошла ошибка. Попробуйте позже.")

def main():
    # Получаем токен Telegram бота
    telegram_token = os.getenv("BOT_TOKEN")
    if not telegram_token:
        logger.error("Ошибка: BOT_TOKEN не найден. Добавьте его в переменные окружения.")
        return

    # Создаём приложение
    app = ApplicationBuilder().token(telegram_token).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен и ожидает команды...")
    # Запускаем бота
    app.run_polling()

if __name__ == "__main__":
    main()
