"""
Integration wrapper to run chat API alongside the main bot
Add this to your main.py to enable the chat web app
"""

import threading
import logging

def start_chat_api(db_pool):
    """
    Start the chat API server in a separate thread
    
    Args:
        db_pool: The asyncpg connection pool from main bot
    """
    try:
        import asyncio
        from chat.chat_api_integrated import run_api_server
        
        # Get the current event loop (bot's event loop)
        event_loop = asyncio.get_event_loop()
        
        # Run Flask in a separate thread, passing both pool and event loop
        chat_thread = threading.Thread(
            target=run_api_server,
            args=(db_pool, event_loop),
            daemon=True
        )
        chat_thread.start()
        logging.info("Chat API server started successfully")
        
    except ImportError as e:
        logging.error(f"Failed to import chat API: {e}")
        logging.error("Make sure Flask and Flask-CORS are installed: pip install Flask Flask-CORS")
    except Exception as e:
        logging.error(f"Failed to start chat API server: {e}")


# HOW TO USE:
# 1. Install Flask dependencies: pip install Flask Flask-CORS
# 2. Add this to your main.py after the database pool is created:
#
#    from chat.start_chat_api import start_chat_api
#
#    async def setup():
#        global db, bot_info
#        db = await create_db_pool()
#        bot_info = await bot.get_me()
#        
#        # Start chat API server
#        start_chat_api(db)
#        
#        # ... rest of your setup code
#
# 3. Run your bot normally: python main.py
# 4. The chat API will be available at http://localhost:5001
