import os
import sqlite3
import asyncio
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# ========================
# LOAD ENV
# ========================

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY not set")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN not set")

client = OpenAI(timeout=60.0)

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

    return [
        {
            "role": role,
            "content": content
        }
        for role, content in rows
    ]


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

    resize_keyboard=True

)

# ========================
# GPT STREAM RESPONSE
# ========================

async def ask_gpt_stream(user_id, text, message):

    try:

        save_memory(user_id, "user", text)

        memory = load_memory(user_id)

        stream = client.responses.create(
            model="gpt-4o",
            input=memory,
            stream=True,
        )

        full = ""
        last_edit = 0

        async for event in stream:

            if event.type == "response.output_text.delta":

                full += event.delta

                now = asyncio.get_event_loop().time()

                # обновляем сообщение раз в 0.5 сек
                if now - last_edit > 0.5:

                    try:
                        await message.edit_text(full)
                    except:
                        pass

                    last_edit = now

        save_memory(user_id, "assistant", full)

        return full

    except Exception as e:

        print("GPT ERROR:", e)

        return "Ошибка GPT"


# ========================
# VOICE → TEXT
# ========================

async def voice_to_text(path):

    try:

        with open(path, "rb") as audio:

            transcript = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=audio,
            )

        return transcript.text

    except Exception as e:

        print("STT ERROR:", e)

        return "Ошибка распознавания"


# ========================
# TEXT → VOICE
# ========================

async def text_to_voice(text, path):

    try:

        response = client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice="alloy",
            input=text,
        )

        with open(path, "wb") as f:
            f.write(response.content)

    except Exception as e:

        print("TTS ERROR:", e)


# ========================
# HANDLERS
# ========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "GPT-4o Pro Bot готов 🚀",
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
            "Напиши сообщение или отправь голос"
        )

        return

    msg = await update.message.reply_text("Думаю...")

    await ask_gpt_stream(user_id, text, msg)


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    file = await context.bot.get_file(
        update.message.voice.file_id
    )

    path = f"voice_{user_id}.ogg"

    await file.download_to_drive(path)

    text = await voice_to_text(path)

    msg = await update.message.reply_text("Думаю...")

    await ask_gpt_stream(user_id, text, msg)


async def tts_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)

    memory = load_memory(user_id)

    if not memory:

        await update.message.reply_text("Нет ответа")

        return

    last = memory[-1]["content"]

    path = f"voice_reply_{user_id}.mp3"

    await text_to_voice(last, path)

    with open(path, "rb") as audio:

        await update.message.reply_voice(audio)


# ========================
# APP
# ========================

app = Application.builder().token(TELEGRAM_TOKEN).build()

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

# ========================
# RUN
# ========================

print("GPT-4o Bot running...")

app.run_polling()