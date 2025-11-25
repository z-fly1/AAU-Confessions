"""
Chat Management API for AAU Confessions Bot
Flask-based REST API for the Telegram Mini App
"""

import os
import logging
import hashlib
import hmac
import json
from urllib.parse import parse_qsl
from datetime import datetime
from typing import Optional

from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.pool import SimpleConnectionPool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

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
CORS(app)  # Enable CORS for local development

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("CHAT_API_PORT", "5000"))

# Database connection pool
db_pool = None


def init_db_pool():
    """Initialize database connection pool"""
    global db_pool
    try:
        db_pool = SimpleConnectionPool(1, 10, DATABASE_URL)
        logger.info("Database connection pool initialized")
    except Exception as e:
        logger.error(f"Failed to initialize database pool: {e}")
        raise


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
        
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Get all accepted chat requests
            cursor.execute("""
                SELECT
                    CASE
                        WHEN requester_id = %s THEN recipient_id
                        ELSE requester_id
                    END AS partner_id,
                    created_at
                FROM chat_requests
                WHERE (requester_id = %s OR recipient_id = %s) AND status = 'accepted'
                ORDER BY created_at DESC
            """, (user_id, user_id, user_id))
            chats = cursor.fetchall()
            
            chat_list = []
            for chat in chats:
                partner_id = chat['partner_id']
                
                # Get partner info
                cursor.execute(
                    "SELECT nickname, profile_emoji FROM user_status WHERE user_id = %s",
                    (partner_id,)
                )
                partner_info = cursor.fetchone()
                
                # Get last message
                cursor.execute("""
                    SELECT text, sticker_file_id, animation_file_id, created_at, sender_id
                    FROM chat_messages
                    WHERE (sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s)
                    ORDER BY created_at DESC
                    LIMIT 1
                """, (user_id, partner_id, partner_id, user_id))
                last_message = cursor.fetchone()
                
                # Apply default values for partner info
                partner_name = partner_info['nickname'] if partner_info and partner_info['nickname'] else "Anonymous"
                partner_emoji = partner_info['profile_emoji'] if partner_info and partner_info['profile_emoji'] else "👤"
                
                chat_item = {
                    "partnerId": partner_id,
                    "partnerName": partner_name,
                    "partnerEmoji": partner_emoji,
                    "lastMessage": None,
                    "lastMessageTime": None
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
            
            return jsonify({"chats": chat_list})
        finally:
            db_pool.putconn(conn)
        
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
        before_id = request.args.get('before')  # For pagination
        
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Verify chat exists
            cursor.execute("""
                SELECT 1 FROM chat_requests
                WHERE ((requester_id = %s AND recipient_id = %s) OR (requester_id = %s AND recipient_id = %s))
                AND status = 'accepted'
            """, (user_id, partner_id, partner_id, user_id))
            chat_exists = cursor.fetchone()
            
            if not chat_exists:
                return jsonify({"error": "Chat not found"}), 404
            
            # Get messages
            query = """
                SELECT id, sender_id, recipient_id, text, sticker_file_id, animation_file_id, reply_to_message_id, created_at
                FROM chat_messages
                WHERE (sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s)
            """
            params = [user_id, partner_id, partner_id, user_id]
            
            if before_id:
                query += " AND id < %s"
                params.append(int(before_id))
            
            query += " ORDER BY created_at DESC LIMIT %s"
            params.append(limit)
            
            cursor.execute(query, params)
            messages = cursor.fetchall()
            
            # Get partner info
            cursor.execute(
                "SELECT nickname, profile_emoji FROM user_status WHERE user_id = %s",
                (partner_id,)
            )
            partner_info = cursor.fetchone()
            
            message_list = []
            for msg in reversed(messages):  # Reverse to get chronological order
                message_item = {
                    "id": msg['id'],
                    "senderId": msg['sender_id'],
                    "isOwn": msg['sender_id'] == user_id,
                    "text": msg['text'],
                    "hasSticker": msg['sticker_file_id'] is not None,
                    "hasAnimation": msg['animation_file_id'] is not None,
                    "timestamp": msg['created_at'].isoformat(),
                    "replyToMessageId": msg['reply_to_message_id']
                }
                
                # If this message is a reply, fetch the replied-to message details
                if msg['reply_to_message_id']:
                    cursor.execute("""
                        SELECT text, sticker_file_id, animation_file_id, sender_id
                        FROM chat_messages
                        WHERE id = %s
                    """, (msg['reply_to_message_id'],))
                    replied_msg = cursor.fetchone()
                    
                    if replied_msg:
                        message_item['repliedMessage'] = {
                            "text": replied_msg['text'],
                            "hasSticker": replied_msg['sticker_file_id'] is not None,
                            "hasAnimation": replied_msg['animation_file_id'] is not None,
                            "isOwn": replied_msg['sender_id'] == user_id
                        }
                
                message_list.append(message_item)
            
            # Apply default values for partner info
            partner_name = partner_info['nickname'] if partner_info and partner_info['nickname'] else "Anonymous"
            partner_emoji = partner_info['profile_emoji'] if partner_info and partner_info['profile_emoji'] else "👤"
            
            result = {
                "messages": message_list,
                "partner": {
                    "id": partner_id,
                    "name": partner_name,
                    "emoji": partner_emoji
                }
            }
            
            return jsonify(result)
        finally:
            db_pool.putconn(conn)
        
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
        reply_to_message_id = data.get('reply_to_message_id')
        
        if not text:
            return jsonify({"error": "Message text is required"}), 400
        
        if len(text) > 4000:
            return jsonify({"error": "Message too long"}), 400
        
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Verify chat exists
            cursor.execute("""
                SELECT 1 FROM chat_requests
                WHERE ((requester_id = %s AND recipient_id = %s) OR (requester_id = %s AND recipient_id = %s))
                AND status = 'accepted'
            """, (user_id, partner_id, partner_id, user_id))
            chat_exists = cursor.fetchone()
            
            if not chat_exists:
                return jsonify({"error": "Chat not found"}), 404
            
            # Check if sender is blocked
            cursor.execute("""
                SELECT 1 FROM user_blocks
                WHERE blocker_id = %s AND blocked_id = %s
            """, (partner_id, user_id))
            is_blocked = cursor.fetchone()
            
            if is_blocked:
                return jsonify({"error": "You have been blocked"}), 403
            
            # Validate reply_to_message_id if provided
            if reply_to_message_id:
                cursor.execute("""
                    SELECT 1 FROM chat_messages
                    WHERE id = %s 
                    AND ((sender_id = %s AND recipient_id = %s) OR (sender_id = %s AND recipient_id = %s))
                """, (reply_to_message_id, user_id, partner_id, partner_id, user_id))
                reply_exists = cursor.fetchone()
                
                if not reply_exists:
                    return jsonify({"error": "Reply target message not found"}), 400
            
            # Save message
            cursor.execute("""
                INSERT INTO chat_messages (sender_id, recipient_id, text, reply_to_message_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id, created_at
            """, (user_id, partner_id, text, reply_to_message_id))
            message = cursor.fetchone()
            conn.commit()
            
            result = {
                "id": message['id'],
                "senderId": user_id,
                "isOwn": True,
                "text": text,
                "hasSticker": False,
                "hasAnimation": False,
                "timestamp": message['created_at'].isoformat(),
                "replyToMessageId": reply_to_message_id
            }
            
            # If this is a reply, include the replied message info
            if reply_to_message_id:
                cursor.execute("""
                    SELECT text, sticker_file_id, animation_file_id, sender_id
                    FROM chat_messages
                    WHERE id = %s
                """, (reply_to_message_id,))
                replied_msg = cursor.fetchone()
                
                if replied_msg:
                    result['repliedMessage'] = {
                        "text": replied_msg['text'],
                        "hasSticker": replied_msg['sticker_file_id'] is not None,
                        "hasAnimation": replied_msg['animation_file_id'] is not None,
                        "isOwn": replied_msg['sender_id'] == user_id
                    }
            
            return jsonify({"message": result})
        finally:
            db_pool.putconn(conn)
        
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/api/chats/<int:partner_id>/info', methods=['GET'])
@require_auth
def get_partner_info(partner_id):
    """Get information about a chat partner"""
    try:
        user_id = request.user_id
        
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            # Verify chat exists
            cursor.execute("""
                SELECT 1 FROM chat_requests
                WHERE ((requester_id = %s AND recipient_id = %s) OR (requester_id = %s AND recipient_id = %s))
                AND status = 'accepted'
            """, (user_id, partner_id, partner_id, user_id))
            chat_exists = cursor.fetchone()
            
            if not chat_exists:
                return jsonify({"error": "Chat not found"}), 404
            
            # Get partner info
            cursor.execute("""
                SELECT nickname, profile_emoji, bio
                FROM user_status
                WHERE user_id = %s
            """, (partner_id,))
            partner_info = cursor.fetchone()
            
            if not partner_info:
                result = {
                    "id": partner_id,
                    "name": "Anonymous",
                    "emoji": "👤",
                    "bio": None
                }
            else:
                result = {
                    "id": partner_id,
                    "name": partner_info['nickname'] or "Anonymous",
                    "emoji": partner_info['profile_emoji'] or "👤",
                    "bio": partner_info['bio']
                }
            
            return jsonify(result)
        finally:
            db_pool.putconn(conn)
        
    except Exception as e:
        logger.error(f"Error fetching partner info: {e}")
        return jsonify({"error": "Internal server error"}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    # Initialize database pool
    init_db_pool()
    
    # Run Flask app
    logger.info(f"Starting Chat API server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=True)
