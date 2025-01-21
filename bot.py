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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Проверка и настройка OpenAI API
openai_api_key = os.getenv("OPENAI_API_KEY")
if not openai_api_key:
    logger.error("Ошибка: OPENAI_API_KEY не найден.")
    exit(1)
openai.api_key = openai_api_key

# Настройка Google Sheets
SPREADSHEET_ID = "1FlGPuIRdPcN2ACOQXQaesawAMtgOqd90vdk4f0PlUks"

def fetch_sheets_data():
    """Получение данных из Google Sheets."""
    try:
        sheets_credentials = os.getenv("GOOGLE_SHEETS_KEY")
        if not sheets_credentials:
            raise ValueError("GOOGLE_SHEETS_KEY не найден.")
        
        credentials_info = json.loads(sheets_credentials)
        credentials = Credentials.from_service_account_info(
            credentials_info,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(credentials)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        data = sheet.get_all_records()
        logger.info("Данные успешно получены из Google Sheets.")
        return data
    except Exception as e:
        logger.error(f"Ошибка при получении данных из Google Sheets: {e}")
        return None

# Команда /start
async def start(update: Update, context):
    await update.message.reply_text("Привет! Я бот для обработки данных лаборатории. Введите запрос или используйте /help для помощи.")

# Команда /help
async def help_command(update: Update, context):
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start - Запуск бота\n"
        "/help - Показать это сообщение\n"
        "Просто отправьте сообщение, чтобы узнать о доступных анализах."
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context):
    user_message = update.message.text
    logger.info(f"Получено сообщение: {user_message}")
    
    try:
        data = fetch_sheets_data()
        if data:
            response_text = "Доступные анализы и цены:\n"
            for row in data:
                response_text += f"{row.get('Название анализа', 'Не указано')}: {row.get('Цена', 'Не указано')} тенге\n"
                
                # Ограничение длины ответа
                if len(response_text) > 3500:  # Ограничение Telegram на сообщения до 4096 символов
                    response_text += "\n...и другие анализы. Для полного списка обратитесь в лабораторию."
                    break
        else:
            response_text = "Не удалось получить данные из Google Sheets."
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")

def main():
    telegram_token = os.getenv("BOT_TOKEN")  # Исправлено: использование BOT_TOKEN
    if not telegram_token:
        logger.error("Ошибка: BOT_TOKEN не найден.")
        return
    
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
