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
def format_analysis_response(data, query):
    for row in data:
        if query.lower() in row[0].lower():  # Ищем анализ по названию
            test_name = row[0]
            price = row[1]
            time = row[2]
            return f"{test_name}: Цена — {price} KZT. Срок выполнения — {time}. Для сдачи следуйте инструкциям: избегайте пищи и жидкости перед анализом."
    return "Извините, не удалось найти информацию о запрашиваемом анализе."


def get_lab_context():
    return (
        "Ты — виртуальный помощник медицинской лаборатории. "
        "Отвечай кратко и по существу. "
        "Обязательно указывай стоимость анализа, сроки выполнения и инструкции по подготовке, если пользователь упоминает конкретный анализ. "
        "Не используй длинные приветствия или дополнительные фразы. "
        "Дай пользователю всю необходимую информацию без лишних деталей."
    )

def ask_openai(prompt):
    try:
        lab_context = get_lab_context()
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": lab_context},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,  # Увеличиваем лимит токенов
            temperature=0.5,  # Снижаем случайность ответов
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

async def handle_message(update: Update, context):
    user_message = update.message.text.lower()
    logger.info(f"Получен запрос: {user_message}")

    spreadsheet_id = "1FlGPuIRdPcN2ACOQXQaesawAMtgOqd90vdk4f0PlUks"
    sheet_range = "Лист1!A1:C286"
    data = read_from_sheets(spreadsheet_id, sheet_range)

    if "оам" in user_message:
        if data:
            response = format_analysis_response(data, "ОАМ")
            await update.message.reply_text(response)
        else:
            await update.message.reply_text("Извините, данные о цене и сроках недоступны.")
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
