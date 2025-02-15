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
DB_FILE = "lab_data(2).db"  # Файл базы данных с нашими анализами и конкурентами
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
    """
    Отправляет запрос в OpenAI с контекстом лаборатории и возвращает сгенерированный ответ.
    """
    try:
        lab_context = get_lab_context(analyses)
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # При необходимости переключитесь на gpt-4-turbo
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

def extract_matched_analyses(query, analyses):
    """
    Извлекает из запроса названия анализов, сравнивая каждую часть с данными из нашей базы.
    Запрос разбивается по запятым или " и ". Возвращает строку с найденными названиями, разделёнными запятыми.
    """
    if "," in query:
        parts = [part.strip() for part in query.split(",")]
    elif " и " in query:
        parts = [part.strip() for part in query.split(" и ")]
    else:
        parts = [query.strip()]
    matched = []
    for part in parts:
        for name, _, _ in analyses:
            if part in name or get_close_matches(part, [name], n=1, cutoff=0.5):
                matched.append(name)
    return ", ".join(list(set(matched)))

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

def compare_with_competitors(matched_names):
    """
    Сравнивает цены по анализам, переданным в виде строки с названиями, разделёнными запятыми.
    """
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
    Если ответ от OpenAI содержит фразы об отсутствии анализа, уведомляет администратора
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

    # Если пользователь отправил "сравнить", используем сохранённый список анализов из предыдущего запроса
    if "сравнить" in user_message:
        if user_id in pending_requests:
            saved_names = pending_requests[user_id]  # Это строка с названиями анализов, разделёнными запятыми
            comp_response = compare_with_competitors(saved_names)
            if "не найдена" in comp_response.lower():
                await notify_admin_about_missing_request(saved_names, user_id, context)
                comp_response += "\n\nИзвините, информация по конкурентам отсутствует. Запрос передан оператору."
            await update.message.reply_text(comp_response)
            return
        else:
            await update.message.reply_text("Нет предыдущего запроса для сравнения.")
            return

    # Получаем данные наших анализов
    analyses = get_all_analyses()
    # Извлекаем названия анализов из запроса (с учетом нормализации)
    extracted_names = extract_matched_analyses(user_message, analyses)
    if extracted_names:
        pending_requests[user_id] = extracted_names  # Сохраняем список анализов для сравнения
    else:
        pending_requests[user_id] = user_message  # Если ничего не найдено, сохраняем исходный запрос

    response = ask_openai(user_message, analyses)
    final_response = await process_response(response, user_message, user_id, context)
    
    # Если конкурентные данные есть, предлагаем сравнить цены
    competitor_data = get_competitor_data()
    if competitor_data:
        final_response += "\n\nЕсли хотите сравнить цены с конкурентами, отправьте 'сравнить'."
    
    await update.message.reply_text(final_response)

# Функция извлечения названий анализов из запроса
def extract_matched_analyses(query, analyses):
    """
    Разбивает запрос по запятым или союзам, сравнивает каждую часть с названиями анализов из нашей базы.
    Возвращает строку с найденными названиями, разделёнными запятыми.
    """
    if "," in query:
        parts = [part.strip() for part in query.split(",")]
    elif " и " in query:
        parts = [part.strip() for part in query.split(" и ")]
    else:
        parts = [query.strip()]
    matched = []
    for part in parts:
        for name, _, _ in analyses:
            if part in name or get_close_matches(part, [name], n=1, cutoff=0.5):
                matched.append(name)
    return ", ".join(list(set(matched)))

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
