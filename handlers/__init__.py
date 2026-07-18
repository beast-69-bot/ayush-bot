from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters
)
from handlers.commands import start, plan_menu, make_admin, details, pay_lookup, cmd_settings, admin_menu_cmd
from handlers.callbacks import (
    pay_callback, pay_cancel_callback, pay_utr_callback, pay_admin_callback,
    my_orders_callback, settings_gw_callback, settings_field_callback,
    info_screens_callback, rc_menu_callback, report_issue_callback,
    contact_admin_callback, back_main_callback,
    pre_checkout_callback, successful_payment_callback,
    generic_done_callback, generic_ban_callback, generic_reply_callback,
    settings_main_callback, admin_orders_menu_callback, admin_view_order_callback,
    admin_revenue_menu_callback, admin_broadcast_menu_callback, broadcast_confirm_callback,
    back_admin_main_callback, admin_close_callback, admin_action_callback,
    pay_gw_choice_callback, back_plans_callback,
    admin_withdraw_menu_callback, admin_withdraw_req_prompt_callback, withdraw_done_callback,
    admin_group_menu_callback, admin_connect_group_callback,
    admin_edit_plans_menu_callback, admin_edit_plan_select_callback, admin_edit_plan_field_callback
)
from handlers.messages import handle_incoming_messages

def register_all_handlers(app: Application) -> None:
    # 1. Command Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("plan", start))
    app.add_handler(CommandHandler("pay", start))
    app.add_handler(CommandHandler("makeadmin", make_admin))
    app.add_handler(CommandHandler("details", details))
    app.add_handler(CommandHandler("paylookup", pay_lookup))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("setpay", cmd_settings))
    app.add_handler(CommandHandler("admin", admin_menu_cmd))

    # 2. Callback Query Handlers
    app.add_handler(CallbackQueryHandler(info_screens_callback, pattern=r"^info_"))
    app.add_handler(CallbackQueryHandler(pay_callback, pattern=r"^payplan:"))
    app.add_handler(CallbackQueryHandler(pay_cancel_callback, pattern=r"^paycancel:"))
    app.add_handler(CallbackQueryHandler(pay_utr_callback, pattern=r"^payutr:"))
    app.add_handler(CallbackQueryHandler(pay_admin_callback, pattern=r"^payadm:"))
    app.add_handler(CallbackQueryHandler(my_orders_callback, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(rc_menu_callback, pattern=r"^rc_menu$"))
    app.add_handler(CallbackQueryHandler(report_issue_callback, pattern=r"^report_issue$"))
    app.add_handler(CallbackQueryHandler(contact_admin_callback, pattern=r"^contact_admin$"))
    app.add_handler(CallbackQueryHandler(back_main_callback, pattern=r"^back_main$"))
    
    # Settings callbacks
    app.add_handler(CallbackQueryHandler(settings_gw_callback, pattern=r"^settings_gw:"))
    app.add_handler(CallbackQueryHandler(settings_field_callback, pattern=r"^settings_field:"))
    app.add_handler(CallbackQueryHandler(settings_main_callback, pattern=r"^settings_main$"))
    
    # New Admin Panel callback query handlers
    app.add_handler(CallbackQueryHandler(admin_orders_menu_callback, pattern=r"^admin_orders_menu$"))
    app.add_handler(CallbackQueryHandler(admin_view_order_callback, pattern=r"^admin_view_order:"))
    app.add_handler(CallbackQueryHandler(admin_revenue_menu_callback, pattern=r"^admin_revenue_menu$"))
    app.add_handler(CallbackQueryHandler(admin_broadcast_menu_callback, pattern=r"^admin_broadcast_menu$"))
    app.add_handler(CallbackQueryHandler(broadcast_confirm_callback, pattern=r"^broadcast_confirm:"))
    app.add_handler(CallbackQueryHandler(back_admin_main_callback, pattern=r"^back_admin_main$"))
    app.add_handler(CallbackQueryHandler(admin_close_callback, pattern=r"^admin_close$"))
    app.add_handler(CallbackQueryHandler(admin_action_callback, pattern=r"^admin_action:"))
    
    # Withdrawal callbacks
    app.add_handler(CallbackQueryHandler(admin_withdraw_menu_callback, pattern=r"^admin_withdraw_menu$"))
    app.add_handler(CallbackQueryHandler(admin_withdraw_req_prompt_callback, pattern=r"^admin_withdraw_req_prompt$"))
    app.add_handler(CallbackQueryHandler(withdraw_done_callback, pattern=r"^withdraw_done:"))
    
    # Group Connect callbacks
    app.add_handler(CallbackQueryHandler(admin_group_menu_callback, pattern=r"^admin_group_menu$"))
    app.add_handler(CallbackQueryHandler(admin_connect_group_callback, pattern=r"^admin_connect_group:"))
    
    # Plan Price & Stars Edit callbacks
    app.add_handler(CallbackQueryHandler(admin_edit_plans_menu_callback, pattern=r"^admin_edit_plans_menu$"))
    app.add_handler(CallbackQueryHandler(admin_edit_plan_select_callback, pattern=r"^admin_edit_plan_select:"))
    app.add_handler(CallbackQueryHandler(admin_edit_plan_field_callback, pattern=r"^admin_edit_plan_field:"))
    
    # Donation & Gateway Choice handlers
    app.add_handler(CallbackQueryHandler(pay_gw_choice_callback, pattern=r"^paygwchoice:"))
    app.add_handler(CallbackQueryHandler(back_plans_callback, pattern=r"^back_plans$"))
    
    # Generic admin action callbacks
    app.add_handler(CallbackQueryHandler(generic_done_callback, pattern=r"^done:"))
    app.add_handler(CallbackQueryHandler(generic_ban_callback, pattern=r"^ban:"))
    app.add_handler(CallbackQueryHandler(generic_reply_callback, pattern=r"^reply:"))

    # 3. Telegram Stars checkout handlers
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    # 4. Message Handlers (handles text, photo, and documents)
    # Ignore commands and successful payments which are handled above
    app.add_handler(MessageHandler(
        (filters.TEXT | filters.PHOTO | filters.Document.ALL) & ~filters.COMMAND & ~filters.SUCCESSFUL_PAYMENT,
        handle_incoming_messages
    ))
