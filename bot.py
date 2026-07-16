import logging
from telegram.ext import Application
from config import BOT_TOKEN
from database import Database
from scheduler_tasks import check_expiry
from handlers import register_all_handlers

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(application: Application) -> None:
    """Initialize database and schedule background tasks on bot startup."""
    db = Database('premium_store.db')
    await db.init()
    application.bot_data["db"] = db
    
    # Schedule the premium membership expiry checker (runs every 60 minutes)
    application.job_queue.run_repeating(check_expiry, interval=3600, first=10)
    logger.info("Database initialized and background expiry job scheduled.")

async def post_shutdown(application: Application) -> None:
    """Close database connections when bot shuts down."""
    db = application.bot_data.get("db")
    if db:
        await db.close()
        logger.info("Database connection closed.")

def main() -> None:
    """Start the bot."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not defined in config or env. Exiting.")
        return

    # Build python-telegram-bot application
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register command, callback, message, and checkout handlers
    register_all_handlers(application)

    # Start the Bot
    logger.info("Elite Premium Store Bot (Modular Async v20) is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
