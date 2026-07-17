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

# O'zbekcha yozilgan vaqtni aqlli aniqlash funksiyasi
def parse_departure_time(text):
    text = text.lower().strip()
    # 1) Standard HH:MM formatini tekshirish (masalan: 12:30 yoki 13.00)
    match = re.search(r'([0-1]?\d|2[0-3])[:.]([0-5]\d)', text)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        return h, m
    # 2) "soat 1 da", "2 larda", "13 da" kabi yakka soatlarni tekshirish
    match_hour = re.search(r'(?:soat\s*)?(\d{1,2})\s*(?:da|larda|atrofida|atroflarida|gacha)?', text)
    if match_hour:
        h = int(match_hour.group(1))
        # Agar soat 1 dan 6 gacha bo'lsa, uni tushdan keyingi vaqt (13:00 - 18:00) deb hisoblaymiz
        if 1 <= h <= 6:
            h += 12
        return h, 0
    return None

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
        text += "\n📬 **Muloqot jarayonidagilar:**\n"
        for user_id, info in active_clients.items():
            state_desc = "Lokatsiya kutilmoqda" if info["state"] == "waiting_location" else "Kuyov chiqish vaqti kutilmoqda"
            text += f"• 📞 `{info['phone']}` — 📊 `{state_desc}`\n"
            
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
                await message.reply(f"🚫 **Kutilayotgan muloqot bekor qilindi!**\n📞 Raqam: `{info['phone']}`.")
                canceled = True
                break
                
    if not canceled:
        await message.reply("❌ Ushbu xabarga bog'liq faol buyurtma topilmadi yoki u allaqachon bajarilgan.")

# 3. Guruhdan yangi buyurtmalarni qabul qilish
@app.on_message(filters.chat(GROUP_ID) & filters.text)
async def handle_group_message(client, message):
    text = message.text.strip()
    
    if text.startswith("/"):
        return
        
    # Xabar ichidan vaqt formatini (masalan, 01:17 yoki 15:30) qidirish
    time_match = re.search(r'([0-1]?\d|2[0-3]):([0-5]\d)', text)
    
    if time_match:
        try:
            time_raw = time_match.group(0)
            
            custom_text = None
            if "|" in text:
                parts = text.split("|")
                custom_text = parts[1].strip()
                phone_and_time = parts[0]
            else:
                phone_and_time = text
                
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
                reply_text += f"\n✉️ **Shaxsiy xabar yuboriladi:** `{custom_text}`"
                
            await message.reply(reply_text)
            
        except Exception as e:
            await message.reply(f"❌ Xatolik yuz berdi: {e}")

# 4. Private chatda lokatsiya kelganda uni guruhga yo'naltirish
@app.on_message(filters.private & filters.location)
async def handle_location(client, message):
    user_id = message.from_user.id
    if user_id in active_clients and active_clients[user_id]["state"] == "waiting_location":
        info = active_clients[user_id]
        orig_msg_id = info["msg_id"]
        lat = message.location.latitude
        lon = message.location.longitude
        maps_link = f"https://www.google.com/maps?q={lat},{lon}"
        
        info_text = f"📍 **Mijozdan lokatsiya olindi!**\n\n📞 **Telefon:** `{info['phone']}`\n🗺️ **Google Maps:** {maps_link}"
        await app.send_message(GROUP_ID, info_text, reply_to_message_id=orig_msg_id)
        await app.send_location(GROUP_ID, lat, lon, reply_to_message_id=orig_msg_id)
        
        # Mijozdan kuyovning uydan chiqish vaqtini so'raymiz
        question = (
            "Rahmat! Joylashuv manzili muvaffaqiyatli qabul qilindi. 📍\n\n"
            "Sizdan yana bir juda muhim ma'lumotni bilmoqchi edik:\n"
            "**Kuyov ertaga soat nechida uydan kelinnikiga yo'lga chiqadi (yuradi)?** ⏱️🤵"
        )
        await message.reply(question)
        
        # Holatni (State) kuyov vaqtini kutish rejimiga o'tkazamiz
        active_clients[user_id]["state"] = "waiting_time"
        active_clients[user_id]["sent_time"] = datetime.now(UZB_TZ) # taymerni yangilaymiz
        active_clients[user_id]["reminded"] = False

# 5. Private chatda mijoz vaqtni yozganda ishlov berish
@app.on_message(filters.private & filters.text)
async def handle_private_text(client, message):
    user_id = message.from_user.id
    if user_id in active_clients and active_clients[user_id]["state"] == "waiting_time":
        info = active_clients[user_id]
        text = message.text.strip()
        
        parsed_time = parse_departure_time(text)
        if parsed_time:
            h, m = parsed_time
            
            # Kuyov chiqish vaqti
            dep_dt = datetime.combine(datetime.today(), datetime.time(h, m))
            # Jamoa boradigan vaqt (2 soat oldin)
            arr_dt = dep_dt - timedelta(hours=2)
            
            dep_str = f"{h:02d}:{m:02d}"
            arr_str = arr_dt.strftime("%H:%M")
            
            # Mijozga javob qaytarish
            reply_msg = (
                f"Tushunarli, ma'lumot uchun rahmat! Unda tasvirga olish jamoamiz soat **{arr_str}** da yetib borishadi. 🎥\n\n"
                f"Chunki syomkaga, kuyovni ijodiy rasm va videoga olishga hamda oilaviy rasmlarga "
                f"1.5 - 2 soat vaqt to'liq yetadi. Ungacha jamoamiz barcha tayyorgarliklarni bemalol yakunlab olishadi. 😊✨"
            )
            await message.reply(reply_msg)
            
            # Guruhga to'liq hisobotni yuborish
            group_msg = (
                f"ℹ️ **Mijoz bilan muloqot yakunlandi!**\n\n"
                f"📞 **Telefon:** `{info['phone']}`\n"
                f"⏱️ **Kuyov chiqish vaqti:** `{dep_str}`\n"
                f"🎥 **Jamoa boradigan vaqt (2 soat oldin):** `{arr_str}`\n\n"
                f"🤖 *Ushbu buyurtma bo'yicha barcha avtomatlashtirish muvaffaqiyatli bajarildi!*"
            )
            await app.send_message(GROUP_ID, group_msg, reply_to_message_id=info["msg_id"])
            
            # Mijozni faol ro'yxatdan o'chiramiz (barcha muloqot tugadi)
            del active_clients[user_id]
        else:
            await message.reply(
                "Iltimos, vaqtni aniqroq formatda yozib yuboring.\n"
                "Masalan: `12:00`, `soat 13:30 da` yoki `soat 1 da` kabi. 😊"
            )

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
                        
                        if custom_text:
                            text = custom_text
                        else:
                            text = (
                                "Assalomu alaykum! 🌟\n\n"
                                "**\"To'yxonchi\" Jamoasining Trilliant Creative Studio (VIDEO)** xizmati tomonidan aloqaga chiqmoqdamiz!\n\n"
                                "Sizni va oilangizni bo'lajak nikoh to'yingiz munosabati bilan chin qalbimizdan muborakbod etamiz! Baxtingizga ko'z tegmasin, xonadoningizdan shodlik va quvonch arimasin! 🥂🎉\n\n"
                                "Ertangi to'y tantanasi rejalashtirilgan **joylashuv manzilini (geolokatsiyasini)** ushbu chatga yuborishingizni so'raymiz. Bu bizning jamoamiz o'z vaqtida yetib borishi va eng go'zal lahzalarni yuqori sifatda tasvirga olishi uchun juda muhimdir. 🎬📍"
                            )
                            
                        await app.send_message(user_id, text)
                        
                        active_clients[user_id] = {
                            "phone": phone,
                            "msg_id": msg_id,
                            "sent_time": now_uz,
                            "reminded": False,
                            "state": "waiting_location" # dastlabki holat - lokatsiya kutish
                        }
                        await app.send_message(GROUP_ID, f"✉️ **Tabrik va taklif xabari yuborildi.** Lokatsiya kutilmoqda...", reply_to_message_id=msg_id)
                    else:
                        await app.send_message(GROUP_ID, f"❌ **Xatolik:** {phone} raqamida Telegram topilmadi.", reply_to_message_id=msg_id)
                except Exception as e:
                    await app.send_message(GROUP_ID, f"❌ **Xatolik yuz berdi:** {e}", reply_to_message_id=msg_id)
                
                pending_jobs.remove(job)
                
        # 2. Avtomatik eslatma tizimi
        for user_id, info in list(active_clients.items()):
            # 10 daqiqa (600 soniya) o'tgach eslatish
            if (now_uz - info["sent_time"]).total_seconds() > 600 and not info["reminded"]:
                try:
                    if info["state"] == "waiting_location":
                        reminder_text = (
                            "Iltimos, ertangi tantana uchun joylashuv manzilini yuborishingizni kutyapmiz. "
                            "Tasvirga olish jamoamiz o'z vaqtida yetib borishi uchun bu juda muhimdir. 😊🎬📍"
                        )
                    else: # waiting_time
                        reminder_text = (
                            "Iltimos, kuyov uydan soat nechida chiqishini yozib yuborishingizni kutyapmiz. "
                            "Ushbu ma'lumotga qarab ijodiy jamoamiz yetib borish vaqtini to'g'ri rejalashtiradi. 😊⏱️🤵"
                        )
                        
                    await app.send_message(user_id, reminder_text)
                    
                    info["reminded"] = True
                    await app.send_message(GROUP_ID, f"⏳ **Eslatma yuborildi:** Mijoz hali javob bermadi. Unga qayta eslatma ketdi.", reply_to_message_id=info["msg_id"])
                except Exception as e:
                    print(f"Eslatma yuborishda xato: {e}")
                    
            # Agar muloqot boshlanganiga 20 daqiqa bo'lsa-yu javob bo'lmasa, guruhni ogohlantiramiz
            elif (now_uz - info["sent_time"]).total_seconds() > 1200:
                await app.send_message(GROUP_ID, f"⚠️ **DIQQAT:** {info['phone']} raqamli mijoz yozganimizga 20 daqiqa bo'lsa ham javob bermadi! Kutish to'xtatildi.", reply_to_message_id=info["msg_id"])
                del active_clients[user_id]
                
        await asyncio.sleep(5)

async def main():
    async with app:
        await scheduler_loop()

if __name__ == "__main__":
    app.run(main())
