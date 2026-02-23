import os
import sqlite3
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not set")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set")

client = OpenAI()

# ===== DATABASE =====

conn = sqlite3.connect("memory.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS memory(
user_id TEXT,
role TEXT,
content TEXT
)
""")

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

# ===== KEYBOARD =====

keyboard = ReplyKeyboardMarkup(
    [
        ["🧠 Новая сессия"],
        ["🎤 Голос", "🔊 Озвучить"],
        ["⚙️ Помощь"],
    ],
    resize_keyboard=True,
)

# ===== GPT RESPONSE =====

async def ask_gpt(user_id, text):

    save_memory(user_id, "user", text)

    messages = load_memory(user_id)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.7,
    )

    reply = response.choices[0].message.content

    save_memory(user_id, "assistant", reply)

    return reply

# ===== VOICE → TEXT =====

async def voice_to_text(path):

    with open(path, "rb") as audio:
        transcript = client.audio.transcriptions.create(
            model="gpt-4o-mini-transcribe",
            file=audio,
        )

    return transcript.text

# ===== TEXT → VOICE =====

async def text_to_voice(text, path):

    response = client.audio.speech.create(
        model="gpt-4o-mini-tts",
        voice="alloy",
        input=text,
    )

    with open(path, "wb") as f:
        f.write(response.content)

# ===== HANDLERS =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "GPT Pro Bot готов 🚀",
        reply_markup=keyboard,
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    text = update.message.text

    if text == "🧠 Новая сессия":

        clear_memory(user_id)

        await update.message.reply_text("Память очищена")

        return

    if text == "⚙️ Помощь":

        await update.message.reply_text(
            "Просто напиши сообщение или отправь голос"
        )

        return

    msg = await update.message.reply_text("Думаю...")

    reply = await ask_gpt(user_id, text)

    await msg.edit_text(reply)

async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    file = await context.bot.get_file(
        update.message.voice.file_id
    )

    await file.download_to_drive("voice.ogg")

    text = await voice_to_text("voice.ogg")

    reply = await ask_gpt(user_id, text)

    await update.message.reply_text(reply)

async def tts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    memory = load_memory(user_id)

    if not memory:
        await update.message.reply_text("Нет ответа для озвучки")
        return

    last = memory[-1]["content"]

    await text_to_voice(last, "reply.mp3")

    await update.message.reply_voice(
        voice=open("reply.mp3", "rb")
    )

# ===== MAIN =====

app = Application.builder().token(
    TELEGRAM_TOKEN
).build()

app.add_handler(CommandHandler("start", start))

app.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
)

app.add_handler(
    MessageHandler(filters.VOICE, voice_handler)
)

app.add_handler(
    MessageHandler(filters.Regex("🔊 Озвучить"), tts_handler)
)

import asyncio

async def main():
    print("Bot running...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())