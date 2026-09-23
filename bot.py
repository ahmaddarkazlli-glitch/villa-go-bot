import uuid
import re
import os
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram import InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, ConversationHandler, filters, ContextTypes
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials

import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# سيرفر وهمي مجاني لإرضاء فحص المنافذ في Render
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        return  # لمنع إغراق السجلات بركود الفحص

def run_dummy_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

# تشغيل السيرفر الوهمي في خيط منفصل (Thread)
threading.Thread(target=run_dummy_server, daemon=True).start()


# 1. الاتصال بـ Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
# الكود الجديد للاتصال بـ Google Sheets:
google_creds_json = os.getenv("GOOGLE_CREDENTIALS")

if google_creds_json:
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
else:
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

client = gspread.authorize(creds)
SPREADSHEET_KEY = os.getenv("SPREADSHEET_KEY")

spreadsheet = client.open_by_key(SPREADSHEET_KEY)
farms_sheet = spreadsheet.worksheet("farms")
users_sheet = spreadsheet.worksheet("users")
booking_sheet = spreadsheet.worksheet("booking")

SHAM_CASH_ACCOUNT = os.getenv("SHAM_CASH_ACCOUNT")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID") # ⚠️ Chat ID الخاص بالأدمن

if not all([SPREADSHEET_KEY, SHAM_CASH_ACCOUNT, ADMIN_CHAT_ID]):
    raise ValueError("⚠️ خطأ أمني: يرجى ضبط جميع متغيرات البيئة (SPREADSHEET_KEY, SHAM_CASH_ACCOUNT, ADMIN_CHAT_ID) في ملف .env")

# حالات المحادثة
START_DATE, END_DATE, GUESTS, CHECKIN_TIME, USER_PHONE, CONFIRM, TRANSACTION_ID = range(7)

def get_booked_ranges(farm_id):
    all_bookings = booking_sheet.get_all_records()
    today = datetime.now().date()
    ranges = []

    for b in all_bookings:
        if str(b.get('farm_id')) == str(farm_id) and str(b.get('status')).strip().lower() in ['confirmed', 'pending_owner', 'pending_admin']:
            try:
                b_start = datetime.strptime(str(b.get('start_date')).strip(), "%Y-%m-%d").date()
                b_end = datetime.strptime(str(b.get('end_date')).strip(), "%Y-%m-%d").date()
                if b_end >= today:
                    ranges.append((b_start, b_end))
            except ValueError:
                continue

    ranges.sort(key=lambda x: x[0])
    return ranges

def is_farm_available(farm_id, req_start_dt, req_end_dt):
    booked_ranges = get_booked_ranges(farm_id)
    for b_start, b_end in booked_ranges:
        if req_start_dt < b_end and req_end_dt > b_start:
            return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.full_name

    # استبدل هذا الرابط برابط السياسات والشروط الخاص بك
    TERMS_URL = "https://telegra.ph/Villa-Go---Privacy-Policy-09-15?fbclid=IwY2xjawUWeQJwZG9mAWV4dG4DYWVtAjEwAHNydGMGYXBwX2lkEDIyMjAzOTE3ODgyMDA4OTIAAR5zuApCCLRsbti8No3MeR--R94GdZoU_P1yC-OL5p6ib6NIPJmblE8mSfFExQ_aem_MJsAnnu2HayTihukZ-GkqQ" 

    keyboard = [
        [InlineKeyboardButton("تصفح المزارع حسب المنطقة 📍", callback_data='list_cities')],
        [InlineKeyboardButton("حجوزاتي 📅", callback_data='my_bookings')],
        [InlineKeyboardButton("السياسات والشروط 📜", url=TERMS_URL)]  # <--- استخدام url يفتح الرابط مباشرة
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"أهلاً بك يا {name} في منصة Villa Go اختر من القائمة للبدء:", reply_markup=reply_markup)

async def list_cities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    farms = farms_sheet.get_all_records()
    cities = sorted(list(set(f['city'].strip() for f in farms if str(f.get('status')).strip().lower() == 'active' and f.get('city'))))

    if not cities:
        await query.edit_message_text(
            "⚠️ لا تتوفر أية مناطق أو مزارع حالياً.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 العودة للقائمة الرئيسية", callback_data='main_menu')]])
        )
        return
    
    keyboard = [[InlineKeyboardButton(f"📍 {city}", callback_data=f"city_{city}")] for city in cities]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query.message.photo:
        await query.message.delete()
        await query.message.reply_text("اختر المنطقة التي ترغب بالحجز فيها:", reply_markup=reply_markup)
    else:
        await query.edit_message_text("اختر المنطقة التي ترغب بالحجز فيها:", reply_markup=reply_markup)
        
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # مسح بيانات الحجز المؤقتة إن وجدت عند العودة للرئيسية
    context.user_data.clear()

    TERMS_URL = "https://telegra.ph/Villa-Go---Privacy-Policy-09-15"

    keyboard = [
        [
            InlineKeyboardButton(
                "تصفح المزارع حسب المنطقة 📍", callback_data="list_cities"
            )
        ],
        [InlineKeyboardButton("حجوزاتي 📅", callback_data="my_bookings")],
        [InlineKeyboardButton("السياسات والشروط 📜", url=TERMS_URL)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # التحقق مما إذا كانت الرسالة الحالية تحتوي على صورة لمسحها وإرسال القائمة كنص
    try:
        if query.message.photo:
            await query.message.delete()
            await query.message.reply_text(
                "أهلاً بك مجدداً في منصة Villa Go اختر من القائمة للبدء:",
                reply_markup=reply_markup,
            )
        else:
            await query.edit_message_text(
                "أهلاً بك مجدداً في منصة Villa Go اختر من القائمة للبدء:",
                reply_markup=reply_markup,
            )
    except Exception:
        pass  # لتفادي خطأ Message is not modified في حال تكرار الضغط



async def list_farms_by_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    selected_city = query.data.split("_")[1]
    farms = farms_sheet.get_all_records()
    
    keyboard = []
    for farm in farms:
        if str(farm.get('status')).strip().lower() == 'active' and farm.get('city').strip() == selected_city:
            btn_text = f"🏡 {farm['farm_name']} ({farm['price']}  ل.س / لليلة الواحدة)"
            keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"details_{farm['farm_id']}")])
            
    keyboard.append([InlineKeyboardButton("🔙 العودة للمناطق", callback_data='list_cities')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(f"المزارع المتاحة في منطقة *{selected_city}*:", parse_mode='Markdown', reply_markup=reply_markup)

async def farm_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    farm_id = query.data.split("_")[1]
    farms = farms_sheet.get_all_records()
    selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
    
    if selected_farm:
        booked_ranges = get_booked_ranges(farm_id)
        booked_text = ""
        if booked_ranges:
            booked_text = "\n🚫 *الفترات المحجوزة حالياً:*\n"
            for start_d, end_d in booked_ranges:
                booked_text += f"• من `{start_d}` إلى `{end_d}`\n"
        else:
            booked_text = "\n✨ *جميع التواريخ القادمة متاحة للحجز!*\n"

        details_text = (
            f"🏡 *{selected_farm['farm_name']}*\n\n"
            f"📍 *المنطقة:* {selected_farm['city']} - {selected_farm.get('location', '')}\n"
            f"💰 *السعر لليوم:* {selected_farm['price']} ل.س\n"
            f"👥 *السعة:* {selected_farm['capacity']} أشخاص\n"
            f"🪟 *عدد الغرف:* {selected_farm['bedrooms']}\n"
            f"🏊‍♂️ *مسبح:* {selected_farm['pool']}\n"
            f"📝 *الوصف:* {selected_farm['description']}\n"
            f"{booked_text}"
        )
        
        keyboard = [
            [InlineKeyboardButton("حجز هذه المزرعة 📅", callback_data=f"startbook_{farm_id}")],
            [InlineKeyboardButton("🔙 العودة للمناطق", callback_data='list_cities')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        photo_raw = str(selected_farm.get('photo', '')).strip()
        photo_urls = [url.strip() for url in photo_raw.split(',') if url.strip().startswith('http')]

        if photo_urls:
            await query.message.delete()
            if len(photo_urls) == 1:
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=photo_urls[0],
                    caption=details_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
            else:
                media_group = [InputMediaPhoto(media=url) for url in photo_urls]
                await context.bot.send_media_group(chat_id=query.message.chat_id, media=media_group)
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=details_text,
                    parse_mode='Markdown',
                    reply_markup=reply_markup
                )
        else:
            await query.edit_message_text(details_text, parse_mode='Markdown', reply_markup=reply_markup)

# -------------------------------------------------------------
# ⚠️ قائمة حجوزاتي (مُحدثة لإضافة زر الإلغاء/الحذف للحجوزات المعلقة)
# -------------------------------------------------------------
async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = str(update.effective_user.id).strip()
    existing_users = users_sheet.get_all_records()
    registered_user = next((u for u in existing_users if str(u.get('user_id')).strip() == user_id), None)
    user_phone = re.sub(r'\D', '', str(registered_user.get('phone', ''))) if registered_user else ""
    
    all_bookings = booking_sheet.get_all_records()
    farms = farms_sheet.get_all_records()
    user_bookings = []

    for b in all_bookings:
        b_user_id = str(b.get('user_id', '')).strip()
        b_phone = re.sub(r'\D', '', str(b.get('phone', '')))
        if (b_user_id and b_user_id == user_id) or (user_phone and b_phone == user_phone):
            user_bookings.append(b)
    
    if not user_bookings:
        keyboard = [[InlineKeyboardButton("🔙 العودة للرئيسية", callback_data='list_cities')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📅 ليس لديك أي حجوزات مسجلة حالياً.", reply_markup=reply_markup)
        return

    # مسح الرسالة القديمة لتوليد الرسائل المعروضة بشكل منظم مع أزرار الإلغاء
    await query.message.delete()

    for b in user_bookings:
        status_raw = str(b.get('status')).strip().lower()
        farm_id = str(b.get('farm_id'))
        booking_id = str(b.get('booking_id'))
        selected_farm = next((f for f in farms if str(f['farm_id']) == farm_id), None)
        
        map_info = ""
        keyboard = []

        if status_raw == 'confirmed' and selected_farm:
            map_url = str(selected_farm.get('map_link', '')).strip()
            if map_url.startswith('http'):
                map_info = f"📍 *موقع المزرعة:* [اضغط هنا للفتح على الخريطة]({map_url})\n"

        if status_raw in ['pending_admin', 'pending_owner']:
            status_icon = "⏳ (قيد المراجعة والتدقيق)"
            # إضافة زر إلغاء الحجز الحجز في مرحلة ما قبل التأكيد النهائي
            keyboard.append([InlineKeyboardButton("❌ إلغاء / حذف هذا الحجز", callback_data=f"user_cancel_{booking_id}")])
        elif status_raw == 'confirmed':
            status_icon = "✅ (مؤكد)"
        elif status_raw == 'rejected':
            status_icon = "❌ (مرفوض)"
        elif status_raw == 'cancelled':
            status_icon = "🚫 (ملغى)"
        else:
            status_icon = f"ℹ️ ({status_raw})"

        card_text = (
            f"🆔 *رقم الحجز:* `{booking_id}`\n"
            f"🏡 *المزرعة:* {selected_farm['farm_name'] if selected_farm else ''}\n"
            f"🔢 *رقم الحوالة:* `{b.get('transaction_id', 'غ/م')}`\n"
            f"📅 *تاريخ الوصول:* {b.get('start_date')}\n"
            f"📅 *تاريخ المغادرة:* {b.get('end_date')}\n"
            f"💰 *المبلغ الإجمالي:* {b.get('total_price')} ل.س\n"
            f"📌 *الحالة:* {status_icon}\n"
            f"{map_info}"
        )
        
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=card_text,
            parse_mode='Markdown',
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )

    # زر رجوع عام للرئيسية في الأسفل
    main_keyboard = [[InlineKeyboardButton("🔙 العودة للرئيسية", callback_data='list_cities')]]
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="📌 يمكنك إدارة حجوزاتك من القائمة أعلاه.",
        reply_markup=InlineKeyboardMarkup(main_keyboard)
    )

# -------------------------------------------------------------
# ⚠️ معالجة إلغاء الحجز مباشرة من قبل المستخدم من قائمة "حجوزاتي"
# -------------------------------------------------------------
async def handle_user_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    booking_id = query.data.split("_")[2]
    all_bookings = booking_sheet.get_all_records()

    booking_idx = None
    target_booking = None

    for idx, b in enumerate(all_bookings, start=2):
        if str(b.get('booking_id')) == booking_id:
            booking_idx = idx
            target_booking = b
            break

    if booking_idx and target_booking:
        current_status = str(target_booking.get('status')).strip().lower()

        # التأكد من أن الحجز لم يُحسم بعد (مقبول نهائياً أو مرفوض)
        if current_status in ['pending_admin', 'pending_owner']:
            # 1. تحديث الحالة في Google Sheets
            booking_sheet.update_cell(booking_idx, 12, "cancelled")
            
            # 2. إشعارات للأدمن في حال كان الحجز قيد تدقيق الأدمن
            # 2. إشعارات للأدمن والعميل في حال كان الحجز قيد تدقيق الأدمن (تم الدفع مسبقاً)
            if current_status == 'pending_admin':
                user_info = update.effective_user
                
                # إشعار الأدمن بالتفاصيل الممالية
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=(
                            f"🚨 *إلغاء حجز قيد التدقيق المالي:*\n"
                            f"▪️ *رقم الحجز:* `{booking_id}`\n"
                            f"▪️ *العميل:* {user_info.full_name} (@{user_info.username if user_info.username else 'بدون معرف'})\n"
                            f"▪️ *معرف العميل:* `{user_info.id}`\n\n"
                            f"⚠️ *ملاحظة:* قام العميل بإلغاء الطلب بعد الدفع. يتوجب إعادة الحوالة له عند تواصله."
                        ),
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    print(f"خطأ إشعارات الأدمن عند الإلغاء: {e}")

                # إشعار العميل ويوجه للادمن
                await query.edit_message_text(
                    f"🗑️ تم إلغاء طلب الحجز رقم `{booking_id}` بنجاح.\n\n"
                    f"💳 *ملاحظة مهمة بشأن الحوالة المالية:*\n"
                    f"بما أنك قمت برفع إشعار الدفع سابقاً، يُرجى التواصل مع إدارة البوت لاسترداد المبلغ:\n"
                    f"💬 @Ahmaddarkazlli",
                    parse_mode='Markdown'
                )
                return

            # 3. إشعار صاحب المزرعة في حال كان الحجز لديه
            elif current_status == 'pending_owner':
                farm_id = target_booking.get('farm_id')
                farms = farms_sheet.get_all_records()
                selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
                
                if selected_farm and selected_farm.get('owner_telegram_id'):
                    try:
                        await context.bot.send_message(
                            chat_id=str(selected_farm['owner_telegram_id']).strip(),
                            text=f"ℹ️ *تحديث:* قام العميل بإلغاء طلب الحجز رقم `{booking_id}` للمزرعة *{selected_farm['farm_name']}*.",
                            parse_mode='Markdown'
                        )
                    except Exception as e:
                        print(f"خطأ إشعارات المالك عند الإلغاء: {e}")

            await query.edit_message_text(f"🗑️ تم إلغاء الحجز رقم `{booking_id}` بنجاح وإغلاق الطلب", parse_mode='Markdown')
        else:
            await query.edit_message_text("⚠️ لا يمكن إلغاء هذا الحجز حالياً لأنه مؤكد بالفعل أو تمت معالجته مسبقاً.")
    else:
        await query.edit_message_text("❌ متعذر العثور على الحجز، ربما تم حذفه مسبقاً.")

async def start_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    farm_id = query.data.split("_")[1]
    context.user_data['farm_id'] = farm_id
    
    booked_ranges = get_booked_ranges(farm_id)
    booked_info = ""
    if booked_ranges:
        booked_info = "\n⚠️ *تنبيه - الفترات المحجوزة لهذه المزرعة:*\n"
        for s, e in booked_ranges:
            booked_info += f"• من `{s}` إلى `{e}`\n"

    today_str = datetime.now().strftime("%Y-%m-%d")
    
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=(
            f"يرجى إدخال تاريخ بدء الحجز بصيغة YYYY-MM-DD\n(مثال: `{today_str}`):"
            f"{booked_info}"
        ),
        parse_mode='Markdown'
    )
    return START_DATE

async def get_start_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        start_dt = datetime.strptime(text, "%Y-%m-%d").date()
        today = datetime.now().date()
        
        if start_dt <= today:
            tomorrow_str = (today + timedelta(days=1)).strftime("%Y-%m-%d")
            await update.message.reply_text(
                f"❌ *لا يمكن الحجز في نفس اليوم!*\n"
                f"أقل تاريخ بدء مسموح به للحجز هو غداً (`{tomorrow_str}`) فما بعد.\n\n"
                f"يرجى إدخال تاريخ جديد:",
                parse_mode='Markdown'
            )
            return START_DATE
        
        context.user_data['start_dt'] = start_dt
        context.user_data['start_date'] = text
        await update.message.reply_text("يرجى إدخال تاريخ مغادرة الحجز (مثال: 2026-09-17):")
        return END_DATE
    except ValueError:
        await update.message.reply_text("⚠️ الصيغة غير صحيحة! يرجى الكتابة بالشكل التالي: YYYY-MM-DD")
        return START_DATE

async def get_end_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        end_dt = datetime.strptime(text, "%Y-%m-%d").date()
        start_dt = context.user_data['start_dt']
        
        if end_dt <= start_dt:
            await update.message.reply_text("❌ يجب أن يكون تاريخ المغادرة بعد تاريخ الوصول! أدخل التاريخ مجدداً:")
            return END_DATE
        
        farm_id = context.user_data.get('farm_id')
        if not is_farm_available(farm_id, start_dt, end_dt):
            booked_ranges = get_booked_ranges(farm_id)
            booked_info = "\n".join([f"• من `{s}` إلى `{e}`" for s, e in booked_ranges])
            await update.message.reply_text(
                f"❌ *عذراً، المزرعة محجوزة في هذه الفترة المحددة!*\n\n"
                f"📌 *الفترات المحجوزة حالياً:*\n{booked_info}\n\n"
                f"يرجى إدخال تاريخ بدء جديد للحجز (YYYY-MM-DD):",
                parse_mode='Markdown'
            )
            return START_DATE
        
        context.user_data['end_dt'] = end_dt
        context.user_data['end_date'] = text
        await update.message.reply_text("يرجى إدخال عدد الضيوف:")
        return GUESTS
    except ValueError:
        await update.message.reply_text("⚠️ الصيغة غير صحيحة! يرجى الكتابة بالشكل التالي: YYYY-MM-DD")
        return END_DATE

async def get_guests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("⚠️ يرجى إدخال عدد ضيوف صحيح بالأرقام فقط:")
        return GUESTS

    guests_count = int(text)
    farm_id = context.user_data.get('farm_id')
    farms = farms_sheet.get_all_records()
    selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)

    if selected_farm and selected_farm.get('capacity'):
        max_capacity = int(selected_farm['capacity'])
        if guests_count > max_capacity:
            await update.message.reply_text(
                f"❌ *عذراً، عدد الضيوف أكبر من السعة المسموحة!*\n\n"
                f"الحد الأقصى للضيوف في مزرعة *{selected_farm['farm_name']}* هو *{max_capacity}* أشخاص فقط.\n"
                f"يرجى إدخال عدد مناسب:",
                parse_mode='Markdown'
            )
            return GUESTS

    context.user_data['guests'] = text
    await update.message.reply_text(
        "🕒 يرجى إدخال وقت الوصول المتوقع مع تحديد الفترة (AM / PM أو صباحاً / عصراً / مساءً):\n"
        "(أمثلة: `02:00 PM` ، `11:30 AM` ، `4 عصراً` ، `10 صباحاً`):",
        parse_mode='Markdown'
    )
    return CHECKIN_TIME

async def get_checkin_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    valid_patterns = [r'am', r'pm', r'صباح', r'عصر', r'مساء', r'ظهر']
    if not any(re.search(pattern, text, re.IGNORECASE) for pattern in valid_patterns):
        await update.message.reply_text("⚠️ يرجى توضيح وقت الوصول بدقة مع تحديد الفترة (مثال: `03:00 PM` أو `2 عصراً`)")
        return CHECKIN_TIME

    context.user_data['check_in_time'] = text
    user = update.effective_user
    context.user_data['full_name'] = user.full_name
    
    existing_users = users_sheet.get_all_records()
    registered_user = next((u for u in existing_users if str(u.get('user_id')) == str(user.id)), None)
    
    if registered_user and registered_user.get('phone'):
        context.user_data['phone'] = registered_user.get('phone')
        return await show_summary(update, context)
    else:
        phone_button = KeyboardButton(text="مشاركة رقم الهاتف الموثق 📱", request_contact=True)
        reply_markup = ReplyKeyboardMarkup([[phone_button]], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("📱 لتأكيد الحجز، يرجى الضغط على الزر أدناه لمشاركة رقم هاتفك الموثق:", reply_markup=reply_markup)
        return USER_PHONE

async def get_user_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.contact:
        await update.message.reply_text("⚠️ يرجى الضغط على زر 'مشاركة رقم الهاتف الموثق 📱' في الأسفل لتأكيد رقمك الحقيقي.")
        return USER_PHONE

    phone = update.message.contact.phone_number
    context.user_data['phone'] = phone
    user = update.effective_user
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    users_sheet.append_row([str(user.id), user.full_name, phone, created_at])
    await update.message.reply_text("تم توثيق الرقم بنجاح ✅", reply_markup=ReplyKeyboardRemove())
    return await show_summary(update, context)

# -------------------------------------------------------------
# ⚠️ زر "إلغاء/حذف الحجز ❌" مضاف هنا قبل تأكيد طلب الحجز النهائي
# -------------------------------------------------------------
async def show_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    farm_id = context.user_data['farm_id']
    farms = farms_sheet.get_all_records()
    farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
    
    daily_price = float(farm['price']) if farm else 0
    days_count = (context.user_data['end_dt'] - context.user_data['start_dt']).days
    
    base_price = daily_price * days_count
    commission = base_price * 0.10
    total_price = base_price + commission
    
    context.user_data['base_price'] = base_price
    context.user_data['commission'] = commission
    context.user_data['total_price'] = total_price
    context.user_data['farm_name'] = farm['farm_name'] if farm else ''
    
    summary = (
        "📋 *تأكيد تفاصيل الحجز:*\n\n"
        f"👤 *العميل الموثّق:* {context.user_data['full_name']}\n"
        f"📱 *رقم الهاتف:* `{context.user_data['phone']}`\n"
        f"🏡 *المزرعة:* {context.user_data['farm_name']}\n"
        f"📅 *تاريخ الوصول:* {context.user_data['start_date']}\n"
        f"📅 *تاريخ المغادرة:* {context.user_data['end_date']} ({days_count} أيام)\n"
        f"⏰ *وقت الوصول:* {context.user_data['check_in_time']}\n"
        f"👥 *عدد الضيوف:* {context.user_data['guests']}\n\n"
        f"💵 *السعر الكلي:* {base_price:,.0f} ل.س\n"
        f"💳 *عمولة المنصة (10%):* {commission:,.0f} ل.س\n"
        f"💰 *الإجمالي المطلوب:* {total_price:,.0f} ل.س\n\n"
        f"⚠️ *لتثبيت الحجز:* يرجى دفع مبلغ العمولة ({commission:,.0f} ل.س) عربوناً عبر شام كاش."
    )
    
    keyboard = [
        
        [InlineKeyboardButton("تأكيد وطلب الحجز ✅", callback_data='confirm_booking')],
        [InlineKeyboardButton("إلغاء وحذف الحجز ❌", callback_data='cancel_booking')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(summary, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await update.callback_query.message.reply_text(summary, parse_mode='Markdown', reply_markup=reply_markup)
        
    return CONFIRM

async def ask_transaction_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    farm_id = context.user_data.get('farm_id')
    start_dt = context.user_data.get('start_dt')
    end_dt = context.user_data.get('end_dt')

    if not is_farm_available(farm_id, start_dt, end_dt):
        await query.edit_message_text("❌ للأسف سبقك أحد المستخدمين بحجز المزرعة في هذه الفترة! يرجى اختيار تاريخ آخر.")
        return ConversationHandler.END

    keyboard = [
       
        [InlineKeyboardButton("إلغاء وحذف الحجز ❌", callback_data='cancel_booking')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    commission_val = context.user_data.get('commission', 0)

    await query.edit_message_text(
        f"💳 *تفاصيل التحويل عبر شام كاش*\n\n"
        f"1️⃣ يرجى تحويل مبلغ العمولة: *{commission_val:,.0f} ل.س*\n"
        f"إلى رقم المحفظة التالي (اضغط عليه للنسخ المباشر):\n"
        f"`{SHAM_CASH_ACCOUNT}`\n\n"
        f"2️⃣ بعد إتمام التحويل، يرجى كتابة **رقم الحوالة** (Transaction ID) نصياً هنا في المحادثة:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )
    return TRANSACTION_ID

async def get_transaction_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tx_id = update.message.text.strip()
    
    all_bookings = booking_sheet.get_all_records()
    existing_tx_ids = [str(b.get('transaction_id', '')).strip() for b in all_bookings if b.get('transaction_id')]

    if tx_id in existing_tx_ids:
        await update.message.reply_text("❌ *رقم الحوالة هذا تم استخدامه من قبل في حجز آخر!* يرجى التأكد وإعادة إدخال الرقم الصحيح:", parse_mode='Markdown')
        return TRANSACTION_ID

    context.user_data['transaction_id'] = tx_id
    booking_id = "BK" + str(uuid.uuid4())[:6].upper()
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user_id = str(update.effective_user.id)
    farm_id = context.user_data.get('farm_id')
    
    booking_sheet.append_row([
        booking_id,
        farm_id,
        context.user_data.get('full_name'),
        context.user_data.get('phone'),
        context.user_data.get('start_date'),
        context.user_data.get('end_date'),
        context.user_data.get('check_in_time'),
        context.user_data.get('guests'),
        context.user_data.get('base_price'),
        context.user_data.get('commission'),
        context.user_data.get('total_price'),
        "pending_admin",
        created_at,
        user_id,
        tx_id
    ])
    
    farms = farms_sheet.get_all_records()
    selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
    
    admin_msg = (
        f"🔔 *طلب حجز جديد بانتظار التأكيد المالي (المنصة)*\n\n"
        f"🆔 *رقم الحجز:* `{booking_id}`\n"
        f"🔢 *رقم الحوالة المدخل:* `{tx_id}`\n"
        f"🏡 *المزرعة:* {selected_farm['farm_name'] if selected_farm else ''}\n"
        f"👤 *الزبون:* {context.user_data.get('full_name')}\n"
        f"📱 *رقم الهاتف:* `{context.user_data.get('phone')}`\n"
        f"📅 *الفترة:* من {context.user_data.get('start_date')} إلى {context.user_data.get('end_date')}\n"
        f"💳 *العمولة المفروض استلامها:* {context.user_data.get('commission'):,.0f} ل.س\n\n"
        f"يرجى التحقق من استلام الحوالة رقم `{tx_id}` في حساب شام كاش والموافقة:"
    )
    
    admin_keyboard = [
        [
            InlineKeyboardButton("تأكيد استلام المبلغ ✅", callback_data=f"admin_accept_{booking_id}_{user_id}"),
            InlineKeyboardButton("رفض (لم يصل المبلغ) ❌", callback_data=f"admin_reject_{booking_id}_{user_id}")
        ]
    ]
    
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_msg,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(admin_keyboard)
        )
    except Exception as e:
        print(f"خطأ في إرسال الإشعار للأدمن: {e}")

    msg_text = (
        f"🎉 *تم تقديم طلب الحجز واستلام رقم الحوالة بنجاح!*\n\n"
        f"🆔 *رقم الحجز:* `{booking_id}`\n"
        f"🔢 *رقم الحوالة:* `{tx_id}`\n"
        f"📌 *الحالة:* قيد التدقيق المالي من قبل إدارة المنصة.\n\n"
        f"سيتم تحويل طلبك لمالك المزرعة فور مطابقة الحوالة."
    )
    
    await update.message.reply_text(msg_text, parse_mode='Markdown')
    return ConversationHandler.END

async def handle_admin_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split("_")
    action = data_parts[1]
    booking_id = data_parts[2]
    user_id = data_parts[3]
    
    all_bookings = booking_sheet.get_all_records()
    booking_row = None
    booking_idx = None
    
    for idx, b in enumerate(all_bookings, start=2):
        if str(b.get('booking_id')) == booking_id:
            booking_row = b
            booking_idx = idx
            break

    if not booking_row:
        await query.edit_message_text("❌ لم يتم العثور على بيانات هذا الحجز.")
        return

    if action == "accept":
        booking_sheet.update_cell(booking_idx, 12, "pending_owner")
        await query.edit_message_text(f"✅ تم تأكيد استلام المال للحجز `{booking_id}` وتم تحويل الطلب لصاحب المزرعة.", parse_mode='Markdown')
        
        farm_id = booking_row.get('farm_id')
        farms = farms_sheet.get_all_records()
        selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
        
        if selected_farm and selected_farm.get('owner_telegram_id'):
            owner_id = str(selected_farm['owner_telegram_id']).strip()
            owner_msg = (
                f"🔔 *طلب حجز مدفوع جديد لمزرعتك ({selected_farm['farm_name']})!*\n\n"
                f"🆔 *رقم الحجز:* `{booking_id}`\n"
                f"👤 *اسم العميل:* {booking_row.get('customer_name')}\n"
                f"📱 *رقم الهاتف:* `{booking_row.get('phone')}`\n"
                f"📅 *تاريخ الوصول:* {booking_row.get('start_date')}\n"
                f"📅 *تاريخ المغادرة:* {booking_row.get('end_date')}\n"
                f"⏰ *وقت الوصول:* {booking_row.get('check_in_time')}\n"
                f"👥 *عدد الضيوف:* {booking_row.get('guests')}\n"
                f"💵 *السعر صافي:* {float(booking_row.get('base_price', 0)):,.0f} ل.س\n\n"
                f"يرجى التأكد من التوفر واختيار إحدى الخيارين.:"
            )
            owner_keyboard = [
                [
                    InlineKeyboardButton("قبول الحجز ✅", callback_data=f"owner_accept_{booking_id}_{user_id}"),
                    InlineKeyboardButton("رفض الحجز ❌", callback_data=f"owner_reject_{booking_id}_{user_id}")
                ]
            ]
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=owner_msg,
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup(owner_keyboard)
                )
            except Exception as e:
                print(f"خطأ في إرسال الإشعار للمالك: {e}")
                
    elif action == "reject":
        booking_sheet.update_cell(booking_idx, 12, "rejected")
        await query.edit_message_text(f"❌ تم رفض الحجز `{booking_id}` لعدم استلام المال.", parse_mode='Markdown')
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ تعذر تأكيد حجزك رقم `{booking_id}` لعدم صحة رقم الحوالة أو عدم استلام العمولة.",
                parse_mode='Markdown'
            )
        except Exception as e:
            print(f"خطأ إشعارات العميل: {e}")

async def handle_owner_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data_parts = query.data.split("_")
    action = data_parts[1]
    booking_id = data_parts[2]
    user_id = data_parts[3]
    
    all_bookings = booking_sheet.get_all_records()
    booking_row = None

    for idx, b in enumerate(all_bookings, start=2):
        if str(b.get('booking_id')) == booking_id:
            booking_row = b
            new_status = "confirmed" if action == "accept" else "rejected"
            booking_sheet.update_cell(idx, 12, new_status)
            break

    # إضافة ملاحظة القرار في نهاية نص الرسالة الأصلي
    status_note = "\n\n✅ *تم قبول هذا الحجز بنجاح.*" if action == "accept" else "\n\n❌ *تم رفض هذا الحجز.*"
    updated_text = query.message.text + status_note

    # تحديث النص والإبقاء على التفاصيل كاملة مع إزالة الأزرار (reply_markup=None)
    await query.edit_message_text(text=updated_text, parse_mode='Markdown', reply_markup=None)

    if action == "accept":
        farm_id = booking_row.get('farm_id') if booking_row else None
        farms = farms_sheet.get_all_records()
        selected_farm = next((f for f in farms if str(f['farm_id']) == str(farm_id)), None)
        
        map_url = str(selected_farm.get('map_link', '')).strip() if selected_farm else ""

        client_keyboard = []
        if map_url.startswith('http'):
            client_keyboard.append([InlineKeyboardButton("موقع المزرعة على الخريطة 📍", url=map_url)])
        
        reply_markup = InlineKeyboardMarkup(client_keyboard) if client_keyboard else None

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    f"🎉 *أخبار سارة!* تم قبول طلب حجزك رقم `{booking_id}` نهائياً من قبل صاحب المزرعة.\n\n"
                    f"📍 *موقع المزرعة:* يمكنك الآن استخدام الزر أدناه للوصول المباشر للمزرعة عبر الخريطة.\n\n"
                    f"نتمنى لك إقامة سعيدة!"
                ),
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"فشل إشعارات العميل: {e}")
            
    elif action == "reject":
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"❌ نأسف لإبلاغك بأنه تم رفض طلب حجزك رقم `{booking_id}` من قبل صاحب المزرعة.",
                parse_mode='Markdown'
            )
        except Exception as e:
            print(f"فشل إشعارات العميل: {e}")

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    # تفريغ الذاكرة المؤقتة للزبون
    context.user_data.clear()
    await query.edit_message_text(
        "❌ تم إلغاء عملية الحجز بنجاح.\n\nيمكنك البدء من جديد عبر إرسال /start"
    )
    return ConversationHandler.END

# 12. تشغيل التطبيق
def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    # التحقق من وجود التوكين لمنع الـ Crash
    if not TOKEN:
        raise ValueError("خطأ: لم يتم العثور على TELEGRAM_BOT_TOKEN في متغيرات البيئة!")
    
    app = Application.builder().token(TOKEN).build()
    
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_booking, pattern='^startbook_')],
        states={
            START_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_start_date)],
            END_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_end_date)],
            GUESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_guests)],
            CHECKIN_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_checkin_time)],
            USER_PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), get_user_phone)],
            CONFIRM: [
                CallbackQueryHandler(ask_transaction_id, pattern='^confirm_booking$'),
                CallbackQueryHandler(cancel_booking, pattern='^cancel_booking$')
            ],
            TRANSACTION_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_transaction_id),
                             # ⬇️ إضافـة هذا السطر لالتقاط زر الإلغاء أثناء إدخال رقم الحوالة
                CallbackQueryHandler(cancel_booking, pattern='^cancel_booking$')]
        },
        fallbacks=[CommandHandler("start", start),
                   # ⬇️ إضافـة هذا السطر لضمان التقاط زر الإلغاء أو أمر /cancel من أي خطوة
            CallbackQueryHandler(cancel_booking, pattern='^cancel_booking$'),
            CommandHandler("cancel", cancel_booking)
                   ]
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        CallbackQueryHandler(main_menu, pattern="^main_menu$")
    )
    app.add_handler(CallbackQueryHandler(list_cities, pattern='^list_cities$'))
    app.add_handler(CallbackQueryHandler(list_farms_by_city, pattern='^city_'))
    app.add_handler(CallbackQueryHandler(farm_details, pattern='^details_'))
    app.add_handler(CallbackQueryHandler(my_bookings, pattern='^my_bookings$'))
    
    # معالج إلغاء الحجز المباشر من قائمة "حجوزاتي"
    app.add_handler(CallbackQueryHandler(handle_user_cancel_booking, pattern='^user_cancel_'))
    
    app.add_handler(CallbackQueryHandler(handle_admin_decision, pattern='^admin_'))
    app.add_handler(CallbackQueryHandler(handle_owner_decision, pattern='^owner_'))
    
    app.add_handler(conv_handler)
    
    print("البوت يعمل بنجاح...")
    app.run_polling()

if __name__ == '__main__':
    main()