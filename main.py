import os
import asyncio
import re
from datetime import datetime, timedelta, timezone
from pyrogram import Client, filters
from pyrogram.types import InputPhoneContact
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# O'zbekiston vaqt zonasi (UTC+5)
UZB_TZ = timezone(timedelta(hours=5))

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

# Xotirada saqlash
pending_jobs = []   # Navbatdagi buyurtmalar
active_clients = {} # Lokatsiya kutayotgan faol mijozlar

# 1. Navbatdagi buyurtmalar ro'yxatini ko'rish (/list)
@app.on_message(filters.chat(GROUP_ID) & filters.command("list"))
async def list_jobs(client, message):
    if not pending_jobs and not active_clients:
        await message.reply("📭 Hozircha navbatda hech qanday faol buyurtma yo'q.")
        return
    
    text = "📋 **Hozirgi faol buyurtmalar ro'yxati:**\n\n"
    
    if pending_jobs:
        text += "⏳ **Yuborilishi kutilayotganlar:**\n"
        for idx, job in enumerate(pending_jobs, 1):
            text += f"{idx}. 📞 `{job['phone']}` — ⏰ {job['time'].strftime('%H:%M')} (UZB)\n"
            
    if active_clients:
        text += "\n📬 **Lokatsiya kutilayotganlar (Xabar yuborilgan):**\n"
        for user_id, info in active_clients.items():
            text += f"• 📞 `{info['phone']}` — ⏳ {info['sent_time'].strftime('%H:%M')} da yozildi\n"
            
    await message.reply(text)

# 2. Buyurtmani bekor qilish (/cancel)
@app.on_message(filters.chat(GROUP_ID) & filters.command("cancel"))
async def cancel_job(client, message):
    if not message.reply_to_message:
        await message.reply("❌ **Xatolik:** Bekor qilish uchun tizim qabul qilgan xabarga Reply (Javob) qilib yozing!")
        return
        
    reply_msg_id = message.reply_to_message.id
    canceled = False
    
    # Kutilayotgan navbatdan o'chirish
    for job in pending_jobs[:]:
        if job["msg_id"] == reply_msg_id:
            pending_jobs.remove(job)
            await message.reply(f"🚫 **Buyurtma bekor qilindi!**\n📞 Raqam: `{job['phone']}` navbatdan o'chirildi.")
            canceled = True
            break
            
    # Lokatsiya kutilayotganlar ro'yxatidan o'chirish
    if not canceled:
        for user_id, info in list(active_clients.items()):
            if info["msg_id"] == reply_msg_id:
                del active_clients[user_id]
                await message.reply(f"🚫 **Kutilayotgan lokatsiya bekor qilindi!**\n📞 Raqam: `{info['phone']}`.")
                canceled = True
                break
                
    if not canceled:
        await message.reply("❌ Ushbu xabarga bog'liq faol buyurtma topilmadi yoki u allaqachon bajarilgan.")

# 3. Guruhdan yangi buyurtmalarni qabul qilish
@app.on_message(filters.chat(GROUP_ID) & filters.text)
async def handle_group_message(client, message):
    text = message.text.strip()
    
    # Agar buyruq bo'lsa, uni chetlab o'tamiz
    if text.startswith("/"):
        return
        
    # Xabar ichidan vaqt formatini (masalan, 01:17 yoki 15:30) qidirish
    time_match = re.search(r'([0-1]?\d|2[0-3]):([0-5]\d)', text)
    
    if time_match:
        try:
            time_raw = time_match.group(0)
            
            # Xabarni qismlarga ajratamiz (Shaxsiy matn bormi tekshirish uchun)
            custom_text = None
            if "|" in text:
                parts = text.split("|")
                # parts[0] — raqam va vaqt, parts[1] — shaxsiy xabar matni
                custom_text = parts[1].strip()
                phone_and_time = parts[0]
            else:
                phone_and_time = text
                
            # Raqam qismini tozalash
            phone_raw = phone_and_time.replace(time_raw, "").strip()
            phone = "".join(c for c in phone_raw if c.isdigit() or c == "+")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            if len(phone) < 9:
                await message.reply("❌ Telefon raqami noto'g'ri kiritildi!")
                return
                
            now_uz = datetime.now(UZB_TZ)
            schedule_time = datetime.strptime(time_raw, "%H:%M").time()
            schedule_dt = datetime.combine(now_uz.date(), schedule_time).replace(tzinfo=UZB_TZ)
            
            if now_uz >= schedule_dt:
                if (now_uz - schedule_dt).total_seconds() > 1800:
                    schedule_dt += timedelta(days=1)
                    status_text = "Ertaga yuboriladi"
                else:
                    status_text = "Hozir yuboriladi"
            else:
                status_text = "Kutilmoqda"
            
            pending_jobs.append({
                "phone": phone,
                "time": schedule_dt,
                "msg_id": message.id,
                "custom_text": custom_text
            })
            
            reply_text = (
                f"✅ **Buyurtma qabul qilindi!**\n\n"
                f"📞 **Telefon:** `{phone}`\n"
                f"⏰ **Belgilangan vaqt (UZB):** `{schedule_dt.strftime('%d.%m.%Y %H:%M')}`\n"
                f"📊 **Status:** `{status_text}`"
            )
            if custom_text:
                reply_text += f"\n✉️ **Shaxsiy xabar:** `{custom_text}`"
                
            await message.reply(reply_text)
            
        except Exception as e:
            await message.reply(f"❌ Xatolik yuz berdi: {e}")

# 4. Private chatda lokatsiya kelganda uni guruhga yo'naltirish
@app.on_message(filters.private & filters.location)
async def handle_location(client, message):
    user_id = message.from_user.id
    if user_id in active_clients:
        info = active_clients[user_id]
        orig_msg_id = info["msg_id"]
        lat = message.location.latitude
        lon = message.location.longitude
        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
        
        info_text = f"📍 **Mijozdan lokatsiya olindi!**\n\n📞 **Telefon:** `{info['phone']}`\n🗺️ **Google Maps:** {maps_link}"
        await app.send_message(GROUP_ID, info_text, reply_to_message_id=orig_msg_id)
        await app.send_location(GROUP_ID, lat, lon, reply_to_message_id=orig_msg_id)
        
        await message.reply("Rahmat! Uyingiz lokatsiyasi qabul qilindi. 📍")
        del active_clients[user_id]

# Har 5 soniyada vaqtni tekshirib turuvchi va eslatma beruvchi asosiy loop
async def scheduler_loop():
    while True:
        now_uz = datetime.now(UZB_TZ)
        
        # 1. Navbatdagi xabarlarni yuborish
        for job in pending_jobs[:]:
            if now_uz >= job["time"]:
                phone = job["phone"]
                msg_id = job["msg_id"]
                custom_text = job["custom_text"]
                
                await app.send_message(GROUP_ID, f"⚡ **Vaqti keldi!** {phone} raqamiga yozish boshlanmoqda...", reply_to_message_id=msg_id)
                
                try:
                    contact = await app.import_contacts([
                        InputPhoneContact(phone=phone, first_name=f"Mijoz {phone}")
                    ])
                    
                    if contact.users:
                        user = contact.users[0]
                        user_id = user.id
                        
                        # Xabar matnini aniqlash (shaxsiy yoki standart)
                        if custom_text:
                            text = custom_text
                        else:
                            text = "Assalomu alaykum! Iltimos, uyingizning lokatsiyasini (geolokatsiya) yuboring."
                            
                        await app.send_message(user_id, text)
                        
                        # Lokatsiya kutish ro'yxatiga qo'shamiz (yuborilgan vaqti bilan)
                        active_clients[user_id] = {
                            "phone": phone,
                            "msg_id": msg_id,
                            "sent_time": now_uz,
                            "reminded": False
                        }
                        await app.send_message(GROUP_ID, f"✉️ **Xabar yuborildi.** Lokatsiya kutilmoqda...", reply_to_message_id=msg_id)
                    else:
                        await app.send_message(GROUP_ID, f"❌ **Xatolik:** {phone} raqamida Telegram topilmadi.", reply_to_message_id=msg_id)
                except Exception as e:
                    await app.send_message(GROUP_ID, f"❌ **Xatolik yuz berdi:** {e}", reply_to_message_id=msg_id)
                
                pending_jobs.remove(job)
                
        # 2. Avtomatik eslatma tizimi (Mijoz yozgandan keyin 10 daqiqa o'tsa eslatish)
        for user_id, info in list(active_clients.items()):
            # 10 daqiqa o'tganini tekshirish (test uchun buni 600 soniya qilib yozdik)
            if (now_uz - info["sent_time"]).total_seconds() > 600 and not info["reminded"]:
                try:
                    # Mijozga muloyim eslatma yuboramiz
                    reminder_text = "Iltimos, uyingizning lokatsiyasini yuborishingizni kutyapmiz, kuryerimiz yo'lga chiqishga tayyor. 😊📍"
                    await app.send_message(user_id, reminder_text)
                    
                    info["reminded"] = True # eslatildi deb belgilaymiz
                    await app.send_message(GROUP_ID, f"⏳ **Eslatma yuborildi:** Mijoz hali ham lokatsiya tashlamadi. Unga qayta eslatma xabari ketdi.", reply_to_message_id=info["msg_id"])
                except Exception as e:
                    print(f"Eslatma yuborishda xato: {e}")
                    
            # Agar xabar yuborilganiga 20 daqiqadan oshsa va javob bermasa, guruhni ogohlantiramiz
            elif (now_uz - info["sent_time"]).total_seconds() > 1200:
                await app.send_message(GROUP_ID, f"⚠️ **DIQQAT:** {info['phone']} raqamli mijoz yozganimizga 20 daqiqa bo'lsa ham javob bermadi!", reply_to_message_id=info["msg_id"])
                # Ro'yxatdan o'chirib yuboramiz (kutish to'xtaydi)
                del active_clients[user_id]
                
        await asyncio.sleep(5)

async def main():
    async with app:
        await scheduler_loop()

if __name__ == "__main__":
    app.run(main())
