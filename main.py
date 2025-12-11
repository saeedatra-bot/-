import logging
import os
import sqlite3
from datetime import datetime
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz

# تنظیم لاگ
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# توکن‌ها (تو Render env variables بذار)
BOT_TOKEN = os.getenv('8012668899:AAHtErz9FRMbgiOCkSqga4yvA1i5mOPHXtY')  # از BotFather
WEATHER_API_KEY = os.getenv('a72ed6af225cb70fee9674e0e5665422')  # از OpenWeatherMap

# دیتابیس SQLite
DB_FILE = 'users.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, city TEXT, timezone TEXT, daily_notify BOOLEAN DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

# تابع گرفتن آب‌وهوا
def get_weather(city):
    try:
        # دما و وضعیت از OpenWeather
        url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={WEATHER_API_KEY}&units=metric&lang=fa"
        response = requests.get(url).json()
        if response['cod'] != 200:
            return None
        temp = response['main']['temp']
        feels_like = response['main']['feels_like']
        condition = response['weather'][0]['description']
        wind_speed = response['wind']['speed']
        
        # UV از API جدا
        uv_url = f"https://api.openweathermap.org/data/2.5/uvi?lat={response['coord']['lat']}&lon={response['coord']['lon']}&appid={WEATHER_API_KEY}"
        uv = requests.get(uv_url).json().get('value', 0)
        
        # AQI از aqicn
        aqi_url = f"https://api.waqi.info/feed/{city}/?token=fbba7328b0a9e9b887be1979e2b9764bda901d34"  # توکن رایگان از aqicn.org بگیر، یا برای MVP از OpenWeather AQI استفاده کن
        aqi_response = requests.get(aqi_url).json()
        aqi = aqi_response['data'].get('aqi', 'نامشخص')
        
        return {
            'temp': temp,
            'feels_like': feels_like,
            'condition': condition,
            'wind_speed': wind_speed,
            'uv': uv,
            'aqi': aqi
        }
    except:
        return None

# تابع پیشنهاد لباس بر اساس دما و شرایط (ساده اما واقعی)
def get_outfit_suggestion(weather):
    if not weather:
        return "متأسفانه اطلاعات هوا در دسترس نیست. دوباره امتحان کن!"
    
    temp = weather['temp']
    condition = weather['condition']
    wind = weather['wind_speed']
    uv = weather['uv']
    aqi = weather['aqi']
    
    suggestion = f"🌡️ دما: {temp}°C (حس واقعی {weather['feels_like']}°C)\n"
    suggestion += f"☁️ وضعیت: {condition}\n"
    suggestion += f"💨 باد: {wind} km/h\n"
    suggestion += f"☀️ UV: {uv} ("
    if uv < 3: suggestion += "پایین"
    elif uv < 6: suggestion += "متوسط"
    else: suggestion += "بالا"
    suggestion += ")\n"
    suggestion += f"🌫 AQI: {aqi} ("
    if aqi < 50: suggestion += "خوب"
    elif aqi < 100: suggestion += "متوسط"
    elif aqi < 150: suggestion += "ناسالم برای حساس‌ها"
    else: suggestion += "ناسالم"
    suggestion += ")\n\n"
    
    suggestion += "👔 پیشنهاد امروز:\n"
    if temp > 25:
        suggestion += "• تی‌شرت آستین کوتاه + شلوارک\n• عینک آفتابی و کلاه (UV بالاست!)"
    elif temp > 15:
        suggestion += "• تی‌شرت + شلوار جین + کتونی\n• کرم ضدآفتاب بزن"
    elif temp > 5:
        suggestion += "• هودی + شلوار پارچه‌ای + نیم‌بوت\n• شال‌گردن (باد می‌زنه!)"
    else:
        suggestion += "• کاپشن ضخیم + شلوار گرم + چکمه\n• دستکش و کلاه پشمی"
    
    if aqi > 100:
        suggestion += "\n• ماسک بزن، هوا کثیفِ!"
    
    suggestion += "\n\nاگه بیرون می‌ری، مراقب باش 😉"
    
    return suggestion

# هندلر استارت
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, daily_notify) VALUES (?, 0)", (user_id,))
    conn.commit()
    conn.close()
    
    keyboard = [[InlineKeyboardButton("📍 لوکیشن بفرست", request_location=True)],
                [InlineKeyboardButton("🏙 شهرت رو تایپ کن", callback_data="type_city")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("سلام! شهرت رو بگو یا لوکیشن بفرست تا بگم امروز چی بپوشی 👔", reply_markup=reply_markup)

# هندلر لوکیشن
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    loc = update.message.location
    city = await reverse_geocode(loc.latitude, loc.longitude)  # تابع ساده، بعداً اضافه کن
    if not city:
        city = "تهران"  # دیفالت
    save_city(user_id, city)
    weather = get_weather(city)
    suggestion = get_outfit_suggestion(weather)
    await update.message.reply_text(suggestion)
    
    # دکمه روزانه
    keyboard = [[InlineKeyboardButton("🔔 هر روز صبح برام بفرست", callback_data="enable_daily")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("دکمه زیر رو بزن تا هر روز ۷:۳۰ پیشنهاد بگیری:", reply_markup=reply_markup)

# هندلر متن (شهر)
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    city = update.message.text.strip()
    save_city(user_id, city)
    weather = get_weather(city)
    suggestion = get_outfit_suggestion(weather)
    await update.message.reply_text(suggestion)
    
    keyboard = [[InlineKeyboardButton("🔔 هر روز صبح برام بفرست", callback_data="enable_daily")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("دکمه زیر رو بزن:", reply_markup=reply_markup)

# ذخیره شهر
def save_city(user_id, city):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET city = ? WHERE user_id = ?", (city, user_id))
    conn.commit()
    conn.close()

# هندلر کال‌بک (دکمه‌ها)
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "enable_daily":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE users SET daily_notify = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ فعال شد! هر روز ساعت ۷:۳۰ تهران پیشنهاد می‌فرستم.")
    elif query.data == "type_city":
        await query.edit_message_text("اسم شهرت رو بنویس (مثل: تهران، اصفهان)")

# تابع ارسال روزانه
async def daily_notify():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, city FROM users WHERE daily_notify = 1")
    users = c.fetchall()
    conn.close()
    
    tehran_tz = pytz.timezone('Asia/Tehran')
    now = datetime.now(tehran_tz)
    if now.hour != 7 or now.minute != 30:
        return  # فقط ۷:۳۰
    
    for user_id, city in users:
        weather = get_weather(city)
        suggestion = get_outfit_suggestion(weather)
        try:
            await application.bot.send_message(chat_id=user_id, text=f"🌅 صبح بخیر! {suggestion}")
        except:
            pass  # اگر کاربر بلاک کرده، رد شو

# تابع reverse geocode ساده (با OpenStreetMap، رایگان)
async def reverse_geocode(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json"
        response = requests.get(url, headers={'User-Agent': 'ChiBepooshamBot'}).json()
        return response['address']['city'] or response['address']['town']
    except:
        return None

# ران کردن
async def main():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.LOCATION, location_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # scheduler برای روزانه
    scheduler = AsyncIOScheduler(timezone=pytz.timezone('Asia/Tehran'))
    scheduler.add_job(daily_notify, 'cron', hour=7, minute=30)
    scheduler.start()
    
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
