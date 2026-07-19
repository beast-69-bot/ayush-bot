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
            InlineKeyboardButton("🔥 Faphouse Paid", callback_data="info_faphouse"),
            InlineKeyboardButton("❤️ Test Donation", callback_data="info_donation")
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
    
    plans = await db.get_active_plans()
    
    def _get_plan_limit_highlight(plan_key: str, plans: dict) -> str:
        p = plans.get(plan_key)
        if not p:
            return ""
        lim = p.get("limit")
        if lim is not None:
            sold = p.get("sold_count", 0)
            left = max(0, lim - sold)
            if left <= 0:
                return " 🔴 <b>(OUT OF STOCK)</b>"
            else:
                return f" 🔥 <b>(ONLY {left} LEFT!)</b>"
        return ""
        
    direct_highlight = _get_plan_limit_highlight("direct", plans)
    getpin_highlight = _get_plan_limit_highlight("getpin", plans)
    donation_highlight = _get_plan_limit_highlight("donation", plans)
    
    faphouse_highlights = []
    p1 = plans.get("faphouse_1", {})
    if p1.get("limit") is not None:
        left1 = max(0, p1["limit"] - p1.get("sold_count", 0))
        faphouse_highlights.append(f"1D: {left1} left" if left1 > 0 else "1D: OUT")
        
    p3 = plans.get("faphouse_3", {})
    if p3.get("limit") is not None:
        left3 = max(0, p3["limit"] - p3.get("sold_count", 0))
        faphouse_highlights.append(f"3D: {left3} left" if left3 > 0 else "3D: OUT")
        
    p7 = plans.get("faphouse_7", {})
    if p7.get("limit") is not None:
        left7 = max(0, p7["limit"] - p7.get("sold_count", 0))
        faphouse_highlights.append(f"7D: {left7} left" if left7 > 0 else "7D: OUT")
        
    faphouse_str = ""
    if faphouse_highlights:
        faphouse_str = " 🔥 <b>(" + ", ".join(faphouse_highlights) + ")</b>"

    text = (
        "👑 <b>Welcome to Elite Premium Store!</b>\n\n"
        "Select a premium plan below to get instant access:\n\n"
        f"🎮 <b>Direct Mods</b> - Premium Apps{direct_highlight}\n"
        f"🚫 <b>No Getpin</b> - Remove getpin easily{getpin_highlight}\n"
        f"🔥 <b>Faphouse Paid</b> - Exclusive content{faphouse_str}\n"
        f"❤️ <b>Test Donation</b> - Test payments{donation_highlight}\n\n"
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

async def plan_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if await db.is_bot_banned(user_id):
        return

    # Build the plans list
    keyboard = []
    text = "💎 <b>Elite Premium Store VIP Plans</b> 💎\n\nSelect a VIP plan to upgrade:\n\n"
    
    plans = await db.get_active_plans()
    for plan_key, plan in plans.items():
        is_maintenance = plan.get("status") == "maintenance"
        lim = plan.get("limit")
        is_out_of_stock = False
        stock_text = ""
        
        if lim is not None:
            left = max(0, lim - plan.get("sold_count", 0))
            if left <= 0:
                is_out_of_stock = True
                stock_text = " 🔴 <b>(OUT OF STOCK)</b>"
            else:
                stock_text = f" 🔥 <b>(Only {left} left!)</b>"
                
        status_text = ""
        if is_maintenance:
            status_text = " 🛠 <i>(Under Maintenance)</i>"
            
        text += f"• <b>{plan['label']}</b>:{status_text}{stock_text}\n"
        text += f"  💰 Price: ₹{plan['amount']} / {plan['stars']} Stars ⭐\n\n"
        
        # Add buttons for each plan
        btn_label = f"✅ {plan['label']} (₹{plan['amount']})"
        if is_maintenance:
            btn_label = f"🛠 {plan['label']} (Maintenance)"
        elif is_out_of_stock:
            btn_label = f"❌ {plan['label']} (Out of Stock)"
            
        keyboard.append([InlineKeyboardButton(btn_label, callback_data=f"payplan:{plan_key}")])
        
    # Add back to main menu button
    keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_main")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
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
    plans = await db.get_active_plans()
    total = 0
    for p in payments:
        proc_date = datetime.datetime.utcfromtimestamp(p['processed_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
        plan = plans.get(p['plan_key'], {"label": p['plan_key']})
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
            InlineKeyboardButton("🏷 Edit Plan Prices", callback_data="admin_edit_plans_menu")
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

async def admin_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if not await db.is_admin(user_id):
        return
        
    await show_admin_menu(update, context)

async def show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, message_id=None) -> None:
    keyboard = [
        [
            InlineKeyboardButton("⚙️ Config Settings", callback_data="settings_main"),
            InlineKeyboardButton("📢 Broadcast Message", callback_data="admin_broadcast_menu")
        ],
        [
            InlineKeyboardButton("📦 Orders Details", callback_data="admin_orders_menu"),
            InlineKeyboardButton("📊 Revenue Reports", callback_data="admin_revenue_menu")
        ],
        [
            InlineKeyboardButton("💸 Withdraw Razorpay", callback_data="admin_withdraw_menu"),
            InlineKeyboardButton("🔗 Group Connect", callback_data="admin_group_menu")
        ],
        [
            InlineKeyboardButton("❌ Close Menu", callback_data="admin_close")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (
        "👑 <b>Elite Premium Store - Admin Control Panel</b>\n\n"
        "Select an administrative action below:"
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

