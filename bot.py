import logging
import os
import sqlite3
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Константы
DB_FILE = "lab_data(2).db"

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE

# Telegram ID администратора
ADMIN_TELEGRAM_ID = "5241327545"  # Укажите ваш ID, полученный через @userinfobot

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Подключение к базе данных
def connect_to_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

# Функция нормализации текста
def normalize_text(text):
    text = text.replace("б", "b")
    return text.lower()

# Получение всех анализов из БД
def get_all_analyses():
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

# Поиск наиболее похожего анализа
def find_closest_analysis(query, analyses):
    from difflib import get_close_matches
    query = normalize_text(query)
    names = [name for name, _, _ in analyses]
    matches = get_close_matches(query, names, n=1, cutoff=0.7)  # 70% совпадения
    
    if matches:
        for name, price, timeframe in analyses:
            if name == matches[0]:
                return f"{matches[0]}: Цена — {price} KZT. Срок выполнения — {timeframe}."
    return None

# Формирование контекста для OpenAI
def get_lab_context(analyses):
    analyses_list = "\n".join(
        [f"{name}: Цена — {price} KZT. Срок выполнения — {timeframe}." for name, price, timeframe in analyses]
    )
    return (
        "Ты — виртуальный помощник медицинской лаборатории. Пользователь может запрашивать анализы в произвольной форме. "
        "Вот данные всех анализов, которые есть в базе:\n"
        f"{analyses_list}\n\n"
        "Если пользователь спрашивает про анализ, попытайся найти его среди доступных. "
        "Если он не найден, сообщи пользователю об этом."
    )

# Сохранение необработанных запросов в БД
def log_unprocessed_request(query, user_id):
    conn = connect_to_db()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO unprocessed_requests (user_id, query) VALUES (?, ?)", (user_id, query)
            )
            conn.commit()
            conn.close()
            logger.info(f"Запрос '{query}' от пользователя {user_id} добавлен в необработанные.")
        except sqlite3.Error as e:
            logger.error(f"Ошибка при записи в БД: {e}")

# Запрос к OpenAI
def ask_openai(prompt, analyses):
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

# Обработка запроса пользователя
async def handle_message(update: Update, context):
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat_id
    logger.info(f"Получен запрос от {user_id}: {user_message}")

    analyses = get_all_analyses()
    
    # 1. Поиск анализа в базе
    matched_analysis = find_closest_analysis(user_message, analyses)
    if matched_analysis:
        await update.message.reply_text(matched_analysis)
        return
    
    # 2. Запрос в OpenAI
    response = ask_openai(user_message, analyses)

    # 3. Если OpenAI не нашел анализ, фиксируем запрос
    if "нет в базе" in response.lower() or "не найден" in response.lower():
        log_unprocessed_request(user_message, user_id)
        response += "\n\nВаш запрос передан оператору."

    await update.message.reply_text(response)

# Команда /start
async def start(update: Update, context):
    await update.message.reply_text("Добро пожаловать! Я виртуальный ассистент лаборатории. Чем могу помочь?")

# Команда /reply (ответ администратора)
async def reply(update: Update, context):
    if str(update.message.chat_id) != ADMIN_TELEGRAM_ID:
        return

    try:
        command_parts = update.message.text.split(" ", 2)
        user_id = int(command_parts[1])
        reply_message = command_parts[2]

        await context.bot.send_message(chat_id=user_id, text=reply_message)
        await update.message.reply_text("Ответ отправлен клиенту.")
    except (IndexError, ValueError):
        await update.message.reply_text("Неправильный формат команды. Используйте: /reply <user_id> <ответ>")

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
