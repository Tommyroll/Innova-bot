import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import openai  # Для работы с OpenAI API

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Инициализация OpenAI API
openai.api_key = os.getenv("OPENAI_API_KEY")

# Функция для обработки команды /start
async def start(update: Update, context) -> None:
    logger.info("Команда /start вызвана")
    await update.message.reply_text("Привет! Я ваш помощник. Спросите меня о лабораторных анализах или других услугах!")

# Функция для обработки всех текстовых сообщений
async def handle_message(update: Update, context) -> None:
    user_input = update.message.text
    logger.info(f"Получено сообщение: {user_input}")

    # Запрос в OpenAI API
    try:
        response = openai.Completion.create(
            model="gpt-4o-mini",
            prompt=f"Формально ответь на вопрос: {user_input}. Если это не связано с услугами лаборатории, извинись и откажись отвечать.",
            max_tokens=150,
            temperature=0.5,
        )
        answer = response.choices[0].text.strip()
        logger.info(f"Ответ бота: {answer}")
        await update.message.reply_text(answer)
    except Exception as e:
        logger.error(f"Ошибка при запросе к OpenAI API: {e}")
        await update.message.reply_text("Извините, произошла ошибка. Попробуйте позже.")

def main():
    # Получаем токен Telegram из переменных окружения
    telegram_token = os.getenv("BOT_TOKEN")
    if not telegram_token:
        logger.error("Ошибка: BOT_TOKEN не найден. Добавьте его в переменные окружения.")
        return

    # Проверяем наличие API-ключа OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if not openai_key:
        logger.error("Ошибка: OPENAI_API_KEY не найден. Добавьте его в переменные окружения.")
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
