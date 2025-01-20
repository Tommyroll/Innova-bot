import os
import logging
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import openai
import gspread
from google.oauth2.service_account import Credentials

# Настраиваем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверяем наличие OpenAI API-ключа
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logger.error("Ошибка: OPENAI_API_KEY не найден. Добавьте его в переменные окружения.")
    exit(1)

openai.api_key = openai_api_key

# Настройка для работы с Google Sheets
SPREADSHEET_ID = "1FlGPuIRdPcN2ACOQXQaesawAMtgOqd90vdk4f0PlUks"  # ID Google Sheets

def get_data_from_sheets():
    try:
        # Загружаем ключи из переменной окружения
        sheets_credentials = os.getenv("GOOGLE_SHEETS_KEY")
        if not sheets_credentials:
            raise ValueError("GOOGLE_SHEETS_KEY не найден в переменных окружения.")
        
        credentials_info = json.loads(sheets_credentials)
        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

        # Подключаемся к Google Sheets
        client = gspread.authorize(credentials)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        data = sheet.get_all_records()  # Чтение всех записей
        return data

    except Exception as e:
        logger.error(f"Ошибка при подключении к Google Sheets: {e}")
        return None

# Функция для обработки команды /start
async def start(update: Update, context) -> None:
    logger.info("Команда /start вызвана")
    await update.message.reply_text("Привет! Я ваш бот, готов помочь!")

# Функция для обработки текстовых сообщений
async def handle_message(update: Update, context) -> None:
    user_message = update.message.text
    logger.info(f"Получено сообщение: {user_message}")

    try:
        # Пример использования данных из Google Sheets
        data = get_data_from_sheets()
        if data:
            response_text = "Вот данные из Google Sheets:\n"
            for item in data:
                response_text += f"- {item['Название анализа']}: {item['Цена']} тенге\n"
        else:
            response_text = "Не удалось получить данные из Google Sheets."

        # Отправляем ответ пользователю
        await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
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
