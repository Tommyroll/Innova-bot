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
DB_FILE = "lab_data(2).db"
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE

# Telegram ID администратора
ADMIN_TELEGRAM_ID = "5241327545"

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Глобальный словарь для последнего запроса пользователя
last_user_query = {}

##########################
# Функции работы с БД
##########################

def connect_to_db():
    """Подключение к базе данных SQLite."""
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

def get_all_analyses():
    """Получает все анализы из базы."""
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
    """Загружает данные конкурентов из базы."""
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
    """Приводит текст к нижнему регистру и заменяет кириллическую 'б' на латинскую 'b'."""
    text = text.replace("б", "b")
    return text.lower()

##########################
# Функции для OpenAI
##########################

def get_lab_context(analyses):
    """Формирует системный контекст для OpenAI с перечнем анализов."""
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
    """Отправляет запрос в OpenAI."""
    try:
        lab_context = get_lab_context(analyses)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
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

def find_best_match(query, competitor_data):
    """Ищет наиболее похожий анализ среди данных конкурентов."""
    competitor_names = [name for name, _, _, _ in competitor_data]
    matches = get_close_matches(query, competitor_names, n=1, cutoff=0.6)
    if matches:
        for name, lab, price, timeframe in competitor_data:
            if name == matches[0]:
                return (name, lab, price, timeframe)
    return None

def compare_with_competitors(query):
    """Сравнивает цену нашего анализа с конкурентами."""
    competitor_data = get_competitor_data()
    best = find_best_match(query, competitor_data)
    if best:
        name, lab, price, timeframe = best
        return f"Конкурент ({lab}): {name}: Цена — {price} KZT, Срок — {timeframe}."
    return "Информация по конкурентам не найдена."

##########################
# Логирование необработанных запросов
##########################

pending_requests = {}

async def notify_admin_about_missing_request(query, user_id, context):
    """Уведомляет администратора о пропущенном запросе."""
    pending_requests[user_id] = query
    message = (
        f"⚠️ Пропущенный запрос от пользователя {user_id}:\n\n"
        f"Запрос: {query}\n\n"
        f"/reply {user_id} <Ваш ответ>"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления: {e}")

##########################
# Основной обработчик сообщений
##########################

async def handle_message(update: Update, context):
    """Обрабатывает сообщения пользователей."""
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat_id
    logger.info(f"Запрос от {user_id}: {user_message}")

    if "сравнить" in user_message:
        if user_id in pending_requests:
            original_query = pending_requests[user_id]
            comp_response = compare_with_competitors(original_query)
            await update.message.reply_text(comp_response)
            return
        else:
            await update.message.reply_text("Нет предыдущего запроса для сравнения.")
            return

    pending_requests[user_id] = user_message

    analyses = get_all_analyses()
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
