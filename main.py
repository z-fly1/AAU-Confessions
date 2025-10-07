import logging
import asyncpg
import os
import asyncio
import google.generativeai as genai
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timedelta, timezone
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional, Tuple, Dict, Any, List, Set
from aiogram.dispatcher.middlewares.base import BaseMiddleware
import itertools

# --- Dummy HTTP Server Imports ---
from aiohttp import web

# --- Constants --
CATEGORIES = [
    "Relationship", "Family","Exam", "School", "Friendship",
    "Religion", "Mental", "Addiction", "Harassment", "Crush", "Health", "Trauma", "Sexual",
    "Other"
]
INTERESTS = [
       "Anime", "Manga", "Comics", "Cartoons", "Drawing", "Painting", "Photography", "Filmmaking", "Writing", "Poetry",
"Books", "Novels", "Short Stories", "Philosophy", "History", "Science", "Math", "Psychology", "Politics", "Economics",
"Movies", "TV Shows", "Series", "Theatre", "Acting", "Cinematography", "Screenwriting",
"Music", "Singing", "Instruments", "Piano", "Guitar", "Drums", "DJing", "Rap", "Dance", "Choreography",
"Gaming", "Esports", "Chess", "Puzzles", "Board Games", "Card Games", "Strategy Games", "RPG",
"Tech", "Coding", "AI", "Robotics", "Cybersecurity", "Electronics", "Gadgets", "Web Dev", "App Dev", "Blockchain",
"Sports", "Football", "Basketball", "Tennis", "Running", "Cycling", "Swimming", "Martial Arts", "Boxing", "Yoga", "Gym",
"Travel", "Adventure", "Camping", "Hiking", "Exploration", "Road Trips", "Backpacking",
"Food", "Cooking", "Baking", "Coffee", "Tea", "Wine", "Street Food", "Nutrition", "Veganism",
"Career", "Entrepreneurship", "Startups", "Business", "Finance", "Investing", "Marketing", "Networking",
"Nature", "Animals", "Birdwatching", "Gardening", "Stargazing", "Astronomy",
"Culture", "Languages", "Religion", "Traditions", "Festivals",
"Fashion", "Style", "Makeup", "Design", "DIY",
"Collecting", "Stamps", "Coins", "Sneakers", "Toys", "Figurines",
"Social Media", "Blogging", "Podcasting", "Streaming", "Vlogging"
]
GENDERS = ["Male", "Female", "Helicopter"]
CAMPUSES = ["College of Social Sciences",
  "College of Humanities, Language Studies, Journalism and Communication",
  "College of Development Studies",
  "College of Business and Economics",
  "College of Law and Governance Studies",
  "College of Education and Behavioral Studies",
  "College of Natural and Computational Sciences",
  "Skunder Boghossian College of Performing and Visual Arts",
  "College of Veterinary Medicine and Agriculture",
  "College of Health Sciences",
  "College of Technology and Built Environment (CTBE)"]

YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year", "5th Year", "6th Year", "Other"]
DEPARTMENTS = [
  "African Studies", "Archaeology and Heritage Management", "History", "Political Science",
  "Social Anthropology", "Social Work and Social Development", "Sociology", "Philosophy",
  "Amharic Language, Literature and Folklore", "Foreign Language and Literature",
  "Journalism and Communication", "Linguistics", "Oromo Language, Literature and Folklore",
  "Philology", "Teaching English as Foreign Language", "Tigrigna Language, Literature and Folklore",
  "Centre for Development Research", "Centre for Food Security Studies", "Centre for Gender Studies",
  "Centre for Population Studies", "Centre for Regional and Local Development Studies",
  "Centre for Rural Development", "Centre for Environment and Development",
  "Accounting and Finance", "Economics", "Management", "Public Administration",
  "Business Information System", "Business Leadership", "Corporate Finance", "Development Economics",
  "Digital Marketing", "Human Resource Management", "Logistics and Supply Chain Management",
  "Marketing Management", "Project Management", "School of Law", "Educational Planning and Management",
  "Psychology", "Biology", "Chemistry", "Geology", "Mathematics", "Physics", "Statistics",
  "Design", "Fine Arts", "Multimedia Theater", "Film Production", "Yared School of Music",
  "Yoftahe Nigussie School of Theatrical Arts", "Veterinary Clinical Medicine", "School of Medicine",
  "School of Nursing and Midwifery", "School of Pharmacy", "School of Public Health",
  "School of Chemical and Bio-Engineering", "School of Civil and Environmental Engineering",
  "School of Electrical and Computer Engineering", "School of Mechanical and Industrial Engineering",
  "Architecture and Design", "Construction Technology and Management", "Urban Planning and Environmental Studies"
]


MAX_INTERESTS = 5
POINTS_PER_CONFESSION = 0
POINTS_PER_LIKE_RECEIVED = 1
POINTS_PER_DISLIKE_RECEIVED = -1 # Note: This is negative
MAX_CATEGORIES = 3 # Maximum categories allowed per confession
NICKNAME_COOLDOWN = timedelta(days=30)
PROFILE_EMOJIS = ["👤", "👨", "👩", "🧑", "🧐", "👻", "✨", "😴", "😎", "🦊", "🥲", "🎮", "🎧", "🎨", "☀️"]
AI_ENHANCED_MARKER = "✨" # Marker for AI-enhanced confessions
PROFILE_OPTIONS_PAGE_SIZE = 8 # Number of items for profile selection pagination


# Load environment variables at the top level
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKENS")
ADMIN_ID_STR = os.getenv("ADMIN_ID") # Load as string first for validation
CHANNEL_ID = os.getenv("CHANNEL_ID")
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "15"))  # Number of items per page for pagination
DATABASE_URL = os.getenv("DATABASE_URL")
HTTP_PORT_STR = os.getenv("PORT")
RESERVED_NICKNAMES_STR = os.getenv("RESERVED_NICKNAMES", "Admin,Administrator,Moderator,Mod,Owner,Author,Anonymous,You")
RESERVED_NICKNAMES: Set[str] = {name.strip().lower() for name in RESERVED_NICKNAMES_STR.split(',')}
# --- MODIFICATION: Load multiple Gemini API keys ---
GEMINI_API_KEYS_STR = os.getenv("GEMINI_API_KEYS")
GEMINI_API_KEYS = [key.strip() for key in (GEMINI_API_KEYS_STR or "").split(',') if key.strip()]


# Validate essential environment variables before proceeding
if not BOT_TOKEN: raise ValueError("FATAL: BOT_TOKEN environment variable not set!")
if not ADMIN_ID_STR: raise ValueError("FATAL: ADMIN_ID environment variable not set!")
if not CHANNEL_ID: raise ValueError("FATAL: CHANNEL_ID environment variable not set!")
if not DATABASE_URL: raise ValueError("FATAL: DATABASE_URL environment variable not set!")
if not GEMINI_API_KEYS: raise ValueError("FATAL: GEMINI_API_KEYS environment variable not set or empty!")

try:
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    raise ValueError("FATAL: ADMIN_ID environment variable must be a valid integer!")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Bot and Dispatcher
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# Bot info
bot_info = None

# --- Persistent Reply Keyboards ---
main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✍️ Confess")],
        [KeyboardButton(text="👤 Profile"), KeyboardButton(text="ℹ️ Help")]
    ],
    resize_keyboard=True
)

# --- ADMIN REVIEW --- New keyboard for the admin
admin_main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📬 Review Pending")],
        [KeyboardButton(text="✍️ Confess")],
        [KeyboardButton(text="👤 Profile"), KeyboardButton(text="ℹ️ Help")]
    ],
    resize_keyboard=True
)


cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="❌ Cancel")]],
    resize_keyboard=True
)

# --- FSM States ---
# --- RESTRUCTURED: Simplified confession flow ---
class ConfessionForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_confirmation = State()
    selecting_categories = State()

class CommentForm(StatesGroup):
    waiting_for_comment = State()
    waiting_for_reply = State()

class ContactAdminForm(StatesGroup):
    waiting_for_message = State()

class AdminActions(StatesGroup):
    waiting_for_rejection_reason = State()

class SettingsForm(StatesGroup):
    waiting_for_nickname = State()
    waiting_for_bio = State()
    selecting_interests = State()


class ChatState(StatesGroup):
    in_chat = State()

# --- ADMIN REVIEW --- New state for the review process
class AdminReview(StatesGroup):
    reviewing = State()

# --- Database ---
db = None
async def create_db_pool():
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
        logging.info("Database pool created successfully.")
        return pool
    except Exception as e:
        logging.error(f"Failed to create database pool: {e}")
        raise

async def setup():
    global db, bot_info
    db = await create_db_pool()
    bot_info = await bot.get_me()
    logging.info(f"Bot started: @{bot_info.username}")
    
    # --- MODIFICATION: No initial Gemini configuration needed here ---
    if GEMINI_API_KEYS:
        logging.info(f"Loaded {len(GEMINI_API_KEYS)} Gemini API keys.")
    else:
        logging.warning("Gemini API keys are not configured.")


    async with db.acquire() as conn:
        # --- Confessions Table Schema ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending',
                message_id BIGINT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                rejection_reason TEXT NULL,
                categories TEXT[] NULL
            );
        """)
        logging.info("Checked/Created 'confessions' table.")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_confessions_categories ON confessions USING gin(categories);")
        logging.info("Checked/Created GIN index on 'confessions.categories'.")

        # --- Comments Table Schema ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                text TEXT NULL,
                sticker_file_id TEXT NULL,
                animation_file_id TEXT NULL,
                parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT one_content_type CHECK (num_nonnulls(text, sticker_file_id, animation_file_id) = 1)
            );
        """)
        logging.info("Checked/Created 'comments' table.")

        # --- Reactions Table ---
        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions ( id SERIAL PRIMARY KEY, comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL, reaction_type VARCHAR(10) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id) );
        """)
        logging.info("Checked/Created 'reactions' table.")

        # --- Rebuilt Contact Requests Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
                requester_user_id BIGINT NOT NULL,
                requested_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, approved, denied, approved_no_username, failed_to_notify
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, requester_user_id)
            );
        """)
        logging.info("Checked/Created 'contact_requests' table (V2).")

        # --- User Points Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id BIGINT PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0
            );
        """)
        logging.info("Checked/Created 'user_points' table.")

        # --- Reports Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                comment_id INTEGER NULL REFERENCES comments(id) ON DELETE CASCADE,
                reporter_user_id BIGINT NOT NULL,
                reported_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, reporter_user_id)
            );
        """)
        logging.info("Checked/Created 'reports' table.")
        # --- User to User Chat Requests Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_requests (
                id SERIAL PRIMARY KEY,
                requester_id BIGINT NOT NULL,
                recipient_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, accepted, declined, blocked
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(requester_id, recipient_id)
            );
        """)
        logging.info("Checked/Created 'chat_requests' table.")


        # --- Deletion Requests Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS deletion_requests (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, approved, rejected
                created_at TIMESTAMP WITH TIME ZONE,
                reviewed_at TIMESTAMP WITH TIME ZONE,
                UNIQUE (confession_id, user_id) -- User can only request deletion for their confession once
            );
        """)
        logging.info("Checked/Created 'deletion_requests' table.")

        # --- User Status Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_status (
                user_id BIGINT PRIMARY KEY,
                has_accepted_rules BOOLEAN NOT NULL DEFAULT FALSE,
                is_blocked BOOLEAN NOT NULL DEFAULT FALSE,
                blocked_until TIMESTAMP WITH TIME ZONE NULL,
                block_reason TEXT NULL
            );
        """)
        logging.info("Checked/Created 'user_status' table.")

        # --- Add new columns to user_status if they don't exist ---
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS nickname VARCHAR(32) NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS comments_per_page INTEGER NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS nickname_last_changed_at TIMESTAMP WITH TIME ZONE NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS profile_emoji VARCHAR(8) NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS bio TEXT NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS allow_contact BOOLEAN NOT NULL DEFAULT TRUE;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS gender TEXT NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS campus TEXT NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS year TEXT NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS department TEXT NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS interests TEXT[] NULL;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS show_gender BOOLEAN NOT NULL DEFAULT FALSE;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS show_campus BOOLEAN NOT NULL DEFAULT FALSE;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS show_year BOOLEAN NOT NULL DEFAULT FALSE;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS show_department BOOLEAN NOT NULL DEFAULT FALSE;")
        await conn.execute("ALTER TABLE user_status ADD COLUMN IF NOT EXISTS show_interests BOOLEAN NOT NULL DEFAULT FALSE;")
        logging.info("Ensured all customizable profile columns exist in 'user_status'.")


        logging.info("Database tables setup complete.")


# --- Dummy HTTP Server Functions ---
async def handle_health_check(request):
    """Responds with a simple 'OK' for health checks."""
    logging.debug("Health check endpoint hit.")
    return web.Response(text="OK")

async def start_dummy_server():
    """Starts a minimal HTTP server to respond to Render health checks."""
    if not HTTP_PORT_STR:
        logging.info("PORT environment variable not set. Dummy HTTP server will not start.")
        return

    try: port = int(HTTP_PORT_STR)
    except ValueError:
        logging.error(f"Invalid PORT environment variable: {HTTP_PORT_STR}. Dummy HTTP server will not start.")
        return

    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/healthz', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logging.info(f"Dummy HTTP server started successfully on port {port}.")
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logging.info("Dummy HTTP server task cancelled.")
    except Exception as e:
        logging.error(f"Dummy HTTP server failed to start or crashed on port {port}: {e}", exc_info=True)
    finally:
        await runner.cleanup()
        logging.info("Dummy HTTP server cleaned up and stopped.")


# --- Helper Functions ---
# --- ADMIN REVIEW --- Helper to get the correct keyboard based on user ID
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Returns the appropriate main menu keyboard for a user."""
    if user_id == ADMIN_ID:
        return admin_main_menu_keyboard
    return main_menu_keyboard


def create_category_keyboard(selected_categories: List[str] = None):
    if selected_categories is None:
        selected_categories = []
    builder = InlineKeyboardBuilder()

    builder.row(InlineKeyboardButton(text="🤖 Auto-select Categories", callback_data="category_auto_ai"))
    
    for category in [c for c in CATEGORIES if c]:
        prefix = "✅ " if category in selected_categories else ""
        builder.button(text=f"{prefix}{category}", callback_data=f"category_{category}")
    builder.adjust(2)
    
    if 1 <= len(selected_categories) <= MAX_CATEGORIES:
         builder.row(InlineKeyboardButton(text=f"➡️ Done Selecting ({len(selected_categories)}/{MAX_CATEGORIES})", callback_data="category_done"))
    elif len(selected_categories) > MAX_CATEGORIES:
         builder.row(InlineKeyboardButton(text=f"⚠️ Too Many ({len(selected_categories)}/{MAX_CATEGORIES}) - Click to Confirm", callback_data="category_done"))
    
    builder.row(InlineKeyboardButton(text="❌ Cancel Selection", callback_data="category_cancel"))
    return builder.as_markup()

async def get_comment_reactions(comment_id: int) -> Tuple[int, int]:
    likes, dislikes = 0, 0
    async with db.acquire() as conn:
        counts = await conn.fetchrow(
            "SELECT COALESCE(SUM(CASE WHEN reaction_type = 'like' THEN 1 ELSE 0 END), 0) AS likes, COALESCE(SUM(CASE WHEN reaction_type = 'dislike' THEN 1 ELSE 0 END), 0) AS dislikes FROM reactions WHERE comment_id = $1", comment_id )
        if counts:
            likes, dislikes = counts['likes'], counts['dislikes']
    return likes, dislikes

async def get_user_points(user_id: int) -> int:
    async with db.acquire() as conn:
        points = await conn.fetchval("SELECT points FROM user_points WHERE user_id = $1", user_id)
        return points or 0

async def update_user_points(conn: asyncpg.Connection, user_id: int, delta: int):
    if delta == 0: return
    await conn.execute("INSERT INTO user_points (user_id, points) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET points = user_points.points + $2", user_id, delta)
    logging.debug(f"Updated points for user {user_id} by {delta}")

async def build_comment_keyboard(comment_id: int, commenter_user_id: int, viewer_user_id: int, confession_owner_id: int ):
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="Reply", callback_data=f"reply_{comment_id}")

    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(3, 1)
    else:
        builder.adjust(3)
    return builder.as_markup()


async def safe_send_message(user_id: int, text: str, **kwargs) -> Optional[types.Message]:
    try:
        sent_message = await bot.send_message(user_id, text, **kwargs)
        return sent_message
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        if "bot was blocked" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e):
            logging.warning(f"Could not send message to user {user_id}: Blocked/deactivated. {e}")
        else:
            logging.warning(f"Telegram API error sending to {user_id}: {e}")
    except TelegramRetryAfter as e:
        logging.warning(f"Flood control for {user_id}. Retrying after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        return await safe_send_message(user_id, text, **kwargs)
    except Exception as e:
        logging.error(f"Unexpected error sending message to {user_id}: {e}", exc_info=True)
    
    return None

async def update_channel_post_button(confession_id: int):
    global bot_info; await asyncio.sleep(0.1)
    if not bot_info: logging.error(f"No bot info for {confession_id} button update."); return
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)
        count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id) or 0
    if not conf_data or not conf_data['message_id']: logging.debug(f"No approved conf/msg_id for button update."); return
    ch_msg_id = conf_data['message_id']; link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💬 View / Add Comments ({count})", url=link)]])
    try: await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower(): logging.info(f"Button for {confession_id} already updated ({count}).")
        elif "message to edit not found" in str(e).lower(): logging.warning(f"Msg {ch_msg_id} not found in {CHANNEL_ID} (conf {confession_id}). Maybe deleted?")
        else: logging.error(f"Failed edit channel post {ch_msg_id} for conf {confession_id}: {e}")
    except Exception as e: logging.error(f"Unexpected err updating btn for conf {confession_id}: {e}", exc_info=True)

async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None, page: int = 1):
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT status, user_id FROM confessions WHERE id = $1", confession_id)
        if not conf_data or conf_data['status'] != 'approved':
            err_txt = f"Confession #{confession_id} not found or not approved."
            if message_to_edit: await message_to_edit.edit_text(err_txt, reply_markup=None)
            else: await safe_send_message(user_id, err_txt)
            return

        user_page_size = await conn.fetchval("SELECT comments_per_page FROM user_status WHERE user_id = $1", user_id)
        page_size_to_use = user_page_size if user_page_size is not None else PAGE_SIZE
        use_pagination = page_size_to_use > 0


        confession_owner_id = conf_data['user_id']
        total_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id) or 0
        if total_count == 0:
            msg_text = "<i>No comments yet. Be the first!</i>"
            if message_to_edit: await message_to_edit.edit_text(msg_text, reply_markup=None)
            else: await safe_send_message(user_id, msg_text)
            nav = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]])
            await safe_send_message(user_id, "You can add your own comment below:", reply_markup=nav)
            return

        total_pages = (total_count + page_size_to_use - 1) // page_size_to_use if use_pagination else 1
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size_to_use if use_pagination else 0
        limit = page_size_to_use if use_pagination else None

        query = """
            SELECT c.id, c.user_id, c.text, c.sticker_file_id, c.animation_file_id, c.parent_comment_id, c.created_at,
                   COALESCE(up.points, 0) as user_points,
                   us.nickname,
                   us.profile_emoji
            FROM comments c
            LEFT JOIN user_points up ON c.user_id = up.user_id
            LEFT JOIN user_status us ON c.user_id = us.user_id
            WHERE c.confession_id = $1
            ORDER BY c.created_at ASC
        """
        if use_pagination:
            query += " LIMIT $2 OFFSET $3"
            comments_raw = await conn.fetch(query, confession_id, limit, offset)
        else:
            comments_raw = await conn.fetch(query, confession_id)


    db_id_to_message_id: Dict[int, int] = {}

    if not comments_raw:
        await safe_send_message(user_id, f"<i>No comments on page {page}.</i>")
    else:
        for i, c_data_row in enumerate(comments_raw):
            c_data = dict(c_data_row)
            db_id, commenter_uid = c_data['id'], c_data['user_id']
            medal_str = f" ⚡︎{c_data.get('user_points', 0)} Aura"

            nickname = c_data.get('nickname') or "Anonymous"
            profile_url = f"https://t.me/{bot_info.username}?start=profile_{commenter_uid}"
            
            if commenter_uid == confession_owner_id:
                tag = f"<a href='{profile_url}'>✅ Confession Author</a>"
            elif commenter_uid == user_id:
                tag = f"<a href='{profile_url}'>(You)</a>"
            else:
                tag = f"<a href='{profile_url}'>{html.quote(nickname)}</a>"

            profile_emoji = c_data.get('profile_emoji') or '👤'
            
            admin_info = f" [UID: <code>{commenter_uid}</code>]" if user_id == ADMIN_ID else ""
            display_tag = f" {profile_emoji} {tag}{medal_str}"

            reply_to_msg_id = None
            text_reply_prefix = ""
            parent_db_id = c_data.get('parent_comment_id')
            if parent_db_id:
                if parent_db_id in db_id_to_message_id:
                    reply_to_msg_id = db_id_to_message_id[parent_db_id]
                else: 
                    async with db.acquire() as conn_for_quote:
                        parent_comment_data = await conn_for_quote.fetchrow(
                            "SELECT text, sticker_file_id, animation_file_id FROM comments WHERE id = $1", parent_db_id
                        )
                    if parent_comment_data:
                        if parent_comment_data['text']:
                            quoted_text = html.quote(parent_comment_data['text'][:150]) + ('...' if len(parent_comment_data['text']) > 150 else '')
                        elif parent_comment_data['sticker_file_id']:
                            quoted_text = "<i>[Sticker]</i>"
                        elif parent_comment_data['animation_file_id']:
                             quoted_text = "<i>[GIF]</i>"
                        else:
                            quoted_text = "<i>[Original message]</i>"
                        
                        text_reply_prefix = f"<blockquote>{quoted_text}</blockquote>"
                    else:
                        text_reply_prefix = "↪️ <i>Replying to another comment...</i>\n"


            metadata_text = f"<i>{display_tag}{admin_info}</i>"
            keyboard = await build_comment_keyboard(db_id, commenter_uid, user_id, confession_owner_id)
            
            sent_message = None
            try:
                if c_data['sticker_file_id']:
                    sent_message = await bot.send_sticker(user_id, sticker=c_data['sticker_file_id'], reply_to_message_id=reply_to_msg_id)
                    await bot.send_message(user_id, f"{text_reply_prefix}{metadata_text}", reply_markup=keyboard, disable_web_page_preview=True)
                elif c_data['animation_file_id']:
                    sent_message = await bot.send_animation(user_id, animation=c_data['animation_file_id'], reply_to_message_id=reply_to_msg_id)
                    await bot.send_message(user_id, f"{text_reply_prefix}{metadata_text}", reply_markup=keyboard, disable_web_page_preview=True)
                elif c_data['text']:
                    full_text = f"{text_reply_prefix}💬 {html.quote(c_data['text'])}\n\n{metadata_text}"
                    sent_message = await bot.send_message(user_id, full_text, reply_markup=keyboard, disable_web_page_preview=True, reply_to_message_id=reply_to_msg_id)
                
                if sent_message:
                    db_id_to_message_id[db_id] = sent_message.message_id

            except Exception as e:
                logging.warning(f"Could not send comment to {user_id}: {e}")
                await safe_send_message(user_id, f"⚠️ Error displaying comment.")
            await asyncio.sleep(0.1)

    nav_row = []
    if use_pagination:
        if page > 1: nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"comments_page_{confession_id}_{page-1}"))
        if total_pages > 1: nav_row.append(InlineKeyboardButton(text=f"Page {page}/{total_pages}", callback_data="noop"))
        if page < total_pages: nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"comments_page_{confession_id}_{page+1}"))
    
    nav_keyboard = InlineKeyboardMarkup(inline_keyboard=[nav_row, [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]])
    
    if use_pagination:
        end_txt = f"Displaying page {page}/{total_pages}. Total {total_count} Comments"
    else:
        end_txt = f"Showing all {total_count} comments"

    await safe_send_message(user_id, end_txt, reply_markup=nav_keyboard)

# --- Public Profile Viewer ---
async def show_public_profile(viewer_user_id: int, profile_user_id: int):
    async with db.acquire() as conn:
        await conn.execute("INSERT INTO user_status(user_id) VALUES ($1) ON CONFLICT DO NOTHING", profile_user_id)
        
        profile_data = await conn.fetchrow("""
            SELECT 
                us.nickname, us.profile_emoji, us.bio, us.allow_contact, 
                us.gender, us.campus, us.year, us.department, us.interests,
                us.show_gender, us.show_campus, us.show_year, us.show_department, us.show_interests,
                COALESCE(up.points, 0) as points
            FROM user_status us
            LEFT JOIN user_points up ON us.user_id = up.user_id
            WHERE us.user_id = $1
        """, profile_user_id)

    if not profile_data:
        await safe_send_message(viewer_user_id, "This user's profile could not be found.")
        return

    nickname = html.quote(profile_data.get('nickname') or "Anonymous")
    emoji = profile_data.get('profile_emoji') or '👤'
    points = profile_data.get('points', 0)
    bio = html.quote(profile_data.get('bio') or "This user has not set a bio yet.")
    allow_contact = profile_data.get('allow_contact', False)

    profile_text = (
        f"{emoji} <b>{nickname}'s Public Profile</b>\n\n"
        f"⚡︎ <b>Aura Points:</b> {points}\n\n"
        f"📝 <b>Bio:</b>\n<i>{bio}</i>\n"
    )

    details = []
    if profile_data.get('show_gender') and profile_data.get('gender'):
        details.append(f"<b>Gender:</b> {html.quote(profile_data['gender'])}")
    if profile_data.get('show_campus') and profile_data.get('campus'):
        details.append(f"<b>Campus:</b> {html.quote(profile_data['campus'])}")
    if profile_data.get('show_year') and profile_data.get('year'):
        details.append(f"<b>Year:</b> {html.quote(profile_data['year'])}")
    if profile_data.get('show_department') and profile_data.get('department'):
        details.append(f"<b>Department:</b> {html.quote(profile_data['department'])}")
    if profile_data.get('show_interests') and profile_data.get('interests'):
        interests_str = ", ".join(profile_data['interests'])
        details.append(f"<b>Interests:</b> {html.quote(interests_str)}")
    
    if details:
        profile_text += "\n" + "\n".join(details)


    builder = InlineKeyboardBuilder()
    if viewer_user_id != profile_user_id:
        builder.button(text="⚠️ Report User", callback_data=f"report_user_{profile_user_id}")
        
        async with db.acquire() as conn:
            chat_status = await conn.fetchval(
                "SELECT status FROM chat_requests WHERE (requester_id = $1 AND recipient_id = $2) OR (requester_id = $2 AND recipient_id = $1)",
                viewer_user_id, profile_user_id
            )

        if chat_status == 'accepted':
             builder.button(text="💬 Send Message", callback_data=f"start_chat_{profile_user_id}")
        elif chat_status == 'pending':
            builder.button(text="⏳ Chat Request Sent", callback_data="noop")
        elif allow_contact:
            builder.button(text="🤝 Request to Chat", callback_data=f"request_chat_{profile_user_id}")
    
    builder.adjust(1)
    await safe_send_message(viewer_user_id, profile_text, reply_markup=builder.as_markup())


class BlockUserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: types.TelegramObject, data: Dict[str, Any]) -> Any:
        user = data.get('event_from_user')
        if not user:
            return await handler(event, data)

        user_id = user.id
        if user_id == ADMIN_ID:
            return await handler(event, data)

        async with db.acquire() as conn:
            status = await conn.fetchrow("SELECT is_blocked, blocked_until, block_reason FROM user_status WHERE user_id = $1", user_id)
        
        if status and status['is_blocked']:
            now = datetime.now(timezone.utc)
            if status['blocked_until'] and status['blocked_until'] < now:
                async with db.acquire() as conn:
                    await conn.execute("UPDATE user_status SET is_blocked = FALSE, blocked_until = NULL, block_reason = NULL WHERE user_id = $1", user_id)
                return await handler(event, data)
            else:
                expiry_info = f"until {status['blocked_until'].strftime('%Y-%m-%d %H:%M %Z')}" if status['blocked_until'] else "permanently"
                reason_info = f"\nReason: <i>{html.quote(status['block_reason'])}</i>" if status['block_reason'] else ""
                
                block_message = f"❌ <b>You are blocked from using this bot {expiry_info}.</b>{reason_info}"

                if isinstance(event, types.CallbackQuery):
                    await event.answer(f"You are blocked {expiry_info}.", show_alert=True)
                elif isinstance(event, types.Message):
                    await event.answer(block_message)
                return

        return await handler(event, data)

# --- Handlers ---

@dp.message(Command("rules"))
async def show_rules(message: types.Message):
    rules_text = (
        "<b>📜 Bot Rules & Regulations</b>\n\n"
        "<b>To keep the community safe, respectful, and meaningful, please follow these guidelines when using the bot:</b>\n\n"
        "1.  <b>Stay Relevant:</b> This space is mainly for sharing confessions, experiences, and thoughts.\n\n - Avoid using it just to ask random questions you could easily Google or ask in the right place.\n\n - Some student-related questions may be approved if they benefit the community.\n\n"
        "2.  <b>Respectful Communication:</b> Sensitive topics (political, religious, cultural, etc.) are allowed but must be discussed with respect.\n\n"
        "3.  <b>No Harmful Content:</b> You may mention names, but at your own risk.\n\n - The bot and admins are not responsible for any consequences.\n\n - If someone mentioned requests removal, their name will be taken down.\n\n"
        "4.  <b>Names & Responsibility:</b> Do not share personal identifying information about yourself or others.\n\n"
        "5.  <b>Anonymity & Privacy:</b> don’t reveal private details of others (contacts, adress, etc.) without consent.\n\n"
        "6.  <b>Constructive Environment:</b> Keep confessions genuine. Avoid spam, trolling, or repeated submissions.\n\n - Respect moderators’ decisions on approvals, edits, or removals.\n\n\n"
        "<i>Use this space to connect, share, and learn, not to spread misinformation or cause unnecessary drama.</i>"
    )
    await message.answer(rules_text, reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()
    user_id = message.from_user.id
    keyboard = get_main_keyboard(user_id)

    async with db.acquire() as conn:
        has_accepted = await conn.fetchval("SELECT has_accepted_rules FROM user_status WHERE user_id = $1", user_id)

    if has_accepted is None:
        async with db.acquire() as conn:
            await conn.execute("INSERT INTO user_status(user_id) VALUES ($1) ON CONFLICT DO NOTHING", user_id)
        has_accepted = False


    if not has_accepted:
        rules_text = (
            "<b>📜 Bot Rules & Regulations</b>\n\n"
            "<b>To keep the community safe, respectful, and meaningful, please follow these guidelines when using the bot:</b>\n\n"
            "1.  <b>Stay Relevant:</b> This space is mainly for sharing confessions, experiences, and thoughts.\n\n - Avoid using it just to ask random questions you could easily Google or ask in the right place.\n\n - Some student-related questions may be approved if they benefit the community.\n\n"
            "2.  <b>Respectful Communication:</b> Sensitive topics (political, religious, cultural, etc.) are allowed but must be discussed with respect.\n\n"
            "3.  <b>No Harmful Content:</b> You may mention names, but at your own risk.\n\n - The bot and admins are not responsible for any consequences.\n\n - If someone mentioned requests removal, their name will be taken down.\n\n"
            "4.  <b>Names & Responsibility:</b> Do not share personal identifying information about yourself or others.\n\n"
            "5.  <b>Anonymity & Privacy:</b> don’t reveal private details of others (contacts, adress, etc.) without consent.\n\n"
            "6.  <b>Constructive Environment:</b> Keep confessions genuine. Avoid spam, trolling, or repeated submissions.\n\n - Respect moderators’ decisions on approvals, edits, or removals.\n\n\n"
            "<i>Use this space to connect, share, and learn, not to spread misinformation or cause unnecessary drama.</i>"
        )
        accept_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ I Accept the Rules", callback_data="accept_rules")]
        ])
        await message.answer(rules_text, reply_markup=accept_keyboard)
        return

    deep_link_args = command.args if command else None
    if deep_link_args:
        try:
            if deep_link_args.startswith("view_"):
                parts = deep_link_args.split("_")
                conf_id = int(parts[1])
                
                if len(parts) == 4 and parts[2] == "page":
                    page_num = int(parts[3])
                    logging.info(f"User {user_id} deep linked to conf {conf_id}, page {page_num}")
                    await show_comments_for_confession(user_id, conf_id, page=page_num)
                    return

                logging.info(f"User {message.from_user.id} started via deep link for conf {conf_id}")
                async with db.acquire() as conn:
                    conf_data = await conn.fetchrow("SELECT c.text, c.categories, c.status, c.user_id, COUNT(com.id) as comment_count FROM confessions c LEFT JOIN comments com ON c.id = com.confession_id WHERE c.id = $1 GROUP BY c.id", conf_id)
                if not conf_data or conf_data['status'] != 'approved':
                    await message.answer(f"Confession #{conf_id} not found or not approved."); return
                comm_count = conf_data['comment_count']; categories = conf_data['categories'] or []; category_tags = " ".join([f"#{html.quote(cat)}" for cat in categories]) if categories else "#Unknown"
                txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(conf_data['text'])}\n\n{category_tags}"
                builder = InlineKeyboardBuilder()
                builder.button(text="➕ Add Comment", callback_data=f"add_{conf_id}")
                builder.button(text=f"💬 Browse Comments ({comm_count})", callback_data=f"browse_{conf_id}")
                builder.adjust(1, 1)
                await message.answer(txt, reply_markup=builder.as_markup())
            
            elif deep_link_args.startswith("profile_"):
                profile_user_id = int(deep_link_args.split("_")[1])
                logging.info(f"User {user_id} deep linked to profile of {profile_user_id}")
                await show_public_profile(user_id, profile_user_id)

            else:
                await message.answer("Invalid link.", reply_markup=keyboard)

        except (ValueError, IndexError): await message.answer("Invalid link.")
        except Exception as e: logging.error(f"Err handling deep link '{deep_link_args}': {e}", exc_info=True); await message.answer("Error processing link.")
    else: await message.answer("Welcome! Use Confess to submit confessions", reply_markup=keyboard)

@dp.callback_query(F.data == "accept_rules")
async def handle_accept_rules(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_status (user_id, has_accepted_rules) VALUES ($1, TRUE)
               ON CONFLICT (user_id) DO UPDATE SET has_accepted_rules = TRUE""",
            user_id
        )
    await callback_query.message.delete()
    await callback_query.message.answer("Thank you for accepting the rules! You can now use the bot.\n\n"
                                          "Use the buttons below to get started.",
                                          reply_markup=get_main_keyboard(user_id))
    await callback_query.answer("Rules accepted!")


@dp.message(Command("help"), StateFilter(None))
@dp.message(F.text == "ℹ️ Help", StateFilter(None))
async def show_help(message: types.Message):
    help_text = (
        "<b>Welcome to the Confession Bot!</b>\n\n"
        "Here's how to use the bot:\n"
        "/confess - Submit a new anonymous confession.\n"
        "/profile - View your profile and history.\n"
        "/start - Show the welcome message.\n"
        "/help - Display this help message.\n"
        "/privacy - View information about data privacy.\n\n"
        "<b>Interact with comments using the buttons:</b>\n"
        "👍/👎: Like/Dislike (+1🏅/-1🏅 for the commenter).\n"
        "↪️ Reply: Reply to a comment (Text, Sticker, or GIF).\n"
        "🤝 Request Contact: (Author only) Ask to contact a commenter.\n\n"
        "Need more info or want to reach the admin directly?"
    )
    action_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 Rules & Regulations", callback_data="show_rules_help")],
        [InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin_start")]
    ])
    if message.from_user and message.from_user.id == ADMIN_ID:
        help_text += ("\n\n<b>Admin Commands:</b>\n"
                      "🔹 /id &lt;user_id&gt; - Get user info.\n"
                      "🔹 /warn &lt;user_id&gt; &lt;reason&gt; - Send a warning.\n"
                      "🔹 /block &lt;user_id&gt; &lt;duration&gt; [reason] - Temp block (e.g., 7d, 2w).\n"
                      "🔹 /pblock &lt;user_id&gt; [reason] - Permanently block.\n"
                      "🔹 /unblock &lt;user_id&gt; - Unblock a user.")

    await message.answer(help_text, reply_markup=action_keyboard)

@dp.callback_query(F.data == "show_rules_help")
async def show_rules_from_help(callback_query: types.CallbackQuery):
    await callback_query.answer()
    rules_text = (
        "<b>📜 Bot Rules & Regulations</b>\n\n"
        "<b>To keep the community safe, respectful, and meaningful, please follow these guidelines when using the bot:</b>\n\n"
        "1.  <b>Stay Relevant:</b> This space is mainly for sharing confessions, experiences, and thoughts.\n\n - Avoid using it just to ask random questions you could easily Google or ask in the right place.\n\n - Some student-related questions may be approved if they benefit the community.\n\n"
        "2.  <b>Respectful Communication:</b> Sensitive topics (political, religious, cultural, etc.) are allowed but must be discussed with respect.\n\n"
        "3.  <b>No Harmful Content:</b> You may mention names, but at your own risk.\n\n - The bot and admins are not responsible for any consequences.\n\n - If someone mentioned requests removal, their name will be taken down.\n\n"
        "4.  <b>Names & Responsibility:</b> Do not share personal identifying information about yourself or others.\n\n"
        "5.  <b>Anonymity & Privacy:</b> don’t reveal private details of others (contacts, adress, etc.) without consent.\n\n"
        "6.  <b>Constructive Environment:</b> Keep confessions genuine. Avoid spam, trolling, or repeated submissions.\n\n - Respect moderators’ decisions on approvals, edits, or removals.\n\n\n"
        "<i>Use this space to connect, share, and learn, not to spread misinformation or cause unnecessary drama.</i>"
    )
    await callback_query.message.answer(rules_text, reply_markup=get_main_keyboard(callback_query.from_user.id))


@dp.callback_query(F.data == "contact_admin_start", StateFilter(None))
async def start_contact_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ContactAdminForm.waiting_for_message)
    await callback_query.answer("Please send your message to the admin.")
    await callback_query.message.answer(
        "Please send the message you want to forward to the admin.",
        reply_markup=cancel_keyboard
    )

@dp.message(Command("privacy"), StateFilter(None))
async def show_privacy(message: types.Message):
    privacy_policy_url = "https://telegra.ph/Privacy-Policy-for-AAU-Confessions-Bot-04-27"
    privacy_text = (
        "<b>Privacy Information</b>\n\n"
        "▪️ Your Telegram User ID is stored but never shown to other users.\n"
        "▪️ Comments are posted with your chosen Nickname (default: Anonymous).\n"
        "▪️ Your Aura points (⚡︎) are displayed next to your tag on comments.\n"
        "▪️ The confession author can request to contact you. You must explicitly approve sharing your @username.\n"
        "▪️ Other users can view your public profile (Nickname, Emoji, Bio, Aura, and any other details you choose to share) and request to chat anonymously.\n"
        "▪️ Reporting a comment links your User ID to the report for admin review but is not shown publicly.\n"
        f"▪️ The bot admin (User ID: <code>{ADMIN_ID}</code>) can access stored User IDs for moderation.\n\n"
        f'For more details, read our full <a href="{privacy_policy_url}">Privacy Policy</a>.'
    )
    await message.answer(privacy_text, disable_web_page_preview=True)

@dp.message(Command("cancel"), StateFilter('*'))
@dp.message(F.text == "❌ Cancel", StateFilter('*'))
async def cancel_any_state(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("You have nothing to cancel.", reply_markup=get_main_keyboard(message.from_user.id))
        return

    await state.clear()
    await message.answer("Action cancelled. You are back at the main menu.", reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def receive_admin_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id; user_info = message.from_user; message_text = message.text
    if not user_info: await message.answer("Could not identify sender. Action cancelled."); await state.clear(); return
    if len(message_text) < 5: await message.answer("Message too short."); return
    if len(message_text) > 2000: await message.answer("Message too long."); return
    admin_message = ( f"<b>📬 Contact Request from User</b>\n\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Username:</b> @{user_info.username if user_info.username else 'Not Set'}\n"
        f"<b>Message:</b>\n{html.quote(message_text)}"
        f"\n\n---\nReply to this message to respond to User ID <code>{user_id}</code>." )
    try:
        await bot.send_message(ADMIN_ID, admin_message)
        await message.answer("✅ Your message has been sent to the admin.", reply_markup=get_main_keyboard(user_id))
    except Exception as e: logging.error(f"Failed forward msg from {user_id} to admin: {e}"); await message.answer("❌ Error sending message.")
    finally: await state.clear()

@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def handle_admin_reply(message: types.Message, state: FSMContext):
    if await state.get_state() is not None: return
    replied_to = message.reply_to_message
    if replied_to and replied_to.text and ("⚠️ New Comment Report" in replied_to.text or "⚠️ New User Report" in replied_to.text): return
    global bot_info;
    if not bot_info or not replied_to or not replied_to.from_user or replied_to.from_user.id != bot_info.id: return
    target_user_id = None;
    try:
        text_to_search = replied_to.html_text
        start_index = text_to_search.find("<code>") + len("<code>")
        end_index = text_to_search.find("</code>", start_index)
        target_user_id = int(text_to_search[start_index:end_index])
    except (ValueError, AttributeError):
        logging.warning("Could not extract user ID from admin reply context.")
        return
    if target_user_id:
        sent = await safe_send_message(target_user_id, f"💬 <b>Admin Reply:</b>\n\n{html.quote(message.text or '')}")
        if sent: await message.reply("✅ Reply sent to the user.")
        else: await message.reply("⚠️ Failed to send reply. User may have blocked the bot.")

@dp.message(Command("id"))
async def get_user_info_command(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    if not command.args: await message.reply("Usage: /id <user_id>"); return
    try: target_user_id = int(command.args.strip())
    except ValueError: await message.reply("Invalid User ID."); return
    info_parts = [f"ℹ️ <b>User Info for ID:</b> <code>{target_user_id}</code>\n"];
    try:
        chat_info = await bot.get_chat(target_user_id)
        info_parts.append(f"<b>Username:</b> @{html.quote(chat_info.username or 'Not Set')}")
        info_parts.append(f"<b>First Name:</b> {html.quote(chat_info.first_name or 'N/A')}")
    except Exception as e: info_parts.append(f"⚠️ <b>Telegram Details:</b> Could not fetch. (Error: {e})")
    try:
        async with db.acquire() as conn:
            user_points = await get_user_points(target_user_id)
            conf_count = await conn.fetchval("SELECT COUNT(*) FROM confessions WHERE user_id = $1", target_user_id)
            comm_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE user_id = $1", target_user_id)
            status_data = await conn.fetchrow("SELECT * FROM user_status WHERE user_id = $1", target_user_id)
            
            info_parts.append(f"\n<b>Bot Interaction:</b>\n  - <b>Aura Points:</b> ⚡︎ {user_points}\n  - <b>Confessions:</b> {conf_count}\n  - <b>Comments:</b> {comm_count}")
            if status_data:
                info_parts.append(f"  - <b>Nickname:</b> {html.quote(status_data['nickname'] or 'Default')} {status_data['profile_emoji'] or ''}")
                if status_data['nickname_last_changed_at']:
                    can_change_at = status_data['nickname_last_changed_at'] + NICKNAME_COOLDOWN
                    info_parts.append(f"  - <b>Can Change Nickname:</b> {can_change_at.strftime('%Y-%m-%d')}")
                
                info_parts.append(f"  - <b>Allows Contact:</b> {'Yes' if status_data['allow_contact'] else 'No'}")
                info_parts.append(f"  - <b>Accepted Rules:</b> {'Yes' if status_data['has_accepted_rules'] else 'No'}")
                if status_data['is_blocked']:
                    expiry = f"until {status_data['blocked_until'].strftime('%Y-%m-%d')}" if status_data['blocked_until'] else "Permanently"
                    info_parts.append(f"  - <b>Status:</b> ❌ Blocked ({expiry})")
    except Exception as e: info_parts.append(f"\n❌ <b>Bot Interaction:</b> Error fetching database info: {e}")
    await message.reply("\n".join(info_parts));

# --- /profile Command and Handlers ---

async def _render_settings_menu(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Helper function to build the settings menu text and keyboard."""
    async with db.acquire() as conn:
        settings = await conn.fetchrow("SELECT nickname, comments_per_page, profile_emoji, bio, allow_contact FROM user_status WHERE user_id = $1", user_id)
    
    current_nickname = settings.get('nickname') if settings else None
    current_page_size = settings.get('comments_per_page') if settings else None
    current_emoji = settings.get('profile_emoji') if settings else '👤'
    current_bio = settings.get('bio') if settings else None
    allow_contact = settings.get('allow_contact', True) if settings else True

    cpp_display = "All" if current_page_size == 0 else (current_page_size if current_page_size is not None else f'Default ({PAGE_SIZE})')
    contact_status = "✅ On" if allow_contact else "❌ Off"

    settings_text = (
        "<b>⚙️ General Settings</b>\n\n"
        f"<b>Profile Emoji:</b> {current_emoji}\n"
        f"<b>Nickname:</b> {html.quote(current_nickname or 'Default (Anonymous)')}\n"
        f"<b>Bio:</b> {html.quote(current_bio or 'Not set')}\n"
        f"<b>Comments Per Page:</b> {cpp_display}\n"
        f"<b>Allow Chat Requests:</b> {contact_status}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Change Profile Emoji", callback_data="settings_change_emoji")],
        [InlineKeyboardButton(text="✏️ Change Nickname", callback_data="settings_change_nickname")],
        [InlineKeyboardButton(text="📝 Set/Update Bio", callback_data="settings_change_bio")],
        [InlineKeyboardButton(text="ℹ️ Edit Profile Details & Visibility", callback_data="profile_details_menu")],
        [InlineKeyboardButton(text="🔢 Set Comments Per Page", callback_data="settings_change_cpp")],
        [InlineKeyboardButton(text=f"📬 Toggle Chat Requests ({'Off' if allow_contact else 'On'})", callback_data="settings_toggle_contact")],
        [InlineKeyboardButton(text="⬅️ Back to Profile", callback_data="profile_menu_main_1")]
    ])
    return settings_text, keyboard


def create_profile_pagination_keyboard(base_callback: str, current_page: int, total_pages: int, back_to: str):
    builder = InlineKeyboardBuilder()
    row = []
    if current_page > 1:
        row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"{base_callback}_{current_page - 1}"))
    if total_pages > 1:
        row.append(InlineKeyboardButton(text=f"Page {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{base_callback}_{current_page + 1}"))
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data=back_to))
    return builder.as_markup()

@dp.message(Command("profile"))
@dp.message(F.text == "👤 Profile")
async def user_profile(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    points = await get_user_points(user_id)

    profile_text = f"👤 <b>Your Profile</b>\n\n⚡︎ <b>Aura Points:</b> {points}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📜 History", callback_data="profile_menu_history_1")],
        [InlineKeyboardButton(text="🎨 Customization", callback_data="profile_menu_customization_1")]
    ])
    await message.answer(profile_text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("profile_menu_"))
async def handle_profile_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear() 
    user_id = callback_query.from_user.id
    parts = callback_query.data.split("_")
    action = parts[2]
    page = int(parts[-1])

    try:
        if action == "main":
            points = await get_user_points(user_id)
            profile_text = f"👤 <b>Your Profile</b>\n\n⚡︎ <b>Aura Points :</b> {points}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📜 History", callback_data="profile_menu_history_1")],
                [InlineKeyboardButton(text="🎨 Customization", callback_data="profile_menu_customization_1")]
            ])
            await callback_query.message.edit_text(profile_text, reply_markup=keyboard)

        elif action == "history":
            history_text = "📜 <b>History</b>\n\nSelect which history you would like to view."
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="My Confessions", callback_data="profile_menu_confessions_1")],
                [InlineKeyboardButton(text="My Comments", callback_data="profile_menu_comments_1")],
                [InlineKeyboardButton(text="⬅️ Back to Profile", callback_data="profile_menu_main_1")]
            ])
            await callback_query.message.edit_text(history_text, reply_markup=keyboard)

        elif action == "confessions":
            async with db.acquire() as conn:
                total_count = await conn.fetchval("SELECT COUNT(*) FROM confessions WHERE user_id = $1", user_id) or 0
                if total_count == 0:
                    await callback_query.answer("You haven't submitted any confessions yet.", show_alert=True)
                    return

                total_pages = (total_count + 5 - 1) // 5
                page = max(1, min(page, total_pages))
                offset = (page - 1) * 5
                confessions = await conn.fetch("SELECT id, text, status, created_at FROM confessions WHERE user_id = $1 ORDER BY created_at DESC LIMIT 5 OFFSET $2", user_id, offset)

            response_text = f"<b>📜 Your Confessions (Page {page}/{total_pages})</b>\n\n"
            builder = InlineKeyboardBuilder()
            for conf in confessions:
                snippet = html.quote(conf['text'][:60]) + ('...' if len(conf['text']) > 60 else '')
                status_emoji = {"approved": "✅", "pending": "⏳", "rejected": "❌", "deleted": "🗑️"}.get(conf['status'], "❓")
                response_text += f"<b>ID:</b> #{conf['id']} ({status_emoji} {conf['status'].capitalize()})\n<i>\"{snippet}\"</i>\n\n"
                if conf['status'] in ['approved', 'pending']:
                    builder.row(InlineKeyboardButton(text=f"Request Deletion for #{conf['id']}", callback_data=f"req_del_conf_{conf['id']}"))

            nav_keyboard = create_profile_pagination_keyboard("profile_menu_confessions", page, total_pages, "profile_menu_history_1")
            final_markup = builder.attach(InlineKeyboardBuilder.from_markup(nav_keyboard)).as_markup()
            await callback_query.message.edit_text(response_text, reply_markup=final_markup)

        elif action == "comments":
            async with db.acquire() as conn:
                total_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE user_id = $1", user_id) or 0
                if total_count == 0:
                    await callback_query.answer("You haven't made any comments yet.", show_alert=True)
                    return

                total_pages = (total_count + 5 - 1) // 5
                page = max(1, min(page, total_pages))
                offset = (page - 1) * 5
                comments = await conn.fetch("SELECT id, text, sticker_file_id, animation_file_id, confession_id, created_at FROM comments WHERE user_id = $1 ORDER BY created_at DESC LIMIT 5 OFFSET $2", user_id, offset)

            response_text = f"<b>💬 Your Comments (Page {page}/{total_pages})</b>\n\n"
            for comm in comments:
                if comm['text']: snippet = html.quote(comm['text'][:60]) + ('...' if len(comm['text']) > 60 else '')
                elif comm['sticker_file_id']: snippet = "[Sticker]"
                elif comm['animation_file_id']: snippet = "[GIF]"
                else: snippet = "[Unknown Content]"
                link = f"https://t.me/{bot_info.username}?start=view_{comm['confession_id']}"
                response_text += f"On Confession <a href='{link}'>#{comm['confession_id']}</a>:\n<i>\"{snippet}\"</i>\n\n"

            nav_keyboard = create_profile_pagination_keyboard("profile_menu_comments", page, total_pages, "profile_menu_history_1")
            await callback_query.message.edit_text(response_text, reply_markup=nav_keyboard, disable_web_page_preview=True)
        
        elif action == "customization":
            settings_text, keyboard = await _render_settings_menu(user_id)
            await callback_query.message.edit_text(settings_text, reply_markup=keyboard)

    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logging.info("Content for profile menu was not modified.")
        else:
            raise
    finally:
        await callback_query.answer()


@dp.callback_query(F.data == "settings_change_nickname")
async def settings_change_nickname_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    async with db.acquire() as conn:
        last_changed = await conn.fetchval("SELECT nickname_last_changed_at FROM user_status WHERE user_id = $1", user_id)
    
    if last_changed:
        time_since_change = datetime.now(timezone.utc) - last_changed
        if time_since_change < NICKNAME_COOLDOWN:
            remaining_time = NICKNAME_COOLDOWN - time_since_change
            days, seconds = remaining_time.days, remaining_time.seconds
            hours = seconds // 3600
            await callback_query.answer(
                f"You can change your nickname again in {days} days and {hours} hours.",
                show_alert=True
            )
            return

    await state.set_state(SettingsForm.waiting_for_nickname)
    await callback_query.message.edit_text(
        "Please send your new nickname (max 32 alphanumeric characters).\n"
        "Send 'default' to reset to Anonymous."
    )
    await callback_query.message.answer("Waiting for your nickname...", reply_markup=cancel_keyboard)
    await callback_query.answer()

@dp.message(SettingsForm.waiting_for_nickname, F.text)
async def settings_set_nickname(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    nickname = message.text.strip()
    feedback = ""
    
    async with db.acquire() as conn:
        if nickname.lower() == 'default':
            await conn.execute(
                """INSERT INTO user_status (user_id, nickname, nickname_last_changed_at) VALUES ($1, NULL, NULL)
                   ON CONFLICT (user_id) DO UPDATE SET nickname = NULL, nickname_last_changed_at = NULL""",
                user_id
            )
            feedback = "✅ Nickname reset to default (Anonymous)."
        else:
            if not (3 <= len(nickname) <= 32 and nickname.replace(' ', '').isalnum()):
                await message.answer("⚠️ Invalid nickname. Please use 3-32 alphanumeric characters and spaces.")
                return
            if nickname.lower() in RESERVED_NICKNAMES:
                await message.answer("⚠️ That nickname is reserved. Please choose another.")
                return
            
            existing_user = await conn.fetchval("SELECT user_id FROM user_status WHERE LOWER(nickname) = $1", nickname.lower())
            if existing_user and existing_user != user_id:
                await message.answer("⚠️ That nickname is already taken. Please choose another.")
                return

            await conn.execute(
                """INSERT INTO user_status (user_id, nickname, nickname_last_changed_at) VALUES ($1, $2, CURRENT_TIMESTAMP)
                   ON CONFLICT (user_id) DO UPDATE SET nickname = $2, nickname_last_changed_at = CURRENT_TIMESTAMP""",
                user_id, nickname
            )
            feedback = f"✅ Nickname set to: <b>{html.quote(nickname)}</b>"

    await state.clear()
    await message.answer(feedback, reply_markup=get_main_keyboard(user_id))
    
    settings_text, keyboard = await _render_settings_menu(message.from_user.id)
    await message.answer(settings_text, reply_markup=keyboard)


@dp.callback_query(F.data == "settings_change_cpp")
async def settings_change_cpp_prompt(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    options = [5, 10, 15, 20, 25]
    for opt in options:
        builder.button(text=str(opt), callback_data=f"settings_set_cpp_{opt}")
    builder.adjust(len(options))
    builder.row(InlineKeyboardButton(text="♾️ All", callback_data="settings_set_cpp_0"))
    builder.row(InlineKeyboardButton(text=f"Reset to Default ({PAGE_SIZE})", callback_data="settings_set_cpp_default"))
    builder.row(InlineKeyboardButton(text="⬅️ Back to Customization", callback_data="profile_menu_customization_1"))

    await callback_query.message.edit_text(
        "Select how many comments you want to see per page.",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()

@dp.callback_query(F.data.startswith("settings_set_cpp_"))
async def settings_set_cpp(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    value = callback_query.data.split("_")[-1]

    if value == 'default':
        cpp_to_set = None
        feedback = f"✅ Comments per page reset to default ({PAGE_SIZE})."
    else:
        cpp_to_set = int(value)
        if cpp_to_set == 0:
            feedback = "✅ Comments per page set to show all."
        else:
            feedback = f"✅ Comments per page set to {cpp_to_set}."

    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_status (user_id, comments_per_page) VALUES ($1, $2)
               ON CONFLICT (user_id) DO UPDATE SET comments_per_page = $2""",
            user_id, cpp_to_set
        )
    
    await callback_query.answer(feedback.split("✅ ")[1], show_alert=True)
    
    settings_text, keyboard = await _render_settings_menu(user_id)
    await callback_query.message.edit_text(settings_text, reply_markup=keyboard)


@dp.callback_query(F.data == "settings_change_emoji")
async def settings_change_emoji_prompt(callback_query: types.CallbackQuery):
    builder = InlineKeyboardBuilder()
    for emoji in PROFILE_EMOJIS:
        builder.button(text=emoji, callback_data=f"settings_set_emoji_{emoji}")
    builder.adjust(5) 
    builder.row(InlineKeyboardButton(text="⬅️ Back to Customization", callback_data="profile_menu_customization_1"))
    
    await callback_query.message.edit_text(
        "Choose your new profile emoji.",
        reply_markup=builder.as_markup()
    )
    await callback_query.answer()

@dp.callback_query(F.data.startswith("settings_set_emoji_"))
async def settings_set_emoji(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    emoji = callback_query.data.split("_")[-1]

    if emoji not in PROFILE_EMOJIS:
        await callback_query.answer("Invalid emoji selected.", show_alert=True)
        return

    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_status (user_id, profile_emoji) VALUES ($1, $2)
               ON CONFLICT (user_id) DO UPDATE SET profile_emoji = $2""",
            user_id, emoji
        )
    
    await callback_query.answer(f"Profile emoji set to {emoji}!", show_alert=True)
    
    settings_text, keyboard = await _render_settings_menu(user_id)
    await callback_query.message.edit_text(settings_text, reply_markup=keyboard)


# --- Bio and Contact Settings Handlers ---
@dp.callback_query(F.data == "settings_change_bio")
async def settings_change_bio_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(SettingsForm.waiting_for_bio)
    await callback_query.message.edit_text(
        "Please send your new bio (max 250 characters).\n"
        "Send 'remove' to clear your bio."
    )
    await callback_query.message.answer("Waiting for your bio...", reply_markup=cancel_keyboard)
    await callback_query.answer()

@dp.message(SettingsForm.waiting_for_bio, F.text)
async def settings_set_bio(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    bio_text = message.text.strip()
    feedback = ""

    if len(bio_text) > 250:
        await message.answer("⚠️ Your bio is too long (max 250 characters). Please try again.")
        return

    async with db.acquire() as conn:
        if bio_text.lower() == 'remove':
            await conn.execute(
                "UPDATE user_status SET bio = NULL WHERE user_id = $1", user_id
            )
            feedback = "✅ Your bio has been removed."
        else:
            await conn.execute(
                "INSERT INTO user_status (user_id, bio) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET bio = $2",
                user_id, bio_text
            )
            feedback = "✅ Your bio has been updated."
    
    await state.clear()
    await message.answer(feedback, reply_markup=get_main_keyboard(user_id))

    settings_text, keyboard = await _render_settings_menu(message.from_user.id)
    await message.answer(settings_text, reply_markup=keyboard)


@dp.callback_query(F.data == "settings_toggle_contact")
async def settings_toggle_contact(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    async with db.acquire() as conn:
        new_status = await conn.fetchval(
            """UPDATE user_status SET allow_contact = NOT allow_contact
               WHERE user_id = $1 RETURNING allow_contact""",
            user_id
        )
    
    status_text = "enabled" if new_status else "disabled"
    await callback_query.answer(f"Chat requests have been {status_text}.", show_alert=True)
    
    settings_text, keyboard = await _render_settings_menu(user_id)
    await callback_query.message.edit_text(settings_text, reply_markup=keyboard)

# --- Handlers for New Customizable Profile Details ---

async def _render_profile_details_menu(user_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    """Helper to build the profile details & visibility menu."""
    async with db.acquire() as conn:
        settings = await conn.fetchrow("""
            SELECT gender, campus, year, department, interests,
                   show_gender, show_campus, show_year, show_department, show_interests
            FROM user_status WHERE user_id = $1
        """, user_id)

    def get_status_emoji(is_shown):
        return "✅" if is_shown else "❌"

    menu_text = "<b>ℹ️ Edit Profile Details & Visibility</b>\n\nSet your details and toggle whether they appear on your public profile."
    
    builder = InlineKeyboardBuilder()
    builder.button(text=f"Gender: {settings.get('gender') or 'Not Set'}", callback_data="set_detail_gender_1")
    builder.button(text=f"Campus: {settings.get('campus') or 'Not Set'}", callback_data="set_detail_campus_1")
    builder.button(text=f"Year: {settings.get('year') or 'Not Set'}", callback_data="set_detail_year_1")
    builder.button(text=f"Department: {settings.get('department') or 'Not Set'}", callback_data="set_detail_department_1")
    builder.button(text=f"Select Interests ({len(settings.get('interests') or [])}/{MAX_INTERESTS})", callback_data="set_detail_interests_1")
    builder.adjust(1)

    builder.row(
        InlineKeyboardButton(text=f"Show Gender: {get_status_emoji(settings.get('show_gender'))}", callback_data="toggle_detail_gender"),
        InlineKeyboardButton(text=f"Show Campus: {get_status_emoji(settings.get('show_campus'))}", callback_data="toggle_detail_campus")
    )
    builder.row(
        InlineKeyboardButton(text=f"Show Year: {get_status_emoji(settings.get('show_year'))}", callback_data="toggle_detail_year"),
        InlineKeyboardButton(text=f"Show Department: {get_status_emoji(settings.get('show_department'))}", callback_data="toggle_detail_department")
    )
    builder.row(
        InlineKeyboardButton(text=f"Show Interests: {get_status_emoji(settings.get('show_interests'))}", callback_data="toggle_detail_interests")
    )
    
    builder.row(InlineKeyboardButton(text="⬅️ Back to Customization", callback_data="profile_menu_customization_1"))
    
    return menu_text, builder.as_markup()

@dp.callback_query(F.data == "profile_details_menu")
async def show_profile_details_menu(callback_query: types.CallbackQuery):
    menu_text, keyboard = await _render_profile_details_menu(callback_query.from_user.id)
    await callback_query.message.edit_text(menu_text, reply_markup=keyboard)
    await callback_query.answer()

@dp.callback_query(F.data.startswith("set_detail_"))
async def prompt_for_profile_detail(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split("_")
    field = parts[2]
    page = int(parts[3])

    options_map = {
        "gender": GENDERS, "campus": CAMPUSES,
        "year": YEARS, "department": DEPARTMENTS,
        "interests": INTERESTS
    }

    if field in options_map:
        options = options_map[field]
        builder = InlineKeyboardBuilder()
        
        # --- PAGINATION LOGIC ---
        page_size = PROFILE_OPTIONS_PAGE_SIZE
        total_pages = (len(options) + page_size - 1) // page_size
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        options_on_page = options[start_index:end_index]

        if field == "interests":
            # For interests, we go to a stateful selection mode
            await state.set_state(SettingsForm.selecting_interests)
            async with db.acquire() as conn:
                current_interests = await conn.fetchval("SELECT interests FROM user_status WHERE user_id = $1", callback_query.from_user.id) or []
            await state.update_data(selected_interests=current_interests)
            
            # Use enumerate to get the index for the callback data
            for i, interest in enumerate(options_on_page):
                prefix = "✅ " if interest in current_interests else ""
                builder.button(text=f"{prefix}{interest}", callback_data=f"interest_{interest}")
        else:
            # For other fields, selection is direct
            # --- FIX: Use enumerate to get index for callback_data ---
            for i, option in enumerate(options_on_page):
                # The index passed in callback_data is the global index in the original list
                global_index = start_index + i
                builder.button(text=option, callback_data=f"set_option_{field}_{global_index}")

        builder.adjust(2)

        # Pagination controls
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"set_detail_{field}_{page-1}"))
        if total_pages > 1:
            nav_row.append(InlineKeyboardButton(text=f"Page {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"set_detail_{field}_{page+1}"))
        if nav_row:
            builder.row(*nav_row)

        if field == "interests":
             builder.row(InlineKeyboardButton(text="🗑️ Clear All Interests", callback_data="interest_clear"))
             builder.row(InlineKeyboardButton(text=f"➡️ Done Selecting", callback_data="interest_done"))
        else:
             builder.row(InlineKeyboardButton(text="🗑️ Clear This Field", callback_data=f"set_option_{field}_REMOVE"))

        builder.row(InlineKeyboardButton(text="⬅️ Back", callback_data="profile_details_menu"))
        
        prompt_text = f"Select up to {MAX_INTERESTS} interests." if field == "interests" else f"Select your <b>{field.capitalize()}</b>:"
        
        await callback_query.message.edit_text(prompt_text, reply_markup=builder.as_markup())
        await callback_query.answer()
        return


@dp.callback_query(F.data.startswith("set_option_"))
async def set_option_profile_detail(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    
    prefix = "set_option_"
    if not callback_query.data.startswith(prefix):
        logging.error(f"Invalid callback data format in set_option_profile_detail: {callback_query.data}")
        await callback_query.answer("An error occurred.", show_alert=True)
        return

    rest = callback_query.data[len(prefix):]
    try:
        field, value_str = rest.split("_", 1)
    except ValueError:
        logging.error(f"Could not parse field and value from '{rest}'")
        await callback_query.answer("An error occurred.", show_alert=True)
        return

    db_value = None
    display_value = ""

    if value_str == 'REMOVE':
        db_value = None
        feedback = f"Your {field} has been removed."
    else:
        # --- FIX: Look up the value from the list using the index ---
        try:
            index = int(value_str)
            options_map = {
                "gender": GENDERS, "campus": CAMPUSES,
                "year": YEARS, "department": DEPARTMENTS
            }
            if field in options_map:
                db_value = options_map[field][index]
                display_value = db_value
                feedback = f"Your {field} is now set to {display_value}."
            else:
                await callback_query.answer("Invalid field specified.", show_alert=True)
                return
        except (ValueError, IndexError):
            await callback_query.answer("Invalid selection.", show_alert=True)
            return

    async with db.acquire() as conn:
        allowed_fields = ["gender", "campus", "year", "department"]
        if field not in allowed_fields:
            await callback_query.answer("Invalid field specified.", show_alert=True)
            return
            
        await conn.execute(f"UPDATE user_status SET {field} = $1 WHERE user_id = $2", db_value, user_id)

    await callback_query.answer(feedback, show_alert=False)

    menu_text, keyboard = await _render_profile_details_menu(user_id)
    await callback_query.message.edit_text(menu_text, reply_markup=keyboard)


@dp.callback_query(StateFilter(SettingsForm.selecting_interests), F.data.startswith("interest_"))
async def handle_interest_selection(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    action = callback_query.data.split("_", 1)[1]
    data = await state.get_data()
    selected = data.get("selected_interests", [])

    if action == "cancel" or action == "done":
        if action == "done":
             async with db.acquire() as conn:
                await conn.execute("UPDATE user_status SET interests = $1 WHERE user_id = $2", selected or None, user_id)
             await callback_query.answer("Interests saved!")
        else: # cancel
            await callback_query.answer("Cancelled.")

        await state.clear()
        menu_text, keyboard = await _render_profile_details_menu(user_id)
        await callback_query.message.edit_text(menu_text, reply_markup=keyboard)
        return
    
    # --- NEW: Handle clearing all interests ---
    if action == "clear":
        selected = []
        await callback_query.answer("All interests cleared.")
    else:
        interest = action
        if interest in selected:
            selected.remove(interest)
        elif len(selected) < MAX_INTERESTS:
            selected.append(interest)
        else:
            await callback_query.answer(f"You can only select up to {MAX_INTERESTS} interests.", show_alert=True)
            return
        
        await callback_query.answer(f"'{interest}' {'selected' if interest in selected else 'deselected'}.")


    await state.update_data(selected_interests=selected)
    
    # Re-render the current page of interests with updated selections
    # We can get the current page from the callback_query message's reply_markup if needed,
    # but for simplicity, we'll just go back to page 1 which is a common UX pattern.
    callback_query.data = "set_detail_interests_1"
    await prompt_for_profile_detail(callback_query, state)


@dp.callback_query(F.data.startswith("toggle_detail_"))
async def toggle_profile_detail_visibility(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    field = callback_query.data.replace("toggle_detail_", "")
    db_field = f"show_{field}"
    
    async with db.acquire() as conn:
        await conn.execute(f"UPDATE user_status SET {db_field} = NOT {db_field} WHERE user_id = $1", user_id)
    
    menu_text, keyboard = await _render_profile_details_menu(user_id)
    await callback_query.message.edit_text(menu_text, reply_markup=keyboard)
    await callback_query.answer(f"{field.capitalize()} visibility toggled.")



# --- Deletion Request Handlers ---

@dp.callback_query(F.data.startswith("req_del_conf_"))
async def request_deletion_prompt(callback_query: types.CallbackQuery):
    conf_id = int(callback_query.data.split("_")[-1])
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, Request Deletion", callback_data=f"confirm_del_conf_{conf_id}")],
        [InlineKeyboardButton(text="❌ No, Cancel", callback_data="profile_menu_confessions_1")]
    ])
    await callback_query.message.edit_text(
        f"Are you sure you want to request the deletion of Confession #{conf_id}? This action, if approved by an admin, is irreversible.",
        reply_markup=keyboard
    )
    await callback_query.answer()

@dp.callback_query(F.data.startswith("confirm_del_conf_"))
async def confirm_deletion_request(callback_query: types.CallbackQuery, state: FSMContext):
    conf_id = int(callback_query.data.split("_")[-1])
    user_id = callback_query.from_user.id

    async with db.acquire() as conn:
        try:
            conf_data = await conn.fetchrow("SELECT user_id, text, status FROM confessions WHERE id = $1", conf_id)
            if not conf_data or conf_data['user_id'] != user_id:
                await callback_query.answer("This is not your confession.", show_alert=True); return
            if conf_data['status'] not in ['approved', 'pending']:
                await callback_query.answer(f"This confession cannot be deleted (status: {conf_data['status']}).", show_alert=True); return

            await conn.execute(
                """INSERT INTO deletion_requests (confession_id, user_id, status, created_at) VALUES ($1, $2, 'pending', CURRENT_TIMESTAMP)
                   ON CONFLICT (confession_id, user_id) DO NOTHING""", conf_id, user_id
            )

            snippet = html.quote(conf_data['text'][:200])
            admin_text = (f"🗑️ <b>New Deletion Request</b>\n\n"
                          f"<b>User ID:</b> <code>{user_id}</code>\n"
                          f"<b>Confession ID:</b> <code>{conf_id}</code>\n\n"
                          f"<b>Content Snippet:</b>\n<i>\"{snippet}...\"</i>")
            admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Approve Deletion", callback_data=f"admin_approve_delete_{conf_id}")],
                [InlineKeyboardButton(text="❌ Reject Deletion", callback_data=f"admin_reject_delete_{conf_id}")]
            ])
            await bot.send_message(ADMIN_ID, admin_text, reply_markup=admin_keyboard)
            await callback_query.answer("✅ Deletion request sent. An admin will review it shortly.", show_alert=True)
            
            callback_query.data = "profile_menu_confessions_1"
            await handle_profile_menu(callback_query, state)

        except asyncpg.exceptions.UniqueViolationError:
             await callback_query.answer("You have already requested deletion for this confession.", show_alert=True)
        except Exception as e:
            logging.error(f"Error processing deletion request for conf {conf_id} by user {user_id}: {e}")
            await callback_query.answer("An error occurred while sending your request.", show_alert=True)


# --- NEW CONFESSION SUBMISSION FLOW ---

@dp.message(Command("confess"), StateFilter(None))
@dp.message(F.text == "✍️ Confess", StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    await state.set_state(ConfessionForm.waiting_for_text)
    await message.answer(
        "Please send the text of your confession. You will be able to review, edit, or enhance it next",
        reply_markup=cancel_keyboard
    )

@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    conf_text = message.text
    if len(conf_text) < 10:
        await message.answer("Your confession is too short. Please provide at least 10 characters.")
        return
    if len(conf_text) > 3900:
        await message.answer(f"Your confession is too long (max 3900 characters). Please try again.")
        return

    await state.update_data(confession_text=conf_text)
    await state.set_state(ConfessionForm.waiting_for_confirmation)

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Submit", callback_data="conf_submit")
    builder.button(text="✍️ Edit", callback_data="conf_edit")
    builder.button(text="✨ Enhance with AI", callback_data="conf_enhance_ai")
    builder.button(text="❌ Cancel", callback_data="conf_cancel")
    builder.adjust(1)

    await message.answer(
        "<b>Here is a preview of your confession:</b>\n\n"
        f"<i>{html.quote(conf_text)}</i>\n\n"
        "Please review it and choose an option below.",
        reply_markup=builder.as_markup()
    )

# --- MODIFICATION: Gemini API call with key rotation ---
async def call_gemini_with_rotation(prompt: str) -> Optional[str]:
    """Calls the Gemini API, rotating keys on failure."""
    if not GEMINI_API_KEYS:
        logging.error("No Gemini API keys provided for API call.")
        return None

    for key in GEMINI_API_KEYS:
        try:
            genai.configure(api_key=key)
            model = genai.GenerativeModel('gemini-1.5-flash') # Using a recommended model
            response = await model.generate_content_async(prompt)
            return response.text
        except Exception as e:
            logging.warning(f"Gemini API call failed with key ending in '...{key[-4:]}': {e}")
            continue # Try the next key

    logging.error("All Gemini API keys failed.")
    return None

async def get_ai_enhanced_text(original_text: str) -> Optional[str]:
    """Helper function to call the Gemini API and return the enhanced text."""
    prompt = (f"A user has submitted a confession. Please rephrase it to make it more articulate and clear. "
              f"Fix any broken English, grammar, or spelling mistakes. "
              f"Preserve the original meaning and tone of the confession. Do not add any new information or opinions. "
              f"Only send the confession back, no other text before or after it. "
              f"Use a very casual and simple vocabulary and tone. "
              f"Never use em dashes. "
              f"Only use emojis when necessary (maximum of 1-2). 1 is ideal, and only if the confession needs one. "
              f"Here is the confession:\n\n'{original_text}'")
    
    return await call_gemini_with_rotation(prompt)


@dp.callback_query(StateFilter(ConfessionForm.waiting_for_confirmation), F.data.startswith("conf_"))
async def handle_confession_confirmation(callback_query: types.CallbackQuery, state: FSMContext):
    action = callback_query.data.split("_")[1]
    user_id = callback_query.from_user.id

    if action == "submit":
        await state.set_state(ConfessionForm.selecting_categories)
        await state.update_data(selected_categories=[])
        await callback_query.message.edit_text(
            "Great! Now, please choose categories for your confession.",
            reply_markup=create_category_keyboard([])
        )
        await callback_query.answer()

    elif action == "edit":
        await state.set_state(ConfessionForm.waiting_for_text)
        await callback_query.message.edit_text("Okay, please send the new version of your confession.")
        await callback_query.message.answer("Waiting for your edited confession...", reply_markup=cancel_keyboard)
        await callback_query.answer()

    elif action == "enhance":
        if not GEMINI_API_KEYS:
            await callback_query.answer("Sorry, AI features are currently unavailable.", show_alert=True)
            return

        await callback_query.answer("✨ Enhancing with AI... Please wait.", show_alert=False)
        data = await state.get_data()
        original_text = data.get("confession_text")

        if not original_text:
            await callback_query.answer("Error: Could not find original text.", show_alert=True)
            return

        enhanced_text = await get_ai_enhanced_text(original_text)
        
        if not enhanced_text:
            await callback_query.answer("Sorry, the AI enhancement failed. Please try again later.", show_alert=True)
            return

        # Add AI marker and update state
        final_text = enhanced_text.strip() + f"\n\n{AI_ENHANCED_MARKER}"
        await state.update_data(confession_text=final_text)

        builder = InlineKeyboardBuilder()
        builder.button(text="✅ Submit This Version", callback_data="conf_submit")
        builder.button(text="✍️ Edit", callback_data="conf_edit")
        builder.button(text="❌ Cancel", callback_data="conf_cancel")
        builder.adjust(1)
        
        await callback_query.message.edit_text(
            "<b>Here is the AI-enhanced version:</b>\n\n"
            f"<i>{html.quote(enhanced_text)}</i>\n\n"
            "You can submit this, edit it further, or cancel.",
            reply_markup=builder.as_markup()
        )

    elif action == "cancel":
        await state.clear()
        await callback_query.message.edit_text("Confession submission cancelled.")
        await callback_query.message.answer("You are back at the main menu.", reply_markup=get_main_keyboard(user_id))
        await callback_query.answer()

# --- MODIFICATION: process_confession_submission ---
# I've changed the function signature to accept user_id directly
# and a message object specifically for sending replies.
async def process_confession_submission(user_id: int, state: FSMContext, message: types.Message):
    # The user_id is now passed directly, not inferred from message.from_user.id
    state_data = await state.get_data()
    selected_categories: List[str] = state_data.get("selected_categories", [])
    conf_text: str = state_data.get("confession_text", "")
    keyboard = get_main_keyboard(user_id)

    if not selected_categories or not conf_text:
        await message.answer("⚠️ Error: Information lost. Please start again.", reply_markup=keyboard)
        await state.clear(); return

    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                conf_id = await conn.fetchval(
                    "INSERT INTO confessions (text, user_id, categories, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                    conf_text, user_id, selected_categories)
                if not conf_id: raise Exception("Failed to get confession ID")
                await update_user_points(conn, user_id, POINTS_PER_CONFESSION)

        category_tags = " ".join([f"#{html.quote(cat)}" for cat in selected_categories])
        kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{conf_id}")],
                                                   [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{conf_id}")]])
        admin_msg_text = f"<b>New Confession Review</b>\n<b>ID:</b> {conf_id}\n<b>Categories:</b> {category_tags}\n<b>User ID:</b> <code>{user_id}</code>\n\n<b>Text:</b>\n{html.quote(conf_text)}"

        await bot.send_message(ADMIN_ID, admin_msg_text, reply_markup=kbd)
        await message.answer("✅ Your confession has been submitted and is pending review.", reply_markup=keyboard)
        logging.info(f"Confession #{conf_id} (Cats: {', '.join(selected_categories)}) submitted by User ID {user_id}")

    except Exception as e:
        logging.error(f"Error processing confession from {user_id}: {e}", exc_info=True)
        await message.answer("An internal error occurred.")
    finally:
        await state.clear()

async def get_ai_categories(confession_text: str) -> List[str]:
    valid_categories = [cat for cat in CATEGORIES if cat]
    prompt = (f"Analyze the following confession and select the most relevant categories from the provided list. "
              f"It may have amahric words written in latin letters so understand them too"
              f"You must choose a maximum of {MAX_CATEGORIES} categories. "
              f"Return only the category names, separated by commas. Do not add any other text, explanation, or formatting. "
              f"Try your best to selet atleast 2 catagories"
              f"If no category seems relevant, return 'Other'.\n\n"
              f"Available Categories: {', '.join(valid_categories)}\n\n"
              f"Confession:\n'{html.quote(confession_text)}'")
    
    response_text = await call_gemini_with_rotation(prompt)
    if not response_text:
        return ["Other"] # Default on failure

    selected = [cat.strip() for cat in response_text.split(',') if cat.strip() in valid_categories]
    return selected[:MAX_CATEGORIES] if selected else ["Other"]


@dp.callback_query(StateFilter(ConfessionForm.selecting_categories), F.data == "category_auto_ai")
async def handle_auto_ai_categories(callback_query: types.CallbackQuery, state: FSMContext):
    if not GEMINI_API_KEYS:
        await callback_query.answer("Sorry, AI features are currently unavailable.", show_alert=True)
        return
        
    user_data = await state.get_data()
    confession_text = user_data.get("confession_text")

    if not confession_text:
        await callback_query.answer("Error: Confession text not found. Please start over.", show_alert=True)
        await state.clear(); return

    await callback_query.answer("🤖 Analyzing confession to select categories...")
    
    ai_selected_categories = await get_ai_categories(confession_text)
    await state.update_data(selected_categories=ai_selected_categories)
    
    await callback_query.message.edit_reply_markup(reply_markup=create_category_keyboard(ai_selected_categories))

# --- MODIFICATION: handle_category_selection ---
# This is the function that calls the one above. I've updated the call.
@dp.callback_query(StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
async def handle_category_selection(callback_query: types.CallbackQuery, state: FSMContext):
    action = callback_query.data.split("_", 1)[1]
    user_id = callback_query.from_user.id
    
    if action == "auto": # This is caught by the specific handler above
        return
        
    user_data = await state.get_data()
    selected_categories: List[str] = user_data.get("selected_categories", [])
    
    if action == "cancel":
        await state.clear()
        await callback_query.message.edit_text("Confession submission cancelled.", reply_markup=None)
        await callback_query.message.answer("You are back at the main menu.", reply_markup=get_main_keyboard(user_id))
        await callback_query.answer()
        return

    if action == "done":
        current_selected = (await state.get_data()).get("selected_categories", [])
        if not current_selected:
            await callback_query.answer("Please select at least 1 category.", show_alert=True); return
        if len(current_selected) > MAX_CATEGORIES:
            await callback_query.answer(f"Too many categories (max {MAX_CATEGORIES}). Please remove some.", show_alert=True); return
        
        await callback_query.message.delete()
        # --- FIX ---
        # Instead of passing the whole message object and inferring the ID,
        # we now pass the user's ID directly from the callback query.
        await process_confession_submission(callback_query.from_user.id, state, callback_query.message)
        await callback_query.answer()
        return

    category = action
    if category in CATEGORIES:
        if category in selected_categories:
            selected_categories.remove(category)
        elif len(selected_categories) < MAX_CATEGORIES:
            selected_categories.append(category)
        else:
            await callback_query.answer(f"You can only select up to {MAX_CATEGORIES} categories.", show_alert=True); return

        await state.update_data(selected_categories=selected_categories)
        await callback_query.message.edit_reply_markup(reply_markup=create_category_keyboard(selected_categories))
        await callback_query.answer(f"'{category}' {'selected' if category in selected_categories else 'deselected'}.")


# --- ADMIN REVIEW --- Helper function to display the current confession for review
async def display_current_review_confession(message: types.Message, state: FSMContext, edit_message: bool = False):
    """Fetches, formats, and displays the current pending confession for the admin."""
    data = await state.get_data()
    pending_ids = data.get("pending_ids", [])
    current_index = data.get("current_index", 0)

    if not pending_ids or current_index >= len(pending_ids):
        await state.clear()
        text = "✅ All pending confessions have been reviewed."
        if edit_message:
            await message.edit_text(text, reply_markup=None)
        else:
            await message.answer(text, reply_markup=get_main_keyboard(ADMIN_ID))
        return

    conf_id = pending_ids[current_index]
    async with db.acquire() as conn:
        conf = await conn.fetchrow("SELECT id, text, user_id, categories FROM confessions WHERE id = $1", conf_id)

    if not conf:
        # This confession might have been deleted or processed elsewhere.
        pending_ids.pop(current_index)
        await state.update_data(pending_ids=pending_ids)
        # Retry with the updated list
        await display_current_review_confession(message, state, edit_message=edit_message)
        return

    category_tags = " ".join([f"#{html.quote(cat)}" for cat in conf['categories'] or []])
    review_text = (
        f"<b>📬 Confession Review ({current_index + 1}/{len(pending_ids)})</b>\n\n"
        f"<b>ID:</b> {conf['id']}\n"
        f"<b>User ID:</b> <code>{conf['user_id']}</code>\n"
        f"<b>Categories:</b> {category_tags}\n\n"
        f"<b>Text:</b>\n{html.quote(conf['text'])}"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Approve", callback_data=f"admin_review_approve_{conf['id']}")
    builder.button(text="❌ Reject", callback_data=f"admin_review_reject_{conf['id']}")
    nav_row = []
    if current_index > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data="admin_review_nav_prev"))
    if current_index < len(pending_ids) - 1:
        nav_row.append(InlineKeyboardButton(text="Next ➡️", callback_data="admin_review_nav_next"))
    
    builder.row(*nav_row)
    builder.row(InlineKeyboardButton(text="Exit Review", callback_data="admin_review_nav_exit"))
    
    try:
        if edit_message:
            await message.edit_text(review_text, reply_markup=builder.as_markup())
        else:
            await message.answer(review_text, reply_markup=builder.as_markup())
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            logging.error(f"Error editing review message: {e}")
        # If not modified, we just ignore it.


# --- ADMIN REVIEW --- Handler to start the review process
@dp.message(F.text == "📬 Review Pending", F.from_user.id == ADMIN_ID, StateFilter(None))
async def admin_start_review(message: types.Message, state: FSMContext):
    async with db.acquire() as conn:
        pending_confessions = await conn.fetch("SELECT id FROM confessions WHERE status = 'pending' ORDER BY id ASC")

    if not pending_confessions:
        await message.answer("No pending confessions to review.", reply_markup=get_main_keyboard(ADMIN_ID))
        return

    pending_ids = [row['id'] for row in pending_confessions]
    await state.set_state(AdminReview.reviewing)
    await state.update_data(pending_ids=pending_ids, current_index=0)

    await display_current_review_confession(message, state)


# --- ADMIN REVIEW --- Handler for navigation buttons (Next, Prev, Exit)
@dp.callback_query(StateFilter(AdminReview.reviewing), F.data.startswith("admin_review_nav_"))
async def admin_navigate_review(callback_query: types.CallbackQuery, state: FSMContext):
    action = callback_query.data.split("_")[-1]
    data = await state.get_data()
    current_index = data.get("current_index", 0)
    
    if action == "next":
        current_index += 1
    elif action == "prev":
        current_index -= 1
    elif action == "exit":
        await state.clear()
        await callback_query.message.edit_text("Review session closed.", reply_markup=None)
        await callback_query.message.answer("You are back at the main menu.", reply_markup=get_main_keyboard(ADMIN_ID))
        await callback_query.answer()
        return

    await state.update_data(current_index=current_index)
    await display_current_review_confession(callback_query.message, state, edit_message=True)
    await callback_query.answer()


# --- ADMIN REVIEW --- Handler for Approve/Reject buttons within the review menu
@dp.callback_query(StateFilter(AdminReview.reviewing), F.data.startswith(("admin_review_approve_", "admin_review_reject_")))
async def admin_process_review_action(callback_query: types.CallbackQuery, state: FSMContext):
    action, conf_id_str = callback_query.data.replace("admin_review_", "").split("_", 1)
    conf_id = int(conf_id_str)

    # This re-uses the original admin_action logic for single notifications.
    # We simply change the callback_data to match what that handler expects.
    callback_query.data = f"{action}_{conf_id}"
    await admin_action(callback_query, state)
    
    # After the action, update the review state and show the next item.
    data = await state.get_data()
    pending_ids = data.get("pending_ids", [])
    
    if conf_id in pending_ids:
        # Find the index of the item we just processed
        processed_index = pending_ids.index(conf_id)
        # Remove it from the list
        pending_ids.pop(processed_index)
        
        # Adjust the current index if needed. If we removed an item at or before
        # the current index, the new "current" item is now at a lower index.
        current_index = data.get("current_index", 0)
        if processed_index < current_index:
            current_index -= 1
        
        # Make sure the index is not out of bounds
        if current_index >= len(pending_ids) and len(pending_ids) > 0:
            current_index = len(pending_ids) - 1

        await state.update_data(pending_ids=pending_ids, current_index=current_index)

    # Delete the old message and display the new/next one
    await callback_query.message.delete()
    await display_current_review_confession(callback_query.message, state, edit_message=False)


# --- Admin Action Handlers ---
@dp.callback_query(F.data.startswith(("approve_", "reject_")))
async def admin_action(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id != ADMIN_ID: await callback_query.answer("Unauthorized.", show_alert=True); return
    action, conf_id_str = callback_query.data.split("_", 1); conf_id = int(conf_id_str)
    
    async with db.acquire() as conn:
        conf = await conn.fetchrow("SELECT id, text, user_id, categories, status, message_id FROM confessions WHERE id = $1", conf_id)
        if not conf: await callback_query.answer("Confession not found.", show_alert=True); return
        if conf['status'] != 'pending': await callback_query.answer(f"Already '{conf['status']}'.", show_alert=True); return

        if action == "approve":
            try:
                link = f"https://t.me/{bot_info.username}?start=view_{conf['id']}"
                category_tags = " ".join([f"#{html.quote(cat)}" for cat in conf['categories'] or []])
                channel_post_text = f"<b>Confession #{conf['id']}</b>\n\n{html.quote(conf['text'])}\n\n{category_tags}"
                channel_kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=link)]])
                msg = await bot.send_message(CHANNEL_ID, channel_post_text, reply_markup=channel_kbd)
                await conn.execute("UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2", msg.message_id, conf_id)
                await safe_send_message(conf['user_id'], f"✅ Your confession (#{conf_id}) has been approved!")
                
                # If not in review mode, edit the single notification message
                current_fsm_state = await state.get_state()
                if current_fsm_state != AdminReview.reviewing:
                    await callback_query.message.edit_text(callback_query.message.html_text + "\n\n-- Approved --", reply_markup=None)
                
                await callback_query.answer(f"Confession #{conf_id} approved.")
            except Exception as e: logging.error(f"Error approving Confession {conf_id}: {e}", exc_info=True); await callback_query.answer(f"Error: {e}", show_alert=True)
        elif action == "reject":
            await state.update_data(
                rejecting_conf_id=conf_id,
                original_admin_text=callback_query.message.html_text,
                admin_review_message_id=callback_query.message.message_id
            )
            await state.set_state(AdminActions.waiting_for_rejection_reason)
            reason_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/skip")], [KeyboardButton(text="/cancel")]], resize_keyboard=True, one_time_keyboard=True)
            await callback_query.answer("❓ Provide rejection reason")
            await bot.send_message(callback_query.from_user.id, f"Reason for rejecting Confession #{conf_id}?\nUse /skip or /cancel.", reply_markup=reason_keyboard)

@dp.message(AdminActions.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(message: types.Message, state: FSMContext):
    data = await state.get_data()
    conf_id = data.get("rejecting_conf_id")
    original_admin_text = data.get("original_admin_text")
    admin_review_message_id = data.get("admin_review_message_id")
    keyboard = get_main_keyboard(message.from_user.id)

    if not conf_id: await message.answer("Error: Context lost."); await state.clear(); return
    reason, reason_text_for_user = None, "Your confession was rejected."
    if message.text.startswith("/skip"): await message.answer("Skipping reason.", reply_markup=ReplyKeyboardRemove())
    elif message.text.startswith("/cancel"): await message.answer("Rejection cancelled.", reply_markup=keyboard); await state.clear(); return
    else: reason = message.text.strip(); reason_text_for_user = f"Your confession was rejected for the following reason:\n<i>{html.quote(reason)}</i>"
    
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT user_id, categories FROM confessions WHERE id = $1 AND status = 'pending'", conf_id)
        if not conf_data: await message.answer("Error: Confession no longer pending.", reply_markup=keyboard); await state.clear(); return
        await conn.execute("UPDATE confessions SET status = 'rejected', rejection_reason = $1 WHERE id = $2", reason, conf_id)
        category_tags = " ".join([f"#{html.quote(cat)}" for cat in conf_data['categories'] or []])
        await safe_send_message(conf_data['user_id'], f"❌ {reason_text_for_user}\n(Confession ID: #{conf_id}, Categories: {category_tags})")
        
        current_fsm_state = await state.get_state()
        if current_fsm_state != AdminReview.reviewing:
            try:
                await bot.edit_message_text(original_admin_text + f"\n\n-- Rejected --\nReason: {html.quote(reason or 'Skipped')}", chat_id=ADMIN_ID, message_id=admin_review_message_id, reply_markup=None)
            except Exception as e:
                logging.error(f"Could not edit admin review message {admin_review_message_id} for rejection: {e}")
            
        await message.answer(f"Confession #{conf_id} rejected.", reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith(("admin_approve_delete_", "admin_reject_delete_")))
async def admin_handle_deletion_request(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Unauthorized.", show_alert=True); return

    parts = callback_query.data.split("_")
    action = parts[1]
    conf_id = int(parts[-1])
    final_status = ""

    async with db.acquire() as conn:
        async with conn.transaction():
            req_data = await conn.fetchrow("SELECT id, user_id FROM deletion_requests WHERE confession_id = $1 AND status = 'pending'", conf_id)
            if not req_data:
                await callback_query.answer("Request not found or already processed.", show_alert=True); return

            if action == "approve":
                conf_to_delete = await conn.fetchrow("SELECT message_id, user_id FROM confessions WHERE id = $1", conf_id)
                if conf_to_delete:
                    await conn.execute("DELETE FROM confessions WHERE id = $1", conf_id)
                    try:
                        if conf_to_delete['message_id']:
                            await bot.delete_message(chat_id=CHANNEL_ID, message_id=conf_to_delete['message_id'])
                    except Exception as e:
                        logging.warning(f"Could not delete channel message {conf_to_delete.get('message_id')} for deleted conf {conf_id}: {e}")
                await conn.execute("UPDATE deletion_requests SET status = 'approved', reviewed_at = CURRENT_TIMESTAMP WHERE id = $1", req_data['id'])
                await safe_send_message(req_data['user_id'], f"✅ Your request to delete Confession #{conf_id} has been approved. It has been permanently removed.")
                final_status = "Approved & Deleted"
            else: # Reject
                await conn.execute("UPDATE deletion_requests SET status = 'rejected', reviewed_at = CURRENT_TIMESTAMP WHERE id = $1", req_data['id'])
                await safe_send_message(req_data['user_id'], f"❌ Your request to delete Confession #{conf_id} was rejected by the admin.")
                final_status = "Rejected"

    await callback_query.message.edit_text(callback_query.message.html_text + f"\n\n-- Deletion Request: {final_status} --", reply_markup=None)
    await callback_query.answer(f"Request {final_status}.")


@dp.message(Command("warn"))
async def admin_warn_user(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    
    if not command.args:
        await message.reply("Usage: /warn &lt;user_id&gt; &lt;reason&gt;"); return
    
    parts = command.args.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("Usage: /warn &lt;user_id&gt; &lt;reason&gt;"); return

    try:
        target_user_id = int(parts[0])
        reason = parts[1]
    except ValueError:
        await message.reply("Invalid User ID."); return
    
    warning_text = f"⚠️ <b>You have received a warning from the admin.</b>\n\n<b>Reason:</b> <i>{html.quote(reason)}</i>\n\nPlease adhere to the bot's rules to avoid further action."
    
    sent = await safe_send_message(target_user_id, warning_text)
    if sent:
        await message.reply(f"✅ Warning sent to User ID <code>{target_user_id}</code>.")
    else:
        await message.reply(f"⚠️ Failed to send warning to User ID <code>{target_user_id}</code>. They may have blocked the bot.")

async def apply_block(message: types.Message, user_id: int, reason: Optional[str], is_permanent: bool, duration_str: Optional[str] = None):
    blocked_until = None
    if not is_permanent:
        if not duration_str: return await message.reply("Duration is required for temporary blocks.")
        try:
            val = int(duration_str[:-1])
            unit = duration_str[-1].lower()
            if unit == 'd': blocked_until = datetime.now(timezone.utc) + timedelta(days=val)
            elif unit == 'w': blocked_until = datetime.now(timezone.utc) + timedelta(weeks=val)
            else: raise ValueError("Invalid time unit")
        except (ValueError, IndexError):
            return await message.reply("Invalid duration format. Use 'd' for days or 'w' for weeks (e.g., 7d, 2w).")

    async with db.acquire() as conn:
        await conn.execute("""
            INSERT INTO user_status (user_id, is_blocked, blocked_until, block_reason) 
            VALUES ($1, TRUE, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET
            is_blocked = TRUE, blocked_until = $2, block_reason = $3
        """, user_id, blocked_until, reason)
    
    expiry_info = "permanently" if is_permanent else f"until {blocked_until.strftime('%Y-%m-%d %H:%M %Z')}"
    reason_info = f"\nReason: <i>{html.quote(reason)}</i>" if reason else ""
    notification_text = f"❌ <b>You have been blocked from using this bot {expiry_info}.</b>{reason_info}"
    
    await safe_send_message(user_id, notification_text)
    await message.reply(f"✅ User ID <code>{user_id}</code> has been blocked {expiry_info}.")

@dp.message(Command("block"))
async def admin_block_user(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.reply("Usage: /block &lt;user_id&gt; &lt;duration&gt; [reason]")
    
    parts = command.args.split(maxsplit=2)
    if len(parts) < 2: return await message.reply("Usage: /block &lt;user_id&gt; &lt;duration&gt; [reason]")
    
    try: target_user_id = int(parts[0])
    except ValueError: return await message.reply("Invalid User ID.")
    
    duration = parts[1]
    reason = parts[2] if len(parts) > 2 else None
    await apply_block(message, target_user_id, reason, is_permanent=False, duration_str=duration)

@dp.message(Command("pblock"))
async def admin_pblock_user(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.reply("Usage: /pblock &lt;user_id&gt; [reason]")
    
    parts = command.args.split(maxsplit=1)
    try: target_user_id = int(parts[0])
    except ValueError: return await message.reply("Invalid User ID.")
    
    reason = parts[1] if len(parts) > 1 else None
    await apply_block(message, target_user_id, reason, is_permanent=True)

@dp.message(Command("unblock"))
async def admin_unblock_user(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    if not command.args: return await message.reply("Usage: /unblock &lt;user_id&gt;")
    
    try: target_user_id = int(command.args.strip())
    except ValueError: return await message.reply("Invalid User ID.")
    
    async with db.acquire() as conn:
        result = await conn.execute("""
            UPDATE user_status SET is_blocked = FALSE, blocked_until = NULL, block_reason = NULL 
            WHERE user_id = $1 AND is_blocked = TRUE
        """, target_user_id)
    
    if result == "UPDATE 1":
        await safe_send_message(target_user_id, "✅ You have been unblocked by the admin and can now use the bot again.")
        await message.reply(f"✅ User ID <code>{target_user_id}</code> has been unblocked.")
    else:
        await message.reply(f"ℹ️ User ID <code>{target_user_id}</code> was not blocked.")


# --- Commenting Flow Handlers ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    conf_id = int(callback_query.data.split("_", 1)[1])
    await callback_query.answer("Loading comments...")
    await show_comments_for_confession(callback_query.from_user.id, conf_id, callback_query.message)

@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    conf_id = int(callback_query.data.split("_", 1)[1])
    await state.update_data(confession_id=conf_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    await safe_send_message(
        callback_query.from_user.id,
        f"📝 Please send your comment as text, a sticker, or a GIF.",
        reply_markup=cancel_keyboard
    )
    await callback_query.answer()

@dp.callback_query(F.data.startswith("comments_page_"))
async def comments_page_callback(callback_query: types.CallbackQuery):
    _, _, conf_id, page = callback_query.data.split("_"); conf_id, page = int(conf_id), int(page)
    await callback_query.answer("Loading page...")
    await show_comments_for_confession(callback_query.from_user.id, conf_id, callback_query.message, page=page)

@dp.message(CommentForm.waiting_for_comment, (F.text | F.sticker | F.animation))
async def receive_comment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id; data = await state.get_data(); conf_id = data.get("confession_id")
    keyboard = get_main_keyboard(user_id)
    if not conf_id: await message.answer("⚠️ Error: Context lost. Please try again."); return
    comm_text, sticker_id, animation_id, log_type = None, None, None, "Unknown"
    if message.text: comm_text, log_type = message.text.strip(), "Text"
    elif message.sticker: sticker_id, log_type = message.sticker.file_id, "Sticker"
    elif message.animation: animation_id, log_type = message.animation.file_id, "GIF"
    else: await message.answer("Invalid content. Please send text, sticker, or GIF."); return
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
                if not conf_owner_id: raise Exception("Confession not found or approved.")
                new_comm_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id) VALUES ($1, $2, $3, $4, $5) RETURNING id", conf_id, user_id, comm_text, sticker_id, animation_id)
        await message.answer("💬 Your comment has been added!", reply_markup=keyboard);
        await update_channel_post_button(conf_id)
        if conf_owner_id and conf_owner_id != user_id:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
            preview = html.quote(comm_text[:150]) if comm_text else f"[{log_type}]"
            await safe_send_message(conf_owner_id, f"💬 A new comment has been posted on your Confession #{conf_id}.\n\n<i>{preview}...</i>\n\n<a href='{link}'>Click here to view.</a>", disable_web_page_preview=True)
        await show_comments_for_confession(user_id, conf_id)
    except Exception as e:
        logging.error(f"Error saving {log_type} comment for Conf {conf_id} by {user_id}: {e}", exc_info=True)
        await message.answer("❌ Error saving comment. The confession may have been removed.")
    finally: await state.clear()


@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    parent_id = int(callback_query.data.split("_", 1)[1])
    async with db.acquire() as conn:
        comm_data = await conn.fetchrow("SELECT confession_id, text, sticker_file_id, animation_file_id, user_id FROM comments WHERE id = $1", parent_id)
    if not comm_data: await callback_query.answer("Comment no longer exists.", show_alert=True); return
    if callback_query.from_user.id == comm_data['user_id']: await callback_query.answer("You cannot reply to yourself.", show_alert=True); return

    try:
        reply_preview_message = "<i>Replying to:</i>\n\n"
        if comm_data['text']:
            reply_preview_message += html.quote(comm_data['text'])
        elif comm_data['sticker_file_id']:
            reply_preview_message += "<i>[Sticker]</i>"
        elif comm_data['animation_file_id']:
            reply_preview_message += "<i>[GIF]</i>"

        await safe_send_message(
            callback_query.from_user.id,
            reply_preview_message,
            disable_web_page_preview=True
        )
        
        await bot.send_message(
            callback_query.from_user.id,
            "⬆️ Please send your reply now (text, sticker, or GIF).",
            reply_markup=cancel_keyboard
        )
        
        await state.update_data(
            confession_id=comm_data['confession_id'],
            parent_comment_id=parent_id
        )
        await state.set_state(CommentForm.waiting_for_reply)
        await callback_query.answer()
        
    except Exception as e:
        logging.error(f"Error starting reply prompt for parent comment {parent_id}: {e}", exc_info=True)
        await callback_query.answer("Could not start reply process.", show_alert=True)
        await state.clear()

@dp.message(CommentForm.waiting_for_reply, (F.text | F.sticker | F.animation))
async def receive_reply(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id, parent_id = data.get("confession_id"), data.get("parent_comment_id")
    keyboard = get_main_keyboard(user_id)

    if not all([conf_id, parent_id]):
        await message.answer("⚠️ Error: Reply context lost. Please try again.", reply_markup=keyboard)
        await state.clear()
        return

    reply_text, sticker_id, animation_id, log_type = None, None, None, "Unknown"
    if message.text: reply_text, log_type = message.text.strip(), "Text Reply"
    elif message.sticker: sticker_id, log_type = message.sticker.file_id, "Sticker Reply"
    elif message.animation: animation_id, log_type = message.animation.file_id, "GIF Reply"
    else: await message.answer("Invalid content type for a reply."); return
    
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1", parent_id)
                if not parent_data: await message.answer("⚠️ The comment you were replying to has been deleted."); await state.clear(); return
                conf_data = await conn.fetchrow("SELECT user_id FROM confessions WHERE id = $1", conf_id)
                
                await conn.execute(
                    "INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id) VALUES ($1, $2, $3, $4, $5, $6)",
                    conf_id, user_id, reply_text, sticker_id, animation_id, parent_id
                )

        await message.answer("↪️ Your reply has been sent!", reply_markup=keyboard)
        await update_channel_post_button(conf_id)
        
        if parent_data['user_id'] != user_id:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
            preview = html.quote(reply_text[:150]) if reply_text else f"[{log_type.replace(' Reply', '')}]"
            
            async with db.acquire() as conn:
                user_settings = await conn.fetchrow("SELECT nickname, profile_emoji FROM user_status WHERE user_id = $1", user_id)
            
            nickname = user_settings.get('nickname') if user_settings else 'Anonymous'
            profile_emoji = user_settings.get('profile_emoji') if user_settings else '👤'
            tag = f"{profile_emoji} (Author)" if user_id == conf_data['user_id'] else f"{profile_emoji} {nickname}"

            await safe_send_message(parent_data['user_id'], f"↪️ Someone ({tag}) replied to your comment on Confession #{conf_id}.\n\n<i>{preview}...</i>\n\n<a href='{link}'>Click here to view the reply.</a>", disable_web_page_preview=True)
        
        async with db.acquire() as conn:
             total_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", conf_id) or 0
             user_page_size = await conn.fetchval("SELECT comments_per_page FROM user_status WHERE user_id = $1", user_id)
             page_size_to_use = user_page_size if user_page_size is not None and user_page_size > 0 else PAGE_SIZE
             last_page = (total_count + page_size_to_use - 1) // page_size_to_use if page_size_to_use > 0 else 1

        await show_comments_for_confession(user_id, conf_id, page=last_page)

    except Exception as e:
        logging.error(f"Error saving reply for parent {parent_id} by {user_id}: {e}", exc_info=True)
        await message.answer("❌ Error saving reply.")
    finally:
        await state.clear()


# --- Reaction Handling ---
@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    _, r_type, comm_id = callback_query.data.split("_"); comm_id = int(comm_id); user_id = callback_query.from_user.id
    point_delta, alert = 0, ""
    async with db.acquire() as conn:
        async with conn.transaction():
            info = await conn.fetchrow("SELECT c.user_id as comm_uid, co.user_id as conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comm_id)
            if not info: await callback_query.answer("Comment not found.", show_alert=True); return
            if info['comm_uid'] == user_id: await callback_query.answer("You cannot react to your own comment.", show_alert=True); return
            existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id)
            if existing:
                if existing == r_type: # Remove
                    await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id)
                    point_delta = -POINTS_PER_LIKE_RECEIVED if r_type == 'like' else -POINTS_PER_DISLIKE_RECEIVED
                    alert = f"{r_type.capitalize()} removed"
                else: # Change
                    await conn.execute("UPDATE reactions SET reaction_type = $1 WHERE comment_id = $2 AND user_id = $3", r_type, comm_id, user_id)
                    point_delta = 2 * POINTS_PER_LIKE_RECEIVED if r_type == 'like' else 2 * POINTS_PER_DISLIKE_RECEIVED
                    alert = f"Reaction changed to {r_type}"
            else: # Add new
                await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, user_id, r_type)
                point_delta = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED
                alert = f"{r_type.capitalize()} added"
            if point_delta != 0: await update_user_points(conn, info['comm_uid'], point_delta)
    kbd = await build_comment_keyboard(comm_id, info['comm_uid'], user_id, info['conf_owner_id'])
    try: await callback_query.message.edit_reply_markup(reply_markup=kbd); await callback_query.answer(alert)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower(): logging.warning(f"Could not edit markup for react on comment {comm_id}: {e}")
        else: await callback_query.answer(alert)

# --- Report Comment Handlers ---
@dp.callback_query(F.data.startswith("report_confirm_"))
async def report_confirm_callback(callback_query: types.CallbackQuery):
    comment_id = int(callback_query.data.split("_")[-1]); reporter_user_id = callback_query.from_user.id
    async with db.acquire() as conn:
        comment_data = await conn.fetchrow("SELECT text, user_id FROM comments WHERE id = $1", comment_id)
        if not comment_data: await callback_query.answer("Comment deleted.", show_alert=True); return
        if comment_data['user_id'] == reporter_user_id: await callback_query.answer("You cannot report yourself.", show_alert=True); return
    snippet = html.quote(comment_data['text'][:100]) if comment_data['text'] else "[Sticker/GIF]"
    confirm_text = f"Are you sure you want to report this comment for admin review?\n\n<i>\"{snippet}...\"</i>"
    kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yes, Report", callback_data=f"report_execute_{comment_id}"), InlineKeyboardButton(text="❌ No, Cancel", callback_data="report_cancel")]])
    await safe_send_message(reporter_user_id, confirm_text, reply_markup=kbd); await callback_query.answer()

@dp.callback_query(F.data.startswith("report_execute_"))
async def report_execute_callback(callback_query: types.CallbackQuery):
    comment_id = int(callback_query.data.split("_")[-1]); reporter_user_id = callback_query.from_user.id
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                comment_data = await conn.fetchrow("SELECT user_id, confession_id, text, sticker_file_id, animation_file_id FROM comments WHERE id = $1", comment_id)
                if not comment_data: await callback_query.message.edit_text("Report failed: Comment no longer exists."); return
                reported_user_id = comment_data['user_id']
                if reported_user_id == reporter_user_id: await callback_query.message.edit_text("Action cancelled: Cannot report own comment."); return
                await conn.execute("INSERT INTO reports (comment_id, reporter_user_id, reported_user_id) VALUES ($1, $2, $3) ON CONFLICT (comment_id, reporter_user_id) DO NOTHING", comment_id, reporter_user_id, reported_user_id)
        snippet = html.quote(comment_data['text'][:200]) if comment_data['text'] else f"[Sticker/GIF: <code>{comment_data.get('sticker_file_id') or comment_data.get('animation_file_id')}</code>]"
        conf_link = f"https://t.me/{bot_info.username}?start=view_{comment_data['confession_id']}"
        admin_notification = (f"⚠️ <b>New Comment Report</b> ⚠️\n\n<b>Confession:</b> <a href='{conf_link}'>#{comment_data['confession_id']}</a>\n"
                              f"<b>Comment ID:</b> <code>{comment_id}</code>\n<b>Content:</b>\n<i>{snippet}</i>\n\n"
                              f"<b>Reported User:</b> <code>{reported_user_id}</code>\n<b>Reporter:</b> <code>{reporter_user_id}</code>")
        await safe_send_message(ADMIN_ID, admin_notification, disable_web_page_preview=True)
        await callback_query.message.edit_text("✅ Your report has been submitted. The admin has been notified.", reply_markup=None)
        await callback_query.answer("Report sent.")
    except Exception as e:
        logging.error(f"Error executing report for comment {comment_id} by {reporter_user_id}: {e}")
        await callback_query.message.edit_text("❌ An error occurred while reporting."); await callback_query.answer("Error.", show_alert=True)

@dp.callback_query(F.data == "report_cancel")
async def report_cancel_callback(callback_query: types.CallbackQuery):
    await callback_query.message.edit_text("Report process cancelled."); await callback_query.answer("Cancelled.")


# --- Rebuilt Contact Request Flow ---
@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    comm_id = int(callback_query.data.split("_")[-1]); requester_uid = callback_query.from_user.id
    async with db.acquire() as conn:
        async with conn.transaction():
            comm_data = await conn.fetchrow("SELECT c.user_id as comm_uid, c.text, c.sticker_file_id, c.animation_file_id, co.id as conf_id, co.user_id as conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comm_id)
            if not comm_data: await callback_query.answer("Comment or confession not found.", show_alert=True); return
            commenter_uid, conf_id, conf_owner_id = comm_data['comm_uid'], comm_data['conf_id'], comm_data['conf_owner_id']
            if requester_uid != conf_owner_id: await callback_query.answer("Only the confession author can do this.", show_alert=True); return
            if requester_uid == commenter_uid: await callback_query.answer("You cannot contact yourself.", show_alert=True); return
            
            existing_req = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2", comm_id, requester_uid)
            if existing_req and existing_req != 'denied':
                await callback_query.answer(f"A contact request already exists (status: {existing_req}).", show_alert=True); return

            req_id = await conn.fetchval("""
                INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) VALUES ($1, $2, $3, $4, 'pending')
                ON CONFLICT (comment_id, requester_user_id) DO UPDATE SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """, conf_id, comm_id, requester_uid, commenter_uid)

            snippet = html.quote(comm_data['text'][:100]) if comm_data['text'] else "[Sticker/GIF]"
            notification_to_commenter = (f"🤝 The author of Confession #{conf_id} would like to contact you regarding your comment:\n\n"
                                        f"<i>\"{snippet}...\"</i>\n\n"
                                        "Do you approve sharing your Telegram @username with them? Your User ID is never shared.")
            kbd = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Approve & Share Username", callback_data=f"approve_contact_{req_id}")],
                [InlineKeyboardButton(text="❌ Deny Request", callback_data=f"deny_contact_{req_id}")]
            ])
            sent = await safe_send_message(commenter_uid, notification_to_commenter, reply_markup=kbd)
            if sent:
                await callback_query.answer("✅ Contact request sent to the commenter.", show_alert=False)
            else:
                await conn.execute("UPDATE contact_requests SET status = 'failed_to_notify' WHERE id = $1", req_id)
                await callback_query.answer("⚠️ Could not notify the commenter (they may have blocked the bot).", show_alert=True)

@dp.callback_query(F.data.startswith(("approve_contact_", "deny_contact_")))
async def handle_contact_response(callback_query: types.CallbackQuery):
    action, _, req_id_str = callback_query.data.partition("_contact_"); req_id = int(req_id_str); responder_uid = callback_query.from_user.id
    async with db.acquire() as conn:
        async with conn.transaction():
            req_data = await conn.fetchrow("SELECT * FROM contact_requests WHERE id = $1", req_id)
            if not req_data:
                await callback_query.answer("This request could not be found. It may have expired.", show_alert=True); return
            if responder_uid != req_data['requested_user_id']:
                logging.warning(f"Unauthorized contact response attempt. User {responder_uid} tried to respond to request {req_id} intended for {req_data['requested_user_id']}.")
                await callback_query.answer("This request is not for you.", show_alert=True); return
            if req_data['status'] != 'pending':
                await callback_query.answer(f"This request has already been handled (status: {req_data['status']}).", show_alert=True); return

            author_uid = req_data['requester_user_id']; conf_id = req_data['confession_id']
            notification_to_author = ""
            if action == "approve":
                try:
                    responder_info = await bot.get_chat(responder_uid)
                    username = responder_info.username
                    if username:
                        await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                        notification_to_author = f"✅ Contact Approved for Confession #{conf_id}!\nYou can contact the commenter at: @{html.quote(username)}"
                        await callback_query.message.edit_text(callback_query.message.html_text + "\n\n-- Approved. Your username has been shared. --", reply_markup=None)
                    else:
                        await conn.execute("UPDATE contact_requests SET status = 'approved_no_username', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                        notification_to_author = f"⚠️ Contact Approved for Confession #{conf_id}, but the commenter has no public @username."
                        await callback_query.message.edit_text(callback_query.message.html_text + "\n\n-- Approved, but you have no public username to share. --", reply_markup=None)
                except Exception as e:
                    logging.error(f"Failed to get chat for user {responder_uid} on contact approve: {e}")
                    await callback_query.answer("An error occurred while fetching your info.", show_alert=True); return
            else: # Deny
                await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                notification_to_author = f"❌ The commenter for Confession #{conf_id} has declined your contact request."
                await callback_query.message.edit_text(callback_query.message.html_text + "\n\n-- Denied. The author has been notified. --", reply_markup=None)
            
            await safe_send_message(author_uid, notification_to_author)
            await callback_query.answer("Response recorded.")

# --- Public Profile and User-to-User Chat Handlers ---
@dp.callback_query(F.data.startswith("report_user_"))
async def report_user_callback(callback_query: types.CallbackQuery):
    reported_user_id = int(callback_query.data.split("_")[-1])
    reporter_user_id = callback_query.from_user.id

    if reported_user_id == reporter_user_id:
        await callback_query.answer("You cannot report yourself.", show_alert=True); return

    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO reports (reporter_user_id, reported_user_id) VALUES ($1, $2)",
            reporter_user_id, reported_user_id
        )

    admin_notification = (f"⚠️ <b>New User Report</b> ⚠️\n\n"
                          f"<b>Reported User ID:</b> <code>{reported_user_id}</code>\n"
                          f"<b>Reporter User ID:</b> <code>{reporter_user_id}</code>")
    await safe_send_message(ADMIN_ID, admin_notification)
    await callback_query.answer("✅ User reported to the admin. Thank you.", show_alert=True)


@dp.callback_query(F.data.startswith("request_chat_"))
async def request_chat_callback(callback_query: types.CallbackQuery):
    recipient_id = int(callback_query.data.split("_")[-1])
    requester_id = callback_query.from_user.id

    async with db.acquire() as conn:
        requester_settings = await conn.fetchrow("SELECT nickname, profile_emoji FROM user_status WHERE user_id = $1", requester_id)

        req_id = await conn.fetchval("""
            INSERT INTO chat_requests (requester_id, recipient_id, status) VALUES ($1, $2, 'pending')
            ON CONFLICT (requester_id, recipient_id) DO UPDATE SET status = 'pending', updated_at = CURRENT_TIMESTAMP
            RETURNING id
        """, requester_id, recipient_id)

    requester_nickname = html.quote(requester_settings.get('nickname') or "An anonymous user") if requester_settings else "An anonymous user"
    requester_emoji = requester_settings.get('profile_emoji') if requester_settings else '👤'

    notification_text = f"💬 {requester_emoji} <b>{requester_nickname}</b> would like to start a chat with you."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Accept", callback_data=f"accept_chat_{req_id}")],
        [InlineKeyboardButton(text="❌ Decline", callback_data=f"decline_chat_{req_id}")],
    ])

    sent = await safe_send_message(recipient_id, notification_text, reply_markup=keyboard)
    if sent:
        await callback_query.answer("✅ Chat request sent!", show_alert=False)
        await show_public_profile(requester_id, recipient_id)
    else:
        await callback_query.answer("⚠️ Could not send request. User may have blocked the bot.", show_alert=True)


@dp.callback_query(F.data.startswith(("accept_chat_", "decline_chat_")))
async def handle_chat_response(callback_query: types.CallbackQuery):
    parts = callback_query.data.split("_")
    action = parts[0]
    req_id = int(parts[2])
    responder_id = callback_query.from_user.id

    async with db.acquire() as conn:
        req_data = await conn.fetchrow("SELECT * FROM chat_requests WHERE id = $1", req_id)
        if not req_data or req_data['recipient_id'] != responder_id or req_data['status'] != 'pending':
            await callback_query.answer("This request is invalid or has expired.", show_alert=True); return

        requester_id = req_data['requester_id']
        new_status = 'accepted' if action == 'accept' else 'declined'
        await conn.execute("UPDATE chat_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", new_status, req_id)

        responder_nickname_row = await conn.fetchrow("SELECT nickname FROM user_status WHERE user_id = $1", responder_id)
        responder_nickname = html.quote(responder_nickname_row['nickname'] or "The user") if responder_nickname_row else "The user"
        
        if new_status == 'accepted':
            await safe_send_message(requester_id, f"✅ <b>{responder_nickname}</b> has accepted your chat request! You can now send messages from their profile.")
            await callback_query.message.edit_text("✅ Chat request accepted. You can now chat with this user.")
        else:
            await safe_send_message(requester_id, f"❌ <b>{responder_nickname}</b> has declined your chat request.")
            await callback_query.message.edit_text("❌ You have declined the chat request.")

    await callback_query.answer()


@dp.callback_query(F.data.startswith("start_chat_"))
async def start_chat_callback(callback_query: types.CallbackQuery, state: FSMContext):
    recipient_id = int(callback_query.data.split("_")[-1])
    await state.set_state(ChatState.in_chat)
    await state.update_data(chat_partner_id=recipient_id)

    async with db.acquire() as conn:
        partner_nickname_row = await conn.fetchrow("SELECT nickname FROM user_status WHERE user_id = $1", recipient_id)
        partner_nickname = html.quote(partner_nickname_row['nickname'] or "Anonymous") if partner_nickname_row else "Anonymous"
    
    chat_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/leavechat")]], resize_keyboard=True)
    await callback_query.message.answer(
        f"You are now in a chat with <b>{partner_nickname}</b>. "
        "Any message you send here will be forwarded to them. Use /leavechat to exit.",
        reply_markup=chat_keyboard
    )
    await callback_query.answer()


@dp.message(Command("leavechat"), StateFilter(ChatState.in_chat))
async def leave_chat_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    partner_id = data.get("chat_partner_id")
    keyboard = get_main_keyboard(user_id)
    
    await state.clear()
    await message.answer("You have left the chat.", reply_markup=keyboard)
    
    if partner_id:
        partner_key = StorageKey(bot_id=bot.id, chat_id=partner_id, user_id=partner_id)
        partner_context = FSMContext(storage=dp.storage, key=partner_key)
        await partner_context.clear()
        await safe_send_message(partner_id, "ℹ️ The other user has left the chat. The session has ended.", reply_markup=get_main_keyboard(partner_id))

@dp.message(ChatState.in_chat)
async def forward_chat_message(message: types.Message, state: FSMContext):
    sender_id = message.from_user.id
    data = await state.get_data()
    recipient_id = data.get("chat_partner_id")
    keyboard = get_main_keyboard(sender_id)
    
    if not recipient_id:
        await state.clear()
        await message.answer("Chat session expired. Please start again.", reply_markup=keyboard)
        return

    recipient_key = StorageKey(bot_id=bot.id, chat_id=recipient_id, user_id=recipient_id)
    recipient_context = FSMContext(storage=dp.storage, key=recipient_key)
    recipient_state = await recipient_context.get_state()

    if recipient_state != ChatState.in_chat:
        await recipient_context.set_state(ChatState.in_chat)
        await recipient_context.update_data(chat_partner_id=sender_id)
        chat_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/leavechat")]], resize_keyboard=True)
        await safe_send_message(recipient_id, "You have received a message. You are now in a chat. Send messages here to reply. Use /leavechat to exit.", reply_markup=chat_keyboard)

    async with db.acquire() as conn:
        sender_nickname_row = await conn.fetchrow("SELECT nickname FROM user_status WHERE user_id = $1", sender_id)
        sender_nickname = html.quote(sender_nickname_row['nickname'] or "Anonymous") if sender_nickname_row else "Anonymous"

    prefix = f"💬 <b>Message from {sender_nickname}:</b>\n\n"
    
    if message.text:
        await safe_send_message(recipient_id, prefix + html.quote(message.text))
    elif message.sticker:
        await safe_send_message(recipient_id, prefix)
        await bot.send_sticker(recipient_id, message.sticker.file_id)
    elif message.animation:
        await safe_send_message(recipient_id, prefix)
        await bot.send_animation(recipient_id, message.animation.file_id)
    
    try:
      await message.react([types.ReactionTypeEmoji(emoji="✅")])
    except TelegramBadRequest:
      pass


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply(
        "Hi! 👋 Use the buttons below to navigate the bot.",
        reply_markup=get_main_keyboard(message.from_user.id)
    )

# --- Main Execution ---
async def main():
    try:
        await setup()
        if not db or not bot_info:
            logging.critical("FATAL: Database or bot info missing after setup. Cannot start.")
            return

        dp.message.middleware(BlockUserMiddleware())
        dp.callback_query.middleware(BlockUserMiddleware())

        commands = [
            types.BotCommand(command="start", description="Start/View confession"),
            types.BotCommand(command="confess", description="Submit anonymous confession"),
            types.BotCommand(command="profile", description="View your profile and history"),
            types.BotCommand(command="help", description="Show help and commands"),
            types.BotCommand(command="rules", description="View the bot's rules"),
            types.BotCommand(command="privacy", description="View privacy information"),
            types.BotCommand(command="cancel", description="Cancel current action"),
        ]
        admin_commands = commands + [
            types.BotCommand(command="id", description="ADMIN: Get user info"),
            types.BotCommand(command="warn", description="ADMIN: Warn a user"),
            types.BotCommand(command="block", description="ADMIN: Temporarily block a user"),
            types.BotCommand(command="pblock", description="ADMIN: Permanently block a user"),
            types.BotCommand(command="unblock", description="ADMIN: Unblock a user"),
        ]
        await bot.set_my_commands(commands)
        await bot.set_my_commands(admin_commands, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))

        tasks = [asyncio.create_task(dp.start_polling(bot, skip_updates=True))]
        if HTTP_PORT_STR:
            tasks.append(asyncio.create_task(start_dummy_server()))
        
        logging.info("Starting bot...")
        await asyncio.gather(*tasks)

    except Exception as e:
        logging.critical(f"Fatal error during main execution: {e}", exc_info=True)
    finally:
        logging.info("Shutting down...")
        if bot and bot.session:
            await bot.session.close()
        if db:
            await db.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")