import os
import logging
import json
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
import openai
import gspread
from google.oauth2.service_account import Credentials
from google.cloud import vision
import re

# Настраиваем логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Настройка OpenAI API
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

def match_analysis(query, data):
    """Сопоставляет запрос клиента с базой анализов."""
    results = []
    for row in data:
        analysis_name = row.get('Название анализа', '').lower()
        if query.lower() in analysis_name:  # Простой поиск по названию
            results.append(row)
    return results

# Обработка команды /start
async def start(update: Update, context):
    await update.message.reply_text("Добро пожаловать! Отправьте текст или изображение направления, чтобы получить информацию об анализах.")

# Обработка команды /help
async def help_command(update: Update, context):
    await update.message.reply_text("Отправьте текст или фото направления, чтобы получить информацию о доступных анализах и ценах.")

# Обработка текстового запроса
async def handle_text_request(update: Update, context):
    user_message = update.message.text
    logger.info(f"Получено сообщение: {user_message}")
    
    try:
        data = fetch_sheets_data()
        if data:
            matched_results = match_analysis(user_message, data)
            if matched_results:
                response_text = "Найденные анализы:\n"
                for row in matched_results:
                    response_text += f"{row.get('Название анализа', 'Не указано')}: {row.get('Цена', 'Не указано')} тенге\n"
            else:
                response_text = "Анализы не найдены. Попробуйте уточнить запрос."
            await update.message.reply_text(response_text)
        else:
            await update.message.reply_text("Не удалось получить данные из Google Sheets.")
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")

# Обработка изображений с использованием OCR
async def handle_image_request(update: Update, context):
    try:
        # Скачиваем фото, отправленное клиентом
        file = await update.message.photo[-1].get_file()
        file_path = f"{file.file_id}.jpg"
        await file.download_to_drive(file_path)

        # Распознавание текста с помощью Google Vision
        client = vision.ImageAnnotatorClient()
        with open(file_path, "rb") as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        response = client.text_detection(image=image)
        texts = response.text_annotations

        if not texts:
            await update.message.reply_text("Не удалось распознать текст на изображении. Попробуйте снова.")
            return

        # Извлекаем текст из изображения
        detected_text = texts[0].description
        logger.info(f"Распознанный текст: {detected_text}")

        # Ищем анализы по распознанным данным
        data = fetch_sheets_data()
        if data:
            matched_results = []
            for line in detected_text.split('\n'):
                line = re.sub(r'[^а-яА-Яa-zA-Z0-9\s]', '', line)  # Удаляем лишние символы
                matches = match_analysis(line, data)
                matched_results.extend(matches)

            if matched_results:
                response_text = "Найденные анализы по направлению:\n"
                for row in matched_results:
                    response_text += f"{row.get('Название анализа', 'Не указано')}: {row.get('Цена', 'Не указано')} тенге\n"
            else:
                response_text = "Не удалось найти анализы по предоставленному направлению."
            await update.message.reply_text(response_text)
        else:
            await update.message.reply_text("Не удалось получить данные из Google Sheets.")
    except Exception as e:
        logger.error(f"Ошибка при обработке изображения: {e}")
        await update.message.reply_text("Произошла ошибка при обработке изображения. Попробуйте позже.")

def main():
    telegram_token = os.getenv("BOT_TOKEN")
    if not telegram_token:
        logger.error("Ошибка: BOT_TOKEN не найден.")
        return
    
    app = ApplicationBuilder().token(telegram_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_request))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image_request))
    
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
