import html
import time
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
import config

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None) -> None:
    keyboard = [
        [
            InlineKeyboardButton("🎮 Direct Mods", callback_data="info_direct"),
            InlineKeyboardButton("🚫 No Getpin", callback_data="info_getpin")
        ],
        [
            InlineKeyboardButton("🔥 Faphouse Paid", callback_data="info_faphouse")
        ],
        [
            InlineKeyboardButton("📦 My Orders", callback_data="my_orders"),
            InlineKeyboardButton("📞 Report/Contact", callback_data="rc_menu")
        ]
    ]
    # If admin, show Settings button
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if await db.is_admin(user_id):
        keyboard.append([InlineKeyboardButton("⚙️ Admin Settings", callback_data="settings_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "👑 <b>Welcome to Elite Premium Store!</b>\n\n"
        "Select a premium plan below to get instant access:\n\n"
        "🎮 <b>Direct Mods</b> - Premium Apps\n"
        "🚫 <b>No Getpin</b> - Remove getpin easily\n"
        "🔥 <b>Faphouse Paid</b> - Exclusive content\n\n"
        "Use /plan or /pay to see pricing and purchase premium VIP access."
    )
    
    chat_id = update.effective_chat.id
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user = update.effective_user
    await db.upsert_user(user.id, user.first_name, user.username)
    
    # Check ban
    if await db.is_bot_banned(user.id):
        return

    await show_main_menu(update, context)

async def plan_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if await db.is_bot_banned(user_id):
        return

    # Build the plans list
    keyboard = []
    text = "💎 <b>Elite Premium Store VIP Plans</b> 💎\n\nSelect a VIP plan to upgrade:\n\n"
    
    for plan_key, plan in config.PAY_PLANS.items():
        text += f"• <b>{plan['label']}</b>:\n"
        text += f"  💰 Price: ₹{plan['amount']} / {plan['stars']} Stars ⭐\n\n"
        
        # Add buttons for each plan
        keyboard.append([InlineKeyboardButton(f"✅ {plan['label']} (₹{plan['amount']})", callback_data=f"payplan:{plan_key}")])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def make_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if user_id != config.OWNER_ID:
        return
        
    if not context.args:
        await update.message.reply_text("Format: /makeadmin <user_id>")
        return
        
    try:
        new_admin = int(context.args[0])
        await db.add_admin(new_admin, added_by=user_id)
        await update.message.reply_html(f"✅ User <code>{new_admin}</code> is now admin!")
        try:
            await context.bot.send_message(chat_id=new_admin, text="🎉 You have been granted admin access!")
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("❌ Invalid User ID.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error occurred: {e}")

async def details(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if not await db.is_admin(user_id):
        return
        
    if not context.args:
        await update.message.reply_text("Format: /details 24h | 7d | 30d")
        return
        
    time_str = context.args[0]
    if time_str == '24h':
        delta = datetime.timedelta(hours=24)
    elif time_str == '7d':
        delta = datetime.timedelta(days=7)
    elif time_str == '30d':
        delta = datetime.timedelta(days=30)
    else:
        await update.message.reply_text("Invalid time frame. Use 24h, 7d, or 30d")
        return
        
    now = int(time.time())
    since = now - int(delta.total_seconds())
    
    payments = await db.list_processed_payment_requests(since, now)
    
    if not payments:
        await update.message.reply_text("No processed payments found for this period.")
        return
        
    text = f"📊 <b>Payment Details ({html.escape(time_str)})</b>\n\n"
    total = 0
    for p in payments:
        proc_date = datetime.datetime.utcfromtimestamp(p['processed_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
        plan = config.PAY_PLANS.get(p['plan_key'], {"label": p['plan_key']})
        plan_name = plan.get("label", p['plan_key'])
        text += f"• <code>{proc_date}</code> | User: <code>{p['user_id']}</code> | ₹{p['amount_rs']} | {html.escape(plan_name)}\n"
        total += p['amount_rs']
        
    text += f"\n<b>Total Earnings: ₹{total}</b>"
    await update.message.reply_html(text)

async def pay_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if not await db.is_admin(user_id):
        return
        
    if not context.args:
        await update.message.reply_text("Usage: /paylookup <order_id>")
        return
        
    try:
        rid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid order ID.")
        return
        
    req = await db.get_payment_request(rid)
    if not req:
        await update.message.reply_text("❌ Order not found.")
        return
        
    created_str = datetime.datetime.utcfromtimestamp(req['created_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
    updated_str = datetime.datetime.utcfromtimestamp(req['updated_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    text = (
        f"🔍 <b>Payment Request #{rid} Details:</b>\n\n"
        f"👤 <b>User:</b> <code>{req['user_id']}</code>\n"
        f"🎟 <b>Plan Key:</b> <code>{req['plan_key']}</code> ({req['plan_days']} days)\n"
        f"💰 <b>Amount:</b> ₹{req['amount_rs']}\n"
        f"⚙️ <b>Status:</b> <code>{req['status'].upper()}</code>\n"
        f"📝 <b>UTR submitted:</b> <code>{req['utr_text'] or 'None'}</code>\n"
        f"📅 <b>Created At:</b> <code>{created_str}</code>\n"
        f"📅 <b>Updated At:</b> <code>{updated_str}</code>\n"
    )
    if req['processed_by']:
        text += f"👮 <b>Processed By:</b> <code>{req['processed_by']}</code>\n"
    await update.message.reply_html(text)

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if not await db.is_admin(user_id):
        return
        
    gateway = await db.get_setting("payment_gateway") or "manual"
    upi = await db.get_setting("pay_upi") or "None"
    payee_name = await db.get_setting("pay_name") or "None"
    
    keyboard = [
        [
            InlineKeyboardButton(f"Manual UPI {'✅' if gateway == 'manual' else ''}", callback_data="settings_gw:manual"),
            InlineKeyboardButton(f"Razorpay {'✅' if gateway == 'razorpay' else ''}", callback_data="settings_gw:razorpay"),
            InlineKeyboardButton(f"Stars {'✅' if gateway == 'stars' else ''}", callback_data="settings_gw:stars")
        ],
        [
            InlineKeyboardButton("Change UPI ID", callback_data="settings_field:pay_upi"),
            InlineKeyboardButton("Change Merchant Name", callback_data="settings_field:pay_name")
        ],
        [
            InlineKeyboardButton("Change Manual Text", callback_data="settings_field:pay_text")
        ],
        [
            InlineKeyboardButton("Change Razorpay Key ID", callback_data="settings_field:razorpay_key_id"),
            InlineKeyboardButton("Change Razorpay Secret", callback_data="settings_field:razorpay_key_secret")
        ],
        [
            InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "⚙️ <b>Admin Config Dashboard</b>\n\n"
        f"🔌 <b>Active Gateway:</b> <code>{gateway.upper()}</code>\n"
        f"💳 <b>UPI ID:</b> <code>{upi}</code>\n"
        f"👤 <b>Payee Name:</b> <code>{payee_name}</code>\n\n"
        "Select an option below to update settings:"
    )
    
    chat_id = update.effective_chat.id
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception:
            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await settings_menu(update, context)
