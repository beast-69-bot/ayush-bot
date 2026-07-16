import re
import html
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
import config
from handlers.callbacks import _notify_payment_admins, _update_payment_user_status, _delete_payment_qr_message
from handlers.commands import settings_menu

async def handle_incoming_messages(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat or not update.effective_message:
        return

    db: Database = context.application.bot_data["db"]
    user_id = update.effective_user.id

    # 1. Block Banned Users
    if await db.is_bot_banned(user_id):
        return

    # 1b. Admin Broadcast Handler
    if context.user_data.get("awaiting_broadcast"):
        if not await db.is_admin(user_id):
            return
            
        txt = update.effective_message.text or ""
        if txt.strip().lower() == "cancel":
            context.user_data.pop("awaiting_broadcast", None)
            context.user_data.pop("broadcast_menu_msg_id", None)
            await update.effective_message.reply_text("❌ Broadcast cancelled.")
            from handlers.commands import show_admin_menu
            await show_admin_menu(update, context)
            return
            
        # Copy the message back as preview to the admin
        try:
            preview_msg = await context.bot.copy_message(
                chat_id=update.effective_chat.id,
                from_chat_id=update.effective_chat.id,
                message_id=update.effective_message.message_id
            )
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Failed to generate preview: {e}")
            return
            
        context.user_data["broadcast_template_msg_id"] = update.effective_message.message_id
        context.user_data["broadcast_template_chat_id"] = update.effective_chat.id
        
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Start Broadcast", callback_data="broadcast_confirm:send"),
                InlineKeyboardButton("❌ Cancel", callback_data="broadcast_confirm:cancel")
            ]
        ])
        
        await update.effective_message.reply_text(
            "☝️ <b>This is a preview of your broadcast message.</b>\n\n"
            "Confirm if you want to send this to all users:",
            reply_markup=kb,
            parse_mode="HTML"
        )
        return

    # 1c. Admin Withdrawal Handler
    if context.user_data.get("awaiting_withdraw_amount"):
        if not await db.is_admin(user_id):
            return
            
        txt = (update.effective_message.text or "").strip()
        if txt.lower() == "cancel":
            context.user_data.pop("awaiting_withdraw_amount", None)
            menu_msg_id = context.user_data.pop("withdraw_menu_msg_id", None)
            if menu_msg_id:
                try:
                    await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=menu_msg_id)
                except Exception:
                    pass
            await update.effective_message.reply_text("❌ Withdrawal request cancelled.")
            from handlers.commands import show_admin_menu
            await show_admin_menu(update, context)
            return
            
        try:
            amount = int(txt)
            if amount <= 0:
                raise ValueError()
        except ValueError:
            await update.effective_message.reply_text("⚠️ Please enter a valid positive integer amount (e.g. 500).")
            return
            
        # Calculate Razorpay revenue and left amount
        payments = await db.list_all_processed_payments()
        gw_razorpay = 0
        for p in payments:
            gw = "manual"
            gw_extra_str = p.get("gateway_extra")
            if gw_extra_str:
                try:
                    import json
                    gw_extra = json.loads(gw_extra_str)
                    gw = gw_extra.get("gateway", "manual")
                except Exception:
                    if "razorpay" in gw_extra_str.lower():
                        gw = "razorpay"
            if gw == "razorpay":
                gw_razorpay += p.get("amount_rs", 0)
                
        total_withdrawn = await db.get_total_withdrawn()
        left_to_withdraw = max(0, gw_razorpay - total_withdrawn)
        
        if amount > left_to_withdraw:
            await update.effective_message.reply_text(
                f"❌ Cannot withdraw ₹{amount}. Max available is ₹{left_to_withdraw}.\n"
                "Please enter a valid amount or type `cancel` to abort."
            )
            return
            
        context.user_data.pop("awaiting_withdraw_amount", None)
        menu_msg_id = context.user_data.pop("withdraw_menu_msg_id", None)
        if menu_msg_id:
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=menu_msg_id)
            except Exception:
                pass
        
        # Save request to database
        wid = await db.create_withdrawal_request(user_id, amount)
        
        # Get target Razorpay Owner ID
        target_owner_id = config.RAZORPAY_OWNER_ID
        
        # Send message to owner
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Mark Done", callback_data=f"withdraw_done:{wid}")]
        ])
        
        requester_name = f"@{update.effective_user.username}" if update.effective_user.username else str(user_id)
        owner_msg = (
            "💸 <b>New Withdrawal Request!</b>\n\n"
            f"🆔 Request ID: #{wid}\n"
            f"👤 Requester: {requester_name} (ID: <code>{user_id}</code>)\n"
            f"💰 Amount: <b>₹{amount}</b>\n\n"
            "Click the button below once you have processed/transferred the money:"
        )
        
        sent_to_owner = False
        if target_owner_id:
            try:
                await context.bot.send_message(
                    chat_id=target_owner_id,
                    text=owner_msg,
                    reply_markup=kb,
                    parse_mode="HTML"
                )
                sent_to_owner = True
            except Exception as e:
                print(f"Error notifying Razorpay owner: {e}")
                
        if sent_to_owner:
            await update.effective_message.reply_html(
                f"✅ Withdrawal request of <b>₹{amount}</b> sent to Razorpay Owner successfully (ID: <code>{target_owner_id}</code>)."
            )
        else:
            await update.effective_message.reply_html(
                f"⚠️ Withdrawal request created, but failed to notify Razorpay Owner (ID: <code>{target_owner_id}</code>).\n"
                "Please notify them manually."
            )
            
        return

    # 2. Admin Settings Field Modification
    edit_field = context.user_data.get("settings_edit_field")
    if edit_field:
        new_val = update.effective_message.text
        if new_val is not None:
            new_val = new_val.strip()
            await db.set_setting(edit_field, new_val)
            context.user_data.pop("settings_edit_field", None)
            
            # Retrieve the menu message ID to update it
            menu_msg_id = context.user_data.pop("settings_edit_msg_id", None)
            
            await update.effective_message.reply_html(f"✅ Setting <code>{edit_field}</code> updated successfully!")
            
            # Show updated menu
            await settings_menu(update, context, message_id=menu_msg_id)
        else:
            await update.effective_message.reply_text("⚠️ Please send a valid text value.")
        return

    # 3. Admin Send Reply Handler
    admin_reply_target = context.user_data.get("admin_reply_target")
    if admin_reply_target:
        if not await db.is_admin(user_id):
            return
        reply_text = update.effective_message.text
        if reply_text:
            context.user_data.pop("admin_reply_target", None)
            try:
                await context.bot.send_message(
                    chat_id=admin_reply_target,
                    text=f"💬 <b>Admin Replied:</b>\n\n{html.escape(reply_text)}",
                    parse_mode="HTML"
                )
                await update.effective_message.reply_text("✅ Reply sent successfully!")
            except Exception as e:
                await update.effective_message.reply_text(f"❌ Failed to send reply: {e}")
        else:
            await update.effective_message.reply_text("⚠️ Please enter a text message to reply.")
        return

    # 4. User Submit Report Handler
    if context.user_data.get("awaiting_report"):
        report_text = update.effective_message.text
        if report_text:
            context.user_data.pop("awaiting_report", None)
            await db.add_report(user_id, time.time())
            
            # Forward report to all admins
            admin_ids = await db.list_admin_ids()
            targets = {config.OWNER_ID, *admin_ids}
            
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Reply to User", callback_data=f"reply:{user_id}")]
            ])
            
            admin_msg = (
                f"🚨 <b>New Support Report!</b>\n"
                f"From: <code>{user_id}</code>\n\n"
                f"<blockquote>{html.escape(report_text)}</blockquote>"
            )
            for aid in targets:
                if aid:
                    try:
                        await context.bot.send_message(chat_id=aid, text=admin_msg, reply_markup=kb, parse_mode="HTML")
                    except Exception:
                        pass
            await update.effective_message.reply_text("✅ Aapki report admin ko bhej di gayi hai. Jaldi hi reply aayega.")
        else:
            await update.effective_message.reply_text("⚠️ Please describe your issue in a text message.")
        return

    # 5. User Submit UTR / Screenshot
    pay_utr_rid = context.user_data.get("pay_utr_request_id")
    if pay_utr_rid:
        req = await db.get_payment_request(int(pay_utr_rid))
        if not req:
            context.user_data.pop("pay_utr_request_id", None)
            await update.effective_message.reply_text("❌ Payment request expired/invalid. Please run /pay again.")
            return

        now = int(time.time())
        if req["status"] == "pending" and req["expires_at"] <= now:
            await db.expire_payment_request_if_pending(req["id"])
            await _delete_payment_qr_message(req, context)
            await _update_payment_user_status(req, context, "Payment Timeout", ["Status: <b>Expired</b>"])
            context.user_data.pop("pay_utr_request_id", None)
            await update.effective_message.reply_text("⏳ Payment request expired. Please run /pay again.")
            return

        # Extract UTR text or media info
        utr_text = (update.effective_message.text or "").strip()
        has_media = False
        
        if not utr_text:
            if update.effective_message.photo:
                utr_text = f"screenshot:{update.effective_message.photo[-1].file_id[:15]}"
                has_media = True
            elif update.effective_message.document:
                utr_text = f"document:{update.effective_message.document.file_id[:15]}"
                has_media = True
            else:
                await update.effective_message.reply_text("⚠️ Please send a 12-digit UTR text or a payment screenshot.")
                return

        # Validate UTR text formatting if it's text input (must be a 12-digit number)
        if not has_media:
            if not re.match(r"^\d{12}$", utr_text):
                await update.effective_message.reply_text("❌ Invalid format! UTR must be exactly a 12-digit number. Please enter it again.")
                return

        ok = await db.set_payment_utr(req["id"], utr_text)
        context.user_data.pop("pay_utr_request_id", None)
        
        if not ok:
            await update.effective_message.reply_text("❌ Payment request expired/invalid. Please run /pay again.")
            return

        # Reload updated request
        updated_req = await db.get_payment_request(req["id"])
        
        # Notify admins and forward copy of media proof if present
        admin_ids = await db.list_admin_ids()
        targets = {config.OWNER_ID, *admin_ids}
        
        await _notify_payment_admins(context, updated_req, utr_text)
        
        # Forward the actual screenshot/file proof message to admins
        for aid in targets:
            if aid:
                try:
                    await context.bot.copy_message(
                        chat_id=aid,
                        from_chat_id=update.effective_chat.id,
                        message_id=update.effective_message.message_id
                    )
                except Exception:
                    pass

        await _delete_payment_qr_message(updated_req, context)
        await _update_payment_user_status(
            updated_req,
            context,
            "Payment Verification Underway",
            [
                "Status: <b>Verification Pending</b>",
                "Our team is manually verifying your payment.",
                "Premium VIP benefits will activate immediately upon verification."
            ]
        )
        
        await update.effective_message.reply_text(
            "✅ Proof submitted successfully!\n"
            "Our admin team has been notified. Verification normally takes a few minutes."
        )
        return

    # 6. User Submit Getpin Screenshot
    if context.user_data.get("awaiting_getpin_ss"):
        if update.effective_message.photo:
            context.user_data.pop("awaiting_getpin_ss", None)
            
            admin_ids = await db.list_admin_ids()
            targets = {config.OWNER_ID, *admin_ids}
            
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Order Completed", callback_data=f"done:{user_id}"),
                    InlineKeyboardButton("🚫 Ban User", callback_data=f"ban:{user_id}")
                ]
            ])
            
            # Send photo to admins
            photo_file_id = update.effective_message.photo[-1].file_id
            for aid in targets:
                if aid:
                    try:
                        await context.bot.send_photo(
                            chat_id=aid,
                            photo=photo_file_id,
                            caption=f"📸 <b>Getpin Screenshot Received</b>\nUser ID: <code>{user_id}</code>",
                            reply_markup=kb,
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                        
            await update.effective_message.reply_text("✅ Screenshot mil gayi! Admin verify karke jald hi order complete karenge.")
        else:
            await update.effective_message.reply_text("⚠️ Please send the getpin page screenshot as a photo.")
        return
