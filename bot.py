import logging
import os
import sqlite3
import openai
from difflib import get_close_matches
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
DB_FILE = "lab_data(2).db"  # Файл базы данных с анализами и конкурентами
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=os.getenv("LOG_LEVEL", "INFO"),
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_PATH = DB_FILE
ADMIN_TELEGRAM_ID = "5241327545"  # Ваш Telegram ID

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
        return [(normalize_text(name), price, timeframe) for name, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при извлечении данных из БД: {e}")
        return []
    finally:
        conn.close()

def get_competitor_data():
    """Получает данные из таблицы competitor_prices."""
    conn = connect_to_db()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name, lab, price, timeframe FROM competitor_prices")
        results = cursor.fetchall()
        return [(normalize_text(name), lab, price, timeframe) for name, lab, price, timeframe in results]
    except sqlite3.Error as e:
        logger.error(f"Ошибка при загрузке данных конкурентов: {e}")
        return []
    finally:
        conn.close()

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
# Функция поиска анализов (старая версия)
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

##########################
# Обработчики команд и сообщений
##########################

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

async def notify_admin_about_missing_request(query, user_id, context: ContextTypes.DEFAULT_TYPE):
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

async def process_response(response, user_message, user_id, context: ContextTypes.DEFAULT_TYPE):
    if any(phrase in response.lower() for phrase in ["отсутствует", "нет в базе", "не найден"]):
        await notify_admin_about_missing_request(user_message, user_id, context)
        return ("Извините, этот анализ отсутствует в нашей базе. Мы передали запрос оператору для уточнения. "
                "Вы можете напрямую обратиться по телефону +77073145.")
    return response

##########################
# Дополнительные обработчики (фото, голос, контакт)
##########################

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    При получении фото бот пересылает его оператору вместе с информацией о клиенте,
    а клиент получает уведомление о том, что запрос направлен оператору.
    """
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        user = update.message.from_user
        client_info = f"ID: {update.message.chat.id}\n"
        if user.username:
            client_info += f"Username: @{user.username}\n"
        client_info += f"Имя: {user.first_name} {user.last_name or ''}\n"
        if update.message.contact and update.message.contact.phone_number:
            client_info += f"Телефон: {update.message.contact.phone_number}\n"
        else:
            caption = update.message.caption or ""
            phone_match = re.search(r'(\+7|8)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}', caption)
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
        update.message.text = text
        await handle_message(update, context)
    except Exception as e:
        logger.error(f"Ошибка при обработке голосового сообщения: {e}")
        await update.message.reply_text("Ошибка при обработке вашего голосового сообщения.")

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает полученный контакт."""
    try:
        phone = update.message.contact.phone_number
        user = update.message.from_user
        await context.bot.send_message(
            ADMIN_TELEGRAM_ID,
            f"📱 Новый контакт от {user.first_name} {user.last_name or ''} (ID: {user.id}):\nТелефон: {phone}"
        )
        await update.message.reply_text(
            "✅ Спасибо! Ваш контакт сохранён. Оператор свяжется с вами в ближайшее время.",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке контакта: {e}")

##########################
# Основная функция
##########################

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reply", reply))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
