import logging
import os
import sqlite3
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# Константы
DB_FILE = "lab_data.db"

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

# Словарь для хранения необработанных запросов
pending_requests = {}


# Функция подключения к БД
def connect_to_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Ошибка подключения к базе данных: {e}")
        return None


# Функция приведения строки к нижнему регистру и замены кириллицы
def normalize_text(text):
    text = text.replace("б", "b")
    return text.lower()


# Функция получения всех анализов из БД
def get_all_analyses():
    try:
        conn = connect_to_db()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name, price, timeframe FROM analyses")
            results = cursor.fetchall()
            conn.close()
            return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
    return []


# Формирование контекста для OpenAI
def get_lab_context(analyses):
    analyses_list = "\n".join(
        [f"{name}: Цена — {price} KZT. Срок выполнения — {timeframe}." for name, price, timeframe in analyses]
    )
    return (
        "Ты — виртуальный помощник медицинской лаборатории. Пользователь может запрашивать анализы в произвольной форме. "
        "Вот данные всех анализов, которые есть в базе:\n"
        f"{analyses_list}\n\n"
        "Если пользователь спрашивает про какой-то анализ, попытайся сопоставить его запрос с доступными анализами "
        "и предоставь информацию. Если анализ не найден, скажи, что его нет в базе."
    )


# Отправка уведомления администратору
async def notify_admin_about_missing_request(query, user_id, context):
    pending_requests[user_id] = query  # Сохраняем запрос клиента
    message = (
        f"⚠️ Пропущенный запрос от пользователя {user_id}:\n\n"
        f"Запрос: {query}\n\n"
        f"Ответьте этому пользователю, отправив сообщение боту в формате:\n"
        f"/reply {user_id} [Ваш ответ]"
    )
    try:
        await context.bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=message)
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору: {e}")


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
            max_tokens=200,
            temperature=0.5,
        )
        return response["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {e}")
        return "Извините, я не смог обработать ваш запрос."


# Логика обработки ответа от OpenAI
async def process_response(response, user_message, user_id, context):
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return (
            "Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения."
        )
    return response


# Обработчик команды /start
async def start(update: Update, context):
    await update.message.reply_text(
        "Добро пожаловать! Я виртуальный ассистент лаборатории. Чем могу помочь?"
    )


# Обработчик команды /reply для ответов админа
async def reply(update: Update, context):
    if str(update.message.chat_id) != ADMIN_TELEGRAM_ID:
        return

    try:
        command_parts = update.message.text.split(" ", 2)
        user_id = int(command_parts[1])
        reply_message = command_parts[2]

        if user_id in pending_requests:
            await context.bot.send_message(chat_id=user_id, text=reply_message)
            await update.message.reply_text("Ответ отправлен клиенту.")
            pending_requests.pop(user_id)
        else:
            await update.message.reply_text("Не найден запрос для указанного пользователя.")
    except (IndexError, ValueError):
        await update.message.reply_text("Неправильный формат команды. Используйте: /reply <user_id> <ответ>")


# Обработчик сообщений
async def handle_message(update: Update, context):
    user_message = normalize_text(update.message.text)
    user_id = update.message.chat_id
    logger.info(f"Получен запрос от {user_id}: {user_message}")

    analyses = get_all_analyses()
    response = ask_openai(user_message, analyses)

    # Обрабатываем ответ и отправляем пользователю
    final_response = await process_response(response, user_message, user_id, context)
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
