import logging
import os
import sqlite3
import openai
import re
from fuzzywuzzy import fuzz
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
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
    "иге": "ige",
    "ig e": "ige"
}

def apply_synonyms(text):
    """
    Заменяет в тексте все вхождения синонимов на канонические названия.
    """
    text = text.lower().strip()
    for syn, canon in SYNONYMS.items():
        text = re.sub(r'\b' + re.escape(syn) + r'\b', canon, text, flags=re.IGNORECASE)
    return text

def connect_to_db():
    try:
        return sqlite3.connect(DATABASE_PATH)
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
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
        return []
    finally:
        conn.close()

def get_competitor_data():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, lab, price, timeframe FROM competitor_prices")
        results = cursor.fetchall()
        return [(normalize_text(name), lab, price, timeframe) for name, lab, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при загрузке данных конкурентов: {e}")
        return []
    finally:
        conn.close()

def normalize_text(text):
    """
    Приводит текст к нижнему регистру для обеспечения корректного сопоставления.
    """
    return text.lower()

def get_lab_context(analyses):
    analyses_list = "\n".join([f"{name}: Цена — {price} KZT. Срок — {timeframe}" for name, price, timeframe in analyses])
    return ("Ты — виртуальный помощник медицинской лаборатории. Дай пользователю краткую и точную информацию по анализам. " +
            "Вот данные наших анализов:\n" + analyses_list + "\n\nЕсли анализ не найден, сообщи, что его нет в базе.")

def ask_openai(prompt, analyses):
    try:
        lab_context = get_lab_context(analyses)
        full_prompt = prompt + "\n\nЕсли в запросе упомянуты анализы, которых нет в нашей базе, сообщи, что информация по ним отсутствует."
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # Или gpt-4-turbo
            messages=[
                {"role": "system", "co
