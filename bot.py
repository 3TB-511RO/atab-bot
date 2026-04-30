import os
import json
import asyncio
import re
import base64
import threading
import time
import random
from datetime import datetime, timedelta

from flask import Flask
from PIL import Image
from rembg import remove
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    ChatPermissions,
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
# Config
# =====================

TOKEN = os.getenv("TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")

PRIMARY_DEV_ID = int(os.getenv("PRIMARY_DEV", "8662704115"))
DEV_ASSISTANT_ID = int(os.getenv("DEV_ASSISTANT", "0"))
DEV_LOG_CHAT_ID = os.getenv("DEV_LOG_CHAT_ID", "@rorproto")

client = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None
DATA_FILE = "group_data.json"

# =====================
# Ranks
# =====================

RANK_LEVELS = {
    "dev": 100,
    "dev_assistant": 90,
    "primary_owner": 80,
    "owner": 70,
    "creator": 60,
    "manager": 50,
    "admin": 40,
    "vip": 30,
    "member": 0,
}

RANK_NAMES = {
    "dev": "👨‍💻 المطور Dev",
    "dev_assistant": "🛠 مساعد المطور Dev",
    "primary_owner": "👑 المالك الأساسي",
    "owner": "🔱 المالك",
    "creator": "🌟 المنشئ",
    "manager": "⚙️ المدير",
    "admin": "🛡 الأدمن",
    "vip": "💎 المميز",
    "member": "👤 عضو",
}

RANK_ORDER = [
    "dev",
    "dev_assistant",
    "primary_owner",
    "owner",
    "creator",
    "manager",
    "admin",
    "vip",
    "member",
]

RANK_MAP = {
    "مالك_اساسي": "primary_owner",
    "primary_owner": "primary_owner",
    "مالك": "owner",
    "owner": "owner",
    "منشئ": "creator",
    "creator": "creator",
    "مدير": "manager",
    "manager": "manager",
    "ادمن": "admin",
    "admin": "admin",
    "مميز": "vip",
    "vip": "vip",
    "عضو": "member",
    "member": "member",
}

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

flood_tracker = {}

# =====================
# Data Helpers
# =====================

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_group(data, chat_id):
    cid = str(chat_id)

    if cid not in data:
        data[cid] = {
            "ranks": {},
            "banned": [],
            "muted": [],
            "restricted": [],
            "warnings": {},
            "welcome": "",
            "welcome_buttons": [],
            "rules": "",
            "link": "",
            "custom_commands": {},
            "filters": {},
            "notes": {},
            "locked": False,
            "lock_types": [],
            "anti_flood": 0,
            "anti_flood_action": "mute",
            "anti_link": False,
            "captcha": False,
            "captcha_pending": {},
            "log_channel": "",
            "settings": {"protection": False},
        }

    return data[cid]


def get_rank(data, chat_id, user_id):
    if user_id == PRIMARY_DEV_ID:
        return "dev"

    if user_id == DEV_ASSISTANT_ID:
        return "dev_assistant"

    return get_group(data, chat_id)["ranks"].get(str(user_id), "member")


def get_level(data, chat_id, user_id):
    return RANK_LEVELS[get_rank(data, chat_id, user_id)]


def can_manage(data, chat_id, actor, target):
    return get_level(data, chat_id, actor) > get_level(data, chat_id, target)


async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        return update.message.reply_to_message.from_user

    if context.args:
        try:
            member = await update.effective_chat.get_member(int(context.args[0]))
            return member.user
        except Exception:
            pass

    return None


async def require(update: Update, data, min_rank):
    if get_level(data, update.effective_chat.id, update.effective_user.id) < RANK_LEVELS[min_rank]:
        await update.message.reply_text("❌ ما عندك صلاحية.")
        return False

    return True


async def do_log(context: ContextTypes.DEFAULT_TYPE, data, chat_id, text):
    log_ch = get_group(data, chat_id).get("log_channel", "")

    if log_ch:
        try:
            await context.bot.send_message(chat_id=log_ch, text=f"📋 {text}")
        except Exception:
            pass


def full_permissions():
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )


# =====================
# Privacy
# =====================

async def privacy_reply(update: Update):
    if not update.message or not update.message.text:
        return False

    keywords = [
        "تحفظ",
        "الحفظ",
        "تخزن",
        "تحتفظ",
        "ترسل",
        "خصوصية",
        "بياناتي",
    ]

    if any(w in update.message.text.lower() for w in keywords):
        await update.message.reply_text(
            "قد تُستخدم بعض البيانات لأغراض تشغيلية مع احترام خصوصية المستخدم."
        )
        return True

    return False


# =====================
# Log Media
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

        cap = (
            f"📂 سجل\n"
            f"👤 {user.first_name}\n"
            f"🆔 {user.id}\n"
            f"🔗 @{user.username or 'لا يوجد'}\n"
            f"💬 {text or 'بدون نص'}\n"
            f"📍 {chat.title or 'خاص'}"
        )

        if update.message.photo:
            stats["photos"] += 1
            await context.bot.send_photo(
                chat_id=DEV_LOG_CHAT_ID,
                photo=update.message.photo[-1].file_id,
                caption=cap + "\n🏷️ صورة",
            )

        elif update.message.video:
            stats["videos"] += 1
            await context.bot.send_video(
                chat_id=DEV_LOG_CHAT_ID,
                video=update.message.video.file_id,
                caption=cap + "\n🏷️ فيديو",
            )

    except Exception as e:
        print("Log error:", e)


# =====================
# Start + Help + Ref
# =====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if context.args:
        try:
            ref = int(context.args[0])

            if user_id != ref and user_id not in referred_by:
                referred_by[user_id] = ref
                referrals[ref] = referrals.get(ref, 0) + 1
                paid_uses[ref] = paid_uses.get(ref, 0) + 1

                await context.bot.send_message(
                    chat_id=ref,
                    text="🎉 دخل شخص من رابطك! +1 استخدام 👁️",
                )
        except Exception:
            pass

    await update.message.reply_text(
        "أنا عتب 👁️\n\n"
        "/removebg - إزالة خلفية\n"
        "/image - صناعة صورة\n"
        "/shop - المتجر\n"
        "/balance - رصيدك\n"
        "/ref - رابط الدعوة\n"
        "/help - المساعدة"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "عتب 👁️ — المساعدة\n\n"
        "🖼 /removebg — إزالة خلفية بالرد على صورة\n"
        "🎨 /image [وصف] — صناعة صورة\n"
        "💎 /رتبة — رتبتك\n"
        "📋 /الرتب — قائمة الرتب\n"
        "📜 /القوانين — قوانين المجموعة\n"
        "📝 /ملاحظة [اسم] — عرض ملاحظة\n"
        "⚠️ /الانذارات — عدد إنذاراتك\n"
        "👤 /معلوماتي — معلوماتك"
    )


# =====================
# Remove Background
# =====================

async def remove_bg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not update.message.reply_to_message or not update.message.reply_to_message.photo:
        return await update.message.reply_text("رد على صورة بالأمر /removebg")

    free_used = removebg_usage.get(user_id, 0)
    paid = paid_uses.get(user_id, 0)

    if free_used < 1:
        removebg_usage[user_id] = 1

    elif paid > 0:
        paid_uses[user_id] = paid - 1

    else:
        return await update.message.reply_text("انتهى الرصيد 👁️\n/shop")

    try:
        await update.message.reply_text("جاري إزالة الخلفية…")

        file = await context.bot.get_file(update.message.reply_to_message.photo[-1].file_id)
        await file.download_to_drive("input.png")

        result = remove(Image.open("input.png"))
        result.save("output.png")

        stats["removebg"] += 1

        await context.bot.send_photo(
            chat_id=DEV_LOG_CHAT_ID,
            photo=open("output.png", "rb"),
            caption="🖼 إزالة خلفية",
        )

        await update.message.reply_photo(photo=open("output.png", "rb"))

    except Exception as e:
        print("RemoveBG error:", e)
        await update.message.reply_text("حدث خطأ.")


# =====================
# AI Image
# =====================

async def image_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        return await update.message.reply_text("مفتاح OpenAI غير مضبوط.")

    prompt = " ".join(context.args)

    if not prompt:
        return await update.message.reply_text("اكتب وصف.\nمثال: /image سيارة سوداء")

    await update.message.reply_text("جاري إنشاء الصورة…")

    try:
        result = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
        )

        image_bytes = base64.b64decode(result.data[0].b64_json)

        with open("generated.png", "wb") as f:
            f.write(image_bytes)

        stats["images_created"] += 1

        await update.message.reply_photo(photo=open("generated.png", "rb"))

    except Exception as e:
        print("Image error:", e)
        await update.message.reply_text("ما قدرت أصنع الصورة.")


# =====================
# Shop
# =====================

async def shop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("شراء 3 استخدامات - 5 ⭐", callback_data="buy_pack")]
    ]

    await update.message.reply_text(
        "🛒 متجر عتب 👁️\n\nاختر الباقة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)


async def successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    paid_uses[update.effective_user.id] = paid_uses.get(update.effective_user.id, 0) + 3
    profit_data["stars"] += 5
    profit_data["payments"] += 1

    await update.message.reply_text("تم الدفع ⭐\nأضيف 3 استخدامات 👁️")


async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💳 رصيدك: {paid_uses.get(update.effective_user.id, 0)} استخدام"
    )


async def profit_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_DEV_ID:
        return

    await update.message.reply_text(
        f"📊 الأرباح:\n💰 {profit_data['stars']} ⭐\n🧾 {profit_data['payments']} عملية"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != PRIMARY_DEV_ID:
        return

    await update.message.reply_text(
        f"📊 الإحصائيات:\n"
        f"👥 {len(stats['users'])}\n"
        f"💬 {stats['messages']}\n"
        f"📸 {stats['photos']}\n"
        f"🎬 {stats['videos']}\n"
        f"🖼 {stats['removebg']}\n"
        f"🎨 {stats['images_created']}"
    )


async def ref_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username

    await update.message.reply_text(
        f"🔗 رابط دعوتك:\n"
        f"https://t.me/{bot_username}?start={update.effective_user.id}\n\n"
        f"كل شخص = +1 استخدام."
    )


async def myrefs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"👥 إحالاتك: {referrals.get(update.effective_user.id, 0)}"
    )


# =====================
# Rank Commands
# =====================

async def cmd_setrank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "owner"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص أو اكتب الآيدي.")

    if not context.args:
        return await update.message.reply_text("حدد الرتبة.")

    rank = RANK_MAP.get(context.args[-1].lower())

    if not rank:
        return await update.message.reply_text("رتبة غير معروفة.")

    chat_id = update.effective_chat.id
    actor_id = update.effective_user.id

    if not can_manage(data, chat_id, actor_id, target.id):
        return await update.message.reply_text("❌ ما تقدر تعدّل هذا الشخص.")

    if RANK_LEVELS[rank] >= get_level(data, chat_id, actor_id):
        return await update.message.reply_text("❌ ما تقدر تعطي رتبة مساوية أو أعلى منك.")

    get_group(data, chat_id)["ranks"][str(target.id)] = rank
    save_data(data)

    await do_log(context, data, chat_id, f"تعيين رتبة: {target.first_name} ← {RANK_NAMES[rank]}")
    await update.message.reply_text(f"✅ {target.first_name} ← {RANK_NAMES[rank]}")


async def cmd_promote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id
    actor_id = update.effective_user.id

    if not can_manage(data, chat_id, actor_id, target.id):
        return await update.message.reply_text("❌ ما تقدر ترفع هذا الشخص.")

    current = get_rank(data, chat_id, target.id)
    idx = RANK_ORDER.index(current)

    if idx <= 2:
        return await update.message.reply_text("وصل للحد الأقصى.")

    new_rank = RANK_ORDER[idx - 1]

    if RANK_LEVELS[new_rank] >= get_level(data, chat_id, actor_id):
        return await update.message.reply_text("❌ ما تقدر ترفعه لرتبة أعلى أو مساوية لك.")

    get_group(data, chat_id)["ranks"][str(target.id)] = new_rank
    save_data(data)

    await do_log(context, data, chat_id, f"رفع: {target.first_name} ← {RANK_NAMES[new_rank]}")
    await update.message.reply_text(f"⬆️ {target.first_name} ← {RANK_NAMES[new_rank]}")


async def cmd_demote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id

    if not can_manage(data, chat_id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تنزّل هذا الشخص.")

    current = get_rank(data, chat_id, target.id)
    idx = RANK_ORDER.index(current)

    if idx >= len(RANK_ORDER) - 1:
        return await update.message.reply_text("عضو عادي بالفعل.")

    new_rank = RANK_ORDER[idx + 1]

    get_group(data, chat_id)["ranks"][str(target.id)] = new_rank
    save_data(data)

    await do_log(context, data, chat_id, f"تنزيل: {target.first_name} ← {RANK_NAMES[new_rank]}")
    await update.message.reply_text(f"⬇️ {target.first_name} ← {RANK_NAMES[new_rank]}")


async def cmd_demote_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "primary_owner"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    if not can_manage(data, update.effective_chat.id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تعدل هذا الشخص.")

    get_group(data, update.effective_chat.id)["ranks"][str(target.id)] = "member"
    save_data(data)

    await update.message.reply_text(f"🗑 تم إزالة جميع رتب {target.first_name}")


def make_setrank_cmd(rank_name):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.args = list(context.args or []) + [rank_name]
        await cmd_setrank(update, context)

    return handler


# =====================
# Moderation
# =====================

def parse_time(args):
    until = None
    reason_parts = []

    for arg in args:
        if arg.endswith("m") and arg[:-1].isdigit():
            until = datetime.now() + timedelta(minutes=int(arg[:-1]))
        elif arg.endswith("h") and arg[:-1].isdigit():
            until = datetime.now() + timedelta(hours=int(arg[:-1]))
        elif arg.endswith("d") and arg[:-1].isdigit():
            until = datetime.now() + timedelta(days=int(arg[:-1]))
        else:
            reason_parts.append(arg)

    return until, " ".join(reason_parts) or "بدون سبب"


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id

    if not can_manage(data, chat_id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تحظر هذا الشخص.")

    _, reason = parse_time(context.args[1:] if context.args else [])

    group = get_group(data, chat_id)

    if target.id not in group["banned"]:
        group["banned"].append(target.id)

    save_data(data)

    await update.effective_chat.ban_member(target.id)
    await do_log(context, data, chat_id, f"حظر: {target.first_name} | {reason}")
    await update.message.reply_text(f"🚫 تم حظر {target.first_name}\nالسبب: {reason}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص أو اكتب الآيدي.")

    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)

    if target.id in group["banned"]:
        group["banned"].remove(target.id)

    save_data(data)

    await update.effective_chat.unban_member(target.id)
    await update.message.reply_text(f"✅ رُفع الحظر عن {target.first_name}")


async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    if not can_manage(data, update.effective_chat.id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تطرد هذا الشخص.")

    await update.effective_chat.ban_member(target.id)
    await update.effective_chat.unban_member(target.id)

    await do_log(context, data, update.effective_chat.id, f"طرد: {target.first_name}")
    await update.message.reply_text(f"👢 تم طرد {target.first_name}")


async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id

    if not can_manage(data, chat_id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تكتم هذا الشخص.")

    until, reason = parse_time(context.args[1:] if context.args else [])

    group = get_group(data, chat_id)

    if target.id not in group["muted"]:
        group["muted"].append(target.id)

    save_data(data)

    await update.effective_chat.restrict_member(
        target.id,
        ChatPermissions(can_send_messages=False),
        until_date=until,
    )

    time_txt = " مؤقت" if until else ""

    await do_log(context, data, chat_id, f"كتم: {target.first_name}{time_txt} | {reason}")
    await update.message.reply_text(f"🔇 تم كتم {target.first_name}{time_txt}\nالسبب: {reason}")


async def cmd_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)

    if target.id in group["muted"]:
        group["muted"].remove(target.id)

    save_data(data)

    await update.effective_chat.restrict_member(target.id, full_permissions())
    await update.message.reply_text(f"🔊 رُفع الكتم عن {target.first_name}")


async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id

    if not can_manage(data, chat_id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تنذر هذا الشخص.")

    _, reason = parse_time(context.args[1:] if context.args else [])

    group = get_group(data, chat_id)
    uid = str(target.id)

    group["warnings"][uid] = group["warnings"].get(uid, 0) + 1
    warns = group["warnings"][uid]

    save_data(data)

    await do_log(context, data, chat_id, f"إنذار: {target.first_name} ({warns}/3) | {reason}")

    if warns >= 3:
        await update.effective_chat.ban_member(target.id)
        await update.message.reply_text(f"⚠️ {target.first_name} وصل 3 إنذارات — تم الحظر 🚫")
    else:
        await update.message.reply_text(
            f"⚠️ إنذار لـ {target.first_name}\nالإنذارات: {warns}/3\nالسبب: {reason}"
        )


async def cmd_unwarn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    group = get_group(data, update.effective_chat.id)
    uid = str(target.id)

    if group["warnings"].get(uid, 0) > 0:
        group["warnings"][uid] -= 1

    save_data(data)

    await update.message.reply_text(
        f"✅ تم إلغاء إنذار من {target.first_name}\n"
        f"الإنذارات: {group['warnings'].get(uid, 0)}/3"
    )


async def cmd_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    target = await get_target(update, context) or update.effective_user
    group = get_group(data, update.effective_chat.id)
    warns = group["warnings"].get(str(target.id), 0)

    await update.message.reply_text(f"⚠️ إنذارات {target.first_name}: {warns}/3")


async def cmd_restrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id

    if not can_manage(data, chat_id, update.effective_user.id, target.id):
        return await update.message.reply_text("❌ ما تقدر تقيّد هذا الشخص.")

    until, _ = parse_time(context.args[1:] if context.args else [])

    group = get_group(data, chat_id)

    if target.id not in group["restricted"]:
        group["restricted"].append(target.id)

    save_data(data)

    perms = ChatPermissions(
        can_send_messages=True,
        can_send_photos=False,
        can_send_videos=False,
        can_send_documents=False,
        can_send_audios=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )

    await update.effective_chat.restrict_member(target.id, perms, until_date=until)
    await do_log(context, data, chat_id, f"تقييد: {target.first_name}")
    await update.message.reply_text(f"🔒 تم تقييد {target.first_name}")


async def cmd_unrestrict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    target = await get_target(update, context)

    if not target:
        return await update.message.reply_text("رد على شخص.")

    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)

    if target.id in group["restricted"]:
        group["restricted"].remove(target.id)

    save_data(data)

    await update.effective_chat.restrict_member(target.id, full_permissions())
    await update.message.reply_text(f"🔓 رُفعت القيود عن {target.first_name}")


async def cmd_kick_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تنبيه: Telegram Bot API لا يسمح بجلب كل الأعضاء. أرسل البوتات يدويًا أو استخدم الطرد بالرد.")


async def cmd_kick_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تنبيه: Telegram Bot API لا يسمح بجلب كل الحسابات المحذوفة.")


async def cmd_detect_bots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("تنبيه: Telegram Bot API لا يسمح بجلب قائمة كل البوتات في المجموعة.")


# =====================
# Lock System
# =====================

LOCK_TYPES = {
    "sticker": "ملصقات",
    "gif": "صور متحركة",
    "media": "ميديا",
    "link": "روابط",
    "forward": "تحويل",
    "bot": "بوتات",
    "poll": "استطلاعات",
    "all": "كل شيء",
}


async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not context.args:
        return await update.message.reply_text(
            "أنواع القفل:\n" + "\n".join([f"• {k} — {v}" for k, v in LOCK_TYPES.items()])
        )

    lt = context.args[0].lower()

    if lt not in LOCK_TYPES:
        return await update.message.reply_text("نوع غير معروف.")

    group = get_group(data, update.effective_chat.id)

    if lt == "all":
        group["lock_types"] = list(LOCK_TYPES.keys())
    elif lt not in group["lock_types"]:
        group["lock_types"].append(lt)

    save_data(data)

    await update.message.reply_text(f"🔒 تم قفل: {LOCK_TYPES[lt]}")


async def cmd_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not context.args:
        return await update.message.reply_text("حدد النوع.")

    lt = context.args[0].lower()
    group = get_group(data, update.effective_chat.id)

    if lt == "all":
        group["lock_types"] = []
    elif lt in group["lock_types"]:
        group["lock_types"].remove(lt)

    save_data(data)

    await update.message.reply_text(f"🔓 تم فتح: {LOCK_TYPES.get(lt, lt)}")


async def cmd_locks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    group = get_group(data, update.effective_chat.id)
    locks = group.get("lock_types", [])

    if not locks:
        return await update.message.reply_text("لا يوجد أقفال.")

    await update.message.reply_text(
        "🔒 الأقفال:\n" + "\n".join([f"• {LOCK_TYPES.get(l, l)}" for l in locks])
    )


# =====================
# Filters
# =====================

async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام: /فلتر [كلمة] [الرد]")

    keyword = context.args[0].lower()
    response = " ".join(context.args[1:])

    get_group(data, update.effective_chat.id)["filters"][keyword] = response
    save_data(data)

    await update.message.reply_text(f"✅ فلتر: {keyword}")


async def cmd_stop_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not context.args:
        return await update.message.reply_text("حدد الكلمة.")

    keyword = context.args[0].lower()
    group = get_group(data, update.effective_chat.id)

    if keyword in group["filters"]:
        del group["filters"][keyword]
        save_data(data)
        await update.message.reply_text(f"✅ تم حذف فلتر: {keyword}")
    else:
        await update.message.reply_text("الفلتر غير موجود.")


async def cmd_list_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    filters_d = get_group(data, update.effective_chat.id).get("filters", {})

    if not filters_d:
        return await update.message.reply_text("لا يوجد فلاتر.")

    await update.message.reply_text("📋 الفلاتر:\n" + "\n".join([f"• {k}" for k in filters_d]))


async def cmd_clear_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["filters"] = {}
    save_data(data)

    await update.message.reply_text("✅ تم مسح الفلاتر.")


# =====================
# Notes
# =====================

async def cmd_save_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام: /حفظ [اسم] [المحتوى]")

    name = context.args[0].lower()
    content = " ".join(context.args[1:])

    get_group(data, update.effective_chat.id)["notes"][name] = content
    save_data(data)

    await update.message.reply_text(f"✅ تم حفظ: {name}")


async def cmd_get_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not context.args:
        return await update.message.reply_text("حدد اسم الملاحظة.")

    name = context.args[0].lower()
    note = get_group(data, update.effective_chat.id)["notes"].get(name)

    await update.message.reply_text(f"📝 {name}:\n{note}" if note else "الملاحظة غير موجودة.")


async def cmd_list_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    notes = get_group(data, update.effective_chat.id).get("notes", {})

    if not notes:
        return await update.message.reply_text("لا يوجد ملاحظات.")

    await update.message.reply_text("📋 الملاحظات:\n" + "\n".join([f"• {k}" for k in notes]))


async def cmd_delete_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not context.args:
        return await update.message.reply_text("حدد اسم الملاحظة.")

    name = context.args[0].lower()
    group = get_group(data, update.effective_chat.id)

    if name in group["notes"]:
        del group["notes"][name]
        save_data(data)
        await update.message.reply_text(f"✅ تم حذف: {name}")
    else:
        await update.message.reply_text("الملاحظة غير موجودة.")


# =====================
# Protection
# =====================

async def cmd_antiflood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not context.args:
        return await update.message.reply_text("الاستخدام: /antiflood [عدد] أو off")

    group = get_group(data, update.effective_chat.id)

    if context.args[0].lower() == "off":
        group["anti_flood"] = 0
        save_data(data)
        return await update.message.reply_text("✅ تم إيقاف حماية الفلود.")

    try:
        group["anti_flood"] = int(context.args[0])
        group["anti_flood_action"] = context.args[1].lower() if len(context.args) > 1 else "mute"
        save_data(data)

        await update.message.reply_text(
            f"✅ حماية الفلود: {group['anti_flood']} رسالة | الإجراء: {group['anti_flood_action']}"
        )
    except Exception:
        await update.message.reply_text("رقم غير صحيح.")


async def cmd_antilink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    group = get_group(data, update.effective_chat.id)
    state = (context.args[0].lower() if context.args else "on") != "off"
    group["anti_link"] = state

    save_data(data)

    await update.message.reply_text(f"🔗 حماية الروابط: {'✅' if state else '❌'}")


async def cmd_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    group = get_group(data, update.effective_chat.id)
    state = (context.args[0].lower() if context.args else "on") != "off"
    group["captcha"] = state

    save_data(data)

    await update.message.reply_text(f"🔐 الكابتشا: {'✅' if state else '❌'}")


async def cmd_setlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "owner"):
        return

    if not context.args:
        return await update.message.reply_text("حدد معرف القناة.")

    get_group(data, update.effective_chat.id)["log_channel"] = context.args[0]
    save_data(data)

    await update.message.reply_text(f"✅ قناة السجل: {context.args[0]}")


# =====================
# Delete Commands
# =====================

async def cmd_clear_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if not update.message.reply_to_message:
        return await update.message.reply_text("رد على الرسالة.")

    try:
        await update.message.reply_to_message.delete()
        await update.message.delete()
    except Exception:
        await update.message.reply_text("❌ ما قدرت أمسح.")


async def cmd_clear_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    await update.message.reply_text("هذه الميزة تحتاج صلاحيات حذف رسائل، واستخدم /مسح_بالرد للرسائل المحددة.")


async def cmd_clear_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["custom_commands"] = {}
    save_data(data)

    await update.message.reply_text("✅ تم مسح الأوامر.")


async def cmd_clear_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["welcome"] = ""
    save_data(data)

    await update.message.reply_text("✅ تم مسح الترحيب.")


async def cmd_clear_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["rules"] = ""
    save_data(data)

    await update.message.reply_text("✅ تم مسح القوانين.")


async def cmd_clear_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["link"] = ""
    save_data(data)

    await update.message.reply_text("✅ تم مسح الرابط.")


async def cmd_clear_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["banned"] = []
    save_data(data)

    await update.message.reply_text("✅ تم مسح قائمة المحظورين.")


async def cmd_clear_muted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    get_group(data, update.effective_chat.id)["muted"] = []
    save_data(data)

    await update.message.reply_text("✅ تم مسح قائمة المكتومين.")


def make_clear_rank_cmd(rank_name):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = load_data()

        if not await require(update, data, "owner"):
            return

        group = get_group(data, update.effective_chat.id)
        group["ranks"] = {
            k: v for k, v in group["ranks"].items() if v != rank_name
        }

        save_data(data)

        await update.message.reply_text(f"✅ تم مسح {RANK_NAMES.get(rank_name, rank_name)}")

    return handler


# =====================
# View Commands
# =====================

async def cmd_show_rank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    target = await get_target(update, context) or update.effective_user
    rank = get_rank(data, update.effective_chat.id, target.id)

    await update.message.reply_text(f"👤 {target.first_name}\nالرتبة: {RANK_NAMES[rank]}")


async def cmd_show_ranks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)

    text = "📋 الرتب:\n\n"

    for rk in RANK_ORDER[2:]:
        members = [uid for uid, r in group["ranks"].items() if r == rk]

        if members:
            text += f"{RANK_NAMES[rk]}:\n"

            for uid in members:
                try:
                    m = await update.effective_chat.get_member(int(uid))
                    text += f"  • {m.user.first_name}\n"
                except Exception:
                    text += f"  • {uid}\n"

            text += "\n"

    await update.message.reply_text(text)


def make_show_rank_cmd(rank_name, title, emoji):
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        data = load_data()
        group = get_group(data, update.effective_chat.id)
        members = [uid for uid, r in group["ranks"].items() if r == rank_name]

        if not members:
            return await update.message.reply_text(f"لا يوجد {title}.")

        text = f"{emoji} {title}:\n"

        for uid in members:
            try:
                m = await update.effective_chat.get_member(int(uid))
                text += f"• {m.user.first_name}\n"
            except Exception:
                text += f"• {uid}\n"

        await update.message.reply_text(text)

    return handler


async def cmd_myinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    user = update.effective_user
    chat_id = update.effective_chat.id
    rank = get_rank(data, chat_id, user.id)
    warns = get_group(data, chat_id)["warnings"].get(str(user.id), 0)

    await update.message.reply_text(
        f"👤 معلوماتك:\n\n"
        f"الاسم: {user.first_name}\n"
        f"الآيدي: {user.id}\n"
        f"الرتبة: {RANK_NAMES[rank]}\n"
        f"الإنذارات: {warns}/3"
    )


async def cmd_show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    group = get_group(data, update.effective_chat.id)

    await update.message.reply_text(
        f"⚙️ الإعدادات:\n\n"
        f"Anti-Flood: {group.get('anti_flood', 0)} رسالة\n"
        f"حماية الروابط: {'✅' if group.get('anti_link') else '❌'}\n"
        f"كابتشا: {'✅' if group.get('captcha') else '❌'}\n"
        f"ترحيب: {'✅' if group.get('welcome') else '❌'}\n"
        f"قوانين: {'✅' if group.get('rules') else '❌'}\n"
        f"قناة السجل: {group.get('log_channel', 'غير محددة')}"
    )


async def cmd_show_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    count = await chat.get_member_count()

    await update.message.reply_text(
        f"📊 المجموعة:\n\n"
        f"الاسم: {chat.title}\n"
        f"الآيدي: {chat.id}\n"
        f"الأعضاء: {count}\n"
        f"النوع: {chat.type}"
    )


async def cmd_show_protection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    group = get_group(data, update.effective_chat.id)

    await update.message.reply_text(
        f"🛡 الحماية:\n\n"
        f"حماية الروابط: {'✅' if group.get('anti_link') else '❌'}\n"
        f"Anti-Flood: {'✅' if group.get('anti_flood', 0) > 0 else '❌'}\n"
        f"كابتشا: {'✅' if group.get('captcha') else '❌'}\n"
        f"أقفال: {', '.join(group.get('lock_types', [])) or 'لا يوجد'}"
    )


async def cmd_show_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    group = get_group(data, update.effective_chat.id)

    if not group["banned"]:
        return await update.message.reply_text("لا يوجد محظورين.")

    await update.message.reply_text(
        "🚫 المحظورين:\n" + "\n".join([f"• {uid}" for uid in group["banned"]])
    )


async def cmd_show_muted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    group = get_group(data, update.effective_chat.id)

    if not group["muted"]:
        return await update.message.reply_text("لا يوجد مكتومين.")

    await update.message.reply_text(
        "🔇 المكتومين:\n" + "\n".join([f"• {uid}" for uid in group["muted"]])
    )


async def cmd_show_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    rules = get_group(data, update.effective_chat.id).get("rules", "")

    await update.message.reply_text(f"📜 القوانين:\n{rules}" if rules else "لم تُحدد قوانين.")


async def cmd_show_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    link = get_group(data, update.effective_chat.id).get("link", "")

    await update.message.reply_text(f"🔗 الرابط:\n{link}" if link else "لم يُحدد رابط.")


# =====================
# Settings Commands
# =====================

async def cmd_set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    text = " ".join(context.args)

    if not text:
        return await update.message.reply_text("اكتب نص الترحيب.\nيمكن استخدام {name} لاسم العضو.")

    get_group(data, update.effective_chat.id)["welcome"] = text
    save_data(data)

    await update.message.reply_text("✅ تم تعيين رسالة الترحيب.")


async def cmd_set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    text = " ".join(context.args)

    if not text:
        return await update.message.reply_text("اكتب القوانين.")

    get_group(data, update.effective_chat.id)["rules"] = text
    save_data(data)

    await update.message.reply_text("✅ تم تعيين القوانين.")


async def cmd_set_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    text = " ".join(context.args)

    if not text:
        return await update.message.reply_text("اكتب الرابط.")

    get_group(data, update.effective_chat.id)["link"] = text
    save_data(data)

    await update.message.reply_text("✅ تم تعيين الرابط.")


async def cmd_create_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    try:
        link = await update.effective_chat.create_invite_link()
        await update.message.reply_text(f"🔗 الرابط:\n{link.invite_link}")
    except Exception:
        await update.message.reply_text("❌ ما قدرت أنشئ رابط.")


async def cmd_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "admin"):
        return

    if len(context.args) < 2:
        return await update.message.reply_text("الاستخدام: /اضف_امر [الأمر] [الرد]")

    cmd = context.args[0].lower()
    response = " ".join(context.args[1:])

    get_group(data, update.effective_chat.id)["custom_commands"][cmd] = response
    save_data(data)

    await update.message.reply_text(f"✅ تم إضافة: /{cmd}")


async def cmd_set_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()

    if not await require(update, data, "manager"):
        return

    target = await get_target(update, context) or update.effective_user

    await update.message.reply_text(f"🆔 آيدي {target.first_name}: `{target.id}`", parse_mode="Markdown")


# =====================
# Callback Handler
# =====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_cb = query.data

    if data_cb == "buy_pack":
        await context.bot.send_invoice(
            chat_id=query.message.chat.id,
            title="باقة إزالة الخلفية",
            description="3 استخدامات",
            payload="removebg_pack_3",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("3 استخدامات", 5)],
        )

    elif data_cb.startswith("captcha_"):
        parts = data_cb.split("_")
        user_id = int(parts[1])
        answer = int(parts[2])
        correct = int(parts[3])
        chat_id = query.message.chat.id

        if query.from_user.id != user_id:
            return await query.answer("هذا ليس لك!", show_alert=True)

        if answer == correct:
            data = load_data()
            group = get_group(data, chat_id)
            group["captcha_pending"].pop(str(user_id), None)
            save_data(data)

            try:
                await context.bot.restrict_chat_member(chat_id, user_id, full_permissions())
            except Exception:
                pass

            await query.message.edit_text(f"✅ تم التحقق! مرحباً {query.from_user.first_name} 👁️")

        else:
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
            except Exception:
                pass

            await query.message.edit_text("❌ إجابة خاطئة — تم الطرد.")


# =====================
# Welcome
# =====================

async def welcome_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    chat_id = update.effective_chat.id
    group = get_group(data, chat_id)

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        if group.get("captcha"):
            a = random.randint(1, 10)
            b = random.randint(1, 10)
            correct = a + b
            options = list({correct, correct + random.randint(1, 5), abs(correct - random.randint(1, 4))})

            while len(options) < 3:
                options.append(correct + random.randint(6, 10))

            random.shuffle(options)

            keyboard = [
                [
                    InlineKeyboardButton(
                        str(o),
                        callback_data=f"captcha_{member.id}_{o}_{correct}",
                    )
                    for o in options[:3]
                ]
            ]

            try:
                await update.effective_chat.restrict_member(
                    member.id,
                    ChatPermissions(can_send_messages=False),
                )
            except Exception:
                pass

            await update.message.reply_text(
                f"👋 مرحباً {member.first_name}!\nللتحقق احسب: {a} + {b} = ؟",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )

        elif group.get("welcome"):
            text = group["welcome"].replace("{name}", member.first_name)
            buttons = group.get("welcome_buttons", [])

            markup = (
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton(b["text"], url=b["url"])] for b in buttons]
                )
                if buttons
                else None
            )

            await update.message.reply_text(text, reply_markup=markup)


# =====================
# Main Message Handler
# =====================

async def main_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    data = load_data()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    msg = update.message
    group = get_group(data, chat_id)
    rank_level = get_level(data, chat_id, user_id)

    if rank_level < RANK_LEVELS["vip"]:
        lock_types = group.get("lock_types", [])

        if msg.sticker and "sticker" in lock_types:
            try:
                await msg.delete()
            except Exception:
                pass
            return

        if msg.animation and "gif" in lock_types:
            try:
                await msg.delete()
            except Exception:
                pass
            return

        if (msg.photo or msg.video or msg.document) and "media" in lock_types:
            try:
                await msg.delete()
            except Exception:
                pass
            return

        if getattr(msg, "forward_origin", None) and "forward" in lock_types:
            try:
                await msg.delete()
            except Exception:
                pass
            return

        if msg.poll and "poll" in lock_types:
            try:
                await msg.delete()
            except Exception:
                pass
            return

        if msg.text and group.get("anti_link"):
            if re.search(r"(https?://|t\.me/)", msg.text):
                try:
                    await msg.delete()
                    sent = await update.effective_chat.send_message(
                        f"⚠️ {update.effective_user.first_name}، الروابط ممنوعة."
                    )
                    await asyncio.sleep(5)
                    await sent.delete()
                except Exception:
                    pass
                return

    flood_limit = group.get("anti_flood", 0)

    if flood_limit > 0 and rank_level < RANK_LEVELS["admin"]:
        key = f"{chat_id}_{user_id}"
        now = time.time()

        flood_tracker[key] = [
            t for t in flood_tracker.get(key, []) if now - t < 10
        ]

        flood_tracker[key].append(now)

        if len(flood_tracker[key]) > flood_limit:
            flood_tracker[key] = []
            action = group.get("anti_flood_action", "mute")

            try:
                if action == "ban":
                    await update.effective_chat.ban_member(user_id)
                    await update.effective_chat.send_message(
                        f"🚫 {update.effective_user.first_name} تم حظره بسبب الفلود."
                    )

                elif action == "kick":
                    await update.effective_chat.ban_member(user_id)
                    await update.effective_chat.unban_member(user_id)
                    await update.effective_chat.send_message(
                        f"👢 {update.effective_user.first_name} تم طرده بسبب الفلود."
                    )

                else:
                    await update.effective_chat.restrict_member(
                        user_id,
                        ChatPermissions(can_send_messages=False),
                        until_date=datetime.now() + timedelta(minutes=5),
                    )
                    await update.effective_chat.send_message(
                        f"🔇 {update.effective_user.first_name} تم كتمه بسبب الفلود."
                    )
            except Exception:
                pass

            return

    if msg.text:
        text_lower = msg.text.lower()

        for keyword, response in group.get("filters", {}).items():
            if keyword in text_lower:
                await msg.reply_text(response)
                break

        if msg.text.startswith("/"):
            cmd = msg.text.lstrip("/").split()[0].lower()

            if cmd in group.get("custom_commands", {}):
                await msg.reply_text(group["custom_commands"][cmd])

    if msg.text and await privacy_reply(update):
        return

    if update.effective_chat.type == "private" and msg.text and not msg.text.startswith("/"):
        if not client:
            return await msg.reply_text("مفتاح OpenAI غير مضبوط.")

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "اسمك عتب. صديق دردشة ذكي وهادئ وغامض. "
                            "تتكلم بالعربي بلهجة سعودية خفيفة. ردودك قصيرة وذكية."
                        ),
                    },
                    {"role": "user", "content": msg.text},
                ],
            )

            await msg.reply_text(response.choices[0].message.content)

        except Exception as e:
            print("Chat error:", e)


# =====================
# Flask
# =====================

web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "Bot is running! 👁️"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False,
    )


# =====================
# Run Bot
# =====================

def run_bot():
    if not TOKEN:
        raise ValueError("TOKEN is missing")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("removebg", remove_bg))
    app.add_handler(CommandHandler("image", image_cmd))
    app.add_handler(CommandHandler("shop", shop_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("profit", profit_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("ref", ref_cmd))
    app.add_handler(CommandHandler("myrefs", myrefs_cmd))

    app.add_handler(CommandHandler("رتبة", cmd_show_rank))
    app.add_handler(CommandHandler("الرتب", cmd_show_ranks))
    app.add_handler(CommandHandler("تعيين_رتبة", cmd_setrank))
    app.add_handler(CommandHandler("رفع", cmd_promote))
    app.add_handler(CommandHandler("تنزيل", cmd_demote))
    app.add_handler(CommandHandler("تنزيل_الكل", cmd_demote_all))
    app.add_handler(CommandHandler("رفع_مالك_اساسي", make_setrank_cmd("primary_owner")))
    app.add_handler(CommandHandler("رفع_مالك", make_setrank_cmd("owner")))
    app.add_handler(CommandHandler("تنزيل_مالك", make_setrank_cmd("member")))
    app.add_handler(CommandHandler("رفع_منشئ", make_setrank_cmd("creator")))
    app.add_handler(CommandHandler("رفع_مدير", make_setrank_cmd("manager")))
    app.add_handler(CommandHandler("رفع_ادمن", make_setrank_cmd("admin")))
    app.add_handler(CommandHandler("رفع_مميز", make_setrank_cmd("vip")))

    app.add_handler(CommandHandler("حظر", cmd_ban))
    app.add_handler(CommandHandler("الغاء_الحظر", cmd_unban))
    app.add_handler(CommandHandler("طرد", cmd_kick))
    app.add_handler(CommandHandler("كتم", cmd_mute))
    app.add_handler(CommandHandler("الغاء_الكتم", cmd_unmute))
    app.add_handler(CommandHandler("تقييد", cmd_restrict))
    app.add_handler(CommandHandler("فك_التقييد", cmd_unrestrict))
    app.add_handler(CommandHandler("انذار", cmd_warn))
    app.add_handler(CommandHandler("الغاء_انذار", cmd_unwarn))
    app.add_handler(CommandHandler("الانذارات", cmd_warns))
    app.add_handler(CommandHandler("طرد_البوتات", cmd_kick_bots))
    app.add_handler(CommandHandler("طرد_المحذوفين", cmd_kick_deleted))
    app.add_handler(CommandHandler("كشف_البوتات", cmd_detect_bots))

    app.add_handler(CommandHandler("قفل", cmd_lock))
    app.add_handler(CommandHandler("فتح", cmd_unlock))
    app.add_handler(CommandHandler("الاقفال", cmd_locks))

    app.add_handler(CommandHandler("فلتر", cmd_filter))
    app.add_handler(CommandHandler("حذف_فلتر", cmd_stop_filter))
    app.add_handler(CommandHandler("الفلاتر", cmd_list_filters))
    app.add_handler(CommandHandler("مسح_الفلاتر", cmd_clear_filters))

    app.add_handler(CommandHandler("حفظ", cmd_save_note))
    app.add_handler(CommandHandler("ملاحظة", cmd_get_note))
    app.add_handler(CommandHandler("الملاحظات", cmd_list_notes))
    app.add_handler(CommandHandler("حذف_ملاحظة", cmd_delete_note))

    app.add_handler(CommandHandler("antiflood", cmd_antiflood))
    app.add_handler(CommandHandler("حماية_الروابط", cmd_antilink))
    app.add_handler(CommandHandler("كابتشا", cmd_captcha))
    app.add_handler(CommandHandler("قناة_السجل", cmd_setlog))

    app.add_handler(CommandHandler("مسح_بالرد", cmd_clear_reply))
    app.add_handler(CommandHandler("مسح", cmd_clear_count))
    app.add_handler(CommandHandler("مسح_الاوامر", cmd_clear_commands))
    app.add_handler(CommandHandler("مسح_الترحيب", cmd_clear_welcome))
    app.add_handler(CommandHandler("مسح_القوانين", cmd_clear_rules))
    app.add_handler(CommandHandler("مسح_الرابط", cmd_clear_link))
    app.add_handler(CommandHandler("مسح_المحظورين", cmd_clear_banned))
    app.add_handler(CommandHandler("مسح_المكتومين", cmd_clear_muted))
    app.add_handler(CommandHandler("مسح_المميزين", make_clear_rank_cmd("vip")))
    app.add_handler(CommandHandler("مسح_الادمنية", make_clear_rank_cmd("admin")))
    app.add_handler(CommandHandler("مسح_المدراء", make_clear_rank_cmd("manager")))
    app.add_handler(CommandHandler("مسح_المنشئين", make_clear_rank_cmd("creator")))
    app.add_handler(CommandHandler("مسح_المالكين", make_clear_rank_cmd("owner")))

    app.add_handler(CommandHandler("المالكين", make_show_rank_cmd("owner", "المالكين", "🔱")))
    app.add_handler(CommandHandler("المالكين_الاساسيين", make_show_rank_cmd("primary_owner", "المالكين الأساسيين", "👑")))
    app.add_handler(CommandHandler("المنشئين", make_show_rank_cmd("creator", "المنشئين", "🌟")))
    app.add_handler(CommandHandler("الادمنية", make_show_rank_cmd("admin", "الأدمنية", "🛡")))
    app.add_handler(CommandHandler("المدراء", make_show_rank_cmd("manager", "المدراء", "⚙️")))
    app.add_handler(CommandHandler("المميزين", make_show_rank_cmd("vip", "المميزين", "💎")))
    app.add_handler(CommandHandler("المحظورين", cmd_show_banned))
    app.add_handler(CommandHandler("المكتومين", cmd_show_muted))
    app.add_handler(CommandHandler("القوانين", cmd_show_rules))
    app.add_handler(CommandHandler("الرابط", cmd_show_link))
    app.add_handler(CommandHandler("معلوماتي", cmd_myinfo))
    app.add_handler(CommandHandler("الاعدادات", cmd_show_settings))
    app.add_handler(CommandHandler("المجموعة", cmd_show_group))
    app.add_handler(CommandHandler("الحماية", cmd_show_protection))

    app.add_handler(CommandHandler("ضع_ترحيب", cmd_set_welcome))
    app.add_handler(CommandHandler("ضع_قوانين", cmd_set_rules))
    app.add_handler(CommandHandler("ضع_رابط", cmd_set_link))
    app.add_handler(CommandHandler("انشاء_رابط", cmd_create_link))
    app.add_handler(CommandHandler("اضف_امر", cmd_add_command))
    app.add_handler(CommandHandler("تعيين_الايدي", cmd_set_id))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_handler))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO, log_everything), group=0)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, main_message_handler), group=1)

    print("Atab bot is running 👁️")
    app.run_polling(drop_pending_updates=True, stop_signals=None)


# =====================
# Entry Point
# =====================

if _name_ == "_main_":
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()