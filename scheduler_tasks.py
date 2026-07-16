import time
import asyncio
import logging
from telegram.ext import ContextTypes
from database import Database
import config

logger = logging.getLogger(__name__)

async def check_expiry(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job to check and process expired premium memberships."""
    db: Database = context.application.bot_data["db"]
    now = int(time.time())
    
    try:
        # Fetch users whose premium subscription has expired
        async with db.conn.execute(
            "SELECT user_id FROM users WHERE premium_until > 0 AND premium_until <= ?", 
            (now,)
        ) as cursor:
            expired = await cursor.fetchall()
            
        faphouse_group_str = await db.get_setting("faphouse_group_id")
        faphouse_chat_id = None
        if faphouse_group_str:
            try:
                faphouse_chat_id = int(faphouse_group_str)
            except ValueError:
                pass

        direct_group_str = await db.get_setting("direct_group_id")
        direct_chat_id = None
        if direct_group_str:
            try:
                direct_chat_id = int(direct_group_str)
            except ValueError:
                pass

        for (user_id,) in expired:
            logger.info(f"Subscription expired for user: {user_id}")
            
            # Kick user from the premium channel, config group, and custom connected groups (if configured)
            chats_to_kick = [config.CHANNEL_ID, config.GROUP_ID]
            if faphouse_chat_id:
                chats_to_kick.append(faphouse_chat_id)
            if direct_chat_id:
                chats_to_kick.append(direct_chat_id)
                
            for chat_id in chats_to_kick:
                if chat_id:
                    try:
                        # Ban and then unban immediately to kick user out
                        await context.bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
                        await asyncio.sleep(0.5)
                        await context.bot.unban_chat_member(chat_id=chat_id, user_id=user_id)
                    except Exception as e:
                        logger.error(f"Failed to kick expired user {user_id} from chat {chat_id}: {e}")
            
            # Reset user's premium status in database
            await db.set_premium_until(user_id, 0)
            
            # Check if user's last plan was getpin
            is_getpin = False
            try:
                async with db.conn.execute(
                    "SELECT plan_key FROM payment_requests WHERE user_id=? AND status='processed' ORDER BY processed_at DESC LIMIT 1",
                    (user_id,)
                ) as p_cursor:
                    p_row = await p_cursor.fetchone()
                    if p_row and p_row[0] == 'getpin':
                        is_getpin = True
            except Exception as e:
                logger.error(f"Failed to query last plan type for user {user_id}: {e}")
            
            # Notify the user
            try:
                if is_getpin:
                    msg = "⏰ Aapka No Getpin (1 Month) plan end ho gaya hai. Dobara access ke liye /plan dabayein."
                else:
                    msg = "⏰ Aapka premium plan khatam ho gaya. Dobara access ke liye /plan dabayein."
                await context.bot.send_message(
                    chat_id=user_id,
                    text=msg
                )
            except Exception:
                pass
                
    except Exception as e:
        logger.error(f"Error running check_expiry job: {e}")
