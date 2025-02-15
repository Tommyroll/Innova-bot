import logging
import os
import sqlite3
import openai
from difflib import get_close_matches
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Константы и настройки
DB_FILE = "lab_data(2).db"  # Файл базы данных с нашими анализами и данными конкурентов
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

# Глобальный словарь для хранения последних запросов пользователей
pending_requests = {}

##########################
# Функции работы с базой данных
##########################

def connect_to_db():
    """Подключается к базе данных SQLite."""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

def get_all_analyses():
    """Получает все анализы из таблицы analyses."""
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, timeframe FROM analyses")
        results = cursor.fetchall()
        conn.close()
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
        return []

def get_competitor_data():
    """Получает данные из таблицы competitor_prices."""
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

##########################
# Функции нормализации текста
##########################

def normalize_text(text):
    """
    Приводит текст к нижнему регистру и заменяет кириллическую "б" на латинскую "b".
    Это помогает сопоставлять, например, "витамин б" и "витамин B".
    """
    text = text.replace("б", "b")
    return text.lower()

##########################
# Функции для OpenAI
##########################

def get_lab_context(analyses):
    """
    Формирует системный контекст для OpenAI с перечнем наших анализов.
    """
    analyses_list = "\n".join(
        [f"{name}: Цена — {price} KZT. Срок выполнения — {timeframe}." for name, price, timeframe in analyses]
    )
    return (
        "Ты — виртуальный помощник медицинской лаборатории. Пользователь может запрашивать анализы в произвольной форме. "
        "Вот данные наших анализов:\n"
        f"{analyses_list}\n\n"
        "Если пользователь спрашивает про анализ, попытайся сопоставить его запрос с доступными анализами и предоставить информацию. "
        "Если анализ не найден, сообщи, что его нет в базе."
    )

def ask_openai(prompt, analyses):
    """
    Отправляет запрос в OpenAI с контекстом лаборатории и возвращает сгенерированный ответ.
    """
    try:
        lab_context = get_lab_context(analyses)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # При необходимости переключитесь на другую модель
            messages=[
                {"role": "system", "content": lab_context},
                {"role": "user", "content": prompt},
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

def extract_analysis_names_from_query(query, analyses):
    """
    Извлекает названия анализов из запроса, сравнивая его с именами анализов из нашей базы.
    Возвращает список найденных названий.
    """
    normalized_query = normalize_text(query)
    extracted = []
    for name, price, timeframe in analyses:
        if name in normalized_query:
            extracted.append(name)
    return extracted

def find_best_match(query, competitor_data):
    """
    Использует get_close_matches для поиска наиболее похожего анализа среди данных конкурентов.
    Порог схожести установлен на 0.5.
    """
    competitor_names = [name for name, _, _, _ in competitor_data]
    matches = get_close_matches(query, competitor_names, n=1, cutoff=0.5)
    if matches:
        for name, lab, price, timeframe in competitor_data:
            if name == matches[0]:
                return (name, lab, price, timeframe)
    return None

def compare_with_competitors(query):
    """
    Сравнивает цены для анализов, извлеченных из запроса.
    Вместо использования необработанного запроса, извлекаем имена анализов, используя данные из нашей базы.
    """
    competitor_data = get_competitor_data()
    our_analyses = get_all_analyses()
    extracted_names = extract_analysis_names_from_query(query, our_analyses)
    if not extracted_names:
        return "Не удалось извлечь названия анализов для сравнения."
    
    results = []
    for name in extracted_names:
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
    """
    Отправляет уведомление администратору о том, что запрос не обработан.
    """
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
    """
    Если ответ от OpenAI содержит фразы об отсутствии анализа, уведомляет оператора
    и возвращает шаблонный ответ.
    """
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return "Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения."
    return response

##########################
# Обработчики команд и сообщений
##########################

# Обработчик команды /start
async def start(update: Update, context):
    await update.message.reply_text("Добро пожаловать! Я виртуальный помощник лаборатории. Чем могу помочь?")

# Обработчик команды /reply для оператора
async def reply(update: Update, context):
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

# Основной обработчик сообщений
async def handle_message(update: Update, context):
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat_id
    logger.info(f"Запрос от {user_id}: {user_message}")

    # Если пользователь отправил "сравнить", используем последний сохранённый запрос
    if "сравнить" in user_message:
        if user_id in pending_requests:
            original_query = pending_requests[user_id]
            comp_response = compare_with_competitors(original_query)
            # Если сравнение не найдено, уведомляем администратора
            if "не найдена" in comp_response.lower():
                await notify_admin_about_missing_request(original_query, user_id, context)
                comp_response += "\n\nИзвините, информация по конкурентам отсутствует. Запрос передан оператору."
            await update.message.reply_text(comp_response)
            return
        else:
            await update.message.reply_text("Нет предыдущего запроса для сравнения.")
            return

    # Сохраняем последний запрос пользователя для возможности сравнения
    pending_requests[user_id] = user_message

    analyses = get_all_analyses()
    response = ask_openai(user_message, analyses)
    final_response = await process_response(response, user_message, user_id, context)
    
    # Если конкурентные данные есть, предлагаем сравнить цены
    competitor_data = get_competitor_data()
    if competitor_data:
        final_response += "\n\nДля сравнения цен с конкурентами отправьте 'сравнить'."
    
    await update.message.reply_text(final_response)

# Основная функция
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
