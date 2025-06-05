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
from typing import Optional, Tuple, Dict, Any, List

# --- Dummy HTTP Server Imports ---
from aiohttp import web

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
# PORT for dummy HTTP server, Render sets this for Web Services
HTTP_PORT_STR = os.getenv("PORT")


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


# --- Dummy HTTP Server Functions ---
async def handle_health_check(request):
    """Responds with a simple 'OK' for health checks."""
    logging.debug("Health check endpoint hit.")
    return web.Response(text="OK")

async def start_dummy_server():
    """Starts a minimal HTTP server to respond to Render health checks."""
    if not HTTP_PORT_STR:
        logging.info("PORT environment variable not set. Dummy HTTP server will not start.")
        return # Don't start if not in a Web Service context or PORT is missing

    try:
        port = int(HTTP_PORT_STR)
    except ValueError:
        logging.error(f"Invalid PORT environment variable: {HTTP_PORT_STR}. Dummy HTTP server will not start.")
        return

    app = web.Application()
    # Add routes for common health check paths
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/healthz', handle_health_check) # A common health check path

    runner = web.AppRunner(app)
    await runner.setup()
    # Listen on '0.0.0.0' to accept connections from Render's proxy
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
        logging.info(f"Dummy HTTP server started successfully on port {port}.")
        # Keep the server task alive. site.start() is non-blocking.
        # This task will run until cancelled (e.g., when the bot stops).
        while True:
            await asyncio.sleep(3600) # Sleep for a long time, can be interrupted by cancellation
    except asyncio.CancelledError:
        logging.info("Dummy HTTP server task cancelled.")
    except Exception as e:
        logging.error(f"Dummy HTTP server failed to start or crashed on port {port}: {e}", exc_info=True)
    finally:
        await runner.cleanup()
        logging.info("Dummy HTTP server cleaned up and stopped.")


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
                if message_to_edit:
                	print("Delete")
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
    # first_comment_message = True # Flag to handle initial edit # Not used in current logic

    if not comment_data_list:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
        if message_to_edit:
             try:
                  await message_to_edit.edit_text(comments_html, parse_mode=ParseMode.HTML, reply_markup=None)
             except TelegramBadRequest as e:
                 if "message to edit not found" not in str(e).lower(): # Ignore if original message gone
                     logging.warning(f"Could not edit 'no comments' to {user_id} for {confession_id}: {e}")
             except Exception as e:
                 logging.warning(f"Could not edit 'no comments' to {user_id} for {confession_id}: {e}")
        else:
            await safe_send_message(user_id, comments_html, parse_mode=ParseMode.HTML)
    else:
        # If message_to_edit exists, it was likely a "Loading comments..." message.
        # We can delete it now as we are sending new messages for each comment.
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
            medal_str = f" 🏅{commenter_points} Aura" if commenter_points > -1000 else "" # Threshold for display?

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
                confession_owner_id=confession_owner_id or 0 # Ensure not None
            )

            try:
                # --- *** SEPARATE HANDLING FOR STICKER/GIF vs TEXT *** ---
                metadata_text = f"<i>#{seq_num}{display_tag}{admin_info} {ts}</i>" # Added timestamp

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
            await asyncio.sleep(0.1) # Small delay to avoid hitting rate limits when sending many msgs

    # --- Add Comment Button ---
    add_comm_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])

    end_txt = f"--- End of comments for Confession #{confession_id} ---\n" if comment_data_list else ""
    end_txt += "\nYou can add your own comment below:"

    try:
        # Send the final "Add Comment" prompt as a new message
         await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn, parse_mode=ParseMode.HTML)
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
        return # Let the specific state handler (e.g., for rejection reason) take precedence.

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
                 await callback_query.message.edit_text(callback_query.message.html_text + "\n\n-- Confession Not Found --", reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception as e: logging.warning(f"Could not edit admin review msg for non-existent conf {conf_id}: {e}")
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
                    await callback_query.answer("Confession status changed or not found.", show_alert=True); return

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
                         await callback_query.message.edit_text(fail_txt, reply_markup=None, parse_mode=ParseMode.HTML)
                    except Exception: pass
                except Exception as e:
                    logging.error(f"Unexpected error approval Confession {conf_id}: {e}", exc_info=True)
                    await callback_query.answer(f"Unexpected error: {e}. Check logs.", show_alert=True)
                    try:
                         fail_txt = callback_query.message.html_text + "\n\n-- Approval Failed! Check Logs. --"
                         await callback_query.message.edit_text(fail_txt, reply_markup=None, parse_mode=ParseMode.HTML)
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
                f"Reason for rejecting Confession #{conf_id}?\nType your reason, or use /skip (no reason sent to user) or /cancel to abort rejection.",
                reply_markup=reason_keyboard
            )
            logging.info(f"Admin {callback_query.from_user.id} initiated rejection for Confession #{conf_id}, waiting for reason.")

# --- Handler for Admin Rejection Reason - Modified for categories ---
@dp.message(AdminActions.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    if admin_id != ADMIN_ID: return # Should not happen due to FSM, but good practice

    data = await state.get_data()
    conf_id = data.get("rejecting_conf_id")
    admin_review_chat_id = data.get("admin_review_chat_id")
    admin_review_message_id = data.get("admin_review_message_id")
    original_admin_text = data.get("original_admin_text", f"Review Conf #{conf_id}") # Fallback text

    if not all([conf_id, admin_review_chat_id, admin_review_message_id]):
        logging.error(f"Admin {admin_id} sent rejection reason, but state data is incomplete: {data}")
        await message.answer("Error: Context for rejection was lost. Please try rejecting the confession again from the admin review message.", reply_markup=ReplyKeyboardRemove())
        await state.clear(); return

    reason = None; reason_text_for_user = "Your confession was rejected."; reason_text_for_log = "(No reason)"; final_status_text = "Rejected"

    if message.text.startswith("/"):
        command = message.text.split()[0]
        if command == "/skip":
            reason = None; reason_text_for_log = "(Skipped reason)"
            await message.answer("Skipping reason. The user will receive a generic rejection message.", reply_markup=ReplyKeyboardRemove())
        elif command == "/cancel":
            await message.answer("Rejection process cancelled. The confession remains pending.", reply_markup=ReplyKeyboardRemove())
            logging.info(f"Admin {admin_id} cancelled rejection for Confession {conf_id}.")
            await state.clear(); return # Exit, do not proceed with rejection
        else: # Unrecognized command
            await message.answer("Invalid command. Please provide a reason text, or use /skip or /cancel."); return
    else: # Regular text, treat as reason
        reason = message.text.strip()
        if not reason: # Empty or whitespace reason
             await message.answer("Reason cannot be empty. Please provide a reason, or use /skip or /cancel."); return
        if len(reason) > 500:
            await message.answer("Rejection reason is too long (max 500 characters). Please shorten it, or use /skip or /cancel."); return
        reason_text_for_user = f"Your confession was rejected for the following reason:\n\n<i>{html.quote(reason)}</i>"
        reason_text_for_log = reason; final_status_text = "Rejected (Reason Provided)"
        await message.answer("Reason recorded. Proceeding with rejection...", reply_markup=ReplyKeyboardRemove())

    success = False
    async with db.acquire() as conn:
        async with conn.transaction(): # Ensure atomicity
            try:
                # --- *** MODIFIED: Fetch categories array *** ---
                conf_data = await conn.fetchrow(
                    "SELECT user_id, categories, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id
                )
                if not conf_data:
                    logging.warning(f"Admin {admin_id} was rejecting confession {conf_id}, but it disappeared from the database.")
                    await message.answer("Error: Confession not found in the database. It might have been processed or deleted.", reply_markup=ReplyKeyboardRemove())
                    await state.clear(); return
                if conf_data['status'] != 'pending':
                    logging.warning(f"Admin {admin_id} was rejecting confession {conf_id}, but its status was already '{conf_data['status']}'.")
                    await message.answer(f"Error: This confession is no longer pending (current status: {conf_data['status']}). Action aborted.", reply_markup=ReplyKeyboardRemove())
                    # Attempt to update the original admin review message to reflect this
                    try:
                        updated_admin_text = original_admin_text + f"\n\n-- Already {conf_data['status'].capitalize()} (Action Aborted) --"
                        await bot.edit_message_text(
                            chat_id=admin_review_chat_id, message_id=admin_review_message_id,
                            text=updated_admin_text, reply_markup=None, parse_mode=ParseMode.HTML
                        )
                    except Exception as e_edit:
                        logging.warning(f"Could not edit admin message {admin_review_message_id} for already processed confession {conf_id}: {e_edit}")
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
                    if reason: admin_update_text += f"\nReason: {html.quote(reason)}"
                    await bot.edit_message_text(
                        chat_id=admin_review_chat_id, message_id=admin_review_message_id,
                        text=admin_update_text, reply_markup=None, parse_mode=ParseMode.HTML
                    )
                except Exception as e_edit_final:
                    logging.error(f"Error updating admin message {admin_review_message_id} after successful rejection of {conf_id}: {e_edit_final}")

                logging.info(f"Admin {admin_id} rejected Confession #{conf_id}. Reason provided: '{reason_text_for_log}'")
                success = True

            except Exception as e_db_trans:
                logging.error(f"Database transaction error during rejection of Confession {conf_id} by admin {admin_id}: {e_db_trans}", exc_info=True)
                await message.answer(f"An error occurred during the rejection process: {e_db_trans}. Please check logs.", reply_markup=ReplyKeyboardRemove())
                # Do not clear state here, admin might want to retry or needs to know context failed

    if success:
        await message.answer(f"Confession #{conf_id} has been successfully rejected.", reply_markup=ReplyKeyboardRemove())
    # else: an error message would have already been sent
    await state.clear() # Clear state after completion or definite failure

# --- Commenting Flow Handlers ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid browse cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    await callback_query.answer("Loading comments...")
    # Pass the message object to allow editing it to "No comments" or deleting if comments exist
    await show_comments_for_confession(callback_query.from_user.id, conf_id, callback_query.message)

@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid add comment cb data: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        conf_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
        if not conf_exists:
            logging.warning(f"User {callback_query.from_user.id} tried add comment non-existent/unapproved conf {conf_id}.")
            await callback_query.answer("Confession not available or has been removed.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None) # Remove button if conf gone
            except Exception as e: logging.warning(f"Could not remove 'Add Comment' btn for unavailable conf {conf_id}: {e}")
            return
    await state.update_data(confession_id=conf_id, parent_comment_id=None) # parent_comment_id is None for new top-level comments
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        # --- *** MODIFIED: Prompt text for sticker/gif *** ---
        await safe_send_message(callback_query.from_user.id, f"📝 You are adding a comment to Confession #{conf_id}.\n\nPlease send your comment as text, a sticker, or a GIF. You can also type /cancel to abort.")
        await callback_query.answer() # Acknowledge the button press
    except Exception as e:
        logging.warning(f"Could not send 'add comment' prompt user {callback_query.from_user.id} conf {conf_id}: {e}")
        await callback_query.answer("Could not start commenting process. Please try again.", show_alert=True); await state.clear()

# --- *** MODIFIED: receive_comment - Handle Text, Sticker, GIF *** ---
@dp.message(CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
async def receive_comment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id = data.get("confession_id")

    if not conf_id:
        await message.answer("⚠️ Error: No confession context found. Your session might have expired. Please try adding the comment again from the confession view, or type /cancel.");
        # Not clearing state here, user might /cancel
        logging.error(f"State missing confession_id for user {user_id} in receive_comment"); return

    comm_text: Optional[str] = None
    sticker_id: Optional[str] = None
    animation_id: Optional[str] = None
    log_content_type = "Unknown"

    if message.text:
        comm_text = message.text.strip()
        log_content_type = "Text"
        if not comm_text : await message.answer("Comment text cannot be empty. Please provide some text, or type /cancel."); return
        if len(comm_text) > 1000: await message.answer(f"Your comment is too long (max 1000 characters). It currently has {len(comm_text)} characters. Please shorten it, or type /cancel."); return
    elif message.sticker:
        sticker_id = message.sticker.file_id
        log_content_type = "Sticker"
    elif message.animation:
        animation_id = message.animation.file_id
        log_content_type = "GIF"
    else:
        # This case should ideally not be reached due to the F.text | F.sticker | F.animation filter
        await message.answer("Invalid content type. Please send text, a sticker, or a GIF for your comment, or type /cancel."); return

    conf_owner_id = None; new_comm_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
                if not conf_owner_id:
                    # This means the confession was unapproved or deleted between prompting and sending
                    raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found or no longer approved.")

                # Insert with the correct content type
                new_comm_id = await conn.fetchval(
                    """INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id)
                       VALUES ($1, $2, $3, $4, $5, NULL) RETURNING id""", # parent_comment_id is NULL for new comments
                    conf_id, user_id, comm_text, sticker_id, animation_id
                )
                if not new_comm_id:
                    raise Exception("Failed to get new comment ID after insert.")

        await message.answer("💬 Your comment has been added successfully!");
        logging.info(f"User {user_id} added {log_content_type} comment (ID: {new_comm_id}) to Confession #{conf_id}");
        await update_channel_post_button(conf_id) # Update the comment count on the channel post

        # Notify confession author about the new comment (if they are not the one commenting)
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

            notification_to_author = (f"💬 A new comment has been posted on your Confession #{conf_id}.\n\n"
                                      f"<i>{preview}</i>\n\n"
                                      f"<a href='{link}'>Click here to view all comments.</a>")
            await safe_send_message(conf_owner_id, notification_to_author, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): # Log if bot_info is missing for link generation
            logging.warning(f"Cannot generate notification link for confession author {conf_owner_id} - bot_info is missing.")

        # Show updated comments view to the user who just commented
        await show_comments_for_confession(user_id, conf_id)

    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
         if "one_content_type" in str(e): # Specific check for our constraint
             logging.error(f"Integrity error (one_content_type) saving {log_content_type} comment for conf {conf_id} by {user_id}: {e}")
             await message.answer("❌ An internal error occurred while saving your comment (content type issue). Please try again or contact support if it persists.")
         else:
             logging.error(f"Database integrity error saving {log_content_type} comment for conf {conf_id} by {user_id}: {e}")
             await message.answer("❌ An internal database error occurred while saving your comment. Please try again or contact support if it persists.")
    except asyncpg.exceptions.ForeignKeyViolationError:
        logging.warning(f"User {user_id} attempted to add a {log_content_type} comment to Confession #{conf_id}, but it was not found or no longer approved.")
        await message.answer("⚠️ Cannot add comment. The confession may have been removed or is no longer approved. Please check and try again, or type /cancel.")
    except Exception as e:
        logging.error(f"Unexpected error saving {log_content_type} comment for Confession #{conf_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ An unexpected internal error occurred while saving your comment. Please try again or contact support if it persists.")
    finally:
        await state.clear() # Clear state after processing

# --- Reply Flow Handlers ---
@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try: parent_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid reply cb: {callback_query.data}"); await callback_query.answer("Invalid data for reply.", show_alert=True); return
    # msg_id_reply_to = callback_query.message.message_id # ID of the metadata message # Not directly used anymore

    async with db.acquire() as conn:
        # --- *** MODIFIED: Fetch all content types for preview *** ---
        comm_data = await conn.fetchrow("SELECT confession_id, text, sticker_file_id, animation_file_id, user_id FROM comments WHERE id = $1", parent_id)
        if not comm_data:
            logging.warning(f"User {callback_query.from_user.id} tried reply non-existent parent comment {parent_id}.")
            await callback_query.answer("The comment you are trying to reply to no longer exists.", show_alert=True)
            # Try to remove buttons from the metadata message if it still exists
            try: await callback_query.message.edit_reply_markup(reply_markup=None)
            except Exception as e_edit: logging.warning(f"Could not remove buttons for deleted parent comment {parent_id} (metadata msg {callback_query.message.message_id}): {e_edit}")
            return

    conf_id = comm_data['confession_id']
    parent_comment_author_id = comm_data['user_id']

    # Prevent replying to self
    if callback_query.from_user.id == parent_comment_author_id:
        await callback_query.answer("You cannot reply to your own comment.", show_alert=True)
        return

    # --- *** MODIFIED: Generate preview for text/sticker/gif *** ---
    preview = ""
    if comm_data['text']:
        preview = html.quote(comm_data['text'][:80]) + ('...' if len(comm_data['text']) > 80 else '')
    elif comm_data['sticker_file_id']:
        preview = "[Sticker]"
    elif comm_data['animation_file_id']:
        preview = "[GIF]"
    else:
        preview = "[Unknown Content]" # Should not happen


    await state.update_data(confession_id=conf_id, parent_comment_id=parent_id); # message_id_to_reply_to removed
    await state.set_state(CommentForm.waiting_for_reply)
    try:
        # --- *** MODIFIED: Prompt text for sticker/gif *** ---
        prompt_message = (f"📝 You are replying to the comment:\n<i>\"{preview}\"</i>\n\n"
                          f"Please send your reply as text, a sticker, or a GIF. You can also type /cancel to abort.");
        await safe_send_message(callback_query.from_user.id, prompt_message, parse_mode=ParseMode.HTML);
        await callback_query.answer() # Acknowledge button press
    except Exception as e:
        logging.warning(f"Could not send reply prompt to user {callback_query.from_user.id} for parent comment {parent_id}: {e}");
        await callback_query.answer("Could not start the reply process. Please try again.", show_alert=True);
        await state.clear()

# --- *** MODIFIED: receive_reply - Handle Text, Sticker, GIF *** ---
@dp.message(CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)
async def receive_reply(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id = data.get("confession_id")
    parent_id = data.get("parent_comment_id")

    if not conf_id or not parent_id:
        await message.answer("⚠️ Error: Reply context lost. Your session might have expired. Please try replying again or type /cancel.");
        # Not clearing state, user might /cancel
        logging.error(f"State missing fields (conf_id or parent_id) for user {user_id} in receive_reply: {data}"); return

    reply_text: Optional[str] = None
    sticker_id: Optional[str] = None
    animation_id: Optional[str] = None
    log_content_type = "Unknown"

    if message.text:
        reply_text = message.text.strip()
        log_content_type = "Text Reply"
        if not reply_text: await message.answer("Reply text cannot be empty. Please provide some text, or type /cancel."); return
        if len(reply_text) > 1000: await message.answer(f"Your reply is too long (max 1000 characters). It currently has {len(reply_text)} characters. Please shorten it, or type /cancel."); return
    elif message.sticker:
        sticker_id = message.sticker.file_id
        log_content_type = "Sticker Reply"
    elif message.animation:
        animation_id = message.animation.file_id
        log_content_type = "GIF Reply"
    else:
        # Should not be reached due to filter
        await message.answer("Invalid content type for reply. Please send text, a sticker, or a GIF, or type /cancel."); return

    new_comm_id = None; parent_owner_id = None; conf_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1 FOR UPDATE", parent_id); # Lock parent comment row
                if not parent_data:
                    # Parent comment was deleted between prompt and reply submission
                    await message.answer("⚠️ The comment you were replying to has been deleted. Your reply cannot be sent.")
                    await state.clear(); return
                parent_owner_id = parent_data['user_id']

                # Prevent replying to self (double check, though prompt should prevent it)
                if user_id == parent_owner_id:
                    await message.answer("You cannot reply to your own comment. Action cancelled.")
                    await state.clear(); return

                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status='approved'", conf_id);
                if not conf_owner_id:
                    # Confession was unapproved or deleted
                    await message.answer("⚠️ The confession this comment belongs to has been removed or is no longer approved. Your reply cannot be sent.")
                    await state.clear(); return

                # Insert the reply with correct content type
                new_comm_id = await conn.fetchval(
                    """INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id)
                       VALUES ($1, $2, $3, $4, $5, $6) RETURNING id""",
                    conf_id, user_id, reply_text, sticker_id, animation_id, parent_id
                )
                if not new_comm_id:
                    raise Exception("Failed to get new reply ID after insert.")

        logging.info(f"User {user_id} added {log_content_type} (ID: {new_comm_id}) as reply to comment {parent_id} on Confession #{conf_id}")
        await update_channel_post_button(conf_id) # Update comment count on channel post

        await message.answer("↪️ Your reply has been sent successfully!")
        await show_comments_for_confession(user_id, conf_id) # Show updated list to the replier

        # Notify Parent Comment Author about the reply
        global bot_info
        if parent_owner_id and parent_owner_id != user_id and bot_info and bot_info.username: # Ensure not notifying self
             logging.info(f"Notifying parent comment author {parent_owner_id} of reply {new_comm_id} by user {user_id}")
             link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
             # --- *** MODIFIED: Notification preview for reply *** ---
             preview_for_notification = ""
             if reply_text:
                 preview_for_notification = html.quote(reply_text[:150]) + ('...' if len(reply_text) > 150 else '')
             elif sticker_id:
                 preview_for_notification = "[Sticker]"
             elif animation_id:
                 preview_for_notification = "[GIF]"

             replier_points = await get_user_points(user_id)
             medal_str = f" 🏅{replier_points} Aura" if replier_points > -1000 else "" # Use configured threshold
             tag = "(Author)" if user_id == conf_owner_id else "Anonymous" # Tag if replier is confession author

             notification_to_parent_author = (f"↪️ Someone ({tag}{medal_str}) replied to your comment on Confession #{conf_id}.\n\n"
                                              f"<i>{preview_for_notification}</i>\n\n"
                                              f"<a href='{link}'>Click here to view the reply and other comments.</a>");
             await safe_send_message(parent_owner_id, notification_to_parent_author, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): # Log if bot_info missing for link
              logging.warning(f"Cannot generate notification link for parent comment author {parent_owner_id} - bot_info is missing.")

    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
         if "one_content_type" in str(e):
             logging.error(f"Integrity error (one_content_type) saving {log_content_type} reply to comment {parent_id} by {user_id}: {e}")
             await message.answer("❌ An internal error occurred while saving your reply (content type issue). Please try again.")
         else:
             logging.error(f"Database integrity error saving {log_content_type} reply to comment {parent_id} by {user_id}: {e}")
             await message.answer("❌ An internal database error occurred while saving your reply. Please try again.")
    except asyncpg.exceptions.ForeignKeyViolationError as e:
        logging.warning(f"Foreign key violation during reply save by user {user_id} to parent comment {parent_id}: {e}")
        await message.answer("⚠️ Cannot add reply. The original comment or confession may have been deleted or is no longer available.")
    except Exception as e:
        logging.error(f"Unexpected error saving {log_content_type} in database transaction for parent comment {parent_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ An unexpected internal error occurred while saving your reply. Please try again.")
    finally:
        await state.clear() # Clear state after processing

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
                    await callback_query.answer("You cannot react to your own comment.", show_alert=True); return

                existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", comm_id, user_id)

                if existing:
                    if existing == r_type: # Remove reaction
                        await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id)
                        action = f"Removed {r_type}"; alert = f"{r_type.capitalize()} removed"
                        point_delta = -POINTS_PER_LIKE_RECEIVED if r_type == 'like' else -POINTS_PER_DISLIKE_RECEIVED # Reverse points
                    else: # Change reaction type
                        await conn.execute("UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", r_type, comm_id, user_id)
                        action = f"Changed to {r_type}"; alert = f"Reaction changed to {r_type}"
                        # Calculate point change: remove old points, add new points
                        old_points_effect = -POINTS_PER_LIKE_RECEIVED if existing == 'like' else -POINTS_PER_DISLIKE_RECEIVED
                        new_points_effect = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED
                        point_delta = old_points_effect + new_points_effect
                else: # Add new reaction
                    await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, user_id, r_type)
                    action = f"Added {r_type}"; alert = f"{r_type.capitalize()} added"
                    point_delta = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED

                if point_delta != 0 and comm_uid is not None: # Ensure comm_uid is known
                    await update_user_points(conn, comm_uid, point_delta)
                    logging.info(f"Updated points for commenter {comm_uid} by {point_delta} due to reaction from {user_id} on comment {comm_id}")

                # Rebuild keyboard with updated counts
                kbd = await build_comment_keyboard(comm_id, comm_uid, viewer_id, conf_owner_id);
                logging.info(f"User {user_id} performed action '{action}' on comment {comm_id}. Keyboard rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"Foreign key violation during reaction update for comment {comm_id} by user {user_id}. Comment or confession might be deleted.")
                await callback_query.answer("Comment not found or no longer available.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None) # Try to clean up buttons
                except Exception: pass
                return
            except Exception as db_err:
                logging.error(f"Database error during reaction processing for comment {comm_id} by user {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("A database error occurred while processing your reaction.", show_alert=True)
                return

    # After transaction, if successful, update message markup
    if kbd and action != "none":
        try:
            # This edits the metadata message for stickers/gifs, or the main message for text comments
            await callback_query.message.edit_reply_markup(reply_markup=kbd)
            await callback_query.answer(alert) # Show brief feedback to user
            logging.info(f"Successfully updated markup for comment {comm_id} after reaction '{action}'")
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.info(f"Markup for comment {comm_id} was not modified (already up-to-date).");
                await callback_query.answer(alert + " (No visual change)")
            elif "message to edit not found" in err_str:
                logging.warning(f"Message not found for reaction update on comment {comm_id}. It might have been deleted by the user.");
                await callback_query.answer(alert + " (Counts updated, but display message was gone)", show_alert=False)
            elif "query is too old" in err_str: # Or other transient errors
                logging.warning(f"Callback query was too old for reaction update on comment {comm_id}.");
                await callback_query.answer(alert + " (Counts updated, display may be stale)", show_alert=False)
            else:
                logging.error(f"Telegram API error updating reaction markup for comment {comm_id}: {e}");
                await callback_query.answer("Error updating the display after reaction.", show_alert=True)
        except Exception as e:
            logging.error(f"Unexpected error updating reaction markup for comment {comm_id}: {e}", exc_info=True);
            await callback_query.answer("An unexpected error occurred updating the display.", show_alert=True)
    elif action != "none" and not kbd: # Should not happen if DB ops were successful
        logging.error(f"Reaction action '{action}' for comment {comm_id} was processed, but keyboard (kbd) is None. This indicates an issue in build_comment_keyboard or logic flow.")
        await callback_query.answer(alert + " (Internal Error: Could not update display)", show_alert=True)


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
            await callback_query.answer("You have already reported this comment.", show_alert=True); return

        # --- *** MODIFIED: Fetch comment content for preview *** ---
        comment_data = await conn.fetchrow("SELECT text, sticker_file_id, animation_file_id, user_id FROM comments WHERE id = $1", comment_id)
        if not comment_data:
            await callback_query.answer("Comment not found or has been deleted.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None) # Clean up buttons
            except Exception: pass
            return

        # Prevent reporting own comment
        if comment_data['user_id'] == reporter_user_id:
            await callback_query.answer("You cannot report your own comment.", show_alert=True)
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
         snippet = "[Error: Unknown Content Type]" # Should not happen

    confirm_text = (f"Are you sure you want to report the following comment for admin review?\n\n"
                    f"<i>\"{snippet}\"</i>\n\n"
                    f"This action cannot be undone.")
    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Yes, Report", callback_data=f"report_execute_{comment_id}"),
            InlineKeyboardButton(text="❌ No, Cancel", callback_data=f"report_cancel_{comment_id}")
        ]
    ])
    try:
        # Send confirmation as a new message in PM to avoid cluttering the comment view
        await safe_send_message(reporter_user_id, confirm_text, reply_markup=confirm_keyboard, parse_mode=ParseMode.HTML)
        await callback_query.answer() # Acknowledge the initial "Report" button press
    except Exception as e:
        logging.error(f"Error sending report confirmation for comment {comment_id} to user {reporter_user_id}: {e}")
        await callback_query.answer("Could not start the report process. Please try again.", show_alert=True)

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
                    "SELECT user_id, confession_id, text, sticker_file_id, animation_file_id FROM comments WHERE id = $1 FOR UPDATE", # Lock row
                     comment_id
                )
                if not comment_data:
                    await callback_query.answer("Comment not found or was deleted before reporting.", show_alert=True)
                    try: await callback_query.message.edit_text("Report failed: Comment no longer exists.", reply_markup=None) # Edit the confirmation message
                    except Exception: pass
                    return

                reported_user_id = comment_data['user_id']
                confession_id = comment_data['confession_id']
                comment_text = comment_data['text']
                sticker_id = comment_data['sticker_file_id']
                animation_id = comment_data['animation_file_id']

                # Prevent reporting own comment (double check)
                if reported_user_id == reporter_user_id:
                    await callback_query.answer("You cannot report your own comment.", show_alert=True)
                    try: await callback_query.message.edit_text("Action cancelled: Cannot report own comment.", reply_markup=None)
                    except Exception: pass
                    return

                already_reported = await conn.fetchval("SELECT 1 FROM reports WHERE comment_id = $1 AND reporter_user_id = $2", comment_id, reporter_user_id)
                if already_reported: # Should be caught by confirm_callback, but double check
                    await callback_query.answer("You have already reported this comment.", show_alert=True)
                    try: await callback_query.message.edit_text("Report already submitted.", reply_markup=None)
                    except Exception: pass
                    return

                report_id = await conn.fetchval(
                    """INSERT INTO reports (comment_id, reporter_user_id, reported_user_id, status)
                       VALUES ($1, $2, $3, 'pending') RETURNING id""",
                    comment_id, reporter_user_id, reported_user_id
                )
                if not report_id:
                    raise Exception("Failed to insert report into database or get report ID.")

                logging.info(f"User {reporter_user_id} successfully reported comment {comment_id} (authored by {reported_user_id}). Report ID: {report_id}")

            except asyncpg.exceptions.UniqueViolationError: # Should be caught by previous check, but good safeguard
                await callback_query.answer("You have already reported this comment.", show_alert=True)
                try: await callback_query.message.edit_text("Report already submitted.", reply_markup=None)
                except Exception: pass
                return
            except Exception as e:
                logging.error(f"Database error while saving report for comment {comment_id} by user {reporter_user_id}: {e}", exc_info=True)
                await callback_query.answer("An error occurred while saving your report. Please try again.", show_alert=True)
                try: await callback_query.message.edit_text("Report submission failed due to a database error.", reply_markup=None)
                except Exception: pass
                return

    # --- If transaction successful, notify admin and update reporter's confirmation message ---
    if report_id and reported_user_id and confession_id and bot_info: # bot_info needed for link
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
            snippet = "[Error: Unknown Content Type]" # Should not happen
            content_desc = "Content"

        confession_link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
        admin_notification_message = (
            f"⚠️ <b>New Comment Report (Report ID: {report_id})</b> ⚠️\n\n"
            f"<b>Confession Link:</b> <a href='{confession_link}'>View Confession #{confession_id}</a>\n"
            f"<b>Comment ID (DB):</b> <code>{comment_id}</code>\n"
            f"<b>Reported Comment's {content_desc}:</b>\n<i>{snippet}</i>\n\n"
            f"<b>Reported User ID:</b> <code>{reported_user_id}</code> (Use /id <code>{reported_user_id}</code> for info)\n"
            f"<b>Reporter User ID:</b> <code>{reporter_user_id}</code> (Use /id <code>{reporter_user_id}</code> for info)\n\n"
            f"Please review this report and take appropriate action."
        )
        await safe_send_message(ADMIN_ID, admin_notification_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        try:
            # Edit the confirmation message sent to the reporter
            await callback_query.message.edit_text(
                "✅ Your report for the comment has been submitted successfully. The admin has been notified.",
                reply_markup=None # Remove confirmation buttons
            )
            await callback_query.answer("Report sent.", show_alert=False) # Show brief feedback
        except Exception as e_edit_confirm:
            # If editing fails (e.g., message deleted by user), log it but don't stop. Report is already sent.
            logging.warning(f"Could not edit report confirmation message {callback_query.message.message_id} for user {reporter_user_id}: {e_edit_confirm}")
            # Optionally send a new message if edit fails and it's important for user to know
            # await safe_send_message(reporter_user_id, "✅ Report submitted successfully. Admin notified.")
            await callback_query.answer("Report sent.", show_alert=False)
    elif not bot_info:
        logging.error(f"Report {report_id} created, but bot_info is None. Cannot generate confession link for admin notification.")
        # Edit reporter's message to indicate success but potential admin notification issue
        try:
            await callback_query.message.edit_text("✅ Report submitted. Admin will be notified (link generation may be affected).", reply_markup=None)
            await callback_query.answer("Report sent (admin link issue).", show_alert=False)
        except Exception: pass


# 3. Cancel Report (Unchanged)
@dp.callback_query(F.data.startswith("report_cancel_"))
async def report_cancel_callback(callback_query: types.CallbackQuery):
    try:
        # Edit the confirmation message to show cancellation
        await callback_query.message.edit_text("Report process cancelled. The comment was not reported.", reply_markup=None)
        await callback_query.answer("Report cancelled.")
    except Exception as e:
        # If editing message fails (e.g. user deleted it), just log and acknowledge
        logging.warning(f"Error cancelling report (editing confirmation message {callback_query.message.message_id}): {e}")
        await callback_query.answer("Report cancelled.")


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
            if not comm_data:
                await callback_query.answer("Comment or confession not found, or confession is no longer approved.", show_alert=True);
                return

            commenter_uid = comm_data['comm_uid']; conf_id = comm_data['conf_id']; conf_owner_id = comm_data['conf_owner_id'];

            # --- *** MODIFIED: Generate preview *** ---
            preview = ""
            if comm_data['comm_txt']:
                preview = html.quote(comm_data['comm_txt'][:100]) + ('...' if len(comm_data['comm_txt']) > 100 else '')
            elif comm_data['sticker_file_id']:
                preview = "[Sticker]"
            elif comm_data['animation_file_id']:
                preview = "[GIF]"
            else:
                preview = "[Unknown Content]" # Should not happen


            if req_uid != conf_owner_id:
                logging.warning(f"User {req_uid} (not author) tried to request contact for comment {comm_id} on confession {conf_id} owned by {conf_owner_id}.");
                await callback_query.answer("You can only request contact for comments on your own confessions.", show_alert=True);
                return
            if req_uid == commenter_uid:
                await callback_query.answer("You cannot request to contact yourself (the author of this comment).", show_alert=True);
                return

            # Check if a non-denied request already exists
            existing_request = await conn.fetchrow(
                "SELECT id, status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2",
                comm_id, req_uid
            )

            if existing_request:
                if existing_request['status'] in ('pending', 'approved', 'approved_no_username'):
                    await callback_query.answer(f"A contact request for this comment is already '{existing_request['status']}'.", show_alert=True);
                    return
                # If status is 'denied', we allow a new request by updating the existing one (see ON CONFLICT below)

            request_id = None
            try:
                # Insert new request or update a previously 'denied' one to 'pending'
                request_id = await conn.fetchval(
                    """INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status)
                       VALUES ($1, $2, $3, $4, 'pending')
                       ON CONFLICT (comment_id, requester_user_id) DO UPDATE
                       SET status = 'pending', updated_at = CURRENT_TIMESTAMP
                       WHERE contact_requests.status = 'denied' OR contact_requests.status = 'pending' -- Also allow re-pending a pending one if user clicks again
                       RETURNING id""",
                    conf_id, comm_id, req_uid, commenter_uid
                )
                if not request_id: # This can happen if ON CONFLICT ... WHERE condition isn't met (e.g., status was 'approved')
                    # Re-fetch to tell user the current status if insert/update didn't return ID
                    current_status = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2", comm_id, req_uid)
                    logging.warning(f"Contact request insert/update for comment {comm_id} by user {req_uid} returned no ID. Current status: {current_status}")
                    await callback_query.answer(f"Request already exists (Status: {current_status or 'Unknown'}). No new request sent.", show_alert=True)
                    return
            except Exception as insert_err:
                logging.error(f"Failed to insert/update contact request from user {req_uid} to commenter {commenter_uid} for comment {comm_id}: {insert_err}", exc_info=True);
                await callback_query.answer("Failed to save your contact request due to a database error.", show_alert=True);
                return # Do not proceed to notify if DB failed

            # Notify the commenter
            notification_to_commenter = (f"🤝 The author of Confession #{conf_id} would like to contact you regarding your comment:\n\n"
                                        f"<i>\"{preview}\"</i>\n\n"
                                        f"Do you approve sharing your Telegram profile contact (username, if set) with the author? "
                                        f"This allows them to message you directly. Your User ID is never shared.");
            approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Approve & Share Username", callback_data=f"approve_contact_{request_id}")],
                [InlineKeyboardButton(text="❌ Deny Request", callback_data=f"deny_contact_{request_id}")]
            ]);

            # Send notification to the commenter's PM
            sent_to_commenter = await safe_send_message(commenter_uid, notification_to_commenter, reply_markup=approval_keyboard, parse_mode=ParseMode.HTML)

            if sent_to_commenter:
                await callback_query.answer("✅ Your contact request has been sent to the commenter.", show_alert=False);
                logging.info(f"Contact request ID {request_id} (for comment {comm_id}) sent from author {req_uid} to commenter {commenter_uid}.")
            else:
                # If notification fails (e.g., commenter blocked bot), we should ideally roll back the DB record
                # or mark it as 'failed_to_notify'. For simplicity here, we log and inform author.
                # A more robust solution would use a transaction that commits only after successful notification.
                # For now, the request exists in DB but commenter wasn't notified.
                logging.warning(f"Failed to send contact request notification (ID {request_id}) to commenter {commenter_uid}. They may have blocked the bot. Request is in DB.");
                await conn.execute("UPDATE contact_requests SET status='failed_to_notify' WHERE id=$1", request_id) # Mark as failed
                await callback_query.answer("⚠️ Your contact request was saved, but we could not notify the commenter (they may have blocked the bot).", show_alert=True);
                # No explicit rollback here, but status update reflects issue.

# --- Contact Response Handler (Unchanged logic) ---
def is_contact_response_callback(data: str) -> bool:
    if not isinstance(data, str): return False; parts = data.split("_"); return len(parts) == 3 and parts[0] in ('approve', 'deny') and parts[1] == 'contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data))
async def handle_contact_response(callback_query: types.CallbackQuery):
    try: action, _, req_id_str = callback_query.data.split("_"); req_id = int(req_id_str); responder_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid contact resp cb: {callback_query.data}"); await callback_query.answer("Invalid request data.", show_alert=True); return

    new_db_status = 'approved' if action == 'approve' else 'denied';
    edit_status_for_ui = "" # For updating the responder's message

    async with db.acquire() as conn:
        async with conn.transaction(): # Ensure atomic update and notification
            # Fetch request and lock it
            req_data = await conn.fetchrow(
                "SELECT id, requester_user_id, requested_user_id, status, confession_id, comment_id FROM contact_requests WHERE id = $1 FOR UPDATE",
                 req_id
            )

            if not req_data:
                await callback_query.answer("This contact request was not found. It might have been withdrawn or deleted.", show_alert=True)
                try: await callback_query.message.delete() # Clean up the responder's message
                except Exception: pass
                return

            # Verify the responder is the correct user
            if responder_uid != req_data['requested_user_id']:
                logging.warning(f"User {responder_uid} (not the requested user {req_data['requested_user_id']}) tried to respond to contact request {req_id}.");
                await callback_query.answer("This contact request is not for you.", show_alert=True);
                return

            # Check if already processed
            if req_data['status'] != 'pending' and req_data['status'] != 'failed_to_notify': # Allow responding if failed_to_notify
                current_status_display = req_data['status'].replace('_', ' ').capitalize()
                await callback_query.answer(f"This contact request has already been '{current_status_display}'.", show_alert=True)
                try:
                    orig_txt = callback_query.message.html_text
                    # Append status if not already there (to avoid multiple status lines)
                    if f"Status: {current_status_display}" not in orig_txt:
                         final_txt = f"{orig_txt}\n\n<b>Status: {current_status_display}</b>"
                         await callback_query.message.edit_text(final_txt, reply_markup=None, parse_mode=ParseMode.HTML)
                except Exception: pass
                return

            author_to_notify_uid = req_data['requester_user_id'];
            conf_id_for_notif = req_data['confession_id'];
            comm_id_for_notif = req_data['comment_id'] # For context in notification
            notification_to_author = ""

            if new_db_status == 'approved':
                commenter_username = None
                try:
                    # Fetch responder's (commenter's) current chat info to get username
                    responder_chat_info = await bot.get_chat(responder_uid)
                    commenter_username = responder_chat_info.username
                except Exception as e_chat:
                    logging.warning(f"Could not fetch chat info for user {responder_uid} during contact approval for request {req_id}: {e_chat}")
                    # Proceed without username if fetch fails

                if commenter_username:
                    await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                    notification_to_author = (f"✅ Contact Approved! The commenter has shared their contact.\n\n"
                                              f"For your request regarding Confession #{conf_id_for_notif} (Comment ID approx. {comm_id_for_notif}), "
                                              f"you can now contact the commenter at: @{html.quote(commenter_username)}")
                    await callback_query.answer("Approved! Your username has been shared with the confession author.")
                    logging.info(f"Contact request {req_id} approved by commenter {responder_uid}. Username @{commenter_username} sent to author {author_to_notify_uid}.")
                    edit_status_for_ui = 'Approved (Username Shared)'
                else: # Approved, but no public username
                    new_db_status = 'approved_no_username' # Update the status to reflect this
                    await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", new_db_status, req_id)
                    notification_to_author = (f"⚠️ Contact Approved (No Public Username).\n\n"
                                              f"For your request regarding Confession #{conf_id_for_notif} (Comment ID approx. {comm_id_for_notif}), "
                                              f"the commenter approved contact, but they do not have a public Telegram username set. "
                                              f"Unfortunately, direct contact via username is not possible.")
                    await callback_query.answer("Approved! However, you don't have a public username, so the author cannot contact you directly via username.", show_alert=True)
                    logging.info(f"Contact request {req_id} approved by commenter {responder_uid}, but no username. Author {author_to_notify_uid} notified.")
                    edit_status_for_ui = 'Approved (No Public Username)'
            else: # Denied
                await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                notification_to_author = (f"❌ Contact Denied. The commenter has declined your request.\n\n"
                                          f"For your request regarding Confession #{conf_id_for_notif} (Comment ID approx. {comm_id_for_notif}), "
                                          f"the commenter chose not to share their contact details.")
                await callback_query.answer("Denied. Your contact details will not be shared.")
                logging.info(f"Contact request {req_id} denied by commenter {responder_uid}. Author {author_to_notify_uid} notified.")
                edit_status_for_ui = 'Denied'

            # Send notification to the original requester (confession author)
            await safe_send_message(author_to_notify_uid, notification_to_author, parse_mode=ParseMode.HTML)

            # Update the responder's message to reflect their choice
            try:
                original_responder_message_text = callback_query.message.html_text
                # Append status if not already there (to avoid multiple status lines)
                if f"Status: {edit_status_for_ui}" not in original_responder_message_text:
                    final_responder_message_text = f"{original_responder_message_text}\n\n<b>Status: {edit_status_for_ui}</b>"
                    await callback_query.message.edit_text(final_responder_message_text, reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception as e_edit_responder:
                logging.warning(f"Could not edit responder's ({responder_uid}) notification message {callback_query.message.message_id} for request {req_id}: {e_edit_responder}")

# --- View Contact Requests Handler (Modified for comment content preview) ---
@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    try: _, _, conf_id_str = callback_query.data.split("_", 2); conf_id = int(conf_id_str); viewer_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid view reqs cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return

    async with db.acquire() as conn:
        conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id)

    if not conf_owner_id:
        await callback_query.answer("Confession not found.", show_alert=True); return
    if viewer_uid != conf_owner_id:
        await callback_query.answer("You can only view contact requests for your own confessions.", show_alert=True); return

    async with db.acquire() as conn:
        # --- *** MODIFIED: Fetch comment content type for preview and commenter's username if approved *** ---
        contact_requests_data = await conn.fetch(
             """SELECT cr.id as request_id, cr.comment_id, cr.status, cr.updated_at,
                       c.text as comment_text, c.sticker_file_id, c.animation_file_id,
                       cr.requested_user_id
                FROM contact_requests cr
                JOIN comments c ON cr.comment_id = c.id
                WHERE cr.confession_id = $1 AND cr.requester_user_id = $2
                ORDER BY cr.updated_at DESC""",
             conf_id, viewer_uid
        )
    if not contact_requests_data:
        await callback_query.answer("No contact requests found for this confession yet.", show_alert=False); return

    response_parts = [f"<b>Contact Requests Status for Confession #{conf_id}</b>\n"];
    for req in contact_requests_data:
        # --- *** MODIFIED: Generate preview *** ---
        preview = ""
        if req['comment_text']:
            preview = html.quote(req['comment_text'][:60]) + ('...' if len(req['comment_text']) > 60 else '')
        elif req['sticker_file_id']:
            preview = "[Sticker]"
        elif req['animation_file_id']:
            preview = "[GIF]"
        else:
            preview = "[Unknown Content]" # Should not happen

        status_display = req['status'].replace('_', ' ').capitalize()
        last_updated_time = req['updated_at'].strftime("%Y-%m-%d %H:%M")
        commenter_user_id_for_request = req['requested_user_id'] # User ID of the commenter
        status_emoji = {"pending": "❓", "approved": "✅", "denied": "❌", "approved_no_username": "⚠️", "failed_to_notify": "🚫"}.get(req['status'], "❓")

        request_entry = (
            f"🔹 <b>Request to Commenter (User ID: <code>{commenter_user_id_for_request}</code>)</b>\n"
            f"   <i>Regarding Comment: \"{preview}\"</i>\n"
            f"   <b>Status:</b> {status_emoji} {status_display}\n"
            f"   <b>Last Update:</b> {last_updated_time}"
        )

        if req['status'] == 'approved':
            try:
                # Fetch the commenter's username if the request was approved and username was shared
                commenter_chat_info = await bot.get_chat(commenter_user_id_for_request)
                if commenter_chat_info and commenter_chat_info.username:
                    request_entry += f"\n   <b>Contact:</b> @{html.quote(commenter_chat_info.username)}"
                else: # Should have been 'approved_no_username' if no username, but handle defensively
                    request_entry += "\n   <b>Contact:</b> (Approved, but commenter has no public username)"
            except Exception as e_fetch_uname:
                logging.warning(f"Error fetching username for approved contact request {req['request_id']} (commenter UID {commenter_user_id_for_request}): {e_fetch_uname}");
                request_entry += "\n   <b>Contact:</b> (Error fetching username)"
        elif req['status'] == 'approved_no_username':
             request_entry += "\n   <b>Contact:</b> (Approved, but commenter has no public username)"
        elif req['status'] == 'failed_to_notify':
            request_entry += "\n   <i>Note: We could not deliver the request notification to the commenter.</i>"


        response_parts.append(request_entry)

    final_response_text = "\n\n".join(response_parts);
    if len(final_response_text) > 4096: # Telegram message length limit
        final_response_text = final_response_text[:4090] + "\n\n...(truncated due to length)"

    await safe_send_message(viewer_uid, final_response_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True);
    await callback_query.answer() # Acknowledge the button press

# --- Fallback Handler (Unchanged) ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    logging.debug(f"Received non-command text from user {message.from_user.id} outside state: '{message.text[:50]}...'")
    await message.reply("Hi! 👋 Use /confess to share anonymously or /help for commands.")

# --- Main Execution ---
async def main():
    bot_polling_task = None
    dummy_server_task = None
    try:
        await setup() # Setup DB and bot_info
        if db and bot_info:
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
            except Exception as e: logging.warning(f"Could not set admin commands for ADMIN_ID {ADMIN_ID}: {e}")

            logging.info("Registering handlers...")
            # Registration order matters for overlapping filters

            # Commands
            dp.message.register(start, Command("start")) # Handles deep links and general start
            dp.message.register(show_help, Command("help"), StateFilter(None))
            dp.message.register(show_privacy, Command("privacy"), StateFilter(None))
            dp.message.register(get_user_info_command, Command("id")) # Admin only, checked inside handler
            dp.message.register(start_confession, Command("confess"), StateFilter(None))
            dp.message.register(cancel_any_state, Command("cancel"), StateFilter('*')) # Generic cancel for any state

            # FSM Handlers (Confession Submission)
            dp.callback_query.register(handle_category_selection, StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
            dp.message.register(receive_confession_text, ConfessionForm.waiting_for_text, F.text)

            # FSM Handlers (Commenting and Replying)
            dp.message.register(receive_comment, CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
            dp.message.register(receive_reply, CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)

            # FSM Handlers (Contact Admin Flow)
            dp.callback_query.register(start_contact_admin_callback, F.data == "contact_admin_start", StateFilter(None))
            dp.message.register(receive_admin_message, ContactAdminForm.waiting_for_message, F.text)

            # FSM Handlers (Admin Actions - e.g., Rejection Reason)
            dp.message.register(receive_rejection_reason, AdminActions.waiting_for_rejection_reason, F.text)

            # Callback Query Handlers (Non-FSM, for specific actions)
            dp.callback_query.register(admin_action, lambda c: is_confession_action_callback(c.data)) # Admin approve/reject prompt
            dp.callback_query.register(browse_comments_action, F.data.startswith("browse_"))
            dp.callback_query.register(add_comment_prompt, F.data.startswith("add_"))
            dp.callback_query.register(reply_comment_prompt, F.data.startswith("reply_"))
            dp.callback_query.register(handle_reaction, F.data.startswith("react_"))
            # Report comment callbacks
            dp.callback_query.register(report_confirm_callback, F.data.startswith("report_confirm_"))
            dp.callback_query.register(report_execute_callback, F.data.startswith("report_execute_"))
            dp.callback_query.register(report_cancel_callback, F.data.startswith("report_cancel_"))
            # Contact request callbacks
            dp.callback_query.register(handle_request_contact, F.data.startswith("req_contact_"))
            dp.callback_query.register(handle_contact_response, lambda c: is_contact_response_callback(c.data))
            dp.callback_query.register(view_contact_requests, F.data.startswith("view_reqs_"))

            # Message Handlers (Non-FSM, Non-Command)
            # IMPORTANT: Admin reply handler should be registered before more generic text handlers if it relies on F.reply_to_message
            # However, its FSM check `current_admin_state is not None` should prevent interference with AdminActions.waiting_for_rejection_reason
            dp.message.register(handle_admin_reply, F.from_user.id == ADMIN_ID, F.reply_to_message)

            # Fallback for non-command text outside any state MUST be last for general text messages
            dp.message.register(handle_text_without_state, StateFilter(None), F.text & ~F.text.startswith('/'))

            logging.info("Handler registration complete.")

            # Create tasks for the bot polling and the dummy HTTP server
            bot_polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True), name="BotPollingTask")
            tasks_to_run = [bot_polling_task]

            if HTTP_PORT_STR: # Only start dummy server if PORT is configured
                dummy_server_task = asyncio.create_task(start_dummy_server(), name="DummyHttpServerTask")
                tasks_to_run.append(dummy_server_task)
                logging.info("Starting bot polling and dummy HTTP server...")
            else:
                logging.info("Starting bot polling (dummy HTTP server not configured/needed)...")

            # Wait for any task to complete (e.g., if one crashes, the other should also stop)
            done, pending = await asyncio.wait(
                tasks_to_run,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # If one task finishes (or crashes), cancel the others
            for task in pending:
                logging.info(f"Task {task.get_name()} is pending, cancelling it...")
                task.cancel()
                try:
                    await task # Wait for cancellation to complete
                except asyncio.CancelledError:
                    logging.info(f"Task {task.get_name()} was successfully cancelled.")
                except Exception as e_task_cancel:
                    logging.error(f"Error during cancellation of task {task.get_name()}: {e_task_cancel}", exc_info=True)


            # Log exceptions from completed tasks
            for task in done:
                if task.exception():
                    logging.error(f"Task {task.get_name()} raised an unhandled exception: {task.exception()}", exc_info=task.exception())
                else:
                    logging.info(f"Task {task.get_name()} completed without error.")

        else:
            logging.critical("FATAL: Database (db) or bot info (bot_info) missing after setup. Cannot start.")
    except ValueError as ve: # Catch critical ValueError from env var checks
        logging.critical(f"Configuration Error: {ve}", exc_info=True)
    except Exception as e_main_setup:
        logging.critical(f"Fatal error during initial setup or main task creation: {e_main_setup}", exc_info=True)
    finally:
        logging.info("Shutting down... Attempting to close resources.")

        # Gracefully stop tasks if they are still running (e.g., on KeyboardInterrupt before asyncio.wait completes)
        if bot_polling_task and not bot_polling_task.done():
            logging.info("Cancelling bot polling task...")
            bot_polling_task.cancel()
            try: await bot_polling_task
            except asyncio.CancelledError: logging.info("Bot polling task cancelled.")
            except Exception as e_poll_cancel: logging.error(f"Error cancelling polling task: {e_poll_cancel}")

        if dummy_server_task and not dummy_server_task.done():
            logging.info("Cancelling dummy server task...")
            dummy_server_task.cancel()
            try: await dummy_server_task
            except asyncio.CancelledError: logging.info("Dummy server task cancelled.")
            except Exception as e_serv_cancel: logging.error(f"Error cancelling dummy server task: {e_serv_cancel}")


        if bot and bot.session and not bot.session.closed:
            logging.info("Closing bot session...");
            await bot.session.close();
            logging.info("Bot session closed.")
        if db:
            logging.info("Closing database pool...");
            await db.close();
            logging.info("Database pool closed.")
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as main_run_err: # Catch-all for unexpected errors in asyncio.run itself
        logging.critical(f"Critical error in asyncio.run(main()): {main_run_err}", exc_info=True)
        print(f"Critical error during execution: {main_run_err}")