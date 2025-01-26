import logging
import os
import sqlite3
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Константы
DB_FILE = "lab_data.db"

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY


# Функция подключения к БД
def connect_to_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None


# Функция инициализации БД для логирования
def initialize_db():
    conn = connect_to_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS missing_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS unhandled_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()


# Функция сохранения запросов в таблицы
def log_missing_request(query):
    conn = connect_to_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO missing_requests (query) VALUES (?)", (query,))
        conn.commit()
        conn.close()


def log_unhandled_request(query):
    conn = connect_to_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO unhandled_requests (query) VALUES (?)", (query,))
        conn.commit()
        conn.close()


# Функция получения всех анализов из БД
def get_all_analyses():
    try:
        conn = connect_to_db()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, price, timeframe FROM analyses")
            results = cursor.fetchall()
            conn.close()
            return results
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
    return []


# Формирование контекста для OpenAI
def get_lab_context(analyses):
    analyses_list = "\n".join(
        [f"{name}: Цена — {price} KZT. Срок выполнения — {timeframe}." for name, price, timeframe in analyses]
    )
    return (
        "Ты — виртуальный помощник медицинской лаборатории. Пользователь может запрашивать анализы в произвольной форме. "
        "Вот данные всех анализов, которые есть в базе:\n"
        f"{analyses_list}\n\n"
        "Если пользователь спрашивает про какой-то анализ, попытайся сопоставить его запрос с доступными анализами "
        "и предоставь информацию. Если анализ не найден, скажи, что его нет в базе."
    )


# Запрос к OpenAI
def ask_openai(prompt, analyses):
    try:
        lab_context = get_lab_context(analyses)
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": lab_context},
                {"role": "user", "content": prompt},
            ],
            max_tokens=200,  # Уменьшение длины ответа
            temperature=0.5,
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

    analyses = get_all_analyses()
    response = ask_openai(user_message, analyses)

    # Логика проверки ответа
    if "анализ отсутствует в нашей базе" in response.lower():
        log_missing_request(user_message)  # Логируем отсутствующий анализ
        await update.message.reply_text(
            "Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения."
        )
    elif "не понял" in response.lower() or "не могу обработать" in response.lower():
        log_unhandled_request(user_message)  # Логируем непонятные запросы
        await update.message.reply_text(
            "К сожалению, я не смог обработать ваш запрос. Переключаю на оператора."
        )
    else:
        # Обрезаем лишние фразы перед отправкой
        short_response = response.split("Если вам нужно")[0].strip()
        await update.message.reply_text(short_response)


# Основная функция
def main():
    initialize_db()  # Инициализация БД
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()


if __name__ == "__main__":
    main()
