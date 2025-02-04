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
DB_FILE = "lab_data(2).db"  # Файл вашей базы данных (с нашими анализами и данными конкурентов)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE

# Telegram ID администратора (ваш личный Telegram ID, полученный через @userinfobot)
ADMIN_TELEGRAM_ID = "5241327545"

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Глобальный словарь для сохранения последнего запроса пользователя
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
    """
    Получает все анализы из таблицы analyses.
    Ожидается, что таблица analyses имеет столбцы: name, price, timeframe.
    Приводит названия к нижнему регистру с заменой кириллицы ("б" → "b").
    """
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
    """
    Получает все данные из таблицы competitor_prices.
    Ожидается, что таблица competitor_prices имеет столбцы: name, price, timeframe.
    Здесь нормализация не обязательно, но можно привести название к нижнему регистру.
    """
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, price, timeframe FROM competitor_prices")
        results = cursor.fetchall()
        conn.close()
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных конкурентов: {e}")
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
    Формирует системный контекст для OpenAI, включая список наших анализов.
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
            model="gpt-4o-mini",  # Если доступна, иначе можно переключиться на gpt-4-turbo
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
# Функции для сравнения с конкурентами
##########################

def find_best_match(query, competitor_data):
    """
    Использует get_close_matches для поиска наиболее похожего анализа среди данных конкурентов.
    Если схожесть больше 0.6, возвращает первую найденную запись.
    """
    competitor_names = [name for name, _, _ in competitor_data]
    matches = get_close_matches(query, competitor_names, n=1, cutoff=0.6)
    if matches:
        for name, price, timeframe in competitor_data:
            if name == matches[0]:
                return (name, price, timeframe)
    return None

def compare_with_competitors(query):
    """
    Выполняет сравнительный анализ: ищет подходящий анализ в таблице competitor_prices
    и возвращает строку с информацией.
    """
    competitor_data = get_competitor_data()
    best = find_best_match(query, competitor_data)
    if best:
        name, price, timeframe = best
        return f"Конкурент: {name}: Цена — {price} KZT, Срок — {timeframe}."
    return "Информация по конкурентам не найдена."

##########################
# Логирование необработанных запросов
##########################

async def notify_admin_about_missing_request(query, user_id, context):
    """
    Отправляет уведомление администратору о том, что запрос не обработан (не найден анализ).
    """
    # Сохраняем запрос в глобальном словаре для дальнейшей обработки (если нужно)
    pending_requests[user_id] = query
    message = (
        f"⚠️ Пропущенный запрос от пользователя {user_id}:\n\n"
        f"Запрос: {query}\n\n"
        f"Ответьте этому пользователю, отправив сообщение боту в формате:\n"
        f"/reply {user_id} <Ваш ответ>"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору: {e}")

##########################
# Обработка ответа от OpenAI
##########################

async def process_response(response, user_message, user_id, context):
    """
    Если ответ от OpenAI содержит фразы об отсутствии анализа, уведомляет администратора и возвращает шаблонный ответ.
    """
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return "Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения."
    return response

##########################
# Обработчики команд и сообщений
##########################

# Команда /start
async def start(update: Update, context):
    await update.message.reply_text("Добро пожаловать! Я виртуальный помощник лаборатории. Чем могу помочь?")

# Команда /reply для оператора
async def reply(update: Update, context):
    if str(update.message.chat_id) != ADMIN_TELEGRAM_ID:
        return
    try:
        parts = update.message.text.split(" ", 2)
        target_user = int(parts[1])
        operator_reply = parts[2]
        await context.bot.send_message(chat_id=target_user, text=operator_reply)
        await update.message.reply_text("Ответ отправлен клиенту.")
    except (IndexError, ValueError) as e:
        logger.error(f"Ошибка в команде /reply: {e}")
        await update.message.reply_text("Неправильный формат команды. Используйте: /reply <user_id> <ответ>")

# Основной обработчик сообщений
async def handle_message(update: Update, context):
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat_id
    logger.info(f"Получен запрос от {user_id}: {user_message}")

    # Если пользователь отправил "сравнить", запускаем сравнение по последнему запросу
    if "сравнить" in user_message:
        # Если предыдущий запрос сохранён, используем его для сравнения
        if user_id in pending_requests:
            original_query = pending_requests[user_id]
            comp_response = compare_with_competitors(original_query)
            await update.message.reply_text(comp_response)
            return
        else:
            await update.message.reply_text("Нет предыдущего запроса для сравнения.")
            return

    # Сохраняем последний запрос пользователя (для возможности сравнения)
    last_query = user_message
    pending_requests[user_id] = last_query

    # Получаем данные наших анализов
    analyses = get_all_analyses()
    response = ask_openai(user_message, analyses)
    final_response = await process_response(response, user_message, user_id, context)
    
    # Если в ответе есть информация о наших анализах, предлагаем сравнить цены с конкурентами
    competitor_data = get_competitor_data()
    if competitor_data:
        final_response += "\n\nЕсли хотите сравнить цены с конкурентами, отправьте 'сравнить'."
    
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
