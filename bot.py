import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Функция для обработки команды /start
async def start(update: Update, context) -> None:
    logger.info("Команда /start вызвана")
    await update.message.reply_text("Привет! Я ваш тестовый бот.")

# Функция для обработки всех текстовых сообщений
async def echo(update: Update, context) -> None:
    logger.info(f"Получено сообщение: {update.message.text}")
    await update.message.reply_text(update.message.text)

def main():
    # Получаем токен из переменных окружения
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("Ошибка: BOT_TOKEN не найден. Добавьте его в переменные окружения.")
        return

    # Создаём приложение
    app = ApplicationBuilder().token(token).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    logger.info("Бот запущен и ожидает команды...")
    # Запускаем бота
    app.run_polling()

if __name__ == "__main__":
    main()
