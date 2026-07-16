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
            
        for (user_id,) in expired:
            logger.info(f"Subscription expired for user: {user_id}")
            
            # Kick user from the premium channel and group (if configured)
            for chat_id in (config.CHANNEL_ID, config.GROUP_ID):
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
            
            # Notify the user
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⏰ Aapka premium plan khatam ho gaya. Dobara access ke liye /plan dabayein."
                )
            except Exception:
                pass
                
    except Exception as e:
        logger.error(f"Error running check_expiry job: {e}")
