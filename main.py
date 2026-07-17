import os
import asyncio
import re
import urllib.request
import json
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

# in_memory=True qo'shildi - Render'da SQLite qulflanishini oldini oladi
app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True)

# Xotirada saqlash
pending_jobs = []   # Navbatdagi buyurtmalar
active_clients = {} # Lokatsiya kutayotgan faol mijozlar

# Soat kichik bo'lsa uni kunduzgi vaqtga o'tkazish (masalan: 1 yarim -> 13:30)
def adjust_hour(h):
    if 1 <= h <= 7:
        return h + 12
    return h

# O'zbekcha yozilgan vaqtni (har qanday formatda) juda aqlli aniqlash funksiyasi
def parse_departure_time(text):
    text = text.lower().strip()
    text = text.replace("’", "'").replace("`", "'").replace("‘", "'")

    is_half = any(word in text for word in ["yarim", "ярим", "ярум", "yarm", "yarym"])

    match_std = re.search(r'\b([0-1]?\d|2[0-3])[:.]([0-5]\d)\b', text)
    if match_std:
        h, m = int(match_std.group(1)), int(match_std.group(2))
        return adjust_hour(h), m

    word_to_num = {
        "bir": 1, "birda": 1, "бир": 1,
        "ikki": 2, "ikkida": 2, "икки": 2,
        "uch": 3, "uchda": 3, "уч": 3,
        "to'rt": 4, "tort": 4, "to'rtda": 4, "тўрт": 4, "торт": 4,
        "besh": 5, "beshda": 5, "беш": 5,
        "olti": 6, "oltida": 6, "олti": 6,
        "yetti": 7, "ettida": 7, "етти": 7,
        "sakkiz": 8, "sakkizda": 8, "саккиз": 8,
        "to'qqiz": 9, "toqqiz": 9, "тўққиз": 9,
        "o'n": 10, "on": 10, "ўн": 10,
        "o'n bir": 11, "on bir": 11, "ўн бир": 11,
        "o'n ikki": 12, "on ikki": 12, "ўн икки": 12
    }

    words = re.findall(r"[a-zA-Z'ўўқхшчғўнъа-яА-Я]+", text)
    h = None
    for word in words:
        if word in word_to_num:
            h = word_to_num[word]
            break

    if h is None:
        digit_match = re.search(r'\b(\d{1,2})\b', text)
        if digit_match:
            h = int(digit_match.group(1))

    if h is not None and 0 <= h <= 23:
        h = adjust_hour(h)
        m = 30 if is_half else 0
        return h, m

    return None

# Hugging Face Whisper API orqali ovozli xabarni matnga o'girish
def transcribe_voice_hf(voice_file, hf_token):
    API_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3"
    try:
        with open(voice_file, "rb") as f:
            data = f.read()
        
        req = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "Authorization": f"Bearer {hf_token}",
                "Content-Type": "audio/ogg"
            },
            method="POST"
        )
        
        with urllib.request.urlopen(req, timeout=15) as response:
            res_data = response.read().decode("utf-8")
            return json.loads(res_data).get("text", "").strip()
    except Exception as e:
        print(f"Transcription error: {e}")
        return None

# 1. Navbatdagi buyurtmalar ro'yxatini ko'rish (/list)
@app.on_message(filters.chat(GROUP_ID) & filters.command("list"))
async def list_jobs(client, message):
    try:
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
    except Exception as e:
        await message.reply(f"❌ Xatolik yuz berdi: {e}")

# 2. Buyurtmani bekor qilish (/cancel)
@app.on_message(filters.chat(GROUP_ID) & filters.command("cancel"))
async def cancel_job(client, message):
    try:
        if not message.reply_to_message:
            await message.reply("❌ **Xatolik:** Bekor qilish uchun tizim qabul qilgan xabarga Reply (Javob) qilib yozing!")
            return
            
        reply_msg_id = message.reply_to_message.id
        canceled = False
        
        for job in pending_jobs[:]:
            if job["msg_id"] == reply_msg_id:
                pending_jobs.remove(job)
                await message.reply(f"🚫 **Buyurtma bekor qilindi!**\n📞 Raqam: `{job['phone']}` navbatdan o'chirildi.")
                canceled = True
                break
                
        if not canceled:
            for user_id, info in list(active_clients.items()):
                if info["msg_id"] == reply_msg_id:
                    del active_clients[user_id]
                    await message.reply(f"🚫 **Kutilgan muloqot bekor qilindi!**\n📞 Raqam: `{info['phone']}`.")
                    canceled = True
                    break
                    
        if not canceled:
            await message.reply("❌ Ushbu xabarga bog'liq faol buyurtma topilmadi yoki u allaqachon bajarilgan.")
    except Exception as e:
        await message.reply(f"❌ Xatolik: {e}")

# 3. Guruhdan yangi buyurtmalarni qabul qilish
@app.on_message(filters.chat(GROUP_ID) & filters.text)
async def handle_group_message(client, message):
    text = message.text.strip()
    
    if text.startswith("/"):
        return
        
    # Guruhda raqam kiritilganda biron xato bo'lsa, uni guruhga yozish uchun try-except
    try:
        phone_match = re.search(r'(\+?\d{9,12})', text)
        
        if phone_match:
            phone_raw = phone_match.group(1)
            time_raw = text.replace(phone_raw, "").strip()
            
            custom_text = None
            if "|" in time_raw:
                parts = time_raw.split("|")
                time_raw = parts[0].strip()
                custom_text = parts[1].strip()
                
            phone = "".join(c for c in phone_raw if c.isdigit() or c == "+")
            if not phone.startswith("+"):
                phone = "+" + phone
            
            parsed_time = parse_departure_time(time_raw)
            
            if not parsed_time:
                await message.reply(
                    f"❌ **Vaqtni aniqlab bo'lmadi!**\n"
                    f"Iltimos, vaqtni to'g'ri formatda yozing.\n"
                    f"Misol: `{phone} 01:17` yoki `{phone} 1 yarimda` 😊"
                )
                return
                
            h, m = parsed_time
            now_uz = datetime.now(UZB_TZ)
            schedule_dt = datetime.combine(now_uz.date(), datetime.time(h, m)).replace(tzinfo=UZB_TZ)
            
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
        await message.reply(f"❌ **Guruh xabarida ichki xatolik:** {e}")

# 4. Private chatda lokatsiya kelganda uni guruhga yo'naltirish
@app.on_message(filters.private & filters.location)
async def handle_location(client, message):
    try:
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
            
            question = (
                "Rahmat! Joylashuv manzili muvaffaqiyatli qabul qilindi. 📍\n\n"
                "Sizdan yana bir juda muhim ma'lumotni bilmoqchi edik:\n"
                "**Kuyov ertaga soat nechida uydan kelinnikiga yo'lga chiqadi (yuradi)?** ⏱️🤵"
            )
            await message.reply(question)
            
            active_clients[user_id]["state"] = "waiting_time"
            active_clients[user_id]["sent_time"] = datetime.now(UZB_TZ)
            active_clients[user_id]["reminded"] = False
    except Exception as e:
        print(f"Lokatsiya xatosi: {e}")

# 5. Private chatda mijoz vaqtni yozganda yoki ovozli xabar yuborganda ishlov berish
@app.on_message(filters.private & (filters.text | filters.voice))
async def handle_private_response(client, message):
    user_id = message.from_user.id
    if user_id in active_clients and active_clients[user_id]["state"] == "waiting_time":
        info = active_clients[user_id]
        
        text = ""
        if message.voice:
            await app.send_message(GROUP_ID, f"🎙️ **Mijozdan ovozli xabar keldi.** Matnga aylantirilmoqda...", reply_to_message_id=info["msg_id"])
            
            hf_token = os.environ.get("HF_TOKEN")
            if not hf_token:
                await message.reply("⚠️ Kechirasiz, ovozli xabarni qayta ishlash uchun serverda `HF_TOKEN` sozlanmagan. Iltimos, vaqtni yozma ravishda yuboring. ✍️")
                return
                
            try:
                voice_file = await message.download()
                text = transcribe_voice_hf(voice_file, hf_token)
                if os.path.exists(voice_file):
                    os.remove(voice_file)
                
                if not text:
                    await message.reply("Kechirasiz, ovozingizni tushunib bo'lmadi. Iltimos, qaytadan aniqroq gapiring yoki yozma ravishda yuboring. 😊")
                    return
                
                await app.send_message(GROUP_ID, f"📝 **Ovozli xabar matni:**\n`\"{text}\"`", reply_to_message_id=info["msg_id"])
            except Exception as e:
                await message.reply("Kechirasiz, ovozli xabarni qayta ishlashda xatolik yuz berdi. Iltimos, vaqtni yozma ravishda yuboring. ✍️")
                return
        else:
            text = message.text.strip()
        
        parsed_time = parse_departure_time(text)
        if parsed_time:
            h, m = parsed_time
            dep_dt = datetime.combine(datetime.today(), datetime.time(h, m))
            arr_dt = dep_dt - timedelta(hours=2)
            
            dep_str = f"{h:02d}:{m:02d}"
            arr_str = arr_dt.strftime("%H:%M")
            
            reply_msg = (
                f"Tushunarli, ma'lumot uchun rahmat! Unda tasvirga olish jamoamiz soat **{arr_str}** da yetib borishadi. 🎥\n\n"
                f"Chunki ertangi ijodiy syomkaga, kuyovni rasm va videoga tasvirga olishga hamda oilaviy rasm-videolarga "
                f"1.5 - 2 soat vaqt to'liq yetarli bo'ladi. Ungacha jamoamiz barcha tayyorgarliklarni bemalol yakunlab olishadi. 😊✨"
            )
            await message.reply(reply_msg)
            
            group_msg = (
                f"ℹ️ **Mijoz bilan muloqot yakunlandi!**\n\n"
                f"📞 **Telefon:** `{info['phone']}`\n"
                f"⏱️ **Kuyov chiqish vaqti:** `{dep_str}` (Mijoz xabari: *\"{text}\"*)\n"
                f"🎥 **Jamoa boradigan vaqt (2 soat oldin):** `{arr_str}`\n\n"
                f"🤖 *Ushbu buyurtma bo'yicha barcha avtomatlashtirish muvaffaqiyatli bajarildi!*"
            )
            await app.send_message(GROUP_ID, group_msg, reply_to_message_id=info["msg_id"])
            del active_clients[user_id]
        else:
            await message.reply(
                "Iltimos, vaqtni aniqroq formatda yozib yuboring yoki ovozli xabarda aniqroq ayting.\n"
                "Masalan: `12:00`, `soat 13:30 da`, `1 yarimda` yoki `soat 1 da` kabi. 😊"
            )

# Har 5 soniyada vaqtni tekshirib turuvchi va eslatma beruvchi loop
async def scheduler_loop():
    while True:
        now_uz = datetime.now(UZB_TZ)
        
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
                            "state": "waiting_location"
                        }
                        await app.send_message(GROUP_ID, f"✉️ **Tabrik va taklif xabari yuborildi.** Lokatsiya kutilmoqda...", reply_to_message_id=msg_id)
                    else:
                        await app.send_message(GROUP_ID, f"❌ **Xatolik:** {phone} raqamida Telegram topilmadi.", reply_to_message_id=msg_id)
                except Exception as e:
                    await app.send_message(GROUP_ID, f"❌ **Xatolik yuz berdi:** {e}", reply_to_message_id=msg_id)
                
                pending_jobs.remove(job)
                
        for user_id, info in list(active_clients.items()):
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
                    
            elif (now_uz - info["sent_time"]).total_seconds() > 1200:
                await app.send_message(GROUP_ID, f"⚠️ **DIQQAT:** {info['phone']} raqamli mijoz yozganimizga 20 daqiqa bo'lsa ham javob bermadi! Kutish to'xtatildi.", reply_to_message_id=info["msg_id"])
                del active_clients[user_id]
                
        await asyncio.sleep(5)

async def main():
    async with app:
        await scheduler_loop()

if __name__ == "__main__":
    app.run(main())
