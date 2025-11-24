"""
Chat Management API for AAU Confessions Bot
Integrated version that uses the main bot's database pool
"""

import os
import logging
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from typing import Optional
import asyncio

from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

# Import the database pool from main bot
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

# We'll access the db pool and bot instance after they're initialized
db_pool = None
main_event_loop = None
bot_instance = None  # Will store reference to the bot for sending notifications
# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Flask app setup
app = Flask(__name__)

# Configure CORS - Allow all origins for development
CORS(app, resources={
    r"/api/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
        "expose_headers": ["Content-Type"],
        "supports_credentials": False
    }
})

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKENS")  # Note: Uses BOT_TOKENS to match main.py
PORT = int(os.getenv("CHAT_API_PORT", "5001"))

# Development mode - set to True for local testing without Telegram
# In production, this should be False or remove this line
DEV_MODE = os.getenv("CHAT_API_DEV_MODE", "true").lower() == "true"

if DEV_MODE:
    logger.warning("⚠️  DEVELOPMENT MODE ENABLED - Authentication is bypassed!")
    logger.warning("⚠️  Set CHAT_API_DEV_MODE=false in production!")


def set_db_pool(pool, event_loop, bot):
    """Set the database pool, event loop, and bot instance from main bot"""
    global db_pool, main_event_loop, bot_instance
    db_pool = pool
    main_event_loop = event_loop
    bot_instance = bot
    logger.info("Database pool, event loop, and bot instance set from main bot")


def run_async(coro):
    """
    Run an async coroutine in the main bot's event loop from Flask's thread.
    This is necessary because Flask runs in a separate thread.
    """
    future = asyncio.run_coroutine_threadsafe(coro, main_event_loop)
    return future.result()  # Wait for result


def verify_telegram_web_app_data(init_data: str) -> Optional[dict]:
    """
    Verify Telegram WebApp initData authenticity
    Returns user data if valid, None otherwise
    """
    try:
        # Parse the init data
        parsed_data = dict(parse_qsl(init_data))
        
        # Extract hash
        received_hash = parsed_data.pop('hash', None)
        if not received_hash:
            logger.warning("No hash in initData")
            return None
        
        # Create data check string
        data_check_arr = [f"{k}={v}" for k, v in sorted(parsed_data.items())]
        data_check_string = '\n'.join(data_check_arr)
        
        # Compute secret key
        secret_key = hmac.new(
            "WebAppData".encode(),
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()
        
        # Compute hash
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Verify hash
        if computed_hash != received_hash:
            logger.warning("Hash verification failed")
            return None
        
        # Parse and return user data
        user_data = json.loads(parsed_data.get('user', '{}'))
        return user_data
        
    except Exception as e:
        logger.error(f"Error verifying initData: {e}")
        return None


def require_auth(f):
    """Decorator to require authentication"""
    def decorated_function(*args, **kwargs):
        # Development mode bypass
        if DEV_MODE:
            # Use a test user ID in development
            request.user_id = 7388700051  # Mock user ID for testing
            logger.debug("DEV MODE: Using mock user ID")
            return f(*args, **kwargs)
        
        # Get auth header
        auth_header = request.headers.get('Authorization', '')
        
        if not auth_header.startswith('twa '):
            return jsonify({"error": "Unauthorized"}), 401
        
        init_data = auth_header[4:]  # Remove 'twa ' prefix
        user_data = verify_telegram_web_app_data(init_data)
        
        if not user_data:
            return jsonify({"error": "Invalid authentication"}), 401
        
        # Attach user_id to request
        request.user_id = user_data.get('id')
        return f(*args, **kwargs)
    
    decorated_function.__name__ = f.__name__
    return decorated_function


@app.route('/api/auth/verify', methods=['POST'])
def verify_auth():
    """Verify Telegram WebApp authentication"""
    try:
        data = request.get_json()
        init_data = data.get('initData', '')
        
        user_data = verify_telegram_web_app_data(init_data)
        
        if user_data:
            return jsonify({
                "valid": True,
                "user": user_data
            })
        else:
            return jsonify({"valid": False}), 401
            
    except Exception as e:
        logger.error(f"Error in auth verification: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/chats', methods=['GET'])
@require_auth
def get_chats():
    """Get all active chats for the authenticated user"""
    try:
        user_id = request.user_id
        
        async def fetch_chats():
            async with db_pool.acquire() as conn:
                # Get all accepted chat requests with last message time
                chats = await conn.fetch("""
                    SELECT
                        CASE
                            WHEN cr.requester_id = $1 THEN cr.recipient_id
                            ELSE cr.requester_id
                        END AS partner_id,
                        cr.created_at,
                        (
                            SELECT MAX(cm.created_at)
                            FROM chat_messages cm
                            WHERE (cm.sender_id = $1 AND cm.recipient_id = (
                                CASE WHEN cr.requester_id = $1 THEN cr.recipient_id ELSE cr.requester_id END
                            )) OR (cm.sender_id = (
                                CASE WHEN cr.requester_id = $1 THEN cr.recipient_id ELSE cr.requester_id END
                            ) AND cm.recipient_id = $1)
                        ) AS last_message_time
                    FROM chat_requests cr
                    WHERE (cr.requester_id = $1 OR cr.recipient_id = $1) AND cr.status = 'accepted'
                    ORDER BY last_message_time DESC NULLS LAST, cr.created_at DESC
                """, user_id)
                
                chat_list = []
                for chat in chats:
                    partner_id = chat['partner_id']
                    
                    # Get partner info
                    partner_info = await conn.fetchrow(
                        "SELECT nickname, profile_emoji FROM user_status WHERE user_id = $1",
                        partner_id
                    )
                    
                    # Get last message
                    last_message = await conn.fetchrow("""
                        SELECT text, sticker_file_id, animation_file_id, created_at, sender_id
                        FROM chat_messages
                        WHERE (sender_id = $1 AND recipient_id = $2) OR (sender_id = $2 AND recipient_id = $1)
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, user_id, partner_id)
                    
                    # Count unread messages (messages from partner that are newer than user's last login)
                    # For simplicity, we'll count messages from partner in the last 7 days
                    # In a production system, you'd track last_read_time per chat
                    unread_count = await conn.fetchval("""
                        SELECT COUNT(*)
                        FROM chat_messages
                        WHERE sender_id = $1 AND recipient_id = $2
                        AND created_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
                    """, partner_id, user_id)
                    
                    chat_item = {
                        "partnerId": partner_id,
                        "partnerName": partner_info['nickname'] if partner_info else "Anonymous",
                        "partnerEmoji": partner_info['profile_emoji'] if partner_info else "👤",
                        "lastMessage": None,
                        "lastMessageTime": None,
                        "unreadCount": unread_count or 0
                    }
                    
                    if last_message:
                        if last_message['text']:
                            chat_item['lastMessage'] = last_message['text']
                        elif last_message['sticker_file_id']:
                            chat_item['lastMessage'] = "🎨 Sticker"
                        elif last_message['animation_file_id']:
                            chat_item['lastMessage'] = "🎬 GIF"
                        
                        chat_item['lastMessageTime'] = last_message['created_at'].isoformat()
                        chat_item['isOwn'] = last_message['sender_id'] == user_id
                    
                    chat_list.append(chat_item)
                
                return chat_list
        
        chats = run_async(fetch_chats())
        
        return jsonify({"chats": chats})
        
    except Exception as e:
        logger.error(f"Error fetching chats: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/chats/<int:partner_id>/messages', methods=['GET'])
@require_auth
def get_messages(partner_id):
    """Get message history with a specific partner"""
    try:
        user_id = request.user_id
        limit = int(request.args.get('limit', 50))
        before_id = request.args.get('before')
        
        async def fetch_messages():
            async with db_pool.acquire() as conn:
                # Verify chat exists
                chat_exists = await conn.fetchval("""
                    SELECT 1 FROM chat_requests
                    WHERE ((requester_id = $1 AND recipient_id = $2) OR (requester_id = $2 AND recipient_id = $1))
                    AND status = 'accepted'
                """, user_id, partner_id)
                
                if not chat_exists:
                    return None
                
                # Get messages
                query = """
                    SELECT id, sender_id, recipient_id, text, sticker_file_id, animation_file_id, created_at
                    FROM chat_messages
                    WHERE (sender_id = $1 AND recipient_id = $2) OR (sender_id = $2 AND recipient_id = $1)
                """
                
                params = [user_id, partner_id]
                
                if before_id:
                    query += " AND id < $3"
                    params.append(int(before_id))
                
                query += " ORDER BY created_at DESC LIMIT $" + str(len(params) + 1)
                params.append(limit)
                
                messages = await conn.fetch(query, *params)
                
                # Get partner info
                partner_info = await conn.fetchrow(
                    "SELECT nickname, profile_emoji FROM user_status WHERE user_id = $1",
                    partner_id
                )
                
                message_list = []
                for msg in reversed(messages):
                    message_item = {
                        "id": msg['id'],
                        "senderId": msg['sender_id'],
                        "isOwn": msg['sender_id'] == user_id,
                        "text": msg['text'],
                        "hasSticker": msg['sticker_file_id'] is not None,
                        "hasAnimation": msg['animation_file_id'] is not None,
                        "timestamp": msg['created_at'].isoformat()
                    }
                    message_list.append(message_item)
                
                return {
                    "messages": message_list,
                    "partner": {
                        "id": partner_id,
                        "name": partner_info['nickname'] if partner_info else "Anonymous",
                        "emoji": partner_info['profile_emoji'] if partner_info else "👤"
                    }
                }
        
        result = run_async(fetch_messages())
        
        if result is None:
            return jsonify({"error": "Chat not found"}), 404
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching messages: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/chats/<int:partner_id>/messages', methods=['POST'])
@require_auth
def send_message(partner_id):
    """Send a message to a partner"""
    try:
        user_id = request.user_id
        data = request.get_json()
        text = data.get('text', '').strip()
        
        if not text:
            return jsonify({"error": "Message text is required"}), 400
        
        if len(text) > 4000:
            return jsonify({"error": "Message too long"}), 400
        
        async def save_message():
            async with db_pool.acquire() as conn:
                # Verify chat exists
                chat_exists = await conn.fetchval("""
                    SELECT 1 FROM chat_requests
                    WHERE ((requester_id = $1 AND recipient_id = $2) OR (requester_id = $2 AND recipient_id = $1))
                    AND status = 'accepted'
                """, user_id, partner_id)
                
                if not chat_exists:
                    return None
                
                # Check if sender is blocked
                is_blocked = await conn.fetchval("""
                    SELECT 1 FROM user_blocks
                    WHERE blocker_id = $1 AND blocked_id = $2
                """, partner_id, user_id)
                
                if is_blocked:
                    return {"error": "blocked"}
                
                # Save message
                message = await conn.fetchrow("""
                    INSERT INTO chat_messages (sender_id, recipient_id, text)
                    VALUES ($1, $2, $3)
                    RETURNING id, created_at
                """, user_id, partner_id, text)
                
                # Get sender info for notification
                sender_info = await conn.fetchrow(
                    "SELECT nickname, profile_emoji FROM user_status WHERE user_id = $1",
                    user_id
                )
                
                return {
                    "id": message['id'],
                    "senderId": user_id,
                    "isOwn": True,
                    "text": text,
                    "hasSticker": False,
                    "hasAnimation": False,
                    "timestamp": message['created_at'].isoformat(),
                    "senderName": sender_info['nickname'] if sender_info else "Someone",
                    "senderEmoji": sender_info['profile_emoji'] if sender_info else "👤"
                }
        
        result = run_async(save_message())
        
        if result is None:
            return jsonify({"error": "Chat not found"}), 404
        
        if isinstance(result, dict) and result.get("error") == "blocked":
            return jsonify({"error": "You have been blocked"}), 403
        
        # Send notification to recipient
        if bot_instance:
            try:
                sender_name = result.pop("senderName")
                sender_emoji = result.pop("senderEmoji")
                
                async def send_notification():
                    # Import here to avoid circular dependency
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
                    
                    # Get web app URL from environment or config
                    web_app_url = os.getenv("CHAT_WEB_APP_URL", "https://aau-chat-app.vercel.app")
                    
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(
                            text="💬 Open Chat App",
                            web_app=WebAppInfo(url=web_app_url)
                        )]
                    ])
                    
                    notification_text = f"{sender_emoji} <b>{sender_name}</b> sent you a message:\n\n{text[:100]}{'...' if len(text) > 100 else ''}"
                    
                    try:
                        await bot_instance.send_message(
                            partner_id,
                            notification_text,
                            reply_markup=keyboard
                        )
                    except Exception as e:
                        logger.warning(f"Failed to send notification to {partner_id}: {e}")
                
                # Run notification in bot's event loop
                asyncio.run_coroutine_threadsafe(send_notification(), main_event_loop)
                
            except Exception as e:
                logger.error(f"Error sending notification: {e}")
        
        return jsonify({"message": result})
        
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/chats/<int:partner_id>/info', methods=['GET'])
@require_auth
def get_partner_info(partner_id):
    """Get information about a chat partner"""
    try:
        user_id = request.user_id
        
        async def fetch_info():
            async with db_pool.acquire() as conn:
                # Verify chat exists
                chat_exists = await conn.fetchval("""
                    SELECT 1 FROM chat_requests
                    WHERE ((requester_id = $1 AND recipient_id = $2) OR (requester_id = $2 AND recipient_id = $1))
                    AND status = 'accepted'
                """, user_id, partner_id)
                
                if not chat_exists:
                    return None
                
                # Get partner info
                partner_info = await conn.fetchrow("""
                    SELECT nickname, profile_emoji, bio
                    FROM user_status
                    WHERE user_id = $1
                """, partner_id)
                
                if not partner_info:
                    return {
                        "id": partner_id,
                        "name": "Anonymous",
                        "emoji": "👤",
                        "bio": None
                    }
                
                return {
                    "id": partner_id,
                    "name": partner_info['nickname'] or "Anonymous",
                    "emoji": partner_info['profile_emoji'] or "👤",
                    "bio": partner_info['bio']
                }
        
        result = run_async(fetch_info())
        
        if result is None:
            return jsonify({"error": "Chat not found"}), 404
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error fetching partner info: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"})


def run_api_server(db, event_loop, bot):
    """Run the Flask API server with the provided database pool, event loop, and bot instance"""
    set_db_pool(db, event_loop, bot)
    logger.info(f"Starting Chat API server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


if __name__ == '__main__':
    logger.error("This module should be imported and run from main.py, not executed directly")
    logger.error("See chat/README.md for integration instructions")
    sys.exit(1)
