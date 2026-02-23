import os
import sqlite3
import asyncio
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# ========================
# ENV
# ========================

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)

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
# FAST STREAM GPT
# ========================

async def ask_gpt_stream(update, user_id, text):

    save_memory(user_id, "user", text)

    memory = load_memory(user_id)

    msg = await update.message.reply_text("Думаю...")

    full_text = ""

    def generate():

        stream = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=memory,
            temperature=0.7,
            stream=True,
        )

        result = ""

        for chunk in stream:

            if chunk.choices[0].delta.content:
                result += chunk.choices[0].delta.content

        return result

    reply = await asyncio.to_thread(generate)

    save_memory(user_id, "assistant", reply)

    await msg.edit_text(reply)


# ========================
# VOICE → TEXT
# ========================

async def voice_to_text(path):

    def transcribe():
        with open(path, "rb") as audio:
            result = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio,
            )
            return result.text

    return await asyncio.to_thread(transcribe)


# ========================
# TEXT → VOICE
# ========================

async def text_to_voice(text, path):

    def generate():
        return client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )

    audio = await asyncio.to_thread(generate)

    with open(path, "wb") as f:
        f.write(audio.content)


# ========================
# HANDLERS
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "⚡ Быстрый GPT бот готов",
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
            "Отправь текст или голос"
        )

        return

    await ask_gpt_stream(update, user_id, text)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    file = await context.bot.get_file(
        update.message.voice.file_id
    )

    path = f"voice_{user_id}.ogg"

    await file.download_to_drive(path)

    text = await voice_to_text(path)

    await ask_gpt_stream(update, user_id, text)


async def tts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    memory = load_memory(user_id)

    if not memory:

        await update.message.reply_text("Нет ответа")

        return

    last = memory[-1]["content"]

    path = f"reply_{user_id}.mp3"

    await text_to_voice(last, path)

    with open(path, "rb") as audio:

        await update.message.reply_voice(audio)


# ========================
# APP
# ========================

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

print("⚡ Fast bot running...")

app.run_polling()