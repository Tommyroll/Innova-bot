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
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")

async def process_response(response, user_message, user_id, context):
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return ("Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения. "
                "Вы можете напрямую обратиться по телефону +77073145.")
    return response

def detect_text_from_image(image_path):
    try:
        credentials_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not credentials_json:
            logger.error("Переменная GOOGLE_SERVICE_ACCOUNT_JSON не установлена.")
            return ""
        credentials_info = json.loads(credentials_json)
        credentials = service_account.Credentials.from_service_account_info(credentials_info)
        client = vision.ImageAnnotatorClient(credentials=credentials)
        with io.open(image_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)
        response = client.text_detection(image=image)
        if response.error.message:
            logger.error(f"Google Vision API error: {response.error.message}")
            return ""
        texts = response.text_annotations
        if texts:
            return texts[0].description
        return ""
    except Exception as e:
        logger.error(f"Ошибка при обработке изображения: {e}")
        return ""

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    При получении фото бот пересылает его оператору вместе с информацией о клиенте,
    а клиент получает уведомление о том, что запрос направлен оператору.
    """
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        user = update.message.from_user
        # Формируем информацию о клиенте: ID, username, контакт (если есть) из сообщения или подписи
        client_info = f"ID: {update.message.chat.id}\n"
        if user.username:
            client_info += f"Username: @{user.username}\n"
        client_info += f"Имя: {user.first_name} {user.last_name or ''}\n"
        if update.message.contact and update.message.contact.phone_number:
            client_info += f"Телефон: {update.message.contact.phone_number}\n"
        else:
            caption = update.message.caption or \"\"
            phone_match = re.search(r'(\\+7|8)[\\s-]?\\(?\\d{3}\\)?[\\s-]?\\d{3}[\\s-]?\\d{2}[\\s-]?\\d{2}', caption)
            if phone_match:
                client_info += f"Телефон из подписи: {phone_match.group()}\n"
        
        caption_text = f"📷 Фото от клиента:\n{client_info}\nДля ответа используйте: /reply {update.message.chat.id} <текст>"
        await update.message.forward(chat_id=ADMIN_TELEGRAM_ID)
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=caption_text)
        await update.message.reply_text("Ваш запрос получен и направлен оператору для ручной обработки. Вы можете напрямую обратиться по телефону +77073145.")
    except Exception as e:
        logger.error(f"Ошибка при пересылке фото: {e}")
        await update.message.reply_text("Произошла ошибка при обработке вашего запроса.")

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обрабатывает голосовое сообщение:
    1. Скачивает аудиофайл голосового сообщения.
    2. Отправляет его в OpenAI Whisper для транскрипции.
    3. Обрабатывает полученный текст как обычный запрос.
    """
    try:
        file = await update.message.voice.get_file()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
            temp_file_path = tmp_file.name
        await file.download_to_drive(custom_path=temp_file_path)
        with open(temp_file_path, "rb") as audio_file:
            transcript = openai.Audio.transcribe("whisper-1", audio_file)
        os.remove(temp_file_path)
        text = transcript.get("text", "")
        await update.message.reply_text(f"Распознанный текст: {text}")
        # Обрабатываем текст как обычный запрос
        update.message.text = text
        await handle_message(update, context)
    except Exception as e:
        logger.error(f"Ошибка при обработке голосового сообщения: {e}")
        await update.message.reply_text("Ошибка при обработке вашего голосового сообщения.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добро пожаловать! Я виртуальный помощник лаборатории. Чем могу помочь?")

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat.id) != ADMIN_TELEGRAM_ID:
        return
    try:
        parts = update.message.text.split(" ", 2)
        target_user = int(parts[1])
        operator_reply = parts[2]
        await context.bot.send_message(chat_id=target_user, text=operator_reply)
        await update.message.reply_text("Ответ отправлен клиенту.")
        pending_requests.pop(target_user, None)
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в команде /reply: {e}")
        await update.message.reply_text("Неправильный формат команды. Используйте: /reply <user_id> <ответ>")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat.id
    logger.info(f"Запрос от {user_id}: {user_message}")
    
    if "сравнить" in user_message:
        if user_id in pending_requests:
            saved_names = pending_requests[user_id]
            comp_response = compare_with_competitors(saved_names)
            if "не найдена" in comp_response.lower():
                await notify_admin_about_missing_request(saved_names, user_id, context)
                comp_response += "\n\nИзвините, информация по конкурентам отсутствует. Запрос передан оператору."
            await update.message.reply_text(comp_response)
            return
        else:
            await update.message.reply_text("Нет предыдущего запроса для сравнения.")
            return

    analyses = get_all_analyses()
    extracted_names = extract_matched_analyses(user_message, analyses)
    if extracted_names:
        pending_requests[user_id] = extracted_names
    else:
        pending_requests[user_id] = user_message

    response = ask_openai(user_message, analyses)
    final_response = await process_response(response, user_message, user_id, context)
    
    competitor_data = get_competitor_data()
    if competitor_data:
        final_response += "\n\nЕсли хотите сравнить цены с конкурентами, отправьте 'сравнить'."
    
    await update.message.reply_text(final_response)

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))  # Если функция handle_contact нужна, можно её добавить
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()
