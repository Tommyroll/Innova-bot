import logging
import os
import sqlite3
import openai
import re
from difflib import get_close_matches
from fuzzywuzzy import fuzz, process
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
DB_FILE = "lab_data(2).db"
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_TELEGRAM_ID = "5241327545"

openai.api_key = OPENAI_API_KEY
pending_requests = {}

# Расширенный словарь синонимов с учетом разных раскладок и опечаток
SYNONYMS = {
    r'\bрф\b': 'рф-суммарный',
    r'\b(ige?|иге|ig[\s-]*e|иммуноглобулин[\s-]*[еe])\b': 'иммуноглобулин е',
    r'\bревмофактор\b': 'рф-суммарный',
    r'\bалт\b': 'аланинаминотрансфераза (алт)',
    r'\bаст\b': 'аспартатаминотрансфераза (аст)'
}

def apply_synonyms(text):
    """Нормализует текст с учетом сложных синонимов."""
    text = text.lower().strip()
    # Пример замены латинского на кириллическое написание в ключевых фразах
    text = re.sub(r'(?i)immunoglobulin\s*e', 'иммуноглобулин е', text)
    text = re.sub(r'(?i)(ige|ig\s*e)', 'иммуноглобулин е', text)
    for pattern, replacement in SYNONYMS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text

def connect_to_db():
    try:
        return sqlite3.connect(DB_FILE)
    except sqlite3.Error as e:
        logger.error(f"Ошибка БД: {e}")
        return None

def get_all_analyses():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, timeframe FROM analyses")
        data = cursor.fetchall()
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in data]
    except sqlite3.Error as e:
        logger.error(f"Ошибка данных: {e}")
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
        data = cursor.fetchall()
        return [(normalize_text(name), lab, price, timeframe) for name, lab, price, timeframe in data]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при загрузке данных конкурентов: {e}")
        return []
    finally:
        conn.close()

def normalize_text(text):
    """Улучшенная нормализация текста: приводим к нижнему регистру, убираем спецсимволы и лишние пробелы."""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text

def extract_matched_analyses(query, analyses):
    """
    Извлекает названия анализов, сравнивая слова из запроса с названиями анализов.
    Применяет синонимы и использует fuzzywuzzy для нечеткого поиска.
    """
    matched = set()
    query = apply_synonyms(query)
    query_tokens = re.findall(r'\w+', query)
    analysis_names = [name for name, _, _ in analyses]
    
    # Точное совпадение по регулярке
    for name in analysis_names:
        if re.search(r'\b' + re.escape(name) + r'\b', query, re.IGNORECASE):
            matched.add(name)
    
    # Нечеткий поиск: для каждого слова из запроса смотрим, насколько оно похоже на слово из названия
    for name in analysis_names:
        name_tokens = re.findall(r'\w+', name)
        for token in query_tokens:
            for n_token in name_tokens:
                if fuzz.partial_ratio(token, n_token) > 85:
                    matched.add(name)
                    break
            else:
                continue
            break

    # Дополнительная проверка для критических случаев
    if re.search(r'\bрф\b', query, re.IGNORECASE) and not any("рф-суммарный" in m for m in matched):
        matched.add("рф-суммарный")
    if re.search(r'\b(иге|иммуноглобулин)\b', query, re.IGNORECASE) and not any("иммуноглобулин е" in m for m in matched):
        matched.add("иммуноглобулин е")
    
    logger.info(f"Найдены анализы: {matched} для запроса (токены): {query_tokens}")
    return ', '.join(matched) if matched else ''

def find_best_match(query, competitor_data):
    competitor_names = [name for name, _, _, _ in competitor_data]
    matches = get_close_matches(query, competitor_names, n=1, cutoff=0.5)
    if matches:
        for name, lab, price, timeframe in competitor_data:
            if name == matches[0]:
                return (name, lab, price, timeframe)
    return None

def compare_with_competitors(matched_names):
    competitor_data = get_competitor_data()
    if not matched_names:
        return "Не удалось извлечь названия анализов для сравнения."
    names = [name.strip() for name in matched_names.split(',')]
    results = []
    for name in names:
        best = find_best_match(name, competitor_data)
        if best:
            comp_name, lab, comp_price, comp_timeframe = best
            results.append(f"Конкурент ({lab}): {comp_name} — {comp_price} KZT, Срок: {comp_timeframe}")
        else:
            results.append(f"Для анализа '{name}' информация по конкурентам не найдена.")
    return "\n".join(results)

async def notify_admin_about_missing_request(query, user_id, context):
    pending_requests[user_id] = query
    message = (f"⚠️ Пропущенный запрос от пользователя {user_id}:\n\n"
               f"Запрос: {query}\n\n"
               f"Для ответа используйте команду: /reply {user_id} <Ваш ответ>")
    try:
        await context.bot.send_message(cha
