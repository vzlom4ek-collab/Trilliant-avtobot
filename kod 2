import os
import asyncio
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InputPhoneContact
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==================== DUMMY WEB SERVER (RENDER UCHUN) ====================
class SimpleWebServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot 24/7 faol ishlamoqda!")
    def log_message(self, format, *args):
        return

def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleWebServer)
    server.serve_forever()

threading.Thread(target=start_web_server, daemon=True).start()
# =========================================================================

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")
GROUP_ID = int(os.environ.get("GROUP_ID"))  # Boshqaruv guruhi ID-si

app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# Xotirada vaqtinchalik saqlash
pending_jobs = []
active_clients = {}

# Guruhdan yangi buyurtmalarni qabul qilish
@app.on_message(filters.chat(GROUP_ID) & filters.text)
async def handle_group_message(client, message):
    text = message.text.strip()
    if "|" in text:
        try:
            parts = text.split("|")
            phone_raw = parts 0 .strip()
            time_raw = parts 1 .strip()
            
            # Telefon raqamini tozalash
            phone = "".join(c for c in phone_raw if c.isdigit() or c == "+")
            if not phone.startswith("+"):
                phone = "+" + phone
                
            # Vaqtni o'qish
            now = datetime.now()
            try:
                schedule_time = datetime.strptime(time_raw, "%H:%M").time()
                schedule_dt = datetime.combine(now.date(), schedule_time)
            except ValueError:
                try:
                    schedule_dt = datetime.strptime(time_raw, "%d.%m.%Y %H:%M")
                except ValueError:
                    await message.reply("❌ Vaqt formati xato! Misol: `+998901234567 | 19:30` yoki `+998901234567 | 17.07.2026 19:30`")
                    return
            
            pending_jobs.append({
                "phone": phone,
                "time": schedule_dt,
                "msg_id": message.id
            })
            
            await message.reply(f"✅ Qabul qilindi!\n📞 Telefon: {phone}\n⏰ Vaqt: {schedule_dt.strftime('%d.%m.%Y %H:%M')}\nStatus: Kutilmoqda...")
            print(f"Yangi buyurtma: {phone} | {schedule_dt}")
            
        except Exception as e:
            await message.reply(f"❌ Xatolik yuz berdi: {e}")

# Mijoz lokatsiya yuborganda uni guruhga yo'naltirish
@app.on_message(filters.private & filters.location)
async def handle_location(client, message):
    user_id = message.from_user.id
    if user_id in active_clients:
        orig_msg_id = active_clients[user_id]
        lat = message.location.latitude
        lon = message.location.longitude
        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
        
        # Guruhga javob yozish
        info_text = f"📍 Mijozdan lokatsiya olindi!\nGoogle Maps: {maps_link}"
        await app.send_message(GROUP_ID, info_text, reply_to_message_id=orig_msg_id)
        # Lokatsiyani o'zini ham guruhga yuborish
        await app.send_location(GROUP_ID, lat, lon, reply_to_message_id=orig_msg_id)
        
        await message.reply("Rahmat! Lokatsiyangiz qabul qilindi. 📍")
        del active_clients[user_id]

# Har 10 soniyada vaqtni tekshirib turuvchi reja
async def scheduler_loop():
    while True:
        now = datetime.now()
        for job in pending_jobs[:]:
            if now >= job["time"]:
                phone = job["phone"]
                msg_id = job["msg_id"]
                
                await app.send_message(GROUP_ID, f"⚡ Vaqti keldi! {phone} raqamiga yozish boshlanmoqda...", reply_to_message_id=msg_id)
                
                try:
                    contact = await app.import_contacts([
                        InputPhoneContact(phone=phone, first_name=f"Mijoz {phone}")
                    ])
                    
                    if contact.users:
                        user = contact.users 0 
                        user_id = user.id
                        
                        text = "Assalomu alaykum! Iltimos, uyingizning lokatsiyasini (geolokatsiya) yuboring."
                        await app.send_message(user_id, text)
                        
                        active_clients[user_id] = msg_id
                        await app.send_message(GROUP_ID, f"✉️ Xabar yuborildi. Lokatsiya kutilmoqda...", reply_to_message_id=msg_id)
                    else:
                        await app.send_message(GROUP_ID, f"❌ Xatolik: {phone} raqamida Telegram topilmadi.", reply_to_message_id=msg_id)
                except Exception as e:
                    await app.send_message(GROUP_ID, f"❌ Xatolik yuz berdi: {e}", reply_to_message_id=msg_id)
                
                pending_jobs.remove(job)
                
        await asyncio.sleep(10)

async def main():
    async with app:
        await scheduler_loop()

if __name__ == "__main__":
    app.run(main())
