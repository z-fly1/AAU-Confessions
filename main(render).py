import logging
import asyncpg
import os
import asyncio
from aiogram import Bot, Dispatcher, types, F, html
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
    KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional, Tuple, Dict, Any, List # Added List

# --- Dummy HTTP Server Imports ---
from aiohttp import web
# --- End Dummy HTTP Server Imports ---


# --- Constants ---
# --- *** MODIFIED: Updated Categories *** ---
CATEGORIES = [
    "Relationship", "Education", "Family", "School", "Friendship",
    "Religion", "Mental", "Addiction", "Harassment", "Crush",
    "Exams", "Dorm", "Health", "Trauma", "Sexual Assault",
    "Other"
]
POINTS_PER_CONFESSION = 1
POINTS_PER_LIKE_RECEIVED = 3
POINTS_PER_DISLIKE_RECEIVED = -3 # Note: This is negative
MAX_CATEGORIES = 3 # Maximum categories allowed per confession

# Load environment variables at the top level
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID") # Load as string first for validation
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")
RENDER_PORT = os.getenv("PORT") # For Render Web Service health checks

# Validate essential environment variables before proceeding
if not BOT_TOKEN: raise ValueError("FATAL: BOT_TOKEN environment variable not set!")
if not ADMIN_ID_STR: raise ValueError("FATAL: ADMIN_ID environment variable not set!")
if not CHANNEL_ID: raise ValueError("FATAL: CHANNEL_ID environment variable not set!")
if not DATABASE_URL: raise ValueError("FATAL: DATABASE_URL environment variable not set!")

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

# --- FSM States ---
class ConfessionForm(StatesGroup):
    # --- *** MODIFIED: States for multi-category selection *** ---
    selecting_categories = State() # Renamed for clarity
    waiting_for_text = State()

class CommentForm(StatesGroup):
    waiting_for_comment = State()
    waiting_for_reply = State()

class ContactAdminForm(StatesGroup): # State for contacting admin
    waiting_for_message = State()

class AdminActions(StatesGroup):
    waiting_for_rejection_reason = State()

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

    async with db.acquire() as conn:
        # --- *** MODIFIED: Confessions Table Schema *** ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending',
                message_id BIGINT,
                -- Removed old 'category' column
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                rejection_reason TEXT NULL,
                -- Added new 'categories' array column
                categories TEXT[] NULL -- Store multiple categories as a text array
            );
        """)
        logging.info("Checked/Created 'confessions' table.")
        await conn.execute("""
            ALTER TABLE confessions
            ADD COLUMN IF NOT EXISTS categories TEXT[];
        """)
        # --- Add GIN index for categories array ---
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_confessions_categories ON confessions USING gin(categories);")
        logging.info("Checked/Created GIN index on 'confessions.categories'.")
        await conn.execute("""
            DO $$ BEGIN
                -- Update existing columns if needed (example checks, adjust as necessary)
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE confessions ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE confessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='rejection_reason') THEN ALTER TABLE confessions ADD COLUMN rejection_reason TEXT NULL; END IF;
                -- Add 'categories' column if it doesn't exist (for smoother upgrades maybe)
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='categories') THEN ALTER TABLE confessions ADD COLUMN categories TEXT[] NULL; END IF;
                -- Attempt to remove old 'category' column if 'categories' exists
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='categories') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='category') THEN
                    -- Optional: Add data migration logic here if needed before dropping
                    -- Example: UPDATE confessions SET categories = ARRAY[category] WHERE categories IS NULL AND category IS NOT NULL;
                    ALTER TABLE confessions DROP COLUMN IF EXISTS category;
                    RAISE NOTICE 'Dropped old confessions.category column.';
                END IF;

                COMMENT ON COLUMN confessions.message_id IS 'Message ID of the post in the channel';
                COMMENT ON COLUMN confessions.rejection_reason IS 'Reason provided by admin upon rejection (optional)';
                COMMENT ON COLUMN confessions.categories IS 'Array of categories chosen by the user';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'confessions'.")

        # --- *** MODIFIED: Comments Table Schema *** ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                text TEXT NULL, -- Text is now nullable
                sticker_file_id TEXT NULL, -- To store sticker file_id
                animation_file_id TEXT NULL, -- To store GIF file_id
                parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                -- Ensure only one content type is set
                CONSTRAINT one_content_type CHECK (
                    num_nonnulls(text, sticker_file_id, animation_file_id) = 1
                )
            );
        """)
        logging.info("Checked/Created 'comments' table.")
        await conn.execute("""
             DO $$ BEGIN
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='parent_comment_id') THEN ALTER TABLE comments ADD COLUMN parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL; END IF;
                 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE comments ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE comments ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                 -- Add new content columns if they don't exist
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='sticker_file_id') THEN ALTER TABLE comments ADD COLUMN sticker_file_id TEXT NULL; END IF;
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='animation_file_id') THEN ALTER TABLE comments ADD COLUMN animation_file_id TEXT NULL; END IF;
                 -- Make text nullable if it's not already
                 ALTER TABLE comments ALTER COLUMN text DROP NOT NULL;
                 -- Add check constraint if it doesn't exist
                 IF NOT EXISTS (SELECT 1 FROM information_schema.constraint_column_usage WHERE table_name='comments' AND constraint_name='one_content_type') THEN
                     ALTER TABLE comments ADD CONSTRAINT one_content_type CHECK (num_nonnulls(text, sticker_file_id, animation_file_id) = 1);
                 END IF;

                 COMMENT ON COLUMN comments.parent_comment_id IS 'ID of the comment this is a reply to';
                 COMMENT ON COLUMN comments.sticker_file_id IS 'File ID of the sticker comment';
                 COMMENT ON COLUMN comments.animation_file_id IS 'File ID of the GIF (animation) comment';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'comments'.")

        # --- Create Reactions Table (Unchanged) ---
        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions ( id SERIAL PRIMARY KEY, comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL, reaction_type VARCHAR(10) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id) );
             COMMENT ON TABLE reactions IS 'Stores likes and dislikes for comments';
        """)
        logging.info("Checked/Created 'reactions' table.")

        # --- Create Contact Requests Table (Unchanged) ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests ( id SERIAL PRIMARY KEY, confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE, requester_user_id BIGINT NOT NULL, requested_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, requester_user_id) );
            COMMENT ON TABLE contact_requests IS 'Stores requests from confession authors to contact commenters.'; COMMENT ON COLUMN contact_requests.requester_user_id IS 'User ID of the confession author making the request.';
            COMMENT ON COLUMN contact_requests.requested_user_id IS 'User ID of the commenter being asked for contact.'; COMMENT ON COLUMN contact_requests.status IS 'pending, approved, denied, approved_no_username';
        """)
        logging.info("Checked/Created 'contact_requests' table.")

        # --- Create User Points Table (Unchanged) ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_points (
                user_id BIGINT PRIMARY KEY,
                points INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_user_points_user_id ON user_points(user_id);
            COMMENT ON TABLE user_points IS 'Stores medal points for each user.';
            COMMENT ON COLUMN user_points.points IS 'Total points accumulated by the user.';
        """)
        logging.info("Checked/Created 'user_points' table and index.")

        # --- Create Reports Table (Unchanged) ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
                reporter_user_id BIGINT NOT NULL,
                reported_user_id BIGINT NOT NULL, -- ID of the user who wrote the comment
                status VARCHAR(20) NOT NULL DEFAULT 'pending', -- e.g., pending, dismissed, action_taken
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, reporter_user_id) -- Prevent duplicate reports
            );
            CREATE INDEX IF NOT EXISTS idx_reports_comment_id ON reports(comment_id);
            CREATE INDEX IF NOT EXISTS idx_reports_reporter_user_id ON reports(reporter_user_id);
            CREATE INDEX IF NOT EXISTS idx_reports_reported_user_id ON reports(reported_user_id);
            COMMENT ON TABLE reports IS 'Stores user reports about comments.';
            COMMENT ON COLUMN reports.reported_user_id IS 'User ID of the person who wrote the reported comment.';
            COMMENT ON COLUMN reports.status IS 'Status of the report (pending, dismissed, action_taken).';
        """)
        logging.info("Checked/Created 'reports' table and indexes.")

        logging.info("Database tables setup complete.")

# --- Dummy HTTP Server for Render Health Checks ---
async def handle_health_check(request: web.Request):
    """Handles health check requests from Render."""
    logging.debug("Health check received")
    return web.Response(text="OK", status=200)

async def start_dummy_server():
    """Starts a minimal HTTP server to respond to Render health checks."""
    if not RENDER_PORT:
        logging.info("PORT environment variable not set by Render. Dummy HTTP server will not start.")
        return

    try:
        port = int(RENDER_PORT)
    except ValueError:
        logging.error(f"Invalid PORT value: '{RENDER_PORT}'. Dummy HTTP server will not start.")
        return

    app = web.Application()
    app.router.add_get('/', handle_health_check)  # Common health check path
    app.router.add_get('/healthz', handle_health_check) # Another common health check path

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port) # Listen on all available interfaces

    try:
        await site.start()
        logging.info(f"Dummy HTTP server started on 0.0.0.0:{port} for Render health checks.")
        # Keep the server running indefinitely, or until the task is cancelled
        while True:
            await asyncio.sleep(3600) # Sleep for an hour, or until cancelled
    except asyncio.CancelledError:
        logging.info("Dummy HTTP server task cancelled.")
    except Exception as e:
        logging.error(f"Dummy HTTP server failed to start or encountered an error on port {port}: {e}", exc_info=True)
    finally:
        await runner.cleanup()
        logging.info("Dummy HTTP server shut down.")
# --- End Dummy HTTP Server ---

# --- Helper Functions ---

# --- *** MODIFIED: Category Keyboard for Multi-Select *** ---
def create_category_keyboard(selected_categories: List[str] = None):
    """Creates the inline keyboard for category selection, marking selected ones."""
    if selected_categories is None:
        selected_categories = []
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        prefix = "✅ " if category in selected_categories else ""
        builder.button(text=f"{prefix}{category}", callback_data=f"category_{category}")

    builder.adjust(2) # Keep categories in rows of 2

    # Add "Done" button if at least one category is selected
    if 1 <= len(selected_categories) <= MAX_CATEGORIES:
         builder.row(InlineKeyboardButton(text=f"➡️ Done Selecting ({len(selected_categories)}/{MAX_CATEGORIES})", callback_data="category_done"))
    elif len(selected_categories) > MAX_CATEGORIES:
         # Indicate error state, but still allow Done to proceed with correction
         builder.row(InlineKeyboardButton(text=f"⚠️ Too Many ({len(selected_categories)}/{MAX_CATEGORIES}) - Click to Confirm", callback_data="category_done"))

    builder.row(InlineKeyboardButton(text="❌ Cancel Selection", callback_data="category_cancel"))

    return builder.as_markup()

async def get_comment_reactions(comment_id: int) -> Tuple[int, int]:
    likes, dislikes = 0, 0
    async with db.acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(CASE WHEN reaction_type = 'like' THEN 1 ELSE 0 END), 0) AS likes,
                   COALESCE(SUM(CASE WHEN reaction_type = 'dislike' THEN 1 ELSE 0 END), 0) AS dislikes
            FROM reactions WHERE comment_id = $1 """, comment_id )
        if counts:
            likes = counts['likes']
            dislikes = counts['dislikes']
    return likes, dislikes

async def get_user_points(user_id: int) -> int:
    """Fetches the current points for a given user ID."""
    async with db.acquire() as conn:
        points = await conn.fetchval("SELECT points FROM user_points WHERE user_id = $1", user_id)
        return points or 0

async def update_user_points(conn: asyncpg.Connection, user_id: int, delta: int):
    """Atomically updates user points within a transaction."""
    if delta == 0: return
    await conn.execute("""
        INSERT INTO user_points (user_id, points) VALUES ($1, $2)
        ON CONFLICT (user_id) DO UPDATE SET points = user_points.points + $2
        """, user_id, delta)
    logging.debug(f"Updated points for user {user_id} by {delta}")

# --- *** MODIFIED: build_comment_keyboard (unchanged logic, but used differently now) *** ---
# This keyboard will now be attached ONLY to the metadata message when a sticker/gif is used.
async def build_comment_keyboard(comment_id: int, commenter_user_id: int, viewer_user_id: int, confession_owner_id: int ):
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")
    builder.button(text="⚠️", callback_data=f"report_confirm_{comment_id}")

    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(4, 1)
    else:
        builder.adjust(4)
    return builder.as_markup()

async def safe_send_message(user_id: int, text: str, **kwargs):
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        if "bot was blocked" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e): logging.warning(f"Could not send message to user {user_id}: Blocked/deactivated. {e}")
        else: logging.warning(f"Telegram API error sending to {user_id}: {e}")
    except TelegramRetryAfter as e:
        logging.warning(f"Flood control for {user_id}. Retrying after {e.retry_after}s")
        await asyncio.sleep(e.retry_after); return await safe_send_message(user_id, text, **kwargs)
    except Exception as e: logging.error(f"Unexpected error sending message to {user_id}: {e}", exc_info=True)
    return False

# --- *** MODIFIED: update_channel_post_button (minor change for GIN index query hint if needed) *** ---
async def update_channel_post_button(confession_id: int):
    global bot_info; await asyncio.sleep(0.1)
    if not bot_info: logging.error(f"No bot info for {confession_id} button update."); return
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)
        # Hint index usage if planner gets it wrong (unlikely for simple count): /*+ IndexScan(comments comments_confession_id_idx) */
        count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id) or 0
    if not conf_data or not conf_data['message_id']: logging.debug(f"No approved conf/msg_id for {confession_id} button."); return
    ch_msg_id = conf_data['message_id']; link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💬 View / Add Comments ({count})", url=link)]])
    try: await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower(): logging.info(f"Button for {confession_id} already updated ({count}).")
        elif "message to edit not found" in str(e).lower(): logging.warning(f"Msg {ch_msg_id} not found in {CHANNEL_ID} (conf {confession_id}). Maybe deleted?")
        else: logging.error(f"Failed edit channel post {ch_msg_id} for conf {confession_id}: {e}")
    except Exception as e: logging.error(f"Unexpected err updating btn for conf {confession_id}: {e}", exc_info=True)

# --- *** MODIFIED: show_comments_for_confession (Major changes for Sticker/GIF handling) *** ---
async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None):
    """
    Displays comments for a given confession. Includes commenter's User ID for admin,
    medal points, and handles text, stickers, and GIFs separately.
    """
    confession_owner_id: Optional[int] = None
    comment_data_list: list[Dict[str, Any]] = []

    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT status, user_id FROM confessions WHERE id = $1", confession_id)
        if not conf_data or conf_data['status'] != 'approved':
            err_txt = f"Confession #{confession_id} not found or not approved."
            logging.warning(err_txt + f" (Requested by {user_id})")
            try:
                if message_to_edit: await message_to_edit.edit_text(err_txt, reply_markup=None)
                else: await safe_send_message(user_id, err_txt)
            except Exception as e: logging.warning(f"Could not send/edit 'conf not found' to {user_id}: {e}")
            return
        confession_owner_id = conf_data['user_id']

        # Fetch comments including new content types
        comments_raw = await conn.fetch(
            """
            SELECT
                c.id, c.user_id, c.text, c.sticker_file_id, c.animation_file_id,
                c.parent_comment_id, c.created_at, COALESCE(up.points, 0) as user_points
            FROM comments c
            LEFT JOIN user_points up ON c.user_id = up.user_id
            WHERE c.confession_id = $1
            ORDER BY c.created_at ASC
            """,
            confession_id
        )
        comment_data_list = [dict(row) for row in comments_raw]

    sent_msg_ids = {}; comment_id_to_seq = {}; counter = 0
    # first_comment_message = True # Flag to handle initial edit # Not used currently

    if not comment_data_list:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
        if message_to_edit:
             try:
                  await message_to_edit.edit_text(comments_html, parse_mode=ParseMode.HTML, reply_markup=None)
             except TelegramBadRequest as e:
                 if "message is not modified" not in str(e).lower(): # Only log if it's not just "not modified"
                     logging.warning(f"Could not edit 'no comments' msg for conf {confession_id} to {user_id}: {e}")
             except Exception as e:
                 logging.warning(f"Could not edit 'no comments' msg for conf {confession_id} to {user_id}: {e}")
        else:
            await safe_send_message(user_id, comments_html, parse_mode=ParseMode.HTML)
    else:
        if message_to_edit: # Delete the "Loading..." message as we will send new messages
            try:
                await message_to_edit.delete()
            except Exception as e:
                logging.warning(f"Could not delete 'loading...' message for conf {confession_id}, user {user_id}: {e}")

        temp_map = {}
        for i, c_data in enumerate(comment_data_list):
            counter = i + 1
            db_id = c_data['id']
            comment_id_to_seq[db_id] = counter
            temp_map[db_id] = c_data

        for c_data in comment_data_list:
            comm_id = c_data['id']
            seq_num = comment_id_to_seq[comm_id]
            commenter_uid = c_data['user_id']
            comm_text = c_data['text'] # Might be None
            sticker_id = c_data['sticker_file_id'] # Might be None
            animation_id = c_data['animation_file_id'] # Might be None
            ts_raw: datetime = c_data['created_at']
            ts = ts_raw.strftime("%Y-%m-%d %H:%M") if ts_raw else "Unknown time"

            commenter_points = c_data['user_points']
            medal_str = f" 🏅{commenter_points} Aura" if commenter_points is not None else "" # Check for None

            reply_prefix = ""
            if c_data['parent_comment_id'] and c_data['parent_comment_id'] in comment_id_to_seq:
                reply_prefix = f"↪️ <i>Replying to #{comment_id_to_seq[c_data['parent_comment_id']]}</i>\n"
            elif c_data['parent_comment_id']:
                reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"

            tag = ""
            if confession_owner_id is not None:
                if commenter_uid == confession_owner_id: tag = "(Author)"
                elif commenter_uid == user_id: tag = "(You)"
                else: tag = "Anonymous"
            else: tag = "Anonymous"
            display_tag = f" {tag}{medal_str}" if tag else f" Anonymous{medal_str}"

            admin_info = ""
            if user_id == ADMIN_ID:
                admin_info = f" [UID: <code>{commenter_uid}</code>]"

            # Build keyboard (needed for both text and sticker/gif metadata messages)
            keyboard = await build_comment_keyboard(
                comment_id=comm_id,
                commenter_user_id=commenter_uid,
                viewer_user_id=user_id,
                confession_owner_id=confession_owner_id or 0 # Ensure it's not None
            )

            try:
                # --- *** SEPARATE HANDLING FOR STICKER/GIF vs TEXT *** ---
                metadata_text = f"<i>#{seq_num}{display_tag}{admin_info} ({ts})</i>"

                if sticker_id:
                    await bot.send_sticker(user_id, sticker=sticker_id)
                    # Send metadata and keyboard separately
                    sent_meta_msg = await bot.send_message(
                        user_id,
                        f"{reply_prefix}{metadata_text}", # Include reply prefix with metadata
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML
                    )
                    sent_msg_ids[comm_id] = sent_meta_msg.message_id # Store ID of the metadata message
                elif animation_id:
                    await bot.send_animation(user_id, animation=animation_id)
                    # Send metadata and keyboard separately
                    sent_meta_msg = await bot.send_message(
                        user_id,
                        f"{reply_prefix}{metadata_text}", # Include reply prefix with metadata
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML
                    )
                    sent_msg_ids[comm_id] = sent_meta_msg.message_id
                elif comm_text:
                    # Original behavior: text + metadata + keyboard in one message
                    full_text = f"{reply_prefix}💬 {html.quote(comm_text)}\n\n{metadata_text}"
                    sent_msg = await bot.send_message(
                        user_id,
                        full_text,
                        reply_markup=keyboard,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True
                    )
                    sent_msg_ids[comm_id] = sent_msg.message_id
                else:
                     # Should not happen due to DB constraint, but handle defensively
                     logging.error(f"Comment {comm_id} has no text, sticker, or animation!")
                     await bot.send_message(user_id, f"⚠️ Error displaying comment #{seq_num} (DB ID: {comm_id}) - Content Missing")

            except Exception as e:
                logging.warning(f"Could not send comment #{seq_num} (DB ID: {comm_id}) to {user_id}: {e}")
                await safe_send_message(user_id, f"⚠️ Error displaying comment #{seq_num}.")

    # --- Add Comment Button ---
    add_comm_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])

    end_txt = f"--- End of comments for Confession #{confession_id} ---\n" if comment_data_list else ""
    end_txt += "\nYou can add your own comment below:"

    try:
        # Send the final "Add Comment" prompt as a new message
         await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn, parse_mode=ParseMode.HTML)
         # Clean up the original "Loading..." message if it wasn't edited earlier and this is the first message being sent
         # (This logic was simplified: "Loading..." is deleted at the start if comments exist)

    except Exception as e:
        logging.warning(f"Could not send final 'Add Comment' prompt to {user_id} for {confession_id}: {e}")


# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()

    deep_link_args = command.args if command else None
    if deep_link_args and deep_link_args.startswith("view_"):
        try:
            conf_id = int(deep_link_args.split("_", 1)[1])
            logging.info(f"User {message.from_user.id} started via deep link for conf {conf_id}")
            async with db.acquire() as conn:
                # --- *** MODIFIED: Fetch categories array *** ---
                conf_data = await conn.fetchrow("""
                    SELECT c.text, c.categories, c.status, c.user_id, COUNT(com.id) as comment_count
                    FROM confessions c
                    LEFT JOIN comments com ON c.id = com.confession_id
                    WHERE c.id = $1
                    GROUP BY c.id, c.text, c.categories, c.status, c.user_id
                    """, conf_id)

            if not conf_data or conf_data['status'] != 'approved':
                await message.answer(f"Confession #{conf_id} not found or not approved.")
                return

            comm_count = conf_data['comment_count']
            categories = conf_data['categories'] or [] # Handle NULL case
            category_tags = " ".join([f"#{html.quote(cat)}" for cat in categories]) if categories else "#Unknown"

            txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(conf_data['text'])}\n\n{category_tags}\n---"
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{conf_id}")
            builder.button(text=f"💬 Browse Comments ({comm_count})", callback_data=f"browse_{conf_id}")
            if message.from_user and message.from_user.id == conf_data['user_id']:
                builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{conf_id}")
                builder.adjust(1, 1, 1)
            else:
                builder.adjust(1, 1)
            await message.answer(txt, reply_markup=builder.as_markup())
        except (ValueError, IndexError): logging.warning(f"Invalid deep link from {message.from_user.id}: {deep_link_args}"); await message.answer("Invalid link.")
        except Exception as e: logging.error(f"Err handling deep link '{deep_link_args}' for {message.from_user.id}: {e}", exc_info=True); await message.answer("Error processing link.")
    else: await message.answer("Welcome! Use /confess to share anonymously or /help for more info.", reply_markup=ReplyKeyboardRemove())

# --- Help Command Handler (Unchanged) ---
@dp.message(Command("help"), StateFilter(None))
async def show_help(message: types.Message):
    help_text = (
        "<b>Welcome to the Confession Bot!</b>\n\n"
        "Here's how to use the bot:\n"
        "🔹 /confess - Start the process to submit a new anonymous confession (select up to 3 categories).\n"
        "🔹 /start - Show the welcome message.\n"
        "🔹 /help - Display this help message.\n"
        "🔹 /privacy - View information about data privacy.\n\n"
        "Interact with comments using the buttons:\n"
        "👍/👎: Like/Dislike (+3🏅/-3🏅 for the commenter).\n"
        "↪️ Reply: Add a reply to a comment (Text, Sticker, or GIF).\n"
        "⚠️ Report: Report a comment to the admin.\n"
        "🤝 Request Contact: (Author only) Ask to contact a commenter.\n\n"
        "Need to reach the admin directly?"
    )
    contact_admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin_start")]
        ]
    )
    if message.from_user and message.from_user.id == ADMIN_ID:
        help_text += (
            "\n\n<b>Admin Commands:</b>\n"
            "🔹 /id &lt;user_id&gt; - Get info about a specific user (incl. points)."
        )
    await message.answer(help_text, parse_mode=ParseMode.HTML, reply_markup=contact_admin_keyboard)

# --- Callback Handler to Start Contact Admin Flow (Unchanged) ---
@dp.callback_query(F.data == "contact_admin_start", StateFilter(None))
async def start_contact_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ContactAdminForm.waiting_for_message)
    cancel_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/cancel")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await callback_query.answer("Please send your message to the admin.")
    await callback_query.message.answer(
        "Okay, please send the message you want to forward to the admin.\n"
        "The admin will see your message but not your direct profile initially.\n"
        "Type /cancel to abort.",
        reply_markup=cancel_button
    )

# --- Privacy Command Handler (Unchanged conceptually, minor text edits) ---
@dp.message(Command("privacy"), StateFilter(None))
async def show_privacy(message: types.Message):
    privacy_policy_url = "https://telegra.ph/Privacy-Policy-for-AAU-Confessions-Bot-04-27" # Replace with your actual URL
    privacy_text = (
        "<b>Privacy Information</b>\n\n"
        "Your privacy is important:\n"
        "▪️ When you /confess, your Telegram User ID is stored but never shown to other users. Submission contributes to your medal points.\n"
        "▪️ Comments (Text, Sticker, GIF) are posted anonymously. Your User ID is stored with the comment for identification, reaction points, and reporting, but is not displayed publicly to regular users or authors.\n"
        "▪️ Your medal points (🏅), derived from reactions and confessions, are displayed next to your anonymous tag on comments.\n"
        "▪️ The confession author can request to contact a commenter. You (the commenter) must explicitly 'Approve' sharing your @username (if set).\n"
        "▪️ Reactions (likes/dislikes) are linked to your User ID and affect the commenter's points, but it's not public who reacted.\n"
        "▪️ Reporting a comment links your User ID to the report for admin review but is not shown publicly.\n"
        f"▪️ All data is stored in a secure database hosted potentially outside your region.\n"
        f"▪️ The bot admin (User ID: <code>{ADMIN_ID}</code>) manages the review process and has access to stored User IDs for moderation and operational purposes.\n\n"
        f'For more details, please read our full <a href="{privacy_policy_url}">Privacy Policy</a>.'
    )
    await message.answer(privacy_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- Handler to Cancel Any State (Unchanged) ---
@dp.message(Command("cancel"), StateFilter('*')) # More generic cancel
async def cancel_any_state(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Nothing to cancel.", reply_markup=ReplyKeyboardRemove())
        return
    logging.info(f"User {message.from_user.id} cancelling state {current_state}")
    await state.clear()
    await message.answer("Action cancelled.", reply_markup=ReplyKeyboardRemove())

# --- Contact Admin Message Handling (Unchanged) ---
@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def receive_admin_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id; user_info = message.from_user; message_text = message.text
    if not user_info:
        await message.answer("Could not identify sender. Action cancelled.")
        await state.clear()
        return
    if len(message_text) < 5: await message.answer("Message too short. Please provide more detail or /cancel."); return
    if len(message_text) > 2000: await message.answer("Message too long (max 2000 chars). Please shorten or /cancel."); return
    admin_message = ( f"<b>📬 Contact Request from User</b>\n\n"
        f"<b>User ID:</b> <code>{user_id}</code>\n"
        f"<b>Username:</b> @{user_info.username if user_info.username else 'Not Set'}\n"
        f"<b>First Name:</b> {html.quote(user_info.first_name)}\n\n<b>Message:</b>\n{html.quote(message_text)}"
        f"\n\n---\nReply to this message to send a response to User ID <code>{user_id}</code>." )
    try:
        await bot.send_message(ADMIN_ID, admin_message, parse_mode=ParseMode.HTML)
        await message.answer("✅ Your message has been sent to the admin.", reply_markup=ReplyKeyboardRemove())
        logging.info(f"User {user_id} sent a message to admin.")
    except Exception as e: logging.error(f"Failed forward msg from {user_id} to admin {ADMIN_ID}: {e}"); await message.answer("❌ Error sending message.")
    finally: await state.clear()

# --- Handler for Admin Replies to User Messages (Unchanged) ---
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def handle_admin_reply(message: types.Message, state: FSMContext): # Added state
    current_admin_state = await state.get_state()
    if current_admin_state is not None:
        logging.debug(f"Admin {ADMIN_ID} sent a reply, but is in state {current_admin_state}. Letting state handler process.")
        return
    replied_to_message = message.reply_to_message
    if replied_to_message and replied_to_message.text and "⚠️ New Comment Report" in replied_to_message.text:
        logging.info(f"Admin {ADMIN_ID} replied to a report notification. Ignoring reply action.")
        await message.reply("ℹ️ Replying directly to report notifications doesn't perform any action currently. Use /id <user_id> for info.")
        return

    global bot_info
    if not bot_info: logging.error("Cannot handle admin reply: Bot info not loaded."); return
    admin_reply_text = message.text;
    if not replied_to_message or not replied_to_message.from_user or replied_to_message.from_user.id != bot_info.id: logging.debug("Admin replied to a non-bot message or bot info mismatch. Ignoring."); return
    target_user_id = None; original_html_text = replied_to_message.html_text; original_plain_text = replied_to_message.text
    text_to_search = original_html_text if original_html_text else original_plain_text
    if not text_to_search: logging.warning("Admin replied to a bot message with no text content."); return
    id_marker_start = "User ID:</b> <code>"; id_marker_end = "</code>"; user_id_str = ""
    try:
        start_index = text_to_search.find(id_marker_start)
        if start_index != -1:
            start_parse_index = start_index + len(id_marker_start)
            end_index = text_to_search.find(id_marker_end, start_parse_index)
            if end_index != -1: user_id_str = text_to_search[start_parse_index:end_index]; target_user_id = int(user_id_str.strip()); logging.info(f"Extracted target User ID {target_user_id} from admin reply context.")
            else: logging.warning(f"Found start marker '{id_marker_start}' but not end marker '{id_marker_end}' after it in replied text.")
        else: logging.warning(f"Could not find start marker '{id_marker_start}' in replied message text."); logging.debug(f"Searched text content for User ID extraction:\n{text_to_search}")
    except ValueError: logging.warning(f"Could not convert extracted User ID string '{user_id_str}' to integer."); target_user_id = None
    except Exception as e: logging.error(f"Error parsing User ID from admin reply context: {e}", exc_info=True); target_user_id = None
    if target_user_id:
        try:
            sent = await safe_send_message(target_user_id, f"💬 <b>Admin Reply:</b>\n\n{html.quote(admin_reply_text or '')}", parse_mode=ParseMode.HTML)
            if sent: await message.reply("✅ Reply sent to the user."); logging.info(f"Admin {message.from_user.id} replied to user {target_user_id}.")
            else: await message.reply("⚠️ Failed to send reply. User may have blocked the bot or is deactivated."); logging.warning(f"Failed to send admin reply to user {target_user_id} (likely blocked/deactivated).")
        except Exception as e: logging.error(f"Error sending admin reply to user {target_user_id}: {e}"); await message.reply(f"❌ Error sending reply: {e}")
    else:
        if "Contact Request from User" in text_to_search and "<b>User ID:</b>" in text_to_search:
             await message.reply("⚠️ Couldn't identify the target user ID from the message you replied to. Was the format changed? Please check the User ID provided in the original request.")
             logging.warning(f"Admin {message.from_user.id} replied to a potential contact message, but User ID couldn't be extracted.")
        else:
             if "Contact Request from User" in text_to_search:
                 logging.warning(f"Admin {message.from_user.id} reply format unrecognizable for User ID extraction.")
             else:
                 logging.debug("Admin replied to a generic bot message, or format was unrecognizable. Ignoring.")

# --- Admin /id Command Handler (Unchanged) ---
@dp.message(Command("id"))
async def get_user_info_command(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: logging.warning(f"Unauthorized /id attempt by {message.from_user.id if message.from_user else 'unknown user'}"); return
    if not command.args: await message.reply("Usage: /id <user_id>"); return
    try: target_user_id = int(command.args.strip())
    except ValueError: await message.reply("Invalid User ID. Please provide a numeric ID."); return
    logging.info(f"Admin {ADMIN_ID} requested info for User ID {target_user_id}")
    info_parts = [f"ℹ️ <b>User Info for ID:</b> <code>{target_user_id}</code>\n"]; tg_info_fetched = False
    try:
        chat_info = await bot.get_chat(target_user_id)
        info_parts.append("<b>Telegram Details:</b>")
        info_parts.append(f"  - <b>Type:</b> {html.quote(str(chat_info.type))}")
        if chat_info.username: info_parts.append(f"  - <b>Username:</b> @{html.quote(chat_info.username)}")
        else: info_parts.append("  - <b>Username:</b> Not Set")
        info_parts.append(f"  - <b>First Name:</b> {html.quote(chat_info.first_name or 'N/A')}")
        if chat_info.last_name: info_parts.append(f"  - <b>Last Name:</b> {html.quote(chat_info.last_name)}")
        tg_info_fetched = True
    except TelegramBadRequest as e: info_parts.append(f"⚠️ <b>Telegram Details:</b> Could not fetch info. (Error: {html.quote(str(e))})"); logging.warning(f"Failed to get_chat for {target_user_id}: {e}")
    except Exception as e: info_parts.append(f"❌ <b>Telegram Details:</b> An unexpected error occurred: {html.quote(str(e))}"); logging.error(f"Unexpected error get_chat for {target_user_id}: {e}", exc_info=True)

    info_parts.append("\n<b>Bot Interaction History:</b>")
    try:
        async with db.acquire() as conn:
            user_points = await get_user_points(target_user_id)
            info_parts.append(f"  - <b>Medal Points:</b> 🏅 {user_points}")

            conf_stats = await conn.fetchrow("SELECT COUNT(*) as count, MAX(created_at) as last_ts FROM confessions WHERE user_id = $1", target_user_id)
            conf_count = conf_stats['count'] if conf_stats else 0; last_conf_ts = conf_stats['last_ts'].strftime("%Y-%m-%d %H:%M:%S %Z") if conf_stats and conf_stats['last_ts'] else "N/A"
            info_parts.append(f"  - <b>Confessions Submitted:</b> {conf_count}" + (f" (Last: {last_conf_ts})" if conf_count > 0 else ""))

            comm_stats = await conn.fetchrow("SELECT COUNT(*) as count, MAX(created_at) as last_ts FROM comments WHERE user_id = $1", target_user_id)
            comm_count = comm_stats['count'] if comm_stats else 0; last_comm_ts = comm_stats['last_ts'].strftime("%Y-%m-%d %H:%M:%S %Z") if comm_stats and comm_stats['last_ts'] else "N/A"
            info_parts.append(f"  - <b>Comments Made:</b> {comm_count}" + (f" (Last: {last_comm_ts})" if comm_count > 0 else ""))

            react_stats = await conn.fetchrow("SELECT COUNT(*) as count, MAX(created_at) as last_ts FROM reactions WHERE user_id = $1", target_user_id)
            react_count = react_stats['count'] if react_stats else 0; last_react_ts = react_stats['last_ts'].strftime("%Y-%m-%d %H:%M:%S %Z") if react_stats and react_stats['last_ts'] else "N/A"
            info_parts.append(f"  - <b>Reactions Given:</b> {react_count}" + (f" (Last: {last_react_ts})" if react_count > 0 else ""))

            req_made_stats = await conn.fetchrow("SELECT COUNT(*) as count, MAX(created_at) as last_ts FROM contact_requests WHERE requester_user_id = $1", target_user_id)
            req_made_count = req_made_stats['count'] if req_made_stats else 0; last_req_made_ts = req_made_stats['last_ts'].strftime("%Y-%m-%d %H:%M:%S %Z") if req_made_stats and req_made_stats['last_ts'] else "N/A"
            info_parts.append(f"  - <b>Contact Requests Sent (as Author):</b> {req_made_count}" + (f" (Last: {last_req_made_ts})" if req_made_count > 0 else ""))

            req_rec_stats = await conn.fetchrow("SELECT COUNT(*) as count, MAX(updated_at) as last_ts FROM contact_requests WHERE requested_user_id = $1", target_user_id)
            req_rec_count = req_rec_stats['count'] if req_rec_stats else 0; last_req_rec_ts = req_rec_stats['last_ts'].strftime("%Y-%m-%d %H:%M:%S %Z") if req_rec_stats and req_rec_stats['last_ts'] else "N/A"
            info_parts.append(f"  - <b>Contact Requests Received (as Commenter):</b> {req_rec_count}" + (f" (Last Action: {last_req_rec_ts})" if req_rec_count > 0 else ""))

            reports_made_count = await conn.fetchval("SELECT COUNT(*) FROM reports WHERE reporter_user_id = $1", target_user_id)
            reports_received_count = await conn.fetchval("SELECT COUNT(*) FROM reports WHERE reported_user_id = $1", target_user_id)
            info_parts.append(f"  - <b>Reports Made:</b> {reports_made_count}")
            info_parts.append(f"  - <b>Reports Received (as Commenter):</b> {reports_received_count}")

    except Exception as e: info_parts.append("❌ <b>Bot Interaction History:</b> Error fetching database info."); logging.error(f"Error fetching DB info for user {target_user_id}: {e}", exc_info=True)
    final_message = "\n".join(info_parts); await message.reply(final_message, parse_mode=ParseMode.HTML)

# --- Confession Submission Flow ---

# --- *** MODIFIED: Start Confession - Initialize category selection *** ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    # Initialize empty list for selected categories
    await state.update_data(selected_categories=[])
    await message.answer(
        f"Please choose 1 to {MAX_CATEGORIES} categories for your confession.\n"
        "Click a category to select/deselect it. Click 'Done Selecting' when finished.",
        reply_markup=create_category_keyboard([]) # Start with empty selection
    )
    await state.set_state(ConfessionForm.selecting_categories)

# --- *** MODIFIED: Handle Category Selection Toggles *** ---
@dp.callback_query(StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
async def handle_category_selection(callback_query: types.CallbackQuery, state: FSMContext):
    action = callback_query.data.split("_", 1)[1]
    user_data = await state.get_data()
    selected_categories: List[str] = user_data.get("selected_categories", [])

    if action == "cancel":
        await state.clear()
        await callback_query.answer("Confession cancelled.")
        await callback_query.message.edit_text("Confession submission cancelled.", reply_markup=None)
        return

    if action == "done":
        if not selected_categories:
            await callback_query.answer(f"Please select at least 1 category first.", show_alert=True)
            return
        if len(selected_categories) > MAX_CATEGORIES:
             await callback_query.answer(f"Too many categories selected! Please deselect some (max {MAX_CATEGORIES}).", show_alert=True)
             # Update keyboard to reflect error state if not already done
             await callback_query.message.edit_reply_markup(reply_markup=create_category_keyboard(selected_categories))
             return

        # Proceed to next step
        await state.set_state(ConfessionForm.waiting_for_text)
        category_tags = " ".join([f"#{html.quote(cat)}" for cat in selected_categories])
        await callback_query.message.edit_text(
            f"Categories selected: <b>{category_tags}</b>\n\n"
            "Now, please send me the text of your confession.\n\n"
            "Type /cancel to stop.",
            reply_markup=None # Remove keyboard
        )
        await callback_query.answer() # Ack the "Done" button
        return

    # Handle actual category toggle
    category = action
    if category in CATEGORIES:
        if category in selected_categories:
            selected_categories.remove(category)
        elif len(selected_categories) < MAX_CATEGORIES:
            selected_categories.append(category)
        else:
             await callback_query.answer(f"You can only select up to {MAX_CATEGORIES} categories.", show_alert=True)
             # Don't update state or keyboard if limit reached
             return

        # Update state and keyboard
        await state.update_data(selected_categories=selected_categories)
        await callback_query.message.edit_reply_markup(
            reply_markup=create_category_keyboard(selected_categories)
        )
        await callback_query.answer(f"Category '{category}' {'selected' if category in selected_categories else 'deselected'}.")
    else:
        await callback_query.answer("Invalid category.", show_alert=True)


# --- *** MODIFIED: receive_confession_text - Use categories list *** ---
@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    conf_text = message.text
    user_id = message.from_user.id
    state_data = await state.get_data()
    selected_categories: List[str] = state_data.get("selected_categories")

    # Validate categories retrieved from state
    if not selected_categories or not isinstance(selected_categories, list) or len(selected_categories) == 0:
        await message.answer("⚠️ Error: Category information was lost or invalid. Please start again with /confess.")
        await state.clear()
        logging.error(f"State missing or invalid categories for user {user_id} in receive_confession_text: {selected_categories}")
        return

    if len(selected_categories) > MAX_CATEGORIES:
        # Should have been caught earlier, but double-check
        await message.answer(f"⚠️ Error: Too many categories ({len(selected_categories)}). Please start again with /confess.")
        await state.clear()
        logging.error(f"User {user_id} reached text submission with too many categories: {selected_categories}")
        return

    if len(conf_text) < 10: await message.answer("Your confession is too short. Please provide at least 10 characters, or type /cancel."); return
    if len(conf_text) > 3900: await message.answer(f"Your confession is too long (max ~3900 characters). It currently has {len(conf_text)} characters. Please shorten it, or type /cancel."); return

    conf_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                # Save the list of categories to the TEXT[] column
                conf_id = await conn.fetchval(
                    "INSERT INTO confessions (text, user_id, categories, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                    conf_text, user_id, selected_categories, # Pass the list directly
                )
                if not conf_id:
                    raise Exception("Failed to get confession ID after insert")

                await update_user_points(conn, user_id, POINTS_PER_CONFESSION)
                logging.info(f"Awarded {POINTS_PER_CONFESSION} point(s) to user {user_id} for submitting confession {conf_id}")

        # Format categories for admin review message
        category_tags = " ".join([f"#{html.quote(cat)}" for cat in selected_categories])

        kbd = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{conf_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{conf_id}")]
        ])
        admin_msg_text = (
            f"<b>New Confession Review</b>\n"
            f"<b>ID:</b> {conf_id}\n"
            f"<b>Categories:</b> {category_tags}\n" # Show multiple categories
            f"<b>User ID:</b> <code>{user_id}</code>\n\n"
            f"<b>Text:</b>\n{html.quote(conf_text)}"
        )
        if len(admin_msg_text) > 4090: admin_msg_text = admin_msg_text[:4087] + "..."

        admin_review_msg = await bot.send_message(ADMIN_ID, admin_msg_text, reply_markup=kbd, parse_mode=ParseMode.HTML)

        await state.update_data(admin_review_chat_id=admin_review_msg.chat.id, admin_review_message_id=admin_review_msg.message_id)

        await message.answer("✅ Your confession has been submitted successfully and is pending review.")
        logging.info(f"Confession #{conf_id} (Categories: {', '.join(selected_categories)}) submitted by User ID {user_id}")

    except Exception as e:
        logging.error(f"Error processing confession text from user {user_id} (Conf ID might be {conf_id}): {e}", exc_info=True)
        await message.answer("An internal error occurred while submitting your confession.")
    finally:
        await state.clear()


# --- Admin Action Handler (Approval/Rejection Prompt) - Modified for categories *** ---
def is_confession_action_callback(data: str) -> bool:
    if not isinstance(data, str): return False
    parts = data.split("_")
    return len(parts) == 2 and parts[0] in ('approve', 'reject') and parts[1].isdigit()

@dp.callback_query(lambda c: is_confession_action_callback(c.data))
async def admin_action(callback_query: types.CallbackQuery, state: FSMContext):
    global bot_info
    if not bot_info:
        logging.error("Bot info missing for admin action.")
        await callback_query.answer("Internal error: Bot info not loaded.", show_alert=True)
        return
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized.", show_alert=True)
        return

    try:
        action, conf_id_str = callback_query.data.split("_", 1)
        conf_id = int(conf_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid admin action cb data: {callback_query.data}")
        await callback_query.answer("Invalid data format.", show_alert=True)
        return

    async with db.acquire() as conn:
        conf_status = await conn.fetchval("SELECT status FROM confessions WHERE id = $1", conf_id)

        if not conf_status:
            logging.warning(f"Admin {callback_query.from_user.id} action on non-existent Conf ID {conf_id}")
            await callback_query.answer("Confession not found.", show_alert=True)
            try:
                await callback_query.message.delete() # Delete the admin review message
            except Exception as e: logging.warning(f"Could not delete admin review msg for non-existent conf {conf_id}: {e}")
            return

        if conf_status != 'pending':
            await callback_query.answer(f"Confession #{conf_id} already '{conf_status}'.", show_alert=True)
            try:
                final_admin_txt = callback_query.message.html_text + f"\n\n-- Already {conf_status.capitalize()} --"
                await callback_query.message.edit_text(final_admin_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception as e:
                 logging.warning(f"Could not edit admin msg for already processed conf {conf_id}: {e}")
            return

        # --- Handle Approval ---
        if action == "approve":
            async with conn.transaction():
                # --- *** MODIFIED: Fetch categories array *** ---
                conf = await conn.fetchrow(
                    "SELECT id, text, user_id, categories, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id
                )
                if not conf or conf['status'] != 'pending':
                    await callback_query.answer("Confession status changed.", show_alert=True); return

                user_id = conf["user_id"]; conf_text = conf["text"]; db_id = conf["id"]
                categories = conf["categories"] or [] # Handle NULL
                final_status = ""; channel_post_text = ""

                try:
                    link = f"https://t.me/{bot_info.username}?start=view_{db_id}"
                    category_tags = " ".join([f"#{html.quote(cat)}" for cat in categories]) if categories else "#Unknown"
                    channel_post_text = f"<b>Confession #{db_id}</b>\n\n{html.quote(conf_text)}\n\n{category_tags}"
                    channel_kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=link)]])

                    if len(channel_post_text) > 4096:
                        logging.error(f"Conf {db_id} text too long ({len(channel_post_text)}). Auto-rejecting.")
                        await callback_query.answer("Error: Text too long. Auto-rejecting.", show_alert=True)
                        await conn.execute("UPDATE confessions SET status = 'rejected', rejection_reason = $1 WHERE id = $2", "Content too long for Telegram.", db_id)
                        await safe_send_message(user_id, f"❌ Your confession (#{db_id} - {category_tags}) was rejected: Content too long.")
                        final_status = "Rejected (Too Long)"
                    else:
                        msg = await bot.send_message(CHANNEL_ID, channel_post_text, reply_markup=channel_kbd, parse_mode=ParseMode.HTML)
                        await conn.execute("UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2", msg.message_id, db_id)
                        await safe_send_message(user_id, f"✅ Your confession (#{db_id} - {category_tags}) has been approved!")
                        await callback_query.answer(f"Confession #{db_id} approved.")
                        logging.info(f"Admin {callback_query.from_user.id} approved Confession #{db_id}")
                        final_status = "Approved"

                    if final_status:
                        final_admin_txt = callback_query.message.html_text + f"\n\n-- Status: {final_status} --"
                        await callback_query.message.edit_text(final_admin_txt, reply_markup=None, parse_mode=ParseMode.HTML)

                except (TelegramForbiddenError, TelegramBadRequest) as e:
                    logging.error(f"Error approval Confession {conf_id}: {e}", exc_info=True)
                    await callback_query.answer(f"Error approval: {e}. Check logs.", show_alert=True)
                    try:
                         fail_txt = callback_query.message.html_text + "\n\n-- Approval Failed! Check Logs. --"
                         await callback_query.message.edit_text(fail_txt, reply_markup=None)
                    except Exception: pass
                except Exception as e:
                    logging.error(f"Unexpected error approval Confession {conf_id}: {e}", exc_info=True)
                    await callback_query.answer(f"Unexpected error: {e}. Check logs.", show_alert=True)
                    try:
                         fail_txt = callback_query.message.html_text + "\n\n-- Approval Failed! Check Logs. --"
                         await callback_query.message.edit_text(fail_txt, reply_markup=None)
                    except Exception: pass

        # --- Handle Rejection: Ask for Reason (Unchanged logic here) ---
        elif action == "reject":
            await state.update_data(
                rejecting_conf_id=conf_id,
                admin_review_chat_id=callback_query.message.chat.id,
                admin_review_message_id=callback_query.message.message_id,
                original_admin_text = callback_query.message.html_text
            )
            await state.set_state(AdminActions.waiting_for_rejection_reason)
            reason_keyboard = ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="/skip")], [KeyboardButton(text="/cancel")]],
                resize_keyboard=True, one_time_keyboard=True
            )
            await callback_query.answer("❓ Provide rejection reason", show_alert=False)
            await bot.send_message(
                callback_query.from_user.id,
                f"Reason for rejecting Confession #{conf_id}?\n/skip or /cancel.",
                reply_markup=reason_keyboard
            )
            logging.info(f"Admin {callback_query.from_user.id} initiated rejection for Confession #{conf_id}, waiting for reason.")

# --- Handler for Admin Rejection Reason - Modified for categories ---
@dp.message(AdminActions.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    if admin_id != ADMIN_ID: return

    data = await state.get_data()
    conf_id = data.get("rejecting_conf_id")
    admin_review_chat_id = data.get("admin_review_chat_id")
    admin_review_message_id = data.get("admin_review_message_id")
    original_admin_text = data.get("original_admin_text", f"Review Conf #{conf_id}")

    if not all([conf_id, admin_review_chat_id, admin_review_message_id]):
        logging.error(f"Admin {admin_id} sent rejection reason, missing state: {data}")
        await message.answer("Error: Context lost. Try rejecting again.", reply_markup=ReplyKeyboardRemove())
        await state.clear(); return

    reason = None; reason_text_for_user = "Your confession was rejected."; reason_text_for_log = "(No reason)"; final_status_text = "Rejected"

    if message.text.startswith("/"):
        command = message.text.split()[0]
        if command == "/skip":
            reason = None; reason_text_for_log = "(Skipped reason)"
            await message.answer("Skipping reason.", reply_markup=ReplyKeyboardRemove())
        elif command == "/cancel":
            await message.answer("Rejection cancelled.", reply_markup=ReplyKeyboardRemove())
            logging.info(f"Admin {admin_id} cancelled rejection for Conf {conf_id}.")
            await state.clear(); return # Exit
        else:
            await message.answer("Invalid command. Provide reason, /skip or /cancel."); return
    else:
        reason = message.text
        if len(reason) > 500: await message.answer("Reason too long (max 500). Shorten, /skip or /cancel."); return
        reason_text_for_user = f"Your confession rejected:\n\n<i>{html.quote(reason)}</i>"
        reason_text_for_log = reason; final_status_text = "Rejected (Reason Provided)"
        await message.answer("Reason recorded. Rejecting...", reply_markup=ReplyKeyboardRemove())

    success = False
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                # --- *** MODIFIED: Fetch categories array *** ---
                conf_data = await conn.fetchrow(
                    "SELECT user_id, categories, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id
                )
                if not conf_data:
                    logging.warning(f"Admin {admin_id} rejecting conf {conf_id}, but disappeared.")
                    await message.answer("Error: Confession not found.", reply_markup=ReplyKeyboardRemove())
                    await state.clear(); return
                if conf_data['status'] != 'pending':
                    logging.warning(f"Admin {admin_id} rejecting conf {conf_id}, status already {conf_data['status']}.")
                    await message.answer(f"Error: Confession already {conf_data['status']}.", reply_markup=ReplyKeyboardRemove())
                    try:
                        await bot.edit_message_text(
                            chat_id=admin_review_chat_id, message_id=admin_review_message_id,
                            text=original_admin_text + f"\n\n-- Already {conf_data['status'].capitalize()} --",
                            reply_markup=None, parse_mode=ParseMode.HTML
                        )
                    except Exception as e: logging.warning(f"Could not edit admin msg for already processed conf {conf_id}: {e}")
                    await state.clear(); return

                user_id = conf_data['user_id']
                categories = conf_data['categories'] or [] # Handle NULL
                category_tags = " ".join([f"#{html.quote(cat)}" for cat in categories]) if categories else "#Unknown"

                await conn.execute(
                    "UPDATE confessions SET status = 'rejected', rejection_reason = $1 WHERE id = $2", reason, conf_id
                )

                user_notification = f"❌ {reason_text_for_user}\n(Confession ID: #{conf_id}, Categories: {category_tags})"
                await safe_send_message(user_id, user_notification, parse_mode=ParseMode.HTML)

                try:
                    admin_update_text = original_admin_text + f"\n\n-- Status: {final_status_text} --"
                    await bot.edit_message_text(
                        chat_id=admin_review_chat_id, message_id=admin_review_message_id,
                        text=admin_update_text, reply_markup=None, parse_mode=ParseMode.HTML
                    )
                except Exception as e: logging.error(f"Error updating admin msg {admin_review_message_id} after rejection: {e}")

                logging.info(f"Admin {admin_id} rejected Conf #{conf_id}. Reason: {reason_text_for_log}")
                success = True

            except Exception as e:
                logging.error(f"Error rejection DB/notif for Conf {conf_id}: {e}", exc_info=True)
                await message.answer(f"Error during rejection: {e}", reply_markup=ReplyKeyboardRemove())

    if success: await message.answer(f"Confession #{conf_id} rejected.", reply_markup=ReplyKeyboardRemove())
    await state.clear()

# --- Commenting Flow Handlers ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid browse cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    await callback_query.answer("Loading comments...")
    # Pass the callback_query.message to allow editing it initially (e.g., to show "Loading...")
    # or deleting it if comments are sent as new messages
    await show_comments_for_confession(callback_query.from_user.id, conf_id, callback_query.message)


@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid add comment cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        conf_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
        if not conf_exists:
            logging.warning(f"User {callback_query.from_user.id} tried add comment non-existent/unapproved conf {conf_id}.")
            await callback_query.answer("Confession not available.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception as e: logging.warning(f"Could not remove 'Add Comment' btn for unavailable conf {conf_id}: {e}")
            return
    await state.update_data(confession_id=conf_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        # --- *** MODIFIED: Prompt text for sticker/gif *** ---
        await safe_send_message(callback_query.from_user.id, f"📝 Adding comment to Confession #{conf_id}.\nSend text, sticker, or GIF, or /cancel.")
        await callback_query.answer()
    except Exception as e:
        logging.warning(f"Could not send 'add comment' prompt user {callback_query.from_user.id} conf {conf_id}: {e}")
        await callback_query.answer("Could not start commenting.", show_alert=True); await state.clear()

# --- *** MODIFIED: receive_comment - Handle Text, Sticker, GIF *** ---
@dp.message(CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
async def receive_comment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id = data.get("confession_id")

    if not conf_id:
        await message.answer("⚠️ Error: No confession context. Start again."); await state.clear();
        logging.error(f"State missing conf_id for {user_id} in receive_comment"); return

    comm_text: Optional[str] = None
    sticker_id: Optional[str] = None
    animation_id: Optional[str] = None
    log_content_type = "Unknown"

    if message.text:
        comm_text = message.text
        log_content_type = "Text"
        if len(comm_text) < 1: await message.answer("Comment too short (min 1 char), or /cancel."); return # Allow 1 char now
        if len(comm_text) > 1000: await message.answer(f"Comment too long (max 1000 chars). Has {len(comm_text)}. Shorten or /cancel."); return
    elif message.sticker:
        sticker_id = message.sticker.file_id
        log_content_type = "Sticker"
    elif message.animation:
        animation_id = message.animation.file_id
        log_content_type = "GIF"
    else:
        # Should not happen with the filter, but safeguard
        await message.answer("Invalid content type. Please send text, sticker, or GIF, or /cancel."); return

    conf_owner_id = None; new_comm_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
                if not conf_owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found/approved.")

                # Insert with the correct content type
                new_comm_id = await conn.fetchval(
                    """INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id)
                       VALUES ($1, $2, $3, $4, $5, NULL) RETURNING id""",
                    conf_id, user_id, comm_text, sticker_id, animation_id
                )
                if not new_comm_id: raise Exception("Failed get new comment ID.")

        await message.answer("💬 Comment added!");
        logging.info(f"User {user_id} added {log_content_type} comment {new_comm_id} to conf {conf_id}");
        await update_channel_post_button(conf_id)

        # Notify author
        if conf_owner_id and conf_owner_id != user_id and bot_info and bot_info.username:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
            # --- *** MODIFIED: Notification preview *** ---
            preview = ""
            if comm_text:
                preview = html.quote(comm_text[:150]) + ('...' if len(comm_text) > 150 else '')
            elif sticker_id:
                preview = "[Sticker]"
            elif animation_id:
                preview = "[GIF]"

            notif = (f"💬 Comment on your confession #{conf_id}.\n\n<i>{preview}</i>\n\n<a href='{link}'>View comments.</a>")
            await safe_send_message(conf_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): logging.warning(f"Cannot gen notif link author {conf_owner_id} - bot_info missing.")

        # Show updated comments view
        await show_comments_for_confession(user_id, conf_id)

    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
         if "one_content_type" in str(e): # Specific check for our constraint
             logging.error(f"Integrity error (one_content_type) saving comment {log_content_type} for conf {conf_id} by {user_id}: {e}")
             await message.answer("❌ Internal error saving comment (content type issue).")
         else:
             logging.error(f"Integrity error saving comment {log_content_type} for conf {conf_id} by {user_id}: {e}")
             await message.answer("❌ Internal error saving comment (database constraint).")
    except asyncpg.exceptions.ForeignKeyViolationError: logging.warning(f"Attempt add comment to non-existent/unapproved conf {conf_id} by {user_id}"); await message.answer("⚠️ Cannot add comment. Confession removed/unapproved.")
    except Exception as e: logging.error(f"Error saving {log_content_type} comment for conf {conf_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Internal error saving comment.")
    finally: await state.clear()

# --- Reply Flow Handlers ---
@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try: parent_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid reply cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    msg_id_reply_to = callback_query.message.message_id # ID of the metadata message

    async with db.acquire() as conn:
        # --- *** MODIFIED: Fetch all content types for preview *** ---
        comm_data = await conn.fetchrow("SELECT confession_id, text, sticker_file_id, animation_file_id FROM comments WHERE id = $1", parent_id)
        if not comm_data:
            logging.warning(f"User {callback_query.from_user.id} tried reply non-existent parent {parent_id}.")
            await callback_query.answer("Comment no longer exists.", show_alert=True)
            # Try to remove buttons from the metadata message
            try: await callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception as e: logging.warning(f"Could not remove buttons for deleted parent comment {parent_id} (metadata msg): {e}")
            return

    conf_id = comm_data['confession_id']
    # --- *** MODIFIED: Generate preview for text/sticker/gif *** ---
    preview = ""
    if comm_data['text']:
        preview = html.quote(comm_data['text'][:80]) + ('...' if len(comm_data['text']) > 80 else '')
    elif comm_data['sticker_file_id']:
        preview = "[Sticker]"
    elif comm_data['animation_file_id']:
        preview = "[GIF]"
    else:
        preview = "[Unknown Content]"


    await state.update_data(confession_id=conf_id, parent_comment_id=parent_id, message_id_to_reply_to=msg_id_reply_to);
    await state.set_state(CommentForm.waiting_for_reply)
    try:
        # --- *** MODIFIED: Prompt text for sticker/gif *** ---
        prompt = (f"📝 Replying to comment:\n<i>{preview}</i>\n\nPlease send reply (Text, Sticker, GIF) or /cancel.");
        await safe_send_message(callback_query.from_user.id, prompt, parse_mode=ParseMode.HTML);
        await callback_query.answer()
    except Exception as e: logging.warning(f"Could not send reply prompt to {callback_query.from_user.id}: {e}"); await callback_query.answer("Could not ask for reply.", show_alert=True); await state.clear()

# --- *** MODIFIED: receive_reply - Handle Text, Sticker, GIF *** ---
@dp.message(CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)
async def receive_reply(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id = data.get("confession_id")
    parent_id = data.get("parent_comment_id")
    # msg_id_reply_to = data.get("message_id_to_reply_to") # We don't really need this here anymore

    if not conf_id or not parent_id: # Removed check for msg_id_reply_to
        await message.answer("⚠️ Error: Reply context lost. Try again."); await state.clear();
        logging.error(f"State missing fields for {user_id} in receive_reply: {data}"); return

    reply_text: Optional[str] = None
    sticker_id: Optional[str] = None
    animation_id: Optional[str] = None
    log_content_type = "Unknown"

    if message.text:
        reply_text = message.text
        log_content_type = "Text Reply"
        if len(reply_text) < 1: await message.answer("Reply cannot be empty, or /cancel."); return
        if len(reply_text) > 1000: await message.answer(f"Reply too long (max 1000 chars). Has {len(reply_text)}. Shorten or /cancel."); return
    elif message.sticker:
        sticker_id = message.sticker.file_id
        log_content_type = "Sticker Reply"
    elif message.animation:
        animation_id = message.animation.file_id
        log_content_type = "GIF Reply"
    else:
        await message.answer("Invalid content type. Please send text, sticker, or GIF, or /cancel."); return

    new_comm_id = None; parent_owner_id = None; conf_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1 FOR UPDATE", parent_id);
                if not parent_data:
                    await message.answer("⚠️ Original comment deleted.")
                    await state.clear(); return
                parent_owner_id = parent_data['user_id']

                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id);
                if not conf_owner_id:
                    await message.answer("⚠️ Confession removed.")
                    await state.clear(); return

                # Insert the reply with correct content type
                new_comm_id = await conn.fetchval(
                    """INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id)
                       VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                    conf_id, user_id, reply_text, sticker_id, animation_id, parent_id
                )
                if not new_comm_id: raise Exception("Failed get new reply ID.")

        logging.info(f"User {user_id} added {log_content_type} {new_comm_id} to comment {parent_id} on conf {conf_id}")
        await update_channel_post_button(conf_id)

        await message.answer("↪️ Reply sent!")
        await show_comments_for_confession(user_id, conf_id) # Show updated list

        # Notify Parent Comment Author
        global bot_info
        if parent_owner_id and parent_owner_id != user_id and bot_info and bot_info.username:
             logging.info(f"Notifying parent author {parent_owner_id} of reply {new_comm_id}")
             link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
             # --- *** MODIFIED: Notification preview *** ---
             preview = ""
             if reply_text:
                 preview = html.quote(reply_text[:150]) + ('...' if len(reply_text) > 150 else '')
             elif sticker_id:
                 preview = "[Sticker]"
             elif animation_id:
                 preview = "[GIF]"

             replier_points = await get_user_points(user_id)
             medal_str = f" 🏅{replier_points}" if replier_points is not None else ""
             tag = "(Author)" if user_id == conf_owner_id else "Anonymous"
             notif = (f"↪️ Reply from {tag}{medal_str} to your comment on confession #{conf_id}.\n\n<i>{preview}</i>\n\n<a href='{link}'>View comments.</a>");
             await safe_send_message(parent_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username):
              logging.warning(f"Cannot gen notif link parent author {parent_owner_id} - bot_info missing.")

    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
         if "one_content_type" in str(e):
             logging.error(f"Integrity error (one_content_type) saving reply {log_content_type} to {parent_id} by {user_id}: {e}")
             await message.answer("❌ Internal error saving reply (content type issue).")
         else:
             logging.error(f"Integrity error saving reply {log_content_type} to {parent_id} by {user_id}: {e}")
             await message.answer("❌ Internal error saving reply (database constraint).")
    except asyncpg.exceptions.ForeignKeyViolationError as e:
        logging.warning(f"FK violation reply save by {user_id} to {parent_id}: {e}")
        await message.answer("⚠️ Cannot add reply. Original comment/confession deleted?")
    except Exception as e:
        logging.error(f"Error saving {log_content_type} DB transaction for {parent_id} by {user_id}: {e}", exc_info=True)
        await message.answer("❌ Internal error saving reply.")
    finally:
        await state.clear()

# --- Reaction Handling (Unchanged logic, applies to metadata message) ---
@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    try:
        _, r_type, comm_id_str = callback_query.data.split("_", 2)
        comm_id = int(comm_id_str)
        user_id = callback_query.from_user.id
        if r_type not in ['like', 'dislike']: raise ValueError("Invalid reaction type")
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid react cb: {callback_query.data}"); await callback_query.answer("Invalid reaction.", show_alert=True); return

    action = "none"; kbd = None; alert = None; point_delta = 0
    comm_uid = None; conf_owner_id = None; viewer_id = user_id

    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                info = await conn.fetchrow("SELECT c.user_id as comm_uid, co.user_id as conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comm_id)
                if not info: raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")
                comm_uid = info['comm_uid']; conf_owner_id = info['conf_owner_id']

                if comm_uid == user_id:
                    await callback_query.answer("Cannot react to own comment.", show_alert=True); return

                existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", comm_id, user_id)

                if existing:
                    if existing == r_type: # Remove
                        await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id)
                        action = f"Removed {r_type}"; alert = f"{r_type.capitalize()} removed"
                        point_delta = -POINTS_PER_LIKE_RECEIVED if r_type == 'like' else -POINTS_PER_DISLIKE_RECEIVED
                    else: # Change
                        await conn.execute("UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", r_type, comm_id, user_id)
                        action = f"Changed to {r_type}"; alert = f"Reaction changed to {r_type}"
                        old_points = -POINTS_PER_LIKE_RECEIVED if existing == 'like' else -POINTS_PER_DISLIKE_RECEIVED
                        new_points = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED
                        point_delta = old_points + new_points
                else: # Add new
                    await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, user_id, r_type)
                    action = f"Added {r_type}"; alert = f"{r_type.capitalize()} added"
                    point_delta = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED

                if point_delta != 0 and comm_uid is not None: # Ensure comm_uid is not None
                    await update_user_points(conn, comm_uid, point_delta)
                    logging.info(f"Updated points commenter {comm_uid} by {point_delta} from {user_id} on comment {comm_id}")

                kbd = await build_comment_keyboard(comm_id, comm_uid or 0, viewer_id, conf_owner_id or 0); # Ensure not None
                logging.info(f"User {user_id} action '{action}' on comment {comm_id}. Kbd rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK viol reaction update comm {comm_id} user {user_id}")
                await callback_query.answer("Comment not found.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return
            except Exception as db_err:
                logging.error(f"DB error reaction proc comm {comm_id} by {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("DB Error processing reaction.", show_alert=True)
                return

    if kbd and action != "none":
        try:
            # This edits the metadata message for stickers/gifs, or the main message for text
            await callback_query.message.edit_reply_markup(reply_markup=kbd)
            await callback_query.answer(alert)
            logging.info(f"Updated markup comm {comm_id} after {action}")
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str: logging.info(f"Markup {comm_id} not modified."); await callback_query.answer(alert + " (No visual change)")
            elif "message to edit not found" in err_str: logging.warning(f"Msg not found react update {comm_id}."); await callback_query.answer(alert + " (Counts updated, view not)", show_alert=False)
            elif "query is too old" in err_str: logging.warning(f"Query old react update {comm_id}."); await callback_query.answer(alert + " (Counts updated, view stale)", show_alert=False)
            else: logging.error(f"TG error update react markup {comm_id}: {e}"); await callback_query.answer("Error updating display.", show_alert=True)
        except Exception as e:
            logging.error(f"Unexpected error update react markup {comm_id}: {e}", exc_info=True);
            await callback_query.answer("Error updating display.", show_alert=True)
    elif action != "none":
        logging.error(f"Action {action} comm {comm_id} DB done, but kbd is None.")
        await callback_query.answer(alert + " (Internal Error updating view)", show_alert=True)


# --- Report Comment Handlers (Modified for comment content preview) ---

# 1. Confirmation Prompt
@dp.callback_query(F.data.startswith("report_confirm_"))
async def report_confirm_callback(callback_query: types.CallbackQuery):
    try:
        comment_id = int(callback_query.data.split("_", 2)[2])
        reporter_user_id = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid report confirm cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return

    async with db.acquire() as conn:
        already_reported = await conn.fetchval("SELECT 1 FROM reports WHERE comment_id = $1 AND reporter_user_id = $2", comment_id, reporter_user_id)
        if already_reported:
            await callback_query.answer("Already reported.", show_alert=True); return

        # --- *** MODIFIED: Fetch comment content for preview *** ---
        comment_data = await conn.fetchrow("SELECT text, sticker_file_id, animation_file_id FROM comments WHERE id = $1", comment_id)
        if not comment_data:
            await callback_query.answer("Comment not found.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception: pass
            return

    # --- *** MODIFIED: Generate snippet based on content *** ---
    snippet = ""
    if comment_data['text']:
        snippet = html.quote(comment_data['text'][:100]) + ('...' if len(comment_data['text']) > 100 else '')
    elif comment_data['sticker_file_id']:
        snippet = "[Sticker]"
    elif comment_data['animation_file_id']:
        snippet = "[GIF]"
    else:
         snippet = "[Error: Unknown Content]"

    confirm_text = f"Are you sure you want to report this comment?\n\n<i>\"{snippet}\"</i>"
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Report", callback_data=f"report_execute_{comment_id}"),
            InlineKeyboardButton(text="❌ No, Cancel", callback_data=f"report_cancel_{comment_id}")
        ]
    ])
    try:
        # Send confirmation as a new message in PM
        await safe_send_message(reporter_user_id, confirm_text, reply_markup=confirm_keyboard, parse_mode=ParseMode.HTML)
        await callback_query.answer() # Ack button press
    except Exception as e:
        logging.error(f"Error sending report confirmation comment {comment_id} to user {reporter_user_id}: {e}")
        await callback_query.answer("Could not ask for confirmation.", show_alert=True)


# 2. Execute Report
@dp.callback_query(F.data.startswith("report_execute_"))
async def report_execute_callback(callback_query: types.CallbackQuery):
    try:
        comment_id = int(callback_query.data.split("_", 2)[2])
        reporter_user_id = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid report execute cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return

    reported_user_id = None; confession_id = None; comment_text = None
    sticker_id = None; animation_id = None; report_id = None

    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                # --- *** MODIFIED: Fetch comment content details *** ---
                comment_data = await conn.fetchrow(
                    "SELECT user_id, confession_id, text, sticker_file_id, animation_file_id FROM comments WHERE id = $1 FOR UPDATE",
                     comment_id
                )
                if not comment_data:
                    await callback_query.answer("Comment not found.", show_alert=True)
                    try:
                        await callback_query.message.delete() # Delete the confirmation message
                    except Exception: pass
                    return

                reported_user_id = comment_data['user_id']
                confession_id = comment_data['confession_id']
                comment_text = comment_data['text']
                sticker_id = comment_data['sticker_file_id']
                animation_id = comment_data['animation_file_id']

                if reported_user_id == reporter_user_id:
                    await callback_query.answer("You cannot report your own comment.", show_alert=True)
                    try: await callback_query.message.delete()
                    except Exception: pass
                    return

                already_reported = await conn.fetchval("SELECT 1 FROM reports WHERE comment_id = $1 AND reporter_user_id = $2", comment_id, reporter_user_id)
                if already_reported:
                    await callback_query.answer("Already reported.", show_alert=True)
                    try: await callback_query.message.edit_text("Report already submitted.", reply_markup=None)
                    except Exception: pass
                    return

                report_id = await conn.fetchval(
                    """INSERT INTO reports (comment_id, reporter_user_id, reported_user_id, status)
                       VALUES ($1, $2, $3, 'pending') RETURNING id""",
                    comment_id, reporter_user_id, reported_user_id
                )
                if not report_id: raise Exception("Failed insert report.")

                logging.info(f"User {reporter_user_id} reported comment {comment_id} (author: {reported_user_id}). Report ID: {report_id}")

            except asyncpg.exceptions.UniqueViolationError:
                await callback_query.answer("Already reported.", show_alert=True)
                try: await callback_query.message.edit_text("Report already submitted.", reply_markup=None)
                except Exception: pass
                return
            except Exception as e:
                logging.error(f"Error saving report comment {comment_id} by {reporter_user_id}: {e}", exc_info=True)
                await callback_query.answer("Error saving report.", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass
                return

    # --- If transaction successful, notify admin and reporter ---
    if report_id and reported_user_id and confession_id and bot_info:
        # --- *** MODIFIED: Admin notification snippet *** ---
        snippet = ""
        content_desc = ""
        if comment_text:
            snippet = html.quote(comment_text[:200]) + ('...' if len(comment_text) > 200 else '')
            content_desc = "Text Snippet"
        elif sticker_id:
            snippet = f"[Sticker: <code>{html.quote(sticker_id)}</code>]"
            content_desc = "Sticker"
        elif animation_id:
            snippet = f"[GIF: <code>{html.quote(animation_id)}</code>]"
            content_desc = "GIF"
        else:
            snippet = "[Error: Unknown Content Type]"
            content_desc = "Content"

        confession_link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
        admin_message = (
            f"⚠️ <b>New Comment Report (ID: {report_id})</b> ⚠️\n\n"
            f"<b>Confession:</b> <a href='{confession_link}'>#{confession_id}</a>\n"
            f"<b>Comment ID:</b> <code>{comment_id}</code>\n"
            f"<b>Comment {content_desc}:</b>\n<i>{snippet}</i>\n\n" # Use dynamic description
            f"<b>Reported User ID:</b> <code>{reported_user_id}</code>\n"
            f"<b>Reporter User ID:</b> <code>{reporter_user_id}</code>\n\n"
            f"Use /id <code>{reported_user_id}</code> for user info."
        )
        await safe_send_message(ADMIN_ID, admin_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        try:
            await callback_query.message.edit_text(
                "✅ Comment reported. Admin notified.", reply_markup=None
            )
            await callback_query.answer("Report sent.", show_alert=False)
        except Exception as e:
            logging.warning(f"Could not edit report confirmation msg {callback_query.message.message_id} for user {reporter_user_id}: {e}")
            # If edit fails, try sending a new message
            await safe_send_message(reporter_user_id, "✅ Comment reported successfully.")
            await callback_query.answer("Report sent.", show_alert=False)


# 3. Cancel Report (Unchanged)
@dp.callback_query(F.data.startswith("report_cancel_"))
async def report_cancel_callback(callback_query: types.CallbackQuery):
    try:
        await callback_query.message.edit_text("Report cancelled.", reply_markup=None)
        await callback_query.answer("Report cancelled.")
    except Exception as e:
        logging.warning(f"Error cancelling report (edit msg {callback_query.message.message_id}): {e}")
        await callback_query.answer("Report cancelled.") # Still ack the button


# --- Contact Request Flow Handlers (Modified for comment content preview) ---
@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    try: _, _, comm_id_str = callback_query.data.split("_", 2); comm_id = int(comm_id_str); req_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid req contact cb: {callback_query.data}"); await callback_query.answer("Invalid request data.", show_alert=True); return

    async with db.acquire() as conn:
        async with conn.transaction():
            # --- *** MODIFIED: Fetch comment content *** ---
            comm_data = await conn.fetchrow(
                """SELECT c.user_id comm_uid, c.text comm_txt, c.sticker_file_id, c.animation_file_id,
                          co.id conf_id, co.user_id conf_owner_id
                   FROM comments c JOIN confessions co ON c.confession_id = co.id
                   WHERE c.id = $1 AND co.status = 'approved'""",
                   comm_id
            )
            if not comm_data: await callback_query.answer("Comment/confession not found.", show_alert=True); return

            comm_uid = comm_data['comm_uid']; conf_id = comm_data['conf_id']; conf_owner_id = comm_data['conf_owner_id'];

            # --- *** MODIFIED: Generate preview *** ---
            preview = ""
            if comm_data['comm_txt']:
                preview = html.quote(comm_data['comm_txt'][:100]) + ('...' if len(comm_data['comm_txt']) > 100 else '')
            elif comm_data['sticker_file_id']:
                preview = "[Sticker]"
            elif comm_data['animation_file_id']:
                preview = "[GIF]"
            else:
                preview = "[Unknown Content]"


            if req_uid != conf_owner_id: logging.warning(f"User {req_uid} tried req contact comm {comm_id} but not owner {conf_owner_id}."); await callback_query.answer("Only for your confessions.", show_alert=True); return
            if req_uid == comm_uid: await callback_query.answer("Cannot request contact self.", show_alert=True); return

            existing = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2 AND status IN ('pending', 'approved', 'approved_no_username')", comm_id, req_uid)
            if existing: await callback_query.answer(f"Request already {existing}.", show_alert=True); return

            request_id = None
            try:
                request_id = await conn.fetchval("INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) VALUES ($1, $2, $3, $4, 'pending') ON CONFLICT (comment_id, requester_user_id) DO UPDATE SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE contact_requests.status = 'denied' RETURNING id", conf_id, comm_id, req_uid, comm_uid)
                if not request_id:
                    existing_s = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2", comm_id, req_uid)
                    logging.warning(f"Contact req insert comm {comm_id} by {req_uid} returned no ID. Existing: {existing_s}")
                    await callback_query.answer(f"Request already exists (Status: {existing_s or 'Unknown'}).", show_alert=True)
                    return
            except Exception as insert_err: logging.error(f"Failed insert contact req {req_uid} to {comm_uid} for comm {comm_id}: {insert_err}", exc_info=True); await callback_query.answer("Failed save request.", show_alert=True); return

            notification_text = (f"🤝 Author of Confession #{conf_id} wants to contact you regarding your comment:\n\n<i>{preview}</i>\n\nDo you approve sharing your Telegram profile contact (username, if set)?");
            approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_contact_{request_id}")], [InlineKeyboardButton(text="❌ Deny", callback_data=f"deny_contact_{request_id}")]]);
            sent = await safe_send_message(comm_uid, notification_text, reply_markup=approval_keyboard, parse_mode=ParseMode.HTML)

            if sent: await callback_query.answer("✅ Contact request sent.", show_alert=False); logging.info(f"Contact req {request_id} (comm {comm_id}) sent from {req_uid} to {comm_uid}.")
            else: logging.warning(f"Failed send contact req {request_id} notif to {comm_uid}. Rolling back."); await callback_query.answer("⚠️ Could not send request (user blocked?).", show_alert=True); raise Exception(f"Failed notify commenter {comm_uid}, rollback req {request_id}")

# --- Contact Response Handler (Unchanged logic) ---
def is_contact_response_callback(data: str) -> bool:
    if not isinstance(data, str): return False; parts = data.split("_"); return len(parts) == 3 and parts[0] in ('approve', 'deny') and parts[1] == 'contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data))
async def handle_contact_response(callback_query: types.CallbackQuery):
    try: action, _, req_id_str = callback_query.data.split("_"); req_id = int(req_id_str); resp_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid contact resp cb: {callback_query.data}"); await callback_query.answer("Invalid request data.", show_alert=True); return
    db_status = 'approved' if action == 'approve' else 'denied'; edit_status = ""
    async with db.acquire() as conn:
        async with conn.transaction():
            req_data = await conn.fetchrow("SELECT id, requester_user_id, requested_user_id, status, confession_id, comment_id FROM contact_requests WHERE id = $1 FOR UPDATE", req_id)
            if not req_data:
                await callback_query.answer("Request not found.", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass
                return
            if resp_uid != req_data['requested_user_id']: logging.warning(f"User {resp_uid} tried respond req {req_id} for {req_data['requested_user_id']}."); await callback_query.answer("Invalid request.", show_alert=True); return
            if req_data['status'] != 'pending':
                await callback_query.answer(f"Request already {req_data['status']}.", show_alert=True)
                try:
                    orig_txt = callback_query.message.html_text
                    final_txt = f"{orig_txt}\n\n<b>Status: {req_data['status'].replace('_', ' ').capitalize()}</b>"
                    await callback_query.message.edit_text(final_txt, reply_markup=None, parse_mode=ParseMode.HTML)
                except Exception: pass
                return
            author_notif = ""; req_uid = req_data['requester_user_id']; conf_id = req_data['confession_id']; comm_id = req_data['comment_id']
            if db_status == 'approved':
                comm_uname = None
                try:
                    resp_user_chat = await bot.get_chat(resp_uid)
                    comm_uname = resp_user_chat.username
                except Exception as e: logging.warning(f"Could not fetch chat info user {resp_uid} contact approval: {e}")

                if comm_uname:
                    await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                    author_notif = (f"✅ Contact Approved!\n\nReq Confession #{conf_id} (Comment ~{comm_id}) APPROVED.\n\nContact: @{html.quote(comm_uname)}")
                    await callback_query.answer("Approved. Username shared.")
                    logging.info(f"Req {req_id} approved by {resp_uid}. Uname @{comm_uname} sent to {req_uid}.")
                    edit_status = 'Approved (Username Shared)'
                else:
                    db_status = 'approved_no_username'
                    await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", db_status, req_id)
                    author_notif = (f"⚠️ Contact Approved (No Public Username)\n\nReq Confession #{conf_id} (Comment ~{comm_id}) APPROVED, but user has no public username.")
                    await callback_query.answer("Approved, but no public username set.", show_alert=True)
                    logging.info(f"Req {req_id} approved by {resp_uid}, no username. Notified {req_uid}.")
                    edit_status = 'Approved (No Username)'
            else: # Denied
                await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                author_notif = (f"❌ Contact Denied\n\nReq Confession #{conf_id} (Comment ~{comm_id}) DENIED by commenter.")
                await callback_query.answer("Denied. Contact details not shared.")
                logging.info(f"Req {req_id} denied by {resp_uid}. Notified {req_uid}.")
                edit_status = 'Denied'

            await safe_send_message(req_uid, author_notif, parse_mode=ParseMode.HTML)

            try:
                orig_txt = callback_query.message.html_text
                if "Status:" not in orig_txt:
                    final_txt = f"{orig_txt}\n\n<b>Status: {edit_status}</b>"
                    await callback_query.message.edit_text(final_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception as e: logging.warning(f"Could not edit commenter ({resp_uid}) notif msg {callback_query.message.message_id} req {req_id}: {e}")

# --- View Contact Requests Handler (Modified for comment content preview) ---
@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    try: _, _, conf_id_str = callback_query.data.split("_", 2); conf_id = int(conf_id_str); viewer_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid view reqs cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id)
    if not conf_owner_id: await callback_query.answer("Confession not found.", show_alert=True); return
    if viewer_uid != conf_owner_id: await callback_query.answer("Only for your confessions.", show_alert=True); return

    async with db.acquire() as conn:
        # --- *** MODIFIED: Fetch comment content type for preview *** ---
        reqs = await conn.fetch(
             """SELECT cr.comment_id, cr.status, cr.updated_at,
                       c.text as comment_text, c.sticker_file_id, c.animation_file_id,
                       cr.requested_user_id
                FROM contact_requests cr JOIN comments c ON cr.comment_id = c.id
                WHERE cr.confession_id = $1 AND cr.requester_user_id = $2
                ORDER BY cr.updated_at DESC""",
             conf_id, viewer_uid
        )
    if not reqs: await callback_query.answer("No contact requests for this confession.", show_alert=False); return

    resp_parts = [f"<b>Contact Requests Status for Confession #{conf_id}</b>\n"];
    for req in reqs:
        # --- *** MODIFIED: Generate preview *** ---
        preview = ""
        if req['comment_text']:
            preview = html.quote(req['comment_text'][:60]) + ('...' if len(req['comment_text']) > 60 else '')
        elif req['sticker_file_id']:
            preview = "[Sticker]"
        elif req['animation_file_id']:
            preview = "[GIF]"
        else:
            preview = "[Unknown Content]"

        status = req['status'].replace('_', ' ').capitalize()
        updated = req['updated_at'].strftime("%Y-%m-%d %H:%M")
        req_uid = req['requested_user_id']
        status_emoji = {"pending": "❓", "approved": "✅", "denied": "❌", "approved_no_username": "⚠️"}.get(req['status'], "❓")

        resp_parts.append(
            f"🔹 <b>To Commenter ID:</b> <code>{req_uid}</code>\n"
            f"   <i>Comment: \"{preview}\"</i>\n"
            f"   <b>Status:</b> {status_emoji} {status}\n"
            f"   <b>Last Update:</b> {updated}"
        )

        if req['status'] == 'approved':
            try:
                req_user_chat = await bot.get_chat(req_uid)
                resp_parts.append(f"   <b>Username:</b> @{html.quote(req_user_chat.username)}" if req_user_chat and req_user_chat.username else "   <b>Username:</b> (Approved, No Public Username)")
            except Exception as e: logging.warning(f"Error fetch username approved req {req['comment_id']} -> {req_uid}: {e}"); resp_parts.append("   <b>Username:</b> (Error fetching username)")
        elif req['status'] == 'approved_no_username':
             resp_parts.append("   <b>Username:</b> (Approved, No Public Username)")

    resp_txt = "\n\n".join(resp_parts);
    if len(resp_txt) > 4096: resp_txt = resp_txt[:4090] + "\n\n...(truncated)"
    await safe_send_message(viewer_uid, resp_txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True); await callback_query.answer()

# --- Fallback Handler (Unchanged) ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    logging.debug(f"Received non-command text from user {message.from_user.id} outside state: '{message.text[:50]}...'")
    await message.reply("Hi! 👋 Use /confess to share anonymously or /help for commands.")

# --- Main Execution ---
async def main():
    # Define tasks
    bot_polling_task = None
    dummy_server_task = None

    try:
        await setup() # Setup DB and bot_info
        if not db or not bot_info:
            logging.critical("FATAL: DB or bot info missing after setup. Cannot start.")
            return

        # Set bot commands
        commands_list = [
            types.BotCommand(command="start", description="Start/View confession"),
            types.BotCommand(command="confess", description="Submit anonymous confession"),
            types.BotCommand(command="help", description="Show help and commands"),
            types.BotCommand(command="privacy", description="View privacy information"),
            types.BotCommand(command="cancel", description="Cancel current action"),
        ]
        admin_commands_list = commands_list + [
            types.BotCommand(command="id", description="ADMIN: Get user info (incl. 🏅)"),
        ]
        await bot.set_my_commands(commands_list)
        try:
            await bot.set_my_commands(admin_commands_list, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
            logging.info(f"Admin commands set for ADMIN_ID {ADMIN_ID}.")
        except Exception as e: logging.warning(f"Could not set admin commands: {e}")

        logging.info("Registering handlers...")
        # Registration order matters for overlapping filters like FSM states vs command filters
        # Register more specific handlers first generally

        # Commands
        dp.message.register(start, Command("start"))
        dp.message.register(show_help, Command("help"), StateFilter(None))
        dp.message.register(show_privacy, Command("privacy"), StateFilter(None))
        dp.message.register(get_user_info_command, Command("id")) # Admin only checked inside
        dp.message.register(start_confession, Command("confess"), StateFilter(None))
        dp.message.register(cancel_any_state, Command("cancel"), StateFilter('*'))

        # FSM Handlers (Confession)
        dp.callback_query.register(handle_category_selection, StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
        dp.message.register(receive_confession_text, ConfessionForm.waiting_for_text, F.text)

        # FSM Handlers (Comment/Reply)
        dp.message.register(receive_comment, CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
        dp.message.register(receive_reply, CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)

        # FSM Handlers (Contact Admin)
        dp.callback_query.register(start_contact_admin_callback, F.data == "contact_admin_start", StateFilter(None))
        dp.message.register(receive_admin_message, ContactAdminForm.waiting_for_message, F.text)

        # FSM Handlers (Admin Actions)
        dp.message.register(receive_rejection_reason, AdminActions.waiting_for_rejection_reason, F.text)

        # Callback Query Handlers (Non-FSM)
        dp.callback_query.register(admin_action, lambda c: is_confession_action_callback(c.data)) # Admin approve/reject prompt
        dp.callback_query.register(browse_comments_action, F.data.startswith("browse_"))
        dp.callback_query.register(add_comment_prompt, F.data.startswith("add_"))
        dp.callback_query.register(reply_comment_prompt, F.data.startswith("reply_"))
        dp.callback_query.register(handle_reaction, F.data.startswith("react_"))
        # Report callbacks
        dp.callback_query.register(report_confirm_callback, F.data.startswith("report_confirm_"))
        dp.callback_query.register(report_execute_callback, F.data.startswith("report_execute_"))
        dp.callback_query.register(report_cancel_callback, F.data.startswith("report_cancel_"))
        # Contact request callbacks
        dp.callback_query.register(handle_request_contact, F.data.startswith("req_contact_"))
        dp.callback_query.register(handle_contact_response, lambda c: is_contact_response_callback(c.data))
        dp.callback_query.register(view_contact_requests, F.data.startswith("view_reqs_"))

        # Message Handlers (Non-FSM, Non-Command)
        dp.message.register(handle_admin_reply, F.from_user.id == ADMIN_ID, F.reply_to_message) # Admin replies
        # Fallback MUST be last for text messages in None state
        dp.message.register(handle_text_without_state, StateFilter(None), F.text & ~F.text.startswith('/'))

        logging.info("Handler registration complete.")

        # Create tasks for the bot polling and the dummy HTTP server
        bot_polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True), name="BotPolling")
        if RENDER_PORT: # Only start dummy server if PORT is set (i.e., likely on Render Web Service)
            dummy_server_task = asyncio.create_task(start_dummy_server(), name="DummyHTTPServer")
            logging.info("Starting bot polling and dummy HTTP server...")
            tasks_to_wait_for = [bot_polling_task, dummy_server_task]
        else:
            logging.info("Starting bot polling (dummy HTTP server not started as PORT env var is not set)...")
            tasks_to_wait_for = [bot_polling_task]

        # Wait for any task to complete (e.g., if one crashes)
        done, pending = await asyncio.wait(
            tasks_to_wait_for,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # If one task finishes (or crashes), cancel the others
        for task in pending:
            logging.info(f"Cancelling pending task: {task.get_name()}")
            task.cancel()
            try:
                await task # Await cancellation
            except asyncio.CancelledError:
                logging.info(f"Task {task.get_name()} was cancelled successfully.")
            except Exception as e:
                logging.error(f"Error during cancellation of task {task.get_name()}: {e}", exc_info=True)


        # Log exceptions from completed tasks
        for task in done:
            task_name = task.get_name() if hasattr(task, 'get_name') else "Unknown Task"
            try:
                result = task.result() # This will re-raise the exception if one occurred
                logging.info(f"Task {task_name} completed with result: {result}")
            except asyncio.CancelledError:
                logging.info(f"Task {task_name} was cancelled.")
            except Exception as e:
                logging.error(f"Task {task_name} raised an exception: {e}", exc_info=True)


    except Exception as e:
        logging.critical(f"Fatal error during main execution or setup: {e}", exc_info=True)
    finally:
        logging.info("Shutting down...")

        # Gracefully stop polling if it's still running and not cancelled
        if dp.is_polling():
            logging.info("Stopping bot polling...")
            await dp.stop_polling()

        # Close bot session
        if bot and bot.session and not bot.session.closed:
            logging.info("Closing bot session...")
            await bot.session.close()
            logging.info("Bot session closed.")

        # Close database pool
        if db:
            logging.info("Closing database pool...")
            await db.close()
            logging.info("Database pool closed.")

        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Critical error in asyncio.run(main()): {main_err}", exc_info=True)
        print(f"Critical error: {main_err}")
# --- END OF FILE main_beta_vNext.py ---