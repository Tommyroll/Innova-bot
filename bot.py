import logging
import os
import sqlite3
import openai
import re
from difflib import get_close_matches
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
import io
import tempfile
import json
import requests
from google.oauth2 import service_account
from google.cloud import vision

# Константы и настройки
DB_FILE = "lab_data(2).db"  # Файл базы данных с анализами и конкурентами
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE
ADMIN_TELEGRAM_ID = "5241327545"  # Ваш Telegram ID

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Глобальный словарь для хранения извлечённых названий анализов из последнего запроса
pending_requests = {}

# Глоссарий синонимов: ключ – вариант, значение – каноническое название анализа (как записано в базе)
SYNONYMS = {
    "рф": "рф-суммарный",
    "иммуноглобулин e": "ige",
    "иммуноглобулин е": "ige",  # Кириллическая "е"
    "иге": "ige",
    "ig e": "ige",
    "ige": "ige",
    "immunoglobulin e": "ige",  # Латиница
    "immunoglobulin е": "ige"   # Латиница + кириллица
}

def apply_synonyms(text):
    """
    Заменяет в тексте все вхождения синонимов на канонические названия.
    """
    for syn, canon in SYNONYMS.items():
        text = re.sub(r'\b' + re.escape(syn) + r'\b', canon, text, flags=re.IGNORECASE)
    return text

def connect_to_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

def get_all_analyses():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, timeframe FROM analyses")
        results = cursor.fetchall()
        conn.close()
        # Приводим названия к нижнему регистру
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
        return []

def normalize_text(text):
    """
    Приводит текст к нижнему регистру для обеспечения корректного сопоставления.
    """
    return text.lower()

def extract_matched_analyses(query, analyses):
    """
    Извлекает названия анализов, сравнивая отдельные слова из текста с названиями анализов.
    Перед сравнением применяется глоссарий синонимов.
    Если в исходном тексте встречаются критические ключевые слова, добавляем канонический вариант.
    Возвращает строку с найденными анализами, разделёнными запятыми.
    """
    matched = set()
    # Применяем синонимы ко всему тексту
    query_syn = apply_synonyms(query)
    query_tokens = re.findall(r'\w+', query_syn)
    for name, _, _ in analyses:
        name_tokens = re.findall(r'\w+', name)
        for n_token in name_tokens:
            for token in query_tokens:
                if token == n_token or get_close_matches(token, [n_token], n=1, cutoff=0.6):  # Понижен порог совпадения
                    matched.add(name)
                    break
            else:
                continue
            break
    # Дополнительная проверка для критических случаев
    if any(word in query.lower() for word in ["рф", "rf"]) and "рф-суммарный" not in matched:
        matched.add("рф-суммарный")
    if any(word in query.lower() for word in ["иммуноглобулин", "immunoglobulin", "иге", "ige"]) and "ige" not in matched:
        matched.add("ige")
    logger.info(f"OCR текст: {query_tokens}, найденные анализы: {matched}")
    return ", ".join(matched)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    При получении фото, бот пересылает его оператору вместе с информацией о клиенте,
    а клиент получает уведомление о том, что запрос направлен оператору.
    """
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        # Формируем информацию о клиенте: chat ID и username, если телефон недоступен
        user = update.message.from_user
        client_info = f"Chat ID: {update.message.chat.id}"
        if user.username:
            client_info += f", Username: {user.username}"
        # Если клиент отправил контакт, используем его номер
        if update.message.contact and update.message.contact.phone_number:
            client_info += f", Телефон: {update.message.contact.phone_number}"
        else:
            client_info += ", Телефон: Не указан"
        caption = f"Фото от клиента:\n{client_info}"
        await update.message.forward(chat_id=ADMIN_TELEGRAM_ID)
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=caption)
        await update.message.reply_text("Ваш запрос получен и направлен оператору для ручной обработки. "
                                         "Вы можете напрямую обратиться по телефону +77073145.")
    except Exception as e:
        logger.error(f"Ошибка при пересылке фото: {e}")
        await update.message.reply_text("Произошла ошибка при обработке вашего запроса.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start)) 
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()
