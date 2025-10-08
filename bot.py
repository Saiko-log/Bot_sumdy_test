import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ics import Calendar

from aiohttp import web
import aiohttp_cors

# --- 1. CONFIGURATION ---
BOT_TOKEN = "7962722551:AAG3zZ0TAm3FdL3C416-ntl3N5XmCSs_G4s"
WEB_APP_URL = "https://saiko-log.github.io/Bot_sumdy_test/"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Kiev")

# --- 2. DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY, uid TEXT UNIQUE, user_id INTEGER, summary TEXT,
        dtstart DATETIME, dtend DATETIME, description TEXT, location TEXT,
        link TEXT, notes TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS subject_links (
        summary TEXT PRIMARY KEY,
        global_link TEXT
    )
    ''')
    conn.commit()
    conn.close()

# --- 3. REMINDER LOGIC ---
async def send_class_reminder(user_id: int, summary: str, dtstart_str: str):
    start_time = datetime.fromisoformat(dtstart_str).strftime("%H:%M")
    await bot.send_message(
        chat_id=user_id,
        text=f"🔔 Напоминание! Скоро начнется пара:\n\n*_{summary}_*\n\nНачало в *{start_time}*",
        parse_mode="Markdown"
    )

def schedule_reminders(user_id: int):
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    now_aware = datetime.now(ZoneInfo("Europe/Kiev"))
    cur.execute("SELECT summary, dtstart FROM classes WHERE user_id = ? AND dtstart > ?", (user_id, now_aware))
    classes = cur.fetchall()
    conn.close()
    for cls in classes:
        summary, dtstart_str = cls
        dtstart = datetime.fromisoformat(dtstart_str)
        trigger_time = dtstart - timedelta(minutes=15)
        if trigger_time > now_aware:
            scheduler.add_job(send_class_reminder, 'date', run_date=trigger_time, args=[user_id, summary, dtstart_str])
    print(f"Запланировано {len(classes)} напоминаний.")

# --- 4. WEB SERVER HANDLERS ---
async def get_schedule(request):
    conn = sqlite3.connect('schedule.db')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('''
        SELECT c.*, sl.global_link
        FROM classes c
        LEFT JOIN subject_links sl ON c.summary = sl.summary
        ORDER BY c.dtstart ASC
    ''')
    schedule_data = [dict(row) for row in cur.fetchall()]
    conn.close()
    return web.Response(text=json.dumps(schedule_data, default=str), content_type='application/json')

async def save_notes(request):
    data = await request.json()
    uid, link, notes = data.get('uid'), data.get('link'), data.get('notes')
    if not uid: return web.Response(status=400, text="UID is required")
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    cur.execute("UPDATE classes SET link = ?, notes = ? WHERE uid = ?", (link, notes, uid))
    conn.commit()
    conn.close()
    return web.Response(text=json.dumps({"status": "ok"}), content_type='application/json')

async def save_global_link(request):
    data = await request.json()
    summary, link = data.get('summary'), data.get('link')
    if not summary: return web.Response(status=400, text="Summary is required")
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO subject_links (summary, global_link) VALUES (?, ?)", (summary, link))
    conn.commit()
    conn.close()
    return web.Response(text=json.dumps({"status": "ok"}), content_type='application/json')

# --- 5. TELEGRAM MESSAGE HANDLERS ---
@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(text="🗓️ Открыть расписание", web_app=WebAppInfo(url=WEB_APP_URL))
    await message.answer("Привет! Я твой помощник по расписанию...", reply_markup=builder.as_markup())

@dp.message(F.document)
async def handle_docs(message: types.Message):
    if not message.document.file_name.endswith('.ics'):
        return await message.reply("Пожалуйста, отправьте файл в формате .ics")
    await message.reply("Получил файл! Обрабатываю...")
    file_info = await bot.get_file(message.document.file_id)
    downloaded_file = await bot.download_file(file_info.file_path)
    ics_content = downloaded_file.read().decode('utf-8')
    cal = Calendar(ics_content)
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    for event in cal.events:
        cur.execute('''
        INSERT OR IGNORE INTO classes (uid, user_id, summary, dtstart, dtend, description, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (event.uid, message.from_user.id, event.name, event.begin.datetime,
              event.end.datetime, event.description, event.location))
    conn.commit()
    conn.close()
    await message.reply("Расписание успешно загружено!")
    schedule_reminders(message.from_user.id)

# --- 6. MAIN STARTUP FUNCTION ---
async def main():
    init_db()
    scheduler.start()
    app = web.Application()
    app.add_routes([
        web.get('/getSchedule', get_schedule),
        web.post('/saveNotes', save_notes),
        web.post('/saveGlobalLink', save_global_link),
    ])
    
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
                allow_methods=["GET", "POST"], # Explicitly allow GET and POST
            )
    })

    for route in list(app.router.routes()):
        cors.add(route)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    print("Веб-сервер запущен на http://0.0.0.0:8080")
    await site.start()

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен.")
