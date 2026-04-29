# ضع مفاتيحك هنا
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler, PreCheckoutQueryHandler
from openai import OpenAI
from rembg import remove
from PIL import Image

# =========================
# مفاتيحك
# =========================
TOKEN = os.getenv("8645142103:AAGthMqYyhUc8S_SHf1jb1SRmMH8kTqQV_Y")
import os

OPENAI_KEY = os.getenv("OPENAI_KEY")
PRIMARY_DEV = 8662704115
DEV_LOG_CHAT_ID = "@rorproto"

client = OpenAI(api_key=OPENAI_KEY)

# =========================
# البيانات
# =========================
removebg_usage = {}
paid_uses = {}

referrals = {}
referred_by = {}

profit_data = {
    "stars": 0,
    "payments": 0
}

# =========================
# البداية + الإحالات
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.args:
        referrer_id = int(context.args[0])

        if user_id != referrer_id and user_id not in referred_by:
            referred_by[user_id] = referrer_id
            referrals[referrer_id] = referrals.get(referrer_id, 0) + 1
            paid_uses[referrer_id] = paid_uses.get(referrer_id, 0) + 1

            await context.bot.send_message(
                chat_id=referrer_id,
                text="🎉 دخل شخص من رابطك! +1 استخدام"
            )

    await update.message.reply_text(
        "أنا عتب 👁️\n"
        "/removebg إزالة الخلفية\n"
        "/image صناعة صورة\n"
        "/shop شراء استخدامات\n"
        "/balance رصيدك"
    )

# =========================
# المتجر
# =========================
async def shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("شراء 3 استخدامات ⭐", callback_data="buy")]]
    await update.message.reply_text("🛒 متجر", reply_markup=InlineKeyboardMarkup(keyboard))

async def shop_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await context.bot.send_invoice(
        chat_id=query.message.chat.id,
        title="باقة",
        description="3 استخدامات",
        payload="pack",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice("3 uses", 5)]
    )

# =========================
# الدفع
# =========================
async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def success(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    paid_uses[user_id] = paid_uses.get(user_id, 0) + 3

    profit_data["stars"] += 5
    profit_data["payments"] += 1

    await update.message.reply_text("تم الدفع ⭐ +3 استخدام")

# =========================
# إزالة الخلفية
# =========================
async def remove_bg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    free_used = removebg_usage.get(user_id, 0)
    paid = paid_uses.get(user_id, 0)

    if free_used < 1:
        removebg_usage[user_id] = 1
        await update.message.reply_text("استخدمت المجاني 👁️")

    elif paid > 0:
        paid_uses[user_id] -= 1

    else:
        return await update.message.reply_text("انتهى الرصيد 👁️ /shop")

    if not update.message.reply_to_message:
        return await update.message.reply_text("رد على صورة")

    photo = update.message.reply_to_message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    await file.download_to_drive("in.png")

    img = Image.open("in.png")
    result = remove(img)
    result.save("out.png")

    await context.bot.send_photo(chat_id=DEV_LOG_CHAT_ID, photo=open("out.png", "rb"))

    await update.message.reply_photo(photo=open("out.png", "rb"))

# =========================
# رصيد
# =========================
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = paid_uses.get(user_id, 0)
    await update.message.reply_text(f"رصيدك: {bal}")

# =========================
# أرباح (لك فقط)
# =========================
async def profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_DEV:
        return

    await update.message.reply_text(
        f"⭐ {profit_data['stars']}\n🧾 {profit_data['payments']}"
    )

# =========================
# تشغيل
# =========================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("shop", shop))
app.add_handler(CommandHandler("removebg", remove_bg))
app.add_handler(CommandHandler("balance", balance))
app.add_handler(CommandHandler("profit", profit))

app.add_handler(CallbackQueryHandler(shop_button))
app.add_handler(PreCheckoutQueryHandler(precheckout))
app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, success))

print("Atab running 👁️")
app.run_polling()

from flask import Flask
import os
import threading

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()
import os
import threading
from flask import Flask

# تشغيل البوت
def run_bot():
    # حط كود البوت حقك هنا
    # مثال:
    import bot  # أو الكود الحالي
    bot.main()  # حسب كودك

# السيرفر
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# تشغيل الاثنين مع بعض
threading.Thread(target=run_bot).start()
threading.Thread(target=run_web).start()