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
from google.oauth2 import service_account

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

# Telegram ID администратора (ваш личный Telegram ID)
ADMIN_TELEGRAM_ID = "5241327545"

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Глобальный словарь для хранения извлечённых названий анализов из последнего запроса
pending_requests = {}

# Глоссарий синонимов: ключ – вариант, значение – каноническое название анализа (как записано в базе)
SYNONYMS = {
    "рф": "рф-суммарный",
    "иммуноглобулин e": "ige", "ig e",
    "иге": "ige",
    # Добавляйте другие синонимы по мере необходимости
}

##########################
# Функции работы с базой данных и нормализации
##########################

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
        # Приводим названия к нижнему регистру для корректного сопоставления
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
        return []

def get_competitor_data():
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, lab, price, timeframe FROM competitor_prices")
        results = cursor.fetchall()
        conn.close()
        return [(normalize_text(name), lab, price, timeframe) for name, lab, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при загрузке данных конкурентов: {e}")
        return []

def normalize_text(text):
    """
    Приводит текст к нижнему регистру для обеспечения корректного сопоставления.
    """
    return text.lower()

##########################
# Функции для OpenAI
##########################

def get_lab_context(analyses):
    analyses_list = "\n".join(
        [f"{name}: Цена — {price} KZT. Срок — {timeframe}" for name, price, timeframe in analyses]
    )
    return (
        "Ты — виртуальный помощник медицинской лаборатории. "
        "Дай пользователю краткую и точную информацию по анализам. "
        "Вот данные наших анализов:\n"
        f"{analyses_list}\n\n"
        "Если анализ не найден, сообщи, что его нет в базе."
    )

def ask_openai(prompt, analyses):
    try:
        lab_context = get_lab_context(analyses)
        full_prompt = prompt + "\n\nЕсли в запросе упомянуты анализы, которых нет в нашей базе, сообщи, что информация по ним отсутствует."
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # При необходимости переключитесь на gpt-4-turbo
            messages=[
                {"role": "system", "content": lab_context},
                {"role": "user", "content": full_prompt},
            ],
            max_tokens=400,
            temperature=0.5,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "Извините, я не смог обработать ваш запрос."

##########################
# Функции сравнения с конкурентами
##########################

def extract_matched_analyses(query, analyses):
    """
    Извлекает названия анализов, сравнивая отдельные слова из текста с названиями анализов.
    Перед сравнением каждое слово преобразуется по глоссарию синонимов.
    Возвращает строку с найденными анализами, разделёнными запятыми.
    """
    matched = set()
    # Получаем список слов из текста (без знаков препинания)
    query_tokens = re.findall(r'\w+', query)
    # Применяем глоссарий: заменяем каждое слово на канонический вариант, если указано в SYNONYMS
    canonical_tokens = [SYNONYMS.get(token, token) for token in query_tokens]
    for name, _, _ in analyses:
        name_tokens = re.findall(r'\w+', name)
        for n_token in name_tokens:
            for token in canonical_tokens:
                if token == n_token or get_close_matches(token, [n_token], n=1, cutoff=0.8):
                    matched.add(name)
                    break
            else:
                continue
            break
    logger.info(f"OCR текст: {query_tokens}, преобразованные токены: {canonical_tokens}, найденные анализы: {matched}")
    return ", ".join(matched)

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
    names = [name.strip() for name in matched_names.split(",")]
    results = []
    for name in names:
        best = find_best_match(name, competitor_data)
        if best:
            comp_name, lab, comp_price, comp_timeframe = best
            results.append(f"Конкурент ({lab}): {comp_name} — {comp_price} KZT, Срок: {comp_timeframe}")
        else:
            results.append(f"Для анализа '{name}' информация по конкурентам не найдена.")
    return "\n".join(results)

##########################
# Логирование необработанных запросов и уведомление оператора
##########################

async def notify_admin_about_missing_request(query, user_id, context):
    pending_requests[user_id] = query
    message = (
        f"⚠️ Пропущенный запрос от пользователя {user_id}:\n\n"
        f"Запрос: {query}\n\n"
        f"Для ответа используйте команду: /reply {user_id} <Ваш ответ>"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")

async def process_response(response, user_message, user_id, context):
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return ("Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения. Вы можете напрямую обратиться по телефону +77073145. ")
    return response

##########################
# Функции интеграции с фотографиями
##########################

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик фотографий:
    1. Пересылает фото, отправленное пользователем, оператору для ручной обработки.
    2. Уведомляет клиента, что запрос направлен оператору.
    """
    try:
        # Пересылаем сообщение (фото) оператору
        await update.message.forward(chat_id=ADMIN_TELEGRAM_ID)
        # Отправляем клиенту уведомление
        await update.message.reply_text(
            "Ваш запрос получен и направлен оператору для ручной обработки. "
            "Вы можете напрямую обратиться по телефону +77073145."
        )
    except Exception as e:
        logger.error(f"Ошибка при пересылке фото: {e}")
        await update.message.reply_text("Произошла ошибка при обработке вашего запроса.")

##########################
# Обработчики команд и сообщений
##########################

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добро пожаловать! Я виртуальный помощник лаборатории. Чем могу помочь?")

async def reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.message.chat_id) != ADMIN_TELEGRAM_ID:
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
    user_id = update.message.chat_id
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

##########################
# Основная функция
##########################

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))  # Обработчик фотографий
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
