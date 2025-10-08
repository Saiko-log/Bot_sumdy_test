import asyncio
import sqlite3
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # ИСПРАВЛЕНО: Добавлен импорт для часовых зон

from aiogram import F, Bot, Dispatcher, types # ИСПРАВЛЕНО: Добавлен импорт F
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ics import Calendar

# Импортируем все необходимое для веб-сервера и CORS
from aiohttp import web
import aiohttp_cors

# --- Конфигурация ---
BOT_TOKEN = "7962722551:AAG3zZ0TAm3FdL3C416-ntl3N5XmCSs_G4s" 
WEB_APP_URL = "https://saiko-log.github.io/Bot_sumdy_test/" 

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone="Europe/Kiev")


def init_db():
    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY,
        uid TEXT UNIQUE,
        user_id INTEGER,
        summary TEXT,
        dtstart DATETIME,
        dtend DATETIME,
        description TEXT,
        location TEXT,
        link TEXT,
        notes TEXT
    )
    ''')
    conn.commit()
    conn.close()

# --- Логика напоминаний ---
async def send_class_reminder(user_id: int, summary: str, dtstart_str: str):
    start_time = datetime.fromisoformat(dtstart_str).strftime("%H:%M")
    await bot.send_message(
        chat_id=user_id,
        text=f"🔔 Напоминание! Скоро начнется пара:\n\n*_{summary}_*\n\nНачало в *{start_time}*",
        parse_mode="Markdown"
    )

# ИСПРАВЛЕНО: Функция теперь корректно работает со временем
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

# --- Обработчики веб-запросов ---
async def get_schedule(request):
    conn = sqlite3.connect('schedule.db')
    conn.row_factory = sqlite3.Row 
    cur = conn.cursor()
    cur.execute("SELECT * FROM classes ORDER BY dtstart ASC")
    schedule_data = [dict(row) for row in cur.fetchall()]
    conn.close()
    return web.Response(text=json.dumps(schedule_data, default=str), content_type='application/json')

async def save_notes(request):
    data = await request.json()
    uid = data.get('uid')
    link = data.get('link')
    notes = data.get('notes')

    if not uid:
        return web.Response(status=400, text="UID is required")

    conn = sqlite3.connect('schedule.db')
    cur = conn.cursor()
    cur.execute("UPDATE classes SET link = ?, notes = ? WHERE uid = ?", (link, notes, uid))
    conn.commit()
    conn.close()

    return web.Response(text=json.dumps({"status": "ok"}), content_type='application/json')

# --- Обработчики команд и сообщений Telegram ---
@dp.message(CommandStart())
async def send_welcome(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🗓️ Открыть расписание",
        web_app=WebAppInfo(url=WEB_APP_URL)
    )
    await message.answer(
        "Привет! Я твой помощник по расписанию.\n\n"
        "Отправь мне файл `schedule.ics`, чтобы я его загрузил.\n"
        "А кнопка ниже откроет твое расписание.",
        reply_markup=builder.as_markup()
    )

# ИСПРАВЛЕНО: Фильтр сообщений теперь работает в aiogram 3.x
@dp.message(F.document)
async def handle_docs(message: types.Message):
    if not message.document.file_name.endswith('.ics'):
        await message.reply("Пожалуйста, отправьте файл в формате .ics")
        return

    await message.reply("Получил файл! Начинаю обработку...")
    
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
        ''', (
            event.uid,
            message.from_user.id,
            event.name,
            event.begin.datetime,
            event.end.datetime,
            event.description,
            event.location
        ))

    conn.commit()
    conn.close()
    
    await message.reply("Расписание успешно загружено и сохранено! Теперь я буду присылать напоминания.")
    
    schedule_reminders(message.from_user.id)

# --- Основная функция запуска ---
async def main():
    init_db()
    scheduler.start()
    
    # --- Настройка веб-сервера с CORS ---
    app = web.Application()
    app.add_routes([
        web.get('/getSchedule', get_schedule),
        web.post('/saveNotes', save_notes),
    ])

    # ИСПРАВЛЕНО: Добавлена настройка CORS, чтобы убрать ошибку с белым экраном
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })

    for route in list(app.router.routes()):
        cors.add(route)
    
    # Запуск веб-сервера
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