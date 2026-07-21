import html
import time
import json
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes
from database import Database
import config
import razorpay_service
from handlers.commands import show_main_menu, settings_menu

# Helper to format UTC timestamps
def _format_utc(ts: int) -> str:
    return datetime.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S UTC") if int(ts) > 0 else "-"

# Helper to format manual payment notes
def _manual_payment_note(user_id: int, request_id: int, plan_days: int, projected_until: int) -> str:
    expiry_tag = datetime.datetime.utcfromtimestamp(int(projected_until)).strftime("%Y%m%d") if int(projected_until) > 0 else "NA"
    return f"VIP UID{int(user_id)} ORD{int(request_id)} P{int(plan_days)}D EXP{expiry_tag}"

# Helper to format UPI URI
def _upi_uri(upi_id: str, amount_rs: int, payee_name: str, note: str) -> str:
    from urllib.parse import quote
    pa = quote(upi_id, safe="")
    pn = quote(payee_name, safe="")
    tn = quote(note, safe="")
    return f"upi://pay?pa={pa}&pn={pn}&am={amount_rs}&cu=INR&tn={tn}"

# Helper to get public QR code server URL
def _upi_qr_image_url(upi_uri: str) -> str:
    from urllib.parse import quote
    return f"https://api.qrserver.com/v1/create-qr-code/?size=700x700&data={quote(upi_uri, safe='')}"

# Activate plan helper
async def _activate_payment_plan(db: Database, req: dict, bot, context: ContextTypes.DEFAULT_TYPE = None) -> int:
    plan_key = str(req.get("plan_key") or "")
    plans = await db.get_active_plans()
    plan = plans.get(plan_key, {})
    days = int(req.get("plan_days") or 30)
    user_id = int(req["user_id"])
    
    if plan_key == 'getpin':
        if context and context.application and context.application.user_data is not None:
            if user_id not in context.application.user_data:
                context.application.user_data[user_id] = {}
            context.application.user_data[user_id]["awaiting_getpin_ss"] = True
        
        ss_request_msg = (
            "🚫 <b>No Getpin (1 Month)</b>\n\n"
            "Remove getpin for 1 month for any ONE Apk.\n\n"
            "⚠️ After payment, open the apk, go to the getpin page, take a screenshot, and send it here."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👨‍💼 Contact Admin", url="https://t.me/Mgayushff")]
        ])
        await bot.send_message(
            chat_id=user_id,
            text=ss_request_msg,
            reply_markup=kb,
            parse_mode='HTML'
        )
        return 0
        
    # Calculate and extend subscription
    until = await db.add_premium_seconds(user_id, days * 24 * 60 * 60)
    
    # Action plan benefits: unban & generate links
    chat_id = plan.get("chat_id")
    
    # Custom Group ID fetch for Faphouse plan
    if plan_key.startswith("faphouse"):
        connected_group = await db.get_setting("faphouse_group_id")
        if connected_group:
            try:
                chat_id = int(connected_group)
            except ValueError:
                pass
                
    if chat_id:
        try:
            # Unban member if banned
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            
            # Custom invite link duration (days of plan for faphouse)
            if plan_key.startswith("faphouse"):
                expire_seconds = 600
                validity_desc = "10 minutes"
            else:
                expire_seconds = 600
                validity_desc = "10 minutes"
                
            # Create a one-time invite link
            invite_link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,
                expire_date=int(time.time()) + expire_seconds
            )
            
            if plan_key.startswith("faphouse"):
                key = await db.generate_faphouse_key(user_id, days)
                success_msg = (
                    "🎉 <b>Welcome to Faphouse Paid VIP!</b>\n\n"
                    f"🔗 <b>Group Join Link (Valid for {validity_desc}, one-time use):</b>\n"
                    f"{invite_link.invite_link}\n\n"
                    f"🔑 <b>License Key:</b>\n"
                    f"<code>{key}</code>\n\n"
                    "📝 <b>Instructions:</b>\n"
                    "Group join karke wahan se APK download aur install karein. Uske baad open karke upar di gayi key enter karein."
                )
            else:
                success_msg = (
                    f"🎉 Welcome to premium VIP!\n\n"
                    f"Here is your join link (valid for {validity_desc}, one-time use):\n"
                    f"🔗 {invite_link.invite_link}"
                )
            await bot.send_message(chat_id=user_id, text=success_msg, parse_mode="HTML")
        except Exception as e:
            print(f"Error unbanning user or creating invite link: {e}")
            await bot.send_message(
                chat_id=user_id,
                text="🎉 Premium Activated! Group join links generation failed. Please contact admin to get access."
            )
    
    return until

async def _notify_payment_admins(context: ContextTypes.DEFAULT_TYPE, req: dict, utr_preview: str) -> None:
    db: Database = context.application.bot_data["db"]
    admin_ids = await db.list_admin_ids()
    targets = {config.OWNER_ID, *admin_ids}
    
    user_id = req['user_id']
    user_data = await db.get_user(user_id)
    username = f"@{user_data['username']}" if user_data and user_data.get('username') else "None"
    
    note = (
        "💰 <b>New Payment UTR Submitted</b>\n\n"
        f"Request ID: <code>#{req['id']}</code>\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Username: {username}\n"
        f"Plan: <b>{req['plan_key']}</b> ({req['plan_days']} days)\n"
        f"Amount: ₹{req['amount_rs']}\n"
        f"UTR: <code>{utr_preview}</code>\n\n"
        "Please review the proof below and choose an option:"
    )
    
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"payadm:approve:{req['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"payadm:reject:{req['id']}")
        ]
    ])
    
    for aid in targets:
        if not aid:
            continue
        try:
            msg = await context.bot.send_message(chat_id=aid, text=note, reply_markup=kb, parse_mode="HTML")
            await db.add_admin_notification(req["id"], aid, msg.message_id)
        except Exception:
            pass

async def _delete_payment_qr_message(req: dict, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = req.get("user_chat_id")
    qr_id = req.get("qr_msg_id")
    if chat_id and qr_id:
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=int(qr_id))
        except Exception:
            pass

async def _update_payment_user_status(
    req: dict,
    context: ContextTypes.DEFAULT_TYPE,
    title: str,
    detail_html_lines: list[str],
    reply_markup: InlineKeyboardMarkup | None = None
) -> None:
    chat_id = req.get("user_chat_id")
    message_id = req.get("details_msg_id")
    if not chat_id or not message_id:
        return
        
    db = context.application.bot_data["db"]
    plans = await db.get_active_plans()
    plan = plans.get(req['plan_key'], {"label": req['plan_key']})
    plan_label = plan.get("label", req['plan_key'])
    
    status_html = [
        f"<b>{html.escape(title)}</b>",
        "",
        f"Order ID: <code>#{int(req['id'])}</code>",
        f"User ID: <code>{int(req['user_id'])}</code>",
        f"Plan: <b>{html.escape(plan_label)}</b>",
        f"Amount: ₹{int(req.get('amount_rs') or 0)}",
    ]
    
    projected_until = int(req.get("projected_premium_until") or 0)
    if projected_until > 0:
        expiry_str = datetime.datetime.utcfromtimestamp(projected_until).strftime("%Y-%m-%d %H:%M:%S UTC")
        status_html.append(f"Projected Expiry: <code>{html.escape(expiry_str)}</code>")
        
    if detail_html_lines:
        status_html.append("")
        status_html.extend(detail_html_lines)
        
    text = "\n".join(status_html)
    
    try:
        await context.bot.edit_message_text(
            chat_id=int(chat_id),
            message_id=int(message_id),
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    except Exception:
        pass

async def _poll_razorpay_and_complete(
    context: ContextTypes.DEFAULT_TYPE,
    rid: int,
    qr_code_id: str,
    key_id: str,
    key_secret: str
) -> None:
    success = await razorpay_service.wait_for_payment(
        qr_code_id=qr_code_id,
        key_id=key_id,
        key_secret=key_secret,
        timeout_minutes=5,
        poll_interval=2,
    )
    
    db: Database = context.application.bot_data["db"]
    req = await db.get_payment_request(rid)
    if not req or req.get("status") != "pending":
        return
        
    if success:
        ok = await db.approve_payment_request(rid, admin_id=0)
        if not ok:
            return
            
        until = await _activate_payment_plan(db, req, context.bot, context)
        
        if req['plan_key'] == 'getpin':
            await _delete_payment_qr_message(req, context)
            
            # Notify admins
            admin_ids = await db.list_admin_ids()
            targets = {config.OWNER_ID, *admin_ids}
            admin_note = (
                "🚀 <b>Razorpay Auto-Verified (Getpin Plan)</b>\n\n"
                f"Order ID: <code>#{req['id']}</code>\n"
                f"User ID: <code>{req['user_id']}</code>\n"
                f"Plan: <b>{req['plan_key']}</b> ({req['plan_days']} days)\n"
                f"Amount: ₹{req['amount_rs']}\n"
                "Status: <b>Verified (Awaiting User Screenshot)</b>"
            )
            for aid in targets:
                if aid:
                    try:
                        await context.bot.send_message(chat_id=aid, text=admin_note, parse_mode="HTML")
                    except Exception:
                        pass
                        
            # Update user status card
            await _update_payment_user_status(
                req,
                context,
                "Payment Status: SUCCESS",
                [
                    "Your payment has been auto-verified via Razorpay!",
                    "Please open the apk, go to the getpin page, take a screenshot, and send it here."
                ]
            )
            await db.clear_payment_ui_messages(int(req["id"]))
            return

        expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        await _delete_payment_qr_message(req, context)
        
        # Notify admins
        admin_ids = await db.list_admin_ids()
        targets = {config.OWNER_ID, *admin_ids}
        admin_note = (
            "🚀 <b>Razorpay Auto-Verified</b>\n\n"
            f"Order ID: <code>#{req['id']}</code>\n"
            f"User ID: <code>{req['user_id']}</code>\n"
            f"Plan: <b>{req['plan_key']}</b> ({req['plan_days']} days)\n"
            f"Amount: ₹{req['amount_rs']}\n"
            "Status: <b>Approved (Auto)</b>"
        )
        for aid in targets:
            if aid:
                try:
                    await context.bot.send_message(chat_id=aid, text=admin_note, parse_mode="HTML")
                except Exception:
                    pass
                    
        # Update user status card
        await _update_payment_user_status(
            req,
            context,
            "Payment Status: SUCCESS",
            ["Your payment has been auto-verified via Razorpay!"]
        )
        
        # User notification card
        db = context.application.bot_data["db"]
        plans = await db.get_active_plans()
        plan = plans.get(req['plan_key'], {"label": req['plan_key']})
        plan_name = plan.get("label", req['plan_key'])
        
        if req['plan_key'] == "donation":
            success_text = (
                "❤️ <b>Thank you for your donation!</b> ❤️\n\n"
                "Your test donation of ₹1 was received and verified successfully.\n"
                "The Razorpay auto-payment verification flow is working perfectly! (Test Complete)\n\n"
                "Thank you for supporting us! 🙏"
            )
        else:
            success_text = (
                "⭐ <b>Premium Activated Successfully!</b> ⭐\n\n"
                "Congratulations! Your payment has been verified and your account has been upgraded to Premium.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🎟 <b>Plan:</b> {plan_name}\n"
                f"📅 <b>Duration:</b> {req['plan_days']} Days\n"
                f"🆔 <b>Order ID:</b> #{req['id']}\n"
                f"🕒 <b>Expiry Date:</b> {expiry_utc}\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enjoy your Premium VIP Experience!"
            )
        try:
            await context.bot.send_message(chat_id=int(req["user_id"]), text=success_text, parse_mode="HTML")
        except Exception:
            pass
        await db.clear_payment_ui_messages(int(req["id"]))
    else:
        # Expire
        changed = await db.expire_payment_request_if_pending(rid)
        if changed:
            await _delete_payment_qr_message(req, context)
            await _update_payment_user_status(
                req,
                context,
                "Payment Timeout",
                [
                    "Status: <b>Expired</b>",
                    "Payment expired because no transaction was captured within 5 minutes.",
                ]
            )
            try:
                await context.bot.send_message(
                    chat_id=int(req["user_id"]),
                    text="⏳ Razorpay payment request expired. Please run /pay again if you want to purchase."
                )
            except Exception:
                pass
            await db.clear_payment_ui_messages(int(req["id"]))

async def pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if await db.is_bot_banned(user_id):
        return
        
    data = q.data or ""
    plan_key = data.split(":")[1]
    plans = await db.get_active_plans()
    plan = plans.get(plan_key)
    if not plan:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Invalid plan.")
        return
        
    is_maintenance = plan.get("status") == "maintenance"
    lim = plan.get("limit")
    is_out_of_stock = (lim is not None) and (plan.get("sold_count", 0) >= lim)
    
    if is_maintenance:
        await q.answer("⚠️ This plan is currently under maintenance. Please try again later.", show_alert=True)
        return
    if is_out_of_stock:
        await q.answer("❌ Sorry, this plan is currently out of stock!", show_alert=True)
        return
        
    keyboard = [
        [
            InlineKeyboardButton("⚡ Fast Auto UPI (GPay, Paytm, PhonePe)", callback_data=f"paygwchoice:{plan_key}:razorpay")
        ],
        [
            InlineKeyboardButton("⭐ Fast Stars & Crypto (Telegram Stars)", callback_data=f"paygwchoice:{plan_key}:stars")
        ]
    ]
    
    if plan_key != "donation":
        keyboard.append([
            InlineKeyboardButton("🎟 Manual UPI (UTR Approval)", callback_data=f"paygwchoice:{plan_key}:manual")
        ])
        
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_plans")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        f"🛍 <b>Payment Selection: {plan['label']}</b>\n\n"
        f"💰 Price: <b>₹{plan['amount']}</b> / <b>{plan.get('stars', plan['amount'])} Stars ⭐</b>\n\n"
        "Please choose your preferred payment method:\n\n"
        "⚡ <b>Fast Auto UPI:</b> Generates a dynamic QR code. Scans & auto-approves instantly via Paytm/GPay/PhonePe.\n"
        "⭐ <b>Fast Stars & Crypto:</b> Pay instantly using Telegram Stars (supports card/crypto/in-app wallets).\n"
    )
    if plan_key != "donation":
        text += "\n🎟 <b>Manual UPI:</b> Send payment to our UPI ID and submit a 12-digit UTR/Screenshot for manual admin approval."
        
    try:
        await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=reply_markup, parse_mode="HTML")

async def pay_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    data = q.data or ""
    rid = int(data.split(":")[1])
    
    req = await db.get_payment_request(rid)
    if not req or req['user_id'] != update.effective_user.id:
        return
        
    if req['status'] != 'pending':
        await q.answer("Order already processed.", show_alert=True)
        return
        
    await db.expire_payment_request_if_pending(rid)
    await _delete_payment_qr_message(req, context)
    await _update_payment_user_status(
        req,
        context,
        "Order Cancelled",
        ["This order was cancelled by the user."]
    )
    await db.clear_payment_ui_messages(rid)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="✅ Payment request cancelled. Use /plan to start over."
    )

async def pay_utr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    data = q.data or ""
    rid = int(data.split(":")[1])
    
    req = await db.get_payment_request(rid)
    if not req or req['user_id'] != update.effective_user.id:
        return
        
    if req['status'] != 'pending':
        await q.answer("Order already processed.", show_alert=True)
        return
        
    context.user_data["pay_utr_request_id"] = rid
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"📩 <b>Please submit proof for Request ID #{rid} now.</b>\n\n"
            "You can type your 12-digit numeric UTR/Transaction ID, or send a payment screenshot."
        ),
        parse_mode="HTML"
    )

async def pay_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    parts = data.split(":")
    action, rid = parts[1], int(parts[2])
    
    req = await db.get_payment_request(rid)
    if not req:
        await q.answer("Request not found", show_alert=True)
        return
        
    if req['status'] in ('processed', 'rejected'):
        await q.answer("Request already handled.", show_alert=True)
        return
        
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else str(user_id)
    
    if action == "approve":
        ok = await db.approve_payment_request(rid, user_id)
        if not ok:
            await q.answer("Already handled", show_alert=True)
            return
            
        until = await _activate_payment_plan(db, req, context.bot, context)
        
        if req['plan_key'] == 'getpin':
            sync_text = (
                f"✅ <b>Payment Approved (Getpin Plan)</b>\n\n"
                f"Request ID: #{rid}\n"
                f"User ID: {req['user_id']}\n"
                f"Approved By: {admin_name}\n"
                f"Status: <b>Awaiting User Screenshot</b>"
            )
            await q.edit_message_text(text=sync_text, parse_mode="HTML")
            
            # Sync other admins
            notifications = await db.list_admin_notifications(rid)
            for n in notifications:
                if n["admin_id"] == update.effective_chat.id:
                    continue
                try:
                    await context.bot.edit_message_text(
                        chat_id=n["admin_id"],
                        message_id=n["message_id"],
                        text=sync_text,
                        reply_markup=None,
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

            await _delete_payment_qr_message(req, context)
            await _update_payment_user_status(
                req,
                context,
                "Payment Approved",
                [
                    "Status: <b>Approved (Awaiting Screenshot)</b>",
                    f"Approved By: <code>{admin_name}</code>",
                    "Please open the apk, go to the getpin page, take a screenshot, and send it here."
                ]
            )
            await db.clear_payment_ui_messages(rid)
            return

        expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        try:
            await context.bot.send_message(
                chat_id=int(req["user_id"]),
                text=(
                    f"🎉 <b>Payment Approved! Your VIP premium status is active.</b>\n\n"
                    f"🎟 Plan: <b>{req['plan_key']}</b>\n"
                    f"📅 Duration: {req['plan_days']} Days\n"
                    f"🕒 Expires: <code>{expiry_utc}</code>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
            
        sync_text = (
            f"✅ <b>Payment Approved</b>\n\n"
            f"Request ID: #{rid}\n"
            f"User ID: {req['user_id']}\n"
            f"Plan: {req['plan_key']}\n"
            f"Approved By: {admin_name}"
        )
        await q.edit_message_text(text=sync_text, parse_mode="HTML")
        
        # Sync other admins
        notifications = await db.list_admin_notifications(rid)
        for n in notifications:
            if n["admin_id"] == update.effective_chat.id:
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=n["admin_id"],
                    message_id=n["message_id"],
                    text=sync_text,
                    reply_markup=None,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        await _delete_payment_qr_message(req, context)
        await _update_payment_user_status(
            req,
            context,
            "Payment Approved",
            [
                "Status: <b>Approved</b>",
                f"Approved By: <code>{admin_name}</code>",
                f"Premium Until: <code>{expiry_utc}</code>"
            ]
        )
        await db.clear_payment_ui_messages(rid)
        
    elif action == "reject":
        ok = await db.reject_payment_request(rid, user_id)
        if not ok:
            await q.answer("Already handled", show_alert=True)
            return
            
        try:
            await context.bot.send_message(
                chat_id=int(req["user_id"]),
                text=(
                    f"❌ <b>Payment Rejected!</b>\n\n"
                    "Your payment proof/UTR could not be verified by our team.\n"
                    "Please verify transaction details and try again or contact support."
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
            
        sync_text = (
            f"❌ <b>Payment Rejected</b>\n\n"
            f"Request ID: #{rid}\n"
            f"User ID: {req['user_id']}\n"
            f"Plan: {req['plan_key']}\n"
            f"Rejected By: {admin_name}"
        )
        await q.edit_message_text(text=sync_text, parse_mode="HTML")
        
        # Sync other admins
        notifications = await db.list_admin_notifications(rid)
        for n in notifications:
            if n["admin_id"] == update.effective_chat.id:
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=n["admin_id"],
                    message_id=n["message_id"],
                    text=sync_text,
                    reply_markup=None,
                    parse_mode="HTML"
                )
            except Exception:
                pass
        
        await _delete_payment_qr_message(req, context)
        await _update_payment_user_status(
            req,
            context,
            "Payment Rejected",
            [
                "Status: <b>Rejected</b>",
                f"Rejected By: <code>{admin_name}</code>",
                "Please run /pay to start a new checkout."
            ]
        )
        await db.clear_payment_ui_messages(rid)

async def my_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    until = await db.get_premium_until(user_id)
    now = int(time.time())
    
    active_keys = await db.get_active_user_keys(user_id)
    
    has_active_premium = until > now
    
    if not has_active_premium and not active_keys:
        text = (
            "📦 <b>My Premium Subscriptions</b>\n\n"
            "Aapke paas abhi koi active premium plan nahi hai.\n"
            "Kripya main menu ya /plan command se purchase karein."
        )
    else:
        text = "📦 <b>My Active Orders</b>\n\n"
        if has_active_premium:
            expiry_str = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
            text += (
                f"👑 Status: <b>VIP Premium Member</b>\n"
                f"⏳ Expiry Date: <code>{expiry_str}</code>\n\n"
            )
        if active_keys:
            text += "🔑 <b>Your Faphouse Keys:</b>\n"
            for k in active_keys:
                text += f"• <code>{k['key']}</code> (Exp: <code>{k['expiry_date']}</code>)\n"
        
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]])
    await q.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")

# --- ADMIN SETTINGS CONFIG CALLBACKS ---

async def settings_gw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    data = q.data or ""
    gw = data.split(":")[1]
    
    await db.set_setting("payment_gateway", gw)
    await settings_menu(update, context, message_id=q.message.message_id)

async def settings_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    data = q.data or ""
    field = data.split(":")[1]
    
    context.user_data["settings_edit_field"] = field
    context.user_data["settings_edit_msg_id"] = q.message.message_id
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✍️ <b>Please enter the new value for:</b> <code>{field}</code>",
        parse_mode="HTML"
    )

# --- INFO SCREENS & MAIN MENU NAVIGATION ---

async def info_screens_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    plans = await db.get_active_plans()
    
    data = q.data or ""
    kb = []
    
    if data == 'info_direct':
        p = plans.get("direct", {})
        p_amount = p.get("amount", 35)
        p_stars = p.get("stars", 35)
        text = (
            "🎮 <b>Direct Mods (1 Month)</b>\n\n"
            "Get direct access to our Premium Private Channel for 1 month.\n\n"
            f"<b>Price: ₹{p_amount} / {p_stars} Stars ⭐</b>"
        )
        kb.append([InlineKeyboardButton("💳 Buy VIP access", callback_data="payplan:direct")])
    elif data == 'info_getpin':
        p = plans.get("getpin", {})
        p_amount = p.get("amount", 30)
        p_stars = p.get("stars", 30)
        text = (
            "🚫 <b>No Getpin (1 Month)</b>\n\n"
            "Remove getpin for 1 month for any ONE Apk.\n\n"
            "⚠️ <b>After payment, open the apk, go to the getpin page, take a screenshot, and send it here.</b>\n\n"
            f"<b>Price: ₹{p_amount} / {p_stars} Stars ⭐</b>"
        )
        kb.append([InlineKeyboardButton("💳 Buy VIP access", callback_data="payplan:getpin")])
    elif data == 'info_faphouse':
        p1 = plans.get("faphouse_1", {})
        p3 = plans.get("faphouse_3", {})
        p7 = plans.get("faphouse_7", {})
        text = (
            "🔥 <b>Faphouse Paid VIP</b>\n\n"
            "✨ <b>SPECIAL LIMITED TIME OFFER!</b> ✨\n"
            "⚠️ <i>Grab this opportunity without missing it!</i> ⚠️\n\n"
            "Get Faphouse paid mod (No getpin needed).\n"
            "🔗 After payment, you will get a one-time group link to join and your license key.\n\n"
            "Select subscription duration:"
        )
        kb.append([
            InlineKeyboardButton(f"🔥 1 Day (₹{p1.get('amount', 9)})", callback_data="payplan:faphouse_1")
        ])
        kb.append([
            InlineKeyboardButton(f"🔥 3 Days (₹{p3.get('amount', 19)})", callback_data="payplan:faphouse_3"),
            InlineKeyboardButton(f"🔥 7 Days (₹{p7.get('amount', 29)})", callback_data="payplan:faphouse_7")
        ])
    elif data == 'info_donation':
        p = plans.get("donation", {})
        p_amount = p.get("amount", 1)
        p_stars = p.get("stars", 1)
        text = (
            "❤️ <b>Test Donation (₹1 / 1 Star)</b> ❤️\n\n"
            "Support our development and test the Razorpay/Stars auto-verification flows.\n\n"
            f"<b>Price: ₹{p_amount} / {p_stars} Star ⭐</b>"
        )
        kb.append([InlineKeyboardButton("💳 Donate now", callback_data="payplan:donation")])
        
    kb.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
    reply_markup = InlineKeyboardMarkup(kb)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def rc_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    kb = [
        [
            InlineKeyboardButton("🚨 Report Issue", callback_data="report_issue"),
            InlineKeyboardButton("👨💼 Contact Admin", callback_data="contact_admin")
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="back_main")]
    ]
    reply_markup = InlineKeyboardMarkup(kb)
    await q.edit_message_text(text="Choose an option:", reply_markup=reply_markup)

async def report_issue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    last = await db.get_last_report(user_id)
    if last and time.time() - last < 86400:
        await q.answer("Aap 24 ghante me ek hi baar report kar sakte hain.", show_alert=True)
        return
        
    context.user_data["awaiting_report"] = True
    await q.edit_message_text("Aapko jo bhi problem hai, type karke bhej dijiye. Admin ko chala jayega.")

async def contact_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Agar koi promotion ya contact karna ho toh @Mgayushff pe msg karein (Only for promotion/contacts)."
    )

async def back_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    await show_main_menu(update, context, message_id=q.message.message_id)

# --- TELEGRAM STARS CALLBACK HANDLERS ---

async def pre_checkout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answers pre-checkout query to approve payment."""
    query = update.pre_checkout_query
    db: Database = context.application.bot_data["db"]
    try:
        payload = query.invoice_payload
        try:
            rid = int(payload)
            req = await db.get_payment_request(rid)
            if req and req.get("status") == "pending":
                await query.answer(ok=True)
                return
        except ValueError:
            pass
        await query.answer(ok=False, error_message="Order not found or already processed.")
    except Exception as e:
        print(f"Error answering pre-checkout query: {e}")

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles successful payments (XTR / Telegram Stars)."""
    message = update.message
    if not message or not message.successful_payment:
        return
        
    payment = message.successful_payment
    payload = payment.invoice_payload
    db: Database = context.application.bot_data["db"]
    
    try:
        rid = int(payload)
    except (ValueError, TypeError):
        print(f"Invalid invoice payload in successful payment: {payload}")
        return
        
    req = await db.get_payment_request(rid)
    if not req:
        print(f"Payment request not found for request id: {rid}")
        return
        
    if req.get("status") != "pending":
        print(f"Payment request {rid} already processed or expired. Status: {req.get('status')}")
        return
        
    # Process and approve payment request
    ok = await db.approve_payment_request(rid, admin_id=0)
    if not ok:
        print(f"Failed to approve payment request {rid} in database")
        return
        
    until = await _activate_payment_plan(db, req, context.bot, context)
    
    if req['plan_key'] == 'getpin':
        admin_ids = await db.list_admin_ids()
        targets = {config.OWNER_ID, *admin_ids}
        admin_note = (
            "🚀 <b>Telegram Stars Payment Auto-Verified (Getpin Plan)</b>\n\n"
            f"Order ID: <code>#{req['id']}</code>\n"
            f"User ID: <code>{req['user_id']}</code>\n"
            f"Plan: <b>{req['plan_key']}</b> ({req['plan_days']} days)\n"
            f"Amount: {payment.total_amount} Stars ⭐\n"
            "Status: <b>Verified (Awaiting User Screenshot)</b>"
        )
        for aid in targets:
            if aid:
                try:
                    await context.bot.send_message(chat_id=aid, text=admin_note, parse_mode="HTML")
                except Exception:
                    pass
                    
        await _update_payment_user_status(
            req,
            context,
            "Payment Status: SUCCESS",
            [
                "This order's payment has been verified via Telegram Stars.",
                "Please open the apk, go to the getpin page, take a screenshot, and send it here."
            ]
        )
        await db.clear_payment_ui_messages(rid)
        return

    expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Update admin message/status
    admin_ids = await db.list_admin_ids()
    targets = {config.OWNER_ID, *admin_ids}
    admin_note = (
        "🚀 <b>Telegram Stars Payment Auto-Verified</b>\n\n"
        f"Order ID: <code>#{req['id']}</code>\n"
        f"User ID: <code>{req['user_id']}</code>\n"
        f"Plan: <b>{req['plan_key']}</b> ({req['plan_days']} days)\n"
        f"Amount: {payment.total_amount} Stars ⭐\n"
        "Status: <b>Approved (Auto)</b>"
    )
    for aid in targets:
        if aid:
            try:
                await context.bot.send_message(chat_id=aid, text=admin_note, parse_mode="HTML")
            except Exception:
                pass
                
    await _update_payment_user_status(
        req,
        context,
        "Payment Status: SUCCESS",
        ["This order's payment has been verified via Telegram Stars."]
    )
    
    try:
        plans = await db.get_active_plans()
        plan = plans.get(req['plan_key'], {"label": req['plan_key']})
        plan_name = plan.get("label", req['plan_key'])
        
        if req.get("plan_key") == "donation":
            success_text = (
                "❤️ <b>Thank you for your donation!</b> ❤️\n\n"
                "Your test donation of 1 Star was received and verified successfully.\n"
                "The Telegram Stars auto-payment verification flow is working perfectly! (Test Complete)\n\n"
                "Thank you for supporting us! 🙏"
            )
        else:
            success_text = (
                "⭐ <b>Premium Activated Successfully!</b> ⭐\n\n"
                "Congratulations! Your payment has been verified and your account has been upgraded to Premium VIP.\n\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🎟 <b>Plan:</b> {plan_name}\n"
                f"📅 <b>Duration:</b> {req['plan_days']} Days\n"
                f"🆔 <b>Order ID:</b> #{req['id']}\n"
                f"🕒 <b>Expiry Date:</b> {expiry_utc}\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "Enjoy your Premium VIP Experience!"
            )
        await context.bot.send_message(chat_id=int(req["user_id"]), text=success_text, parse_mode="HTML")
    except Exception as e:
        print(f"Error sending success message: {e}")

        
    await db.clear_payment_ui_messages(int(req["id"]))

# --- ADMIN GROUP CONNECTION CALLBACKS ---

async def admin_group_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    current_group = await db.get_setting("faphouse_group_id") or "Not Connected"
    
    text = (
        "🔌 <b>Connect Group to Plans</b>\n\n"
        f"Current Faphouse Group ID: <code>{current_group}</code>\n\n"
        "Select a plan category to connect a group:"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Faphouse Paid", callback_data="admin_connect_group:faphouse")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_admin_main")]
    ])
    
    await q.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")

async def admin_connect_group_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    parts = q.data.split(":")
    plan_category = parts[1]
    
    context.user_data["awaiting_group_connect_category"] = plan_category
    context.user_data["group_connect_msg_id"] = q.message.message_id
    
    text = (
        f"✉️ <b>Connect Group for {plan_category.capitalize()}</b>\n\n"
        "Please send the Group Chat ID (e.g. <code>-100234567890</code>) where users should be added.\n\n"
        "⚠️ <b>Important:</b> Make sure the bot is added to that group as an administrator!\n\n"
        "Type <code>cancel</code> to abort."
    )
    
    await q.edit_message_text(text=text, parse_mode="HTML")

# --- ADDITIONAL MANUAL VERIFICATION ADMIN CALLBACKS ---

async def generic_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    data = q.data or ""
    target_uid = int(data.split(":")[1])
    db: Database = context.application.bot_data["db"]
    try:
        # Activate 1-month premium subscription (30 days)
        until = await db.add_premium_seconds(target_uid, 30 * 24 * 60 * 60)
        expiry_utc = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        await context.bot.send_message(
            chat_id=target_uid,
            text=(
                "🎉 <b>Aapka No Getpin plan active ho chuka hai!</b>\n\n"
                "🎟 Plan: <b>No Getpin (1 Month)</b>\n"
                "📅 Duration: 30 Days\n"
                f"🕒 Expires: <code>{expiry_utc}</code>\n\n"
                "Aapka getpin order successfully complete aur activate ho gaya hai."
            ),
            parse_mode="HTML"
        )
        await q.edit_message_caption("✅ Order Completed!")
    except Exception as e:
        await q.answer(f"Failed to process completion: {e}", show_alert=True)

async def generic_ban_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    data = q.data or ""
    target_uid = int(data.split(":")[1])
    
    await db.ban_from_bot(target_uid)
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text="🚫 <b>Aapko Elite Premium Store se ban kar diya gaya hai. Fake details/proofs verify hone ke karan.</b>",
            parse_mode="HTML"
        )
        await q.edit_message_caption("🚫 Banned!")
    except Exception:
        pass

async def generic_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    data = q.data or ""
    target_uid = int(data.split(":")[1])
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💬 Reply enter karein jo user <code>{target_uid}</code> ko jayega:",
        parse_mode="HTML"
    )

async def settings_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    await settings_menu(update, context, message_id=q.message.message_id)

async def admin_orders_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    stats = await db.get_payment_stats()
    recent = await db.list_recent_payment_requests(limit=5)
    
    # Format stats
    text = (
        "📦 <b>Orders Details & Statistics</b>\n\n"
        f"✅ Processed: <code>{stats.get('processed', 0)}</code>\n"
        f"⏳ Pending: <code>{stats.get('pending', 0)}</code>\n"
        f"🎟 Submitted: <code>{stats.get('submitted', 0)}</code>\n"
        f"❌ Rejected: <code>{stats.get('rejected', 0)}</code>\n"
        f"⏰ Expired: <code>{stats.get('expired', 0)}</code>\n\n"
        "👇 <b>Recent Orders (Last 5):</b>"
    )
    
    keyboard = []
    plans = await db.get_active_plans()
    for r in recent:
        plan = plans.get(r['plan_key'], {"label": r['plan_key']})
        plan_label = plan.get("label", r['plan_key'])
        status_emoji = {
            "processed": "✅",
            "pending": "⏳",
            "submitted": "🎟",
            "rejected": "❌",
            "expired": "⏰"
        }.get(r['status'], "❓")
        
        keyboard.append([
            InlineKeyboardButton(
                f"#{r['id']} | {plan_label} | {status_emoji} {r['status'].upper()} (₹{r['amount_rs']})",
                callback_data=f"admin_view_order:{r['id']}"
            )
        ])
        
    # Actions & Back
    keyboard.append([
        InlineKeyboardButton("🔍 Lookup Order By ID", callback_data="admin_action:lookup")
    ])
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="back_admin_main")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_view_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    rid = int(data.split(":")[1])
    req = await db.get_payment_request(rid)
    
    if not req:
        await q.answer("Order not found", show_alert=True)
        return
        
    created_str = datetime.datetime.utcfromtimestamp(req['created_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
    updated_str = datetime.datetime.utcfromtimestamp(req['updated_at']).strftime('%Y-%m-%d %H:%M:%S UTC')
    
    text = (
        f"🔍 <b>Order #{rid} Details</b>\n\n"
        f"👤 <b>User ID:</b> <code>{req['user_id']}</code>\n"
        f"🎟 <b>Plan Key:</b> <code>{req['plan_key']}</code> ({req['plan_days']} days)\n"
        f"💰 <b>Amount:</b> ₹{req['amount_rs']}\n"
        f"⚙️ <b>Status:</b> <code>{req['status'].upper()}</code>\n"
        f"📝 <b>UTR submitted:</b> <code>{req['utr_text'] or 'None'}</code>\n"
        f"📅 <b>Created At:</b> <code>{created_str}</code>\n"
        f"📅 <b>Updated At:</b> <code>{updated_str}</code>\n"
    )
    if req['processed_by']:
        text += f"👮 <b>Processed By:</b> <code>{req['processed_by']}</code>\n"
        
    keyboard = []
    if req['status'] in ('pending', 'submitted'):
        keyboard.append([
            InlineKeyboardButton("✅ Approve", callback_data=f"payadm:approve:{rid}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"payadm:reject:{rid}")
        ])
        
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Orders List", callback_data="admin_orders_menu")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_revenue_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    payments = await db.list_all_processed_payments()
    
    total_rs = 0
    total_stars = 0
    
    # Gateways
    gw_razorpay = 0
    gw_stars = 0
    gw_manual = 0
    
    # Plans
    plan_revenue = {}
    plans = await db.get_active_plans()
    for plan_key in plans.keys():
        plan_revenue[plan_key] = 0
        
    for p in payments:
        # Determine gateway
        gw = "manual"
        gw_extra_str = p.get("gateway_extra")
        if gw_extra_str:
            try:
                gw_extra = json.loads(gw_extra_str)
                gw = gw_extra.get("gateway", "manual")
            except Exception:
                if "razorpay" in gw_extra_str.lower():
                    gw = "razorpay"
                elif "stars" in gw_extra_str.lower():
                    gw = "stars"
        else:
            # Fallback for old orders
            if p.get("processed_by") == 0:
                gw = "stars"
            else:
                gw = "manual"
                
        amount = p.get("amount_rs", 0)
        
        if gw == "stars":
            gw_stars += amount
            total_stars += amount
        elif gw == "razorpay":
            gw_razorpay += amount
            total_rs += amount
        else:
            gw_manual += amount
            total_rs += amount
            
        plan_key = p.get("plan_key")
        if plan_key in plan_revenue:
            plan_revenue[plan_key] += amount
        else:
            plan_revenue[plan_key] = amount
            
    total_withdrawn = await db.get_total_withdrawn()
    left_to_withdraw = max(0, gw_razorpay - total_withdrawn)

    text = (
        "📊 <b>Elite Premium Store - Complete Revenue Report</b>\n\n"
        f"💰 <b>Total Earnings (Fiat):</b> ₹{total_rs}\n"
        f"⭐ <b>Total Earnings (Stars):</b> {total_stars} Stars\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔌 <b>Revenue by Payment Flow:</b>\n"
        f"• 💳 Manual UPI: ₹{gw_manual}\n"
        f"• ⚡ Razorpay Gateway: ₹{gw_razorpay}\n"
        f"  └─ 💸 Total Withdrawn: ₹{total_withdrawn}\n"
        f"  └─ 💰 Left to Withdraw: ₹{left_to_withdraw}\n"
        f"• ⭐ Telegram Stars: {gw_stars} Stars\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 <b>Revenue by Individual Plan (Total):</b>\n"
    )
    
    for pk, rev in plan_revenue.items():
        plan = plans.get(pk, {"label": pk})
        label = plan.get("label", pk)
        text += f"• {label}: ₹{rev}\n"
        
    keyboard = [
        [InlineKeyboardButton("📅 Daily Revenue Breakdown", callback_data="admin_daily_revenue")],
        [InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="back_admin_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_broadcast_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    context.user_data["awaiting_broadcast"] = True
    context.user_data["broadcast_menu_msg_id"] = q.message.message_id
    
    text = (
        "📢 <b>Broadcast Message Feature</b>\n\n"
        "Aap jo message sabhi users ko bhejna chahte hain use niche type karein ya send karein.\n\n"
        "• Aap text, image, document, custom formatting, emojis aadi kuch bhi bhej sakte hain.\n"
        "• Cancel karne ke liye type karein: <code>cancel</code>"
    )
    
    keyboard = [[
        InlineKeyboardButton("❌ Cancel & Go Back", callback_data="back_admin_main")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def broadcast_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    action = data.split(":")[1]
    
    template_msg_id = context.user_data.pop("broadcast_template_msg_id", None)
    template_chat_id = context.user_data.pop("broadcast_template_chat_id", None)
    context.user_data.pop("awaiting_broadcast", None)
    
    if action == "cancel":
        await q.edit_message_text("❌ Broadcast cancelled.")
        from handlers.commands import show_admin_menu
        await show_admin_menu(update, context)
        return
        
    if not template_msg_id or not template_chat_id:
        await q.edit_message_text("❌ Error: Broadcast template message not found. Please try again.")
        return
        
    await q.edit_message_text("🚀 <b>Broadcasting message to all users...</b> Please wait.", parse_mode="HTML")
    
    user_ids = await db.list_all_user_ids()
    
    success_count = 0
    fail_count = 0
    
    import asyncio
    for uid in user_ids:
        try:
            await context.bot.copy_message(
                chat_id=uid,
                from_chat_id=template_chat_id,
                message_id=template_msg_id
            )
            success_count += 1
        except Exception:
            fail_count += 1
        await asyncio.sleep(0.05)
        
    text = (
        "📢 <b>Broadcast Completed!</b>\n\n"
        f"✅ <b>Sent Successfully:</b> <code>{success_count}</code> users\n"
        f"❌ <b>Failed:</b> <code>{fail_count}</code> users"
    )
    
    keyboard = [[
        InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="back_admin_main")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def back_admin_main_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    context.user_data.pop("awaiting_broadcast", None)
    context.user_data.pop("broadcast_template_msg_id", None)
    context.user_data.pop("broadcast_template_chat_id", None)
    
    from handlers.commands import show_admin_menu
    await show_admin_menu(update, context, message_id=q.message.message_id)

async def admin_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    try:
        await q.message.delete()
    except Exception:
        pass

async def admin_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    action = data.split(":")[1]
    
    if action == "lookup":
        text = (
            "🔍 <b>Lookup Order ID</b>\n\n"
            "Order details dekhne ke liye please direct chat me niche diye format me type/send karein:\n"
            "<code>/paylookup &lt;order_id&gt;</code>\n\n"
            "<i>Example: /paylookup 102</i>"
        )
    else:
        return
        
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Orders List", callback_data="admin_orders_menu")]
    ])
    await q.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")

async def pay_gw_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    if await db.is_bot_banned(user_id):
        return
        
    data = q.data or ""
    parts = data.split(":")
    plan_key = parts[1]
    gw_choice = parts[2]
    
    plans = await db.get_active_plans()
    plan = plans.get(plan_key)
    if not plan:
        return
        
    is_maintenance = plan.get("status") == "maintenance"
    lim = plan.get("limit")
    is_out_of_stock = (lim is not None) and (plan.get("sold_count", 0) >= lim)
    
    if is_maintenance or is_out_of_stock:
        await q.answer("⚠️ This plan is currently unavailable (Maintenance or Out of Stock).", show_alert=True)
        return
        
    # Delete selection message to keep chat clean
    try:
        await q.message.delete()
    except Exception:
        pass
        
    existing = await db.get_latest_open_payment_request(user_id)
    if existing:
        await db.expire_payment_request_if_pending(existing["id"])
        await _delete_payment_qr_message(existing, context)
        await _update_payment_user_status(
            existing,
            context,
            "Payment Request Replaced",
            ["Status: <b>Expired</b>", "A newer order was started by the user."]
        )
        await db.clear_payment_ui_messages(existing["id"])

    rid = await db.create_payment_request(user_id, plan_key, plan["days"], plan["amount"])
    req = await db.get_payment_request(rid)
    
    if gw_choice == "stars":
        stars_amount = plan.get("stars", plan["amount"])
        prices = [LabeledPrice(label=plan["label"], amount=stars_amount)]
        try:
            invoice_link = await context.bot.create_invoice_link(
                title=f"Premium - {plan['label']}",
                description=f"{plan['days']} Days Premium Subscription VIP Access.",
                payload=str(rid),
                provider_token="",
                currency="XTR",
                prices=prices,
            )
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"⭐ Pay {stars_amount} Stars ⭐", url=invoice_link)],
                [InlineKeyboardButton("❌ Cancel Order", callback_data=f"paycancel:{rid}")]
            ])
            
            status_text = (
                "💎 <b>Premium VIP Purchase</b>\n\n"
                f"🛍 Plan: <b>{plan['label']}</b>\n"
                f"💰 Price: {stars_amount} Stars ⭐\n"
                f"⏳ Duration: {plan['days']} Days\n\n"
                "Tap the button below to complete payment via Telegram Stars."
            )
            
            sent_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=status_text,
                reply_markup=kb,
                parse_mode="HTML"
            )
            
            await db.set_payment_ui_messages(rid, update.effective_chat.id, sent_msg.message_id, None)
            gateway_data = {"gateway": "stars"}
            await db.set_payment_gateway_extra(rid, json.dumps(gateway_data))
            
        except Exception as e:
            print(f"Stars invoice error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Failed to generate invoice. Please contact the admin."
            )
    elif gw_choice == "razorpay":
        key_id = await db.get_setting("razorpay_key_id")
        key_secret = await db.get_setting("razorpay_key_secret")
        
        if not key_id or not key_secret:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Razorpay credentials are not configured by admin."
            )
            return
            
        loading_msg = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⏳ Generating Razorpay UPI QR Code..."
        )
        
        try:
            username = update.effective_user.username or f"id_{user_id}"
            create_res = await razorpay_service.create_qr_code(
                amount_rs=plan["amount"],
                order_id=rid,
                key_id=key_id,
                key_secret=key_secret,
                user_id=user_id,
                username=username,
                plan_label=plan["label"]
            )
            
            qr_code_id = create_res.get("id")
            image_url = create_res.get("image_url")
            
            if not qr_code_id or not image_url:
                raise ValueError("No QR code ID or image URL received from Razorpay.")
                
            gateway_data = {"qr_code_id": qr_code_id, "image_url": image_url, "gateway": "razorpay"}
            await db.set_payment_gateway_extra(rid, json.dumps(gateway_data))
            
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=loading_msg.message_id)
            
            caption = (
                "⚡ <b>RAZORPAY DYNAMIC QR PAYMENT</b> ⚡\n\n"
                f"💎 <b>Plan:</b> {plan['label']}\n"
                f"💰 <b>Amount:</b> ₹{plan['amount']}\n"
                f"🆔 <b>Order ID:</b> <code>#{rid}</code>\n\n"
                "📲 <b>How to pay:</b>\n"
                "1️⃣ Scan the QR Code above using any UPI app.\n"
                "2️⃣ Mobile users: Screenshot the QR and upload it in GPay/PhonePe scanner.\n"
                "3️⃣ Premium activates automatically once payment is complete!\n\n"
                "⏳ Complete payment within 5 minutes.\n"
                "🚀 Activation is automatic — no action needed after payment."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel Order", callback_data=f"paycancel:{rid}")]
            ])
            
            qr_msg = await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=image_url,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML"
            )
            
            status_text = (
                f"<b>Payment Request #{rid} Created</b>\n\n"
                f"Plan: <b>{plan['label']}</b>\n"
                f"Amount: ₹{plan['amount']}\n"
                "Status: <b>Pending</b>\n\n"
                "Complete the payment using the QR Code above. Your premium will be activated automatically."
            )
            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=status_text,
                parse_mode="HTML"
            )
            
            await db.set_payment_ui_messages(rid, update.effective_chat.id, status_msg.message_id, qr_msg.message_id)
            
            import asyncio
            asyncio.create_task(_poll_razorpay_and_complete(context, rid, qr_code_id, key_id, key_secret))
            
        except Exception as e:
            print(f"Razorpay gateway error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Razorpay gateway error. Please use manual UPI payment or contact admin."
            )
    elif gw_choice == "manual":
        upi_id = await db.get_setting("pay_upi")
        pay_name = await db.get_setting("pay_name") or "Premium Store"
        pay_text = await db.get_setting("pay_text") or "Scan the QR and pay. Enter UTR to verify."
        
        if not upi_id:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ UPI payment is not configured by admin yet. Please contact admin."
            )
            return
            
        projected_until = req.get("projected_premium_until") or 0
        note = _manual_payment_note(user_id, rid, plan["days"], projected_until)
        upi_uri = _upi_uri(upi_id=upi_id, amount_rs=plan["amount"], payee_name=pay_name, note=note)
        qr_url = _upi_qr_image_url(upi_uri)
        
        caption = (
            "⚡ <b>MANUAL UPI PAYMENT</b> ⚡\n\n"
            f"💎 <b>Plan:</b> {plan['label']}\n"
            f"💰 <b>Amount:</b> ₹{plan['amount']}\n"
            f"🆔 <b>Order ID:</b> <code>#{rid}</code>\n"
            f"👤 <b>User ID:</b> <code>{user_id}</code>\n\n"
            f"💳 <b>UPI ID:</b> <code>{upi_id}</code>\n"
            f"🧾 <b>Payment Note:</b> <code>{note}</code>\n\n"
            f"📝 <b>Instructions:</b>\n{pay_text}\n\n"
            "⏳ Valid for 5 minutes only."
        )
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎟 Submit UTR", callback_data=f"payutr:{rid}")],
            [InlineKeyboardButton("❌ Cancel Order", callback_data=f"paycancel:{rid}")]
        ])
        
        try:
            qr_msg = await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=qr_url,
                caption=caption,
                reply_markup=kb,
                parse_mode="HTML"
            )
            
            status_text = (
                f"<b>Payment Request #{rid} Created</b>\n\n"
                f"Plan: <b>{plan['label']}</b>\n"
                f"Amount: ₹{plan['amount']}\n"
                "Status: <b>Pending</b>\n\n"
                "Scan the QR above to pay. After payment, click **Submit UTR** and enter your transaction ID."
            )
            status_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=status_text,
                reply_markup=kb,
                parse_mode="HTML"
            )
            
            await db.set_payment_ui_messages(rid, update.effective_chat.id, status_msg.message_id, qr_msg.message_id)
            gateway_data = {"gateway": "manual"}
            await db.set_payment_gateway_extra(rid, json.dumps(gateway_data))
        except Exception as e:
            print(f"Manual payment QR error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"QR Code generation failed. Please transfer directly to the UPI ID: <code>{upi_id}</code>\nNote: {note}",
                parse_mode="HTML"
            )

async def back_plans_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    from handlers.commands import show_main_menu
    await show_main_menu(update, context, message_id=q.message.message_id)

async def admin_withdraw_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    # Clear any awaiting withdrawal status if user cancels/goes back
    context.user_data.pop("awaiting_withdraw_amount", None)
    context.user_data.pop("withdraw_menu_msg_id", None)
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    # Calculate Razorpay revenue
    payments = await db.list_all_processed_payments()
    gw_razorpay = 0
    for p in payments:
        gw = "manual"
        gw_extra_str = p.get("gateway_extra")
        if gw_extra_str:
            try:
                gw_extra = json.loads(gw_extra_str)
                gw = gw_extra.get("gateway", "manual")
            except Exception:
                if "razorpay" in gw_extra_str.lower():
                    gw = "razorpay"
        if gw == "razorpay":
            gw_razorpay += p.get("amount_rs", 0)
            
    total_withdrawn = await db.get_total_withdrawn()
    left_to_withdraw = max(0, gw_razorpay - total_withdrawn)
    
    text = (
        "💸 <b>Razorpay Withdrawal Panel</b>\n\n"
        f"⚡ <b>Total Razorpay Revenue:</b> ₹{gw_razorpay}\n"
        f"💸 <b>Total Withdrawn:</b> ₹{total_withdrawn}\n"
        f"💰 <b>Left Amount to Withdraw (Available):</b> ₹{left_to_withdraw}\n\n"
        "Click the button below to request a withdrawal from the Razorpay account owner."
    )
    
    keyboard = []
    if left_to_withdraw > 0:
        keyboard.append([
            InlineKeyboardButton("💸 Request Withdrawal", callback_data="admin_withdraw_req_prompt")
        ])
    else:
        text += "\n\n⚠️ <i>No balance available to withdraw.</i>"
        
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Admin Menu", callback_data="back_admin_main")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_withdraw_req_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    context.user_data["awaiting_withdraw_amount"] = True
    context.user_data["withdraw_menu_msg_id"] = q.message.message_id
    
    text = (
        "💸 <b>Request Withdrawal</b>\n\n"
        "Please enter the amount (in ₹) you wish to withdraw:\n"
        "• Send a number (e.g. <code>500</code>)\n"
        "• Send <code>cancel</code> to abort."
    )
    
    keyboard = [[
        InlineKeyboardButton("❌ Cancel & Go Back", callback_data="admin_withdraw_menu")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def withdraw_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    # Must be Razorpay owner, Bot owner, or Admin to process withdrawal
    if user_id != config.RAZORPAY_OWNER_ID and user_id != config.OWNER_ID and not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    wid = int(data.split(":")[1])
    
    req = await db.get_withdrawal_request(wid)
    if not req:
        await q.answer("Request not found", show_alert=True)
        return
        
    if req["status"] in ("approved", "rejected"):
        await q.answer(f"Already {req['status']}", show_alert=True)
        return
        
    ok = await db.approve_withdrawal_request(wid, user_id)
    if not ok:
        await q.answer("Failed to approve", show_alert=True)
        return
        
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else str(user_id)
    
    # Update owner's message
    text = (
        "✅ <b>Withdrawal Marked Done</b>\n\n"
        f"Request ID: #{wid}\n"
        f"Amount: ₹{req['amount']}\n"
        f"Requester: <code>{req['requester_id']}</code>\n"
        f"Processed By: {admin_name}"
    )
    await q.edit_message_text(text=text, parse_mode="HTML")
    
    # Notify the requester
    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=(
                f"🎉 <b>Withdrawal Approved & Transferred!</b>\n\n"
                f"💸 Amount: ₹{req['amount']}\n"
                f"🆔 Request ID: #{wid}\n"
                f"👮 Processed By: {admin_name}"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

async def withdraw_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    
    # Must be Razorpay owner, Bot owner, or Admin to process withdrawal
    if user_id != config.RAZORPAY_OWNER_ID and user_id != config.OWNER_ID and not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    wid = int(data.split(":")[1])
    
    req = await db.get_withdrawal_request(wid)
    if not req:
        await q.answer("Request not found", show_alert=True)
        return
        
    if req["status"] in ("approved", "rejected"):
        await q.answer(f"Already {req['status']}", show_alert=True)
        return
        
    ok = await db.reject_withdrawal_request(wid, user_id)
    if not ok:
        await q.answer("Failed to reject", show_alert=True)
        return
        
    admin_name = f"@{update.effective_user.username}" if update.effective_user.username else str(user_id)
    
    # Update owner's message
    text = (
        "❌ <b>Withdrawal Request Rejected</b>\n\n"
        f"Request ID: #{wid}\n"
        f"Amount: ₹{req['amount']}\n"
        f"Requester: <code>{req['requester_id']}</code>\n"
        f"Rejected By: {admin_name}"
    )
    await q.edit_message_text(text=text, parse_mode="HTML")
    
    # Notify the requester
    try:
        await context.bot.send_message(
            chat_id=req["requester_id"],
            text=(
                f"❌ <b>Withdrawal Request Rejected</b>\n\n"
                f"💸 Amount: ₹{req['amount']}\n"
                f"🆔 Request ID: #{wid}\n"
                f"👮 Rejected By: {admin_name}\n\n"
                "The requested amount has been returned to the available balance."
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass

async def admin_daily_revenue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    daily = await db.get_daily_revenue_breakdown()
    
    if not daily:
        text = (
            "📅 <b>Daily Revenue Breakdown Report</b>\n\n"
            "⚠️ <i>No sales/revenue data recorded yet.</i>"
        )
    else:
        text = "📅 <b>Daily Revenue Breakdown Report</b>\n\n"
        total_all_fiat = 0
        total_all_stars = 0
        
        for date_str, stats in list(daily.items())[:15]:
            total_all_fiat += stats['total_rs']
            total_all_stars += stats['stars']
            
            text += (
                f"🗓 <b>{date_str}</b> ({stats['count']} orders):\n"
                f"  💰 Total: <b>₹{stats['total_rs']}</b> / <b>{stats['stars']} Stars ⭐</b>\n"
                f"  ├─ 💳 Manual UPI: ₹{stats['manual']}\n"
                f"  ├─ ⚡ Razorpay: ₹{stats['razorpay']}\n"
                f"  └─ ⭐ Stars: {stats['stars']} Stars\n\n"
            )
            
        text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <b>Total Sales (Period):</b> ₹{total_all_fiat} | {total_all_stars} Stars ⭐"
        )
        
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Revenue Menu", callback_data="admin_revenue_menu")]
    ])
    
    await q.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")

async def admin_edit_plans_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    plans = await db.get_active_plans()
    
    text = (
        "🏷 <b>Edit Product/Plan Prices, Limits & Status</b>\n\n"
        "Niche diye gaye plans me se kisi ek ko select karein jise aap customize karna chahte hain:"
    )
    
    keyboard = []
    for plan_key, plan in plans.items():
        is_m = plan.get("status") == "maintenance"
        lim = plan.get("limit")
        lbl = f"📝 {plan['label']} (₹{plan['amount']} / {plan.get('stars', plan['amount'])} ⭐)"
        if is_m:
            lbl += " 🛠[Maint]"
        if lim is not None:
            lbl += f" 📦[Lim: {lim}]"
            
        keyboard.append([
            InlineKeyboardButton(
                lbl,
                callback_data=f"admin_edit_plan_select:{plan_key}"
            )
        ])
        
    keyboard.append([
        InlineKeyboardButton("🔙 Back to Config Settings", callback_data="settings_main")
    ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_edit_plan_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    plan_key = data.split(":")[1]
    
    plans = await db.get_active_plans()
    plan = plans.get(plan_key)
    if not plan:
        await q.answer("Plan not found", show_alert=True)
        return
        
    status = plan.get("status", "active")
    status_label = "🟢 Active" if status == "active" else "🛠 Under Maintenance"
    limit_val = plan.get("limit")
    limit_label = f"{limit_val} (Sold: {plan.get('sold_count', 0)})" if limit_val is not None else "None (Unlimited)"
    
    text = (
        f"⚙️ <b>Modify Plan: {plan['label']}</b>\n\n"
        f"• <b>Plan Key:</b> <code>{plan_key}</code>\n"
        f"• <b>Current Price:</b> ₹{plan['amount']}\n"
        f"• <b>Current Stars:</b> {plan.get('stars', plan['amount'])} Stars ⭐\n"
        f"• <b>Current Status:</b> {status_label}\n"
        f"• <b>Purchase Limit:</b> <code>{limit_label}</code>\n\n"
        "Aap is plan ke liye kya update karna chahte hain?"
    )
    
    toggle_label = "🔴 Disable (Maintenance)" if status == "active" else "🟢 Enable (Resume)"
    keyboard = [
        [
            InlineKeyboardButton("Change Price (₹)", callback_data=f"admin_edit_plan_field:{plan_key}:amount"),
            InlineKeyboardButton("Change Stars (⭐)", callback_data=f"admin_edit_plan_field:{plan_key}:stars")
        ],
        [
            InlineKeyboardButton("Set Limit", callback_data=f"admin_edit_plan_field:{plan_key}:limit"),
            InlineKeyboardButton(toggle_label, callback_data=f"admin_edit_plan_toggle_status:{plan_key}")
        ],
        [
            InlineKeyboardButton("🔙 Back to Plans List", callback_data="admin_edit_plans_menu")
        ]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await q.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_edit_plan_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    parts = data.split(":")
    plan_key = parts[1]
    field = parts[2] # "amount" or "stars" or "limit"
    
    context.user_data["edit_plan_key"] = plan_key
    context.user_data["edit_plan_field"] = field
    context.user_data["edit_plan_msg_id"] = q.message.message_id
    
    if field == "limit":
        field_label = "Purchase Limit"
        text = (
            f"✍️ <b>Update Plan:</b> <code>{plan_key}</code>\n\n"
            f"Please enter the new value for <b>{field_label}</b>:\n"
            f"• Send a positive number (e.g. <code>100</code>)\n"
            f"• Send <code>none</code> or <code>unlimited</code> to remove limit\n"
            f"• Cancel karne ke liye type karein: <code>cancel</code>"
        )
    else:
        field_label = "Price in Rupees (₹)" if field == "amount" else "Stars count (⭐)"
        text = (
            f"✍️ <b>Update Plan:</b> <code>{plan_key}</code>\n\n"
            f"Please enter the new value for <b>{field_label}</b>:\n"
            f"• Send a positive number (e.g. <code>50</code>)\n"
            f"• Cancel karne ke liye type karein: <code>cancel</code>"
        )
    
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="HTML"
    )

async def admin_edit_plan_toggle_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id
    if not await db.is_admin(user_id):
        await q.answer("Access denied", show_alert=True)
        return
        
    data = q.data or ""
    plan_key = data.split(":")[1]
    
    plans = await db.get_active_plans()
    plan = plans.get(plan_key)
    if not plan:
        await q.answer("Plan not found", show_alert=True)
        return
        
    current_status = plan.get("status", "active")
    new_status = "maintenance" if current_status == "active" else "active"
    
    await db.set_setting(f"plan_status:{plan_key}", new_status)
    await q.answer(f"Plan status updated to: {new_status.upper()}", show_alert=True)
    
    # Reload details menu by modifying callback data
    q.data = f"admin_edit_plan_select:{plan_key}"
    await admin_edit_plan_select_callback(update, context)



