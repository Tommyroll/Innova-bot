import logging
import os
import openai
import json
from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Настройка Google Sheets API
def get_google_sheets_service():
    try:
        credentials = Credentials.from_service_account_info(json.loads(SERVICE_ACCOUNT_JSON))
        service = build("sheets", "v4", credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Ошибка настройки Google Sheets: {e}")
        return None

# Чтение данных из Google Sheets
def read_from_sheets(spreadsheet_id, sheet_range):
    try:
        service = get_google_sheets_service()
        if service:
            sheet = service.spreadsheets()
            result = sheet.values().get(spreadsheetId=spreadsheet_id, range=sheet_range).execute()
            return result.get("values", [])
    except Exception as e:
        logger.error(f"Ошибка чтения Google Sheets: {e}")
    return []

# Формирование списка анализов
def format_analysis_list(data):
    try:
        if not data:
            return "Нет доступных данных."
        response = "Доступные анализы:\n"
        for row in data[1:]:  # Пропускаем заголовок
            name, price, time = row
            response += f"- {name}: {price} тг, срок выполнения: {time}\n"
        return response
    except Exception as e:
        logger.error(f"Ошибка форматирования списка анализов: {e}")
        return "Произошла ошибка при обработке данных."

# Обработка запросов через OpenAI
def ask_openai(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": "Ты дружелюбный помощник лаборатории."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=150,
            temperature=0.7,
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "Извините, я не смог обработать ваш запрос."

# Обработчик команды /start
async def start(update: Update, context):
    await update.message.reply_text(
        "Добро пожаловать! Я виртуальный ассистент лаборатории. Чем могу помочь?"
    )

# Обработчик сообщений
async def handle_message(update: Update, context):
    user_message = update.message.text
    logger.info(f"Получен запрос: {user_message}")

    if "анализ" in user_message.lower():
        spreadsheet_id = "1FlGPuIRdPcN2ACOQXQaesawAMtgOqd90vdk4f0PlUks"
        sheet_range = "Лист1!A1:C286"
        data = read_from_sheets(spreadsheet_id, sheet_range)
        response = format_analysis_list(data)
        await update.message.reply_text(response)
    else:
        ai_response = ask_openai(user_message)
        await update.message.reply_text(ai_response)

# Основная функция
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
