import os
import sqlite3
import asyncio
from dotenv import load_dotenv

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from openai import OpenAI

# ========================
# LOAD ENV
# ========================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not set")

# ВАЖНО: НЕ передаем api_key вручную
client = OpenAI()

print("✅ ENV loaded")

# ========================
# DATABASE
# ========================

conn = sqlite3.connect("memory.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS memory(
    user_id TEXT,
    role TEXT,
    content TEXT
)
""")

conn.commit()


def save_memory(user_id, role, content):
    cursor.execute(
        "INSERT INTO memory VALUES(?,?,?)",
        (user_id, role, content),
    )
    conn.commit()


def load_memory(user_id, limit=20):
    cursor.execute(
        """
        SELECT role, content
        FROM memory
        WHERE user_id=?
        ORDER BY ROWID DESC
        LIMIT ?
        """,
        (user_id, limit),
    )

    rows = cursor.fetchall()
    rows.reverse()

    return [{"role": r[0], "content": r[1]} for r in rows]


def clear_memory(user_id):
    cursor.execute(
        "DELETE FROM memory WHERE user_id=?",
        (user_id,),
    )
    conn.commit()


# ========================
# KEYBOARD
# ========================

keyboard = ReplyKeyboardMarkup(
    [
        ["🧠 Новая сессия"],
        ["🔊 Озвучить"],
        ["⚙️ Помощь"],
    ],
    resize_keyboard=True,
)

# ========================
# GPT SAFE CALL
# ========================

async def ask_gpt(user_id, text):

    try:

        save_memory(user_id, "user", text)

        messages = load_memory(user_id)

        # выполняем в отдельном потоке (ВАЖНО)
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.7,
            )
        )

        reply = response.choices[0].message.content

        save_memory(user_id, "assistant", reply)

        return reply

    except Exception as e:

        print("GPT ERROR:", e)

        return "❌ Ошибка GPT. Проверь OPENAI_API_KEY"


# ========================
# VOICE → TEXT
# ========================

async def voice_to_text(path):

    try:

        transcript = await asyncio.to_thread(
            lambda: client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=open(path, "rb"),
            )
        )

        return transcript.text

    except Exception as e:

        print("STT ERROR:", e)

        return None


# ========================
# TEXT → VOICE
# ========================

async def text_to_voice(text, path):

    try:

        response = await asyncio.to_thread(
            lambda: client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice="alloy",
                input=text,
            )
        )

        with open(path, "wb") as f:
            f.write(response.content)

        return True

    except Exception as e:

        print("TTS ERROR:", e)

        return False


# ========================
# HANDLERS
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🚀 GPT бот готов",
        reply_markup=keyboard,
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    text = update.message.text

    if text == "🧠 Новая сессия":

        clear_memory(user_id)

        await update.message.reply_text("✅ Память очищена")

        return

    if text == "⚙️ Помощь":

        await update.message.reply_text(
            "Напиши сообщение или отправь голос"
        )

        return

    msg = await update.message.reply_text("💭 Думаю...")

    reply = await ask_gpt(user_id, text)

    await msg.edit_text(reply)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    file = await context.bot.get_file(
        update.message.voice.file_id
    )

    path = f"voice_{user_id}.ogg"

    await file.download_to_drive(path)

    text = await voice_to_text(path)

    if not text:

        await update.message.reply_text("❌ Ошибка распознавания")

        return

    reply = await ask_gpt(user_id, text)

    await update.message.reply_text(reply)


async def tts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    memory = load_memory(user_id)

    if not memory:

        await update.message.reply_text("Нет ответа")

        return

    last = memory[-1]["content"]

    path = f"reply_{user_id}.mp3"

    ok = await text_to_voice(last, path)

    if not ok:

        await update.message.reply_text("Ошибка озвучки")

        return

    await update.message.reply_voice(
        voice=open(path, "rb")
    )


# ========================
# MAIN
# ========================

def main():

    print("🚀 Bot starting...")

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(filters.Regex("^🔊 Озвучить$"), tts_handler)
    )

    app.add_handler(
        MessageHandler(filters.VOICE, voice_handler)
    )

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )

    app.run_polling()


if __name__ == "__main__":
    main()