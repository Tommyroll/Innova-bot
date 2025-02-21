import logging
import os
import sqlite3
import openai
import re
from difflib import get_close_matches
from fuzzywuzzy import fuzz, process
from telegram import Update, ReplyKeyboardMarkup
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
    format="%(asasctime)s - %(name)s - %(levelname)s - %(message)s",
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
    """Нормализует текст с учетом сложных синонимов"""
    text = text.lower().strip()
    # Заменяем латинские e на кириллические е в ключевых словах
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
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in cursor.fetchall()]
    except sqlite3.Error as e:
        logger.error(f"Ошибка данных: {e}")
        return []
    finally:
        conn.close()

def normalize_text(text):
    """Улучшенная нормализация текста"""
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)  # Удаляем спецсимволы
    text = re.sub(r'\s+', ' ', text)      # Убираем лишние пробелы
    return text

def extract_matched_analyses(query, analyses):
    """Улучшенное извлечение анализов с нечетким поиском"""
    query = apply_synonyms(query)
    analysis_names = [name for name, _, _ in analyses]
    
    # Поиск точных совпадений
    exact_matches = set()
    for name in analysis_names:
        if re.search(r'\b' + re.escape(name) + r'\b', query, re.IGNORECASE):
            exact_matches.add(name)
    
    # Нечеткий поиск для частичных совпадений
    token_matches = set()
    query_tokens = re.findall(r'\w+', query)
    for name in analysis_names:
        name_tokens = re.findall(r'\w+', name)
        if any(fuzz.partial_ratio(token, name_token) > 85 
               for token in query_tokens 
               for name_token in name_tokens):
            token_matches.add(name)
    
    # Комбинируем результаты
    all_matches = exact_matches.union(token_matches)
    
    # Дополнительные проверки для критических случаев
    if re.search(r'\bрф\b', query, re.IGNORECASE):
        all_matches.add('рф-суммарный')
    if re.search(r'\b(ige?|иге|иммуноглобулин)\b', query, re.IGNORECASE):
        all_matches.add('иммуноглобулин е')
    
    logger.info(f"Найдены анализы: {all_matches}")
    return ', '.join(all_matches) if all_matches else ''

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка фото с извлечением контактных данных"""
    try:
        user = update.message.from_user
        contact_info = ""
        
        # Проверяем прикрепленный контакт
        if update.message.contact:
            phone = update.message.contact.phone_number
            contact_info = f"Телефон: {phone}"
        else:
            # Проверяем подпись к фото
            caption = update.message.caption or ""
            phone_match = re.search(r'(\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}', caption)
            if phone_match:
                contact_info = f"Телефон из подписи: {phone_match.group()}"
        
        # Формируем информацию о клиенте
        client_info = [
            f"ID: {user.id}",
            f"Username: @{user.username}" if user.username else "",
            f"Имя: {user.first_name} {user.last_name or ''}".strip(),
            contact_info
        ]
        client_info = "\n".join(filter(None, client_info))
        
        # Пересылаем фото и информацию
        await update.message.forward(ADMIN_TELEGRAM_ID)
        await context.bot.send_message(
            chat_id=ADMIN_TELEGRAM_ID,
            text=f"📷 Фото от клиента:\n{client_info}\n\n"
                 f"Для ответа используйте /reply {user.id} <текст>"
        )
        
        # Ответ пользователю
        response = ("✅ Ваше направление получено. "
                    "Оператор свяжется с вами в ближайшее время. "
                    "Вы также можете поделиться контактом для быстрой связи:")
        
        reply_keyboard = [[{"text": "📱 Отправить контакт", "request_contact": True}]]
        await update.message.reply_text(
            response,
            reply_markup=ReplyKeyboardMarkup(
                reply_keyboard, 
                resize_keyboard=True,
                one_time_keyboard=True
            )
        )
        
    except Exception as e:
        logger.error(f"Ошибка фото: {e}")
        await update.message.reply_text("⚠️ Ошибка обработки фото. Пожалуйста, попробуйте позже.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка полученного контакта"""
    try:
        phone = update.message.contact.phone_number
        user = update.message.from_user
        await context.bot.send_message(
            ADMIN_TELEGRAM_ID,
            f"📱 Новый контакт от {user.first_name}:\n"
            f"Телефон: {phone}\n"
            f"User ID: {user.id}"
        )
        await update.message.reply_text(
            "✅ Спасибо! Ваш контакт сохранён. "
            "Оператор свяжется с вами в ближайшее время.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Ошибка контакта: {e}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("Бот запущен")
    app.run_polling()
