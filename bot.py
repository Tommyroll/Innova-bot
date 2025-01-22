import os
import logging
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import openai
import gspread
from google.oauth2.service_account import Credentials
import re

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройка API OpenAI
openai.api_key = os.getenv("OPENAI_API_KEY")

# Настройка Google Sheets
SPREADSHEET_ID = "ВАШ_SPREADSHEET_ID"

# Основная информация о лаборатории
ADDRESS = "г. Алматы, ул. Розыбакиева 310А, ЖК 4YOU, вход при аптеке 888 PHARM"
ADDRESS_LINK = "https://go.2gis.com/wz9gi"
WORKING_HOURS = "Мы работаем ежедневно с 07:00 до 17:00."

# Функция загрузки данных из Google Sheets
def fetch_sheets_data():
    try:
        sheets_credentials = os.getenv("GOOGLE_SHEETS_KEY")
        credentials_info = json.loads(sheets_credentials)
        credentials = Credentials.from_service_account_info(
            credentials_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(credentials)
        sheet = client.open_by_key(SPREADSHEET_ID).sheet1
        return sheet.get_all_records()
    except Exception as e:
        logger.error(f"Ошибка при подключении к Google Sheets: {e}")
        return []

# GPT для анализа запросов
def gpt_response(prompt):
    try:
        response = openai.Completion.create(
            model="GPT-4o-mini",
            prompt=prompt,
            max_tokens=200,
            temperature=0.5
        )
        return response.choices[0].text.strip()
    except Exception as e:
        logger.error(f"Ошибка OpenAI GPT: {e}")
        return "Извините, произошла ошибка при обработке вашего запроса."

# Обработка запросов
async def handle_request(update: Update, context):
    user_message = update.message.text
    logger.info(f"Получен запрос: {user_message}")

    # Проверяем, содержит ли запрос ключевые слова
    data = fetch_sheets_data()
    relevant_data = [row for row in data if user_message.lower() in row['Название анализа'].lower()]

    if relevant_data:
        # Формируем ответ
        response = "Мы нашли следующие анализы:\n"
        for row in relevant_data:
            response += f"- {row['Название анализа']}: {row['Цена']} тенге\n"
    else:
        # GPT для уточнений
        prompt = (
            f"Вы ассистент лаборатории. Ответьте на вопрос клиента строго в рамках лаборатории. "
            f"Если вопрос не по теме, вежливо откажитесь. Вопрос: {user_message}"
        )
        response = gpt_response(prompt)
    
    await update.message.reply_text(response)

# Запуск бота
def main():
    application = ApplicationBuilder().token(os.getenv("BOT_TOKEN")).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_request))

    logger.info("Бот запущен.")
    application.run_polling()

async def start(update: Update, context):
    await update.message.reply_text("Добро пожаловать в нашу лабораторию! Чем могу помочь?")

if __name__ == "__main__":
    main()
