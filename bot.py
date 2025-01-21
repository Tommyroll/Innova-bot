import os
import logging
import json
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Константы
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_ID")

# Функция для получения данных из Google Sheets
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
        
        # Логирование структуры данных
        logger.info(f"Данные из Google Sheets: {data}")
        
        if not data:
            raise ValueError("Данные из таблицы пустые.")
        
        return data
    except Exception as e:
        logger.error(f"Ошибка при получении данных из Google Sheets: {e}")
        return None

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение и доступные команды."""
    await update.message.reply_text(
        "Привет! Я бот для обработки данных лаборатории. Введите запрос или используйте /help для помощи.\n\n"
        "Доступные команды:\n"
        "/start - Запуск бота\n"
        "/help - Показать это сообщение\n"
        "Просто отправьте сообщение, чтобы узнать о доступных анализах."
    )

# Обработчик команды /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет информацию о доступных командах."""
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start - Запуск бота\n"
        "/help - Показать это сообщение\n"
        "Просто отправьте сообщение, чтобы узнать о доступных анализах."
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает сообщения пользователя."""
    user_message = update.message.text
    logger.info(f"Получено сообщение: {user_message}")
    
    try:
        data = fetch_sheets_data()
        if data:
            response_text = "Доступные анализы и цены:\n"
            for row in data:
                analysis_name = row.get('Название анализа', 'Не указано')
                price = row.get('Цена', 'Не указано')
                response_text += f"{analysis_name}: {price} тенге\n"
                
                # Ограничение длины сообщения
                if len(response_text) > 3500:  # Telegram позволяет до 4096 символов
                    response_text += "\n...и другие анализы. Для полного списка обратитесь в лабораторию."
                    break
        else:
            response_text = "Не удалось получить данные из Google Sheets или таблица пуста."
        await update.message.reply_text(response_text)
    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте позже.")

# Основная функция для запуска бота
def main():
    """Запускает бота."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не найден.")
        return
    
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    # Обработчик текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    application.run_polling()

if __name__ == "__main__":
    main()
