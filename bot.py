import logging
import os
import openai
import sqlite3
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

# Получение переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = "lab_data.db"  # Укажите путь к вашей базе данных SQLite

# Инициализация OpenAI
openai.api_key = OPENAI_API_KEY

# Функция для подключения к базе данных
def connect_to_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None

# Чтение данных из базы SQLite
def query_analysis(query):
    try:
        conn = connect_to_db()
        if conn:
            cursor = conn.cursor()
            # Поиск по имени анализа (или его синонимам, если нужно)
            cursor.execute("""
                SELECT name, price, timeframe FROM analyses
                WHERE LOWER(name) LIKE ? 
            """, (f"%{query.lower()}%",))
            results = cursor.fetchall()
            conn.close()
            return results
    except sqlite3.Error as e:
        logger.error(f"Ошибка запроса к базе данных: {e}")
    return []

# Формирование ответа из результатов SQLite
def format_analysis_response(results):
    if results:
        response = "\n".join(
            [f"{name}: Цена — {price} KZT. Срок выполнения — {timeframe}." for name, price, timeframe in results]
        )
        return response
    return "Извините, не удалось найти информацию о запрашиваемом анализе."

# Функция для формирования контекста OpenAI
def get_lab_context():
    return (
        "Ты — виртуальный помощник медицинской лаборатории. "
        "Отвечай кратко и по существу. "
        "Обязательно указывай стоимость анализа, сроки выполнения и инструкции по подготовке, если пользователь упоминает конкретный анализ. "
        "Не используй длинные приветствия или дополнительные фразы. "
        "Дай пользователю всю необходимую информацию без лишних деталей."
    )

# Запрос к OpenAI
def ask_openai(prompt):
    try:
        lab_context = get_lab_context()
        response = openai.ChatCompletion.create(
            model="gpt-4-turbo",
            messages=[
                {"role": "system", "content": lab_context},
                {"role": "user", "content": prompt},
            ],
            max_tokens=400,  # Увеличиваем лимит токенов
            temperature=0.5,  # Снижаем случайность ответов
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "Извините, я не смог обработать ваш запрос."

# Обработчик команды /start
async def start(update: Update, context):
    await update.message.reply_text(
        "Добро пожаловать! Я виртуальный ассистент лаборатории. Чем могу помочь?"
    )

# Обработчик сообщений
async def handle_message(update: Update, context):
    user_message = update.message.text.lower()
    logger.info(f"Получен запрос: {user_message}")

    # Поиск в базе SQLite
    results = query_analysis(user_message)
    if results:
        response = format_analysis_response(results)
    else:
        # Если анализ не найден, передать запрос в OpenAI
        response = ask_openai(user_message)

    await update.message.reply_text(response)

# Основная функция
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
