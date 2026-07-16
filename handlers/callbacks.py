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
async def _activate_payment_plan(db: Database, req: dict, bot) -> int:
    plan_key = str(req.get("plan_key") or "")
    plan = config.PAY_PLANS.get(plan_key, {})
    days = int(req.get("plan_days") or 30)
    user_id = int(req["user_id"])
    
    # Calculate and extend subscription
    until = await db.add_premium_seconds(user_id, days * 24 * 60 * 60)
    
    # Action plan benefits: unban & generate links
    chat_id = plan.get("chat_id")
    if chat_id:
        try:
            # Unban member if banned
            await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
            # Create a one-time invite link
            invite_link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                member_limit=1,
                expire_date=int(time.time()) + 600
            )
            success_msg = (
                f"🎉 Welcome to premium VIP!\n\n"
                f"Here is your join link (valid for 10 minutes):\n"
                f"🔗 {invite_link.invite_link}"
            )
            await bot.send_message(chat_id=user_id, text=success_msg)
        except Exception as e:
            print(f"Error unbanning user or creating invite link: {e}")
            await bot.send_message(
                chat_id=user_id,
                text="🎉 Premium Activated! Group join links generation failed. Please contact admin to get access."
            )
    elif plan_key == 'getpin':
        await bot.send_message(
            chat_id=user_id,
            text="✅ <b>Payment Verified!</b>\n\nAb apne app me jao jisme getpin hatana hai, aur <b>getpin wale page ka screenshot</b> yahan bhejo.",
            parse_mode='HTML'
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
            await context.bot.send_message(chat_id=aid, text=note, reply_markup=kb, parse_mode="HTML")
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
        
    plan = config.PAY_PLANS.get(req['plan_key'], {"label": req['plan_key']})
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
            
        until = await _activate_payment_plan(db, req, context.bot)
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
        plan = config.PAY_PLANS.get(req['plan_key'], {"label": req['plan_key']})
        plan_name = plan.get("label", req['plan_key'])
        
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
            await context.bot.send_message(chat_id=int(req["user_id"]), text=success_text)
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
    plan = config.PAY_PLANS.get(plan_key)
    if not plan:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Invalid plan.")
        return
        
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
    gateway = await db.get_setting("payment_gateway") or "manual"
    
    req = await db.get_payment_request(rid)
    
    if gateway == "stars":
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
            
        except Exception as e:
            print(f"Stars invoice error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="⚠️ Failed to generate invoice. Please contact the admin."
            )
            
    elif gateway == "razorpay":
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
            
    else:
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
        except Exception as e:
            print(f"Manual payment QR error: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"QR Code generation failed. Please transfer directly to the UPI ID: <code>{upi_id}</code>\nNote: {note}",
                parse_mode="HTML"
            )

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
            
        until = await _activate_payment_plan(db, req, context.bot)
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
            
        await q.edit_message_text(
            text=(
                f"✅ <b>Payment Approved</b>\n\n"
                f"Request ID: #{rid}\n"
                f"User ID: {req['user_id']}\n"
                f"Plan: {req['plan_key']}\n"
                f"Approved By: {admin_name}"
            ),
            parse_mode="HTML"
        )
        
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
            
        await q.edit_message_text(
            text=(
                f"❌ <b>Payment Rejected</b>\n\n"
                f"Request ID: #{rid}\n"
                f"User ID: {req['user_id']}\n"
                f"Plan: {req['plan_key']}\n"
                f"Rejected By: {admin_name}"
            ),
            parse_mode="HTML"
        )
        
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
    
    if until > now:
        expiry_str = datetime.datetime.utcfromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            "📦 <b>My Premium Subscriptions</b>\n\n"
            f"👑 Status: <b>VIP Premium Member</b>\n"
            f"⏳ Expiry Date: <code>{expiry_str}</code>"
        )
    else:
        text = (
            "📦 <b>My Premium Subscriptions</b>\n\n"
            "Aapke paas abhi koi active premium plan nahi hai.\n"
            "Kripya main menu ya /plan command se purchase karein."
        )
        
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
    
    data = q.data or ""
    kb = []
    
    if data == 'info_direct':
        text = (
            "🎮 <b>Direct Mods (1 Month)</b>\n\n"
            "Get direct access to our Premium Private Channel for 1 month.\n\n"
            "<b>Price: ₹35 / 35 Stars ⭐</b>"
        )
        kb.append([InlineKeyboardButton("💳 Buy VIP access", callback_data="payplan:direct")])
    elif data == 'info_getpin':
        text = (
            "🚫 <b>No Getpin (1 Month)</b>\n\n"
            "Remove getpin for 1 month for any ONE Apk.\n\n"
            "⚠️ <b>After payment, open the apk, go to the getpin page, take a screenshot, and send it here.</b>\n\n"
            "<b>Price: ₹30 / 30 Stars ⭐</b>"
        )
        kb.append([InlineKeyboardButton("💳 Buy VIP access", callback_data="payplan:getpin")])
    elif data == 'info_faphouse':
        text = (
            "🔥 <b>Faphouse Paid VIP</b>\n\n"
            "Get Faphouse paid mod (No getpin needed).\n"
            "🔗 After payment, you will get a one-time group link to join.\n\n"
            "Select subscription duration:"
        )
        kb.append([
            InlineKeyboardButton("🔥 3 Days (₹15)", callback_data="payplan:faphouse_3"),
            InlineKeyboardButton("🔥 7 Days (₹25)", callback_data="payplan:faphouse_7")
        ])
        
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
        
    until = await _activate_payment_plan(db, req, context.bot)
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
        plan = config.PAY_PLANS.get(req['plan_key'], {"label": req['plan_key']})
        plan_name = plan.get("label", req['plan_key'])
        
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
        await context.bot.send_message(chat_id=int(req["user_id"]), text=success_text)
    except Exception:
        pass
        
    await db.clear_payment_ui_messages(int(req["id"]))

# --- ADDITIONAL MANUAL VERIFICATION ADMIN CALLBACKS ---

async def generic_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    
    data = q.data or ""
    target_uid = int(data.split(":")[1])
    try:
        await context.bot.send_message(
            chat_id=target_uid,
            text="✅ <b>Order Completed!</b>\n\nAapka getpin order successfully complete aur activate ho gaya hai.",
            parse_mode="HTML"
        )
        await q.edit_message_caption("✅ Order Completed!")
    except Exception as e:
        await q.answer(f"Failed to notify user: {e}", show_alert=True)

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
    
    context.user_data["admin_reply_target"] = target_uid
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"💬 Reply enter karein jo user <code>{target_uid}</code> ko jayega:",
        parse_mode="HTML"
    )
