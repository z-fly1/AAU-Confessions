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
COMMENTS_PER_PAGE = 14 # New constant for comment pagination

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
    selecting_categories = State()
    waiting_for_text = State()

class CommentForm(StatesGroup):
    waiting_for_comment = State()
    waiting_for_reply = State()

class ContactAdminForm(StatesGroup):
    waiting_for_message = State()

class AdminActions(StatesGroup):
    waiting_for_rejection_reason = State()
    waiting_for_comment_edit = State() # New state for admin editing a comment

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
        await conn.execute("""
            ALTER TABLE confessions
            ADD COLUMN IF NOT EXISTS categories TEXT[];
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_confessions_categories ON confessions USING gin(categories);")
        logging.info("Checked/Created GIN index on 'confessions.categories'.")
        await conn.execute("""
            DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE confessions ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE confessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='rejection_reason') THEN ALTER TABLE confessions ADD COLUMN rejection_reason TEXT NULL; END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='categories') THEN ALTER TABLE confessions ADD COLUMN categories TEXT[] NULL; END IF;
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='categories') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='category') THEN
                    ALTER TABLE confessions DROP COLUMN IF EXISTS category;
                    RAISE NOTICE 'Dropped old confessions.category column.';
                END IF;
                COMMENT ON COLUMN confessions.message_id IS 'Message ID of the post in the channel';
                COMMENT ON COLUMN confessions.rejection_reason IS 'Reason provided by admin upon rejection (optional)';
                COMMENT ON COLUMN confessions.categories IS 'Array of categories chosen by the user';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'confessions'.")

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
                -- Optional: For admin edit audit trail
                edited_at TIMESTAMP WITH TIME ZONE NULL,
                edited_by_admin_id BIGINT NULL,
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
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='sticker_file_id') THEN ALTER TABLE comments ADD COLUMN sticker_file_id TEXT NULL; END IF;
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='animation_file_id') THEN ALTER TABLE comments ADD COLUMN animation_file_id TEXT NULL; END IF;
                 -- Optional: Add columns for admin edit audit trail if they don't exist
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='edited_at') THEN ALTER TABLE comments ADD COLUMN edited_at TIMESTAMP WITH TIME ZONE NULL; END IF;
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='edited_by_admin_id') THEN ALTER TABLE comments ADD COLUMN edited_by_admin_id BIGINT NULL; END IF;

                 ALTER TABLE comments ALTER COLUMN text DROP NOT NULL;
                 IF NOT EXISTS (SELECT 1 FROM information_schema.constraint_column_usage WHERE table_name='comments' AND constraint_name='one_content_type') THEN
                     ALTER TABLE comments ADD CONSTRAINT one_content_type CHECK (num_nonnulls(text, sticker_file_id, animation_file_id) = 1);
                 END IF;
                 COMMENT ON COLUMN comments.parent_comment_id IS 'ID of the comment this is a reply to';
                 COMMENT ON COLUMN comments.sticker_file_id IS 'File ID of the sticker comment';
                 COMMENT ON COLUMN comments.animation_file_id IS 'File ID of the GIF (animation) comment';
                 COMMENT ON COLUMN comments.edited_at IS 'Timestamp of when an admin last edited this comment';
                 COMMENT ON COLUMN comments.edited_by_admin_id IS 'Admin user ID who last edited this comment';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'comments'.")

        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions ( id SERIAL PRIMARY KEY, comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL, reaction_type VARCHAR(10) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id) );
             COMMENT ON TABLE reactions IS 'Stores likes and dislikes for comments';
        """)
        logging.info("Checked/Created 'reactions' table.")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests ( id SERIAL PRIMARY KEY, confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE, requester_user_id BIGINT NOT NULL, requested_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, requester_user_id) );
            COMMENT ON TABLE contact_requests IS 'Stores requests from confession authors to contact commenters.'; COMMENT ON COLUMN contact_requests.requester_user_id IS 'User ID of the confession author making the request.';
            COMMENT ON COLUMN contact_requests.requested_user_id IS 'User ID of the commenter being asked for contact.'; COMMENT ON COLUMN contact_requests.status IS 'pending, approved, denied, approved_no_username, failed_to_notify';
        """)
        logging.info("Checked/Created 'contact_requests' table.")

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

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
                reporter_user_id BIGINT NOT NULL,
                reported_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, reporter_user_id)
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
    logging.debug("Health check endpoint hit.")
    return web.Response(text="OK")

async def start_dummy_server():
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
        while True: await asyncio.sleep(3600)
    except asyncio.CancelledError: logging.info("Dummy HTTP server task cancelled.")
    except Exception as e: logging.error(f"Dummy HTTP server failed: {e}", exc_info=True)
    finally: await runner.cleanup(); logging.info("Dummy HTTP server cleaned up.")


# --- Helper Functions ---
def create_category_keyboard(selected_categories: List[str] = None):
    if selected_categories is None: selected_categories = []
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
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
            "SELECT COALESCE(SUM(CASE WHEN reaction_type = 'like' THEN 1 ELSE 0 END), 0) AS likes, "
            "COALESCE(SUM(CASE WHEN reaction_type = 'dislike' THEN 1 ELSE 0 END), 0) AS dislikes "
            "FROM reactions WHERE comment_id = $1", comment_id )
        if counts: likes, dislikes = counts['likes'], counts['dislikes']
    return likes, dislikes

async def get_user_points(user_id: int) -> int:
    async with db.acquire() as conn:
        points = await conn.fetchval("SELECT points FROM user_points WHERE user_id = $1", user_id)
        return points or 0

async def update_user_points(conn: asyncpg.Connection, user_id: int, delta: int):
    if delta == 0: return
    await conn.execute(
        "INSERT INTO user_points (user_id, points) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET points = user_points.points + $2",
        user_id, delta)
    logging.debug(f"Updated points for user {user_id} by {delta}")

# --- *** MODIFIED: build_comment_keyboard for Admin Edit Button *** ---
async def build_comment_keyboard(comment_id: int, commenter_user_id: int, viewer_user_id: int, confession_owner_id: int ):
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()

    # Row 1: Reactions, Reply, Report
    row1_buttons = [
        InlineKeyboardButton(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}"),
        InlineKeyboardButton(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}"),
        InlineKeyboardButton(text="↪️ Reply", callback_data=f"reply_{comment_id}"),
        InlineKeyboardButton(text="⚠️", callback_data=f"report_confirm_{comment_id}")
    ]
    builder.row(*row1_buttons)

    # Row 2: Conditional buttons (Edit, Request Contact)
    row2_buttons = []
    if viewer_user_id == ADMIN_ID:
        row2_buttons.append(InlineKeyboardButton(text="✏️ Edit Comment", callback_data=f"admin_edit_comment_{comment_id}"))

    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        row2_buttons.append(InlineKeyboardButton(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}"))

    if row2_buttons:
        builder.row(*row2_buttons) # Add second row only if it has buttons

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

async def update_channel_post_button(confession_id: int):
    global bot_info; await asyncio.sleep(0.1)
    if not bot_info: logging.error(f"No bot info for {confession_id} button update."); return
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)
        count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id) or 0
    if not conf_data or not conf_data['message_id']: logging.debug(f"No approved conf/msg_id for {confession_id} button."); return
    ch_msg_id = conf_data['message_id']; link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💬 View / Add Comments ({count})", url=link)]])
    try: await bot.edit_message_reply_markup(chat_id=CHANNEL_ID, message_id=ch_msg_id, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower(): logging.info(f"Button for {confession_id} already updated ({count}).")
        elif "message to edit not found" in str(e).lower(): logging.warning(f"Msg {ch_msg_id} not found for conf {confession_id}.")
        else: logging.error(f"Failed edit channel post {ch_msg_id} for conf {confession_id}: {e}")
    except Exception as e: logging.error(f"Unexpected err updating btn for conf {confession_id}: {e}", exc_info=True)

# --- *** MODIFIED: show_comments_for_confession for PAGINATION *** ---
async def show_comments_for_confession(
    user_id: int,
    confession_id: int,
    offset: int = 0,
    source_message_for_controls: Optional[types.Message] = None # Message that triggered view or previous "Show More"
):
    confession_owner_id: Optional[int] = None
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT status, user_id FROM confessions WHERE id = $1", confession_id)
        if not conf_data or conf_data['status'] != 'approved':
            err_txt = f"Confession #{confession_id} not found or not approved."
            logging.warning(err_txt + f" (Requested by {user_id})")
            if source_message_for_controls and offset == 0: # If initial trigger message exists
                try: await source_message_for_controls.edit_text(err_txt, reply_markup=None)
                except Exception: await safe_send_message(user_id, err_txt) # Fallback
            else:
                await safe_send_message(user_id, err_txt)
            return
        confession_owner_id = conf_data['user_id']

        total_comments = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id) or 0

        # Fetch all comment IDs and their creation order for global sequence numbering of replies
        all_comment_ids_raw = await conn.fetch("SELECT id FROM comments WHERE confession_id = $1 ORDER BY created_at ASC", confession_id)
        all_comment_ids_sorted = [row['id'] for row in all_comment_ids_raw]
        comment_id_to_global_seq = {db_id: i + 1 for i, db_id in enumerate(all_comment_ids_sorted)}

        comments_raw = await conn.fetch(
            """
            SELECT
                c.id, c.user_id, c.text, c.sticker_file_id, c.animation_file_id,
                c.parent_comment_id, c.created_at, COALESCE(up.points, 0) as user_points,
                c.edited_at -- For displaying if admin edited
            FROM comments c
            LEFT JOIN user_points up ON c.user_id = up.user_id
            WHERE c.confession_id = $1
            ORDER BY c.created_at ASC
            LIMIT $2 OFFSET $3
            """,
            confession_id, COMMENTS_PER_PAGE, offset
        )
        comment_data_list = [dict(row) for row in comments_raw]

    # --- Handle initial state (offset 0) ---
    if offset == 0:
        if source_message_for_controls: # This was the message that triggered "browse comments" (e.g., confession summary)
            try:
                await source_message_for_controls.delete()
                logging.info(f"Deleted initial trigger message {source_message_for_controls.message_id} for conf {confession_id} comment view.")
            except Exception as e_del:
                logging.warning(f"Could not delete initial trigger message {source_message_for_controls.message_id}: {e_del}")
            source_message_for_controls = None # Will send a new message for controls later

        if not comment_data_list: # No comments at all in the confession
            no_comments_text = f"<i>Confession #{confession_id}: No comments yet. Be the first!</i>\n"
            await safe_send_message(user_id, no_comments_text, parse_mode=ParseMode.HTML)
            add_comm_btn_markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
            ])
            await safe_send_message(user_id, "You can add your own comment:", reply_markup=add_comm_btn_markup)
            return

    # --- Send the current page of comments ---
    if not comment_data_list and offset > 0: # No more comments to show on this page, but previous pages existed
        logging.info(f"No more comments to display for conf {confession_id} at offset {offset}.")
        # The pagination controls will be updated to reflect this.
        pass
    else:
        for c_data in comment_data_list:
            comm_id = c_data['id']
            seq_num = comment_id_to_global_seq.get(comm_id, "???")
            commenter_uid = c_data['user_id']
            comm_text, sticker_id, animation_id = c_data['text'], c_data['sticker_file_id'], c_data['animation_file_id']
            ts_raw: Optional[datetime] = c_data['created_at']
            ts = ts_raw.strftime("%Y-%m-%d %H:%M") if ts_raw else "Unknown time"
            edited_ts_raw: Optional[datetime] = c_data['edited_at']
            edited_str = f" (edited {edited_ts_raw.strftime('%Y-%m-%d %H:%M')})" if edited_ts_raw else ""


            commenter_points = c_data['user_points']
            medal_str = f" 🏅{commenter_points} Aura" if commenter_points > -1000 else ""

            reply_prefix = ""
            if c_data['parent_comment_id'] and c_data['parent_comment_id'] in comment_id_to_global_seq:
                reply_prefix = f"↪️ <i>Replying to #{comment_id_to_global_seq[c_data['parent_comment_id']]}</i>\n"
            elif c_data['parent_comment_id']:
                reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"

            tag = "Anonymous"
            if confession_owner_id == commenter_uid: tag = "(Author)"
            elif user_id == commenter_uid: tag = "(You)"
            display_tag = f" {tag}{medal_str}"

            admin_info = f" [UID: <code>{commenter_uid}</code>]" if user_id == ADMIN_ID else ""
            metadata_text = f"<i>#{seq_num}{display_tag}{admin_info} {ts}{edited_str}</i>"

            keyboard = await build_comment_keyboard(comm_id, commenter_uid, user_id, confession_owner_id or 0)

            try:
                if sticker_id:
                    await bot.send_sticker(user_id, sticker=sticker_id)
                    await bot.send_message(user_id, f"{reply_prefix}{metadata_text}", reply_markup=keyboard, parse_mode=ParseMode.HTML)
                elif animation_id:
                    await bot.send_animation(user_id, animation=animation_id)
                    await bot.send_message(user_id, f"{reply_prefix}{metadata_text}", reply_markup=keyboard, parse_mode=ParseMode.HTML)
                elif comm_text:
                    full_text = f"{reply_prefix}💬 {html.quote(comm_text)}\n\n{metadata_text}"
                    await bot.send_message(user_id, full_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
                else: # Should not happen
                     logging.error(f"Comment {comm_id} has no content!"); await bot.send_message(user_id, f"⚠️ Error comment #{seq_num}")
            except Exception as e:
                logging.warning(f"Could not send comment #{seq_num} (DB ID: {comm_id}) to {user_id}: {e}")
                await safe_send_message(user_id, f"⚠️ Error displaying comment #{seq_num}.")
            await asyncio.sleep(0.1)

    # --- Send or Edit Pagination Controls and Add Comment Button ---
    current_display_count = offset + len(comment_data_list)
    pagination_text = f"Confession #{confession_id}: S<i>howing comments {offset + 1}-{current_display_count} of {total_comments}.</i>"
    if total_comments == 0 : pagination_text = f"Confession #{confession_id}: <i>No comments yet.</i>"
    if not comment_data_list and offset > 0: # No more comments on this page
        pagination_text = f"Confession #{confession_id}: <i>Showing comments {offset + 1 - COMMENTS_PER_PAGE}-{offset} of {total_comments}. No more comments.</i>"
        if offset == total_comments and total_comments > 0: # exactly landed on the end
             pagination_text = f"Confession #{confession_id}: <i>Showing all {total_comments} comments.</i>"


    builder = InlineKeyboardBuilder()
    if current_display_count < total_comments:
        next_offset = current_display_count
        builder.button(text=f"⬇️ Show More ({total_comments - current_display_count} remaining)", callback_data=f"show_more_{confession_id}_{next_offset}")
    builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
    builder.adjust(1)
    pagination_markup = builder.as_markup() if builder._buttons or (not comment_data_list and offset==0) else InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]])


    if source_message_for_controls: # This is the previous "Show More" message (offset > 0)
        try:
            await source_message_for_controls.edit_text(
                pagination_text, reply_markup=pagination_markup, parse_mode=ParseMode.HTML
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower() and "message to edit not found" not in str(e).lower():
                logging.warning(f"Could not edit pagination controls message {source_message_for_controls.message_id}: {e}")
                await safe_send_message(user_id, pagination_text, reply_markup=pagination_markup, parse_mode=ParseMode.HTML) # Fallback
            elif "message to edit not found" in str(e).lower(): # If original control message gone, send new one
                 await safe_send_message(user_id, pagination_text, reply_markup=pagination_markup, parse_mode=ParseMode.HTML)
    else: # This is the first load (offset == 0), send controls as a new message
        await safe_send_message(user_id, pagination_text, reply_markup=pagination_markup, parse_mode=ParseMode.HTML)


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
                conf_data = await conn.fetchrow(
                    "SELECT c.text, c.categories, c.status, c.user_id, COUNT(com.id) as comment_count "
                    "FROM confessions c LEFT JOIN comments com ON c.id = com.confession_id "
                    "WHERE c.id = $1 GROUP BY c.id, c.text, c.categories, c.status, c.user_id", conf_id)
            if not conf_data or conf_data['status'] != 'approved':
                await message.answer(f"Confession #{conf_id} not found or not approved."); return

            comm_count = conf_data['comment_count']
            categories = conf_data['categories'] or []; category_tags = " ".join([f"#{html.quote(cat)}" for cat in categories]) if categories else "#Unknown"
            txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(conf_data['text'])}\n\n{category_tags}\n---"
            builder = InlineKeyboardBuilder()
            # --- *** MODIFIED: "Browse Comments" now triggers paginated view directly *** ---
            builder.button(text=f"💬 Browse Comments ({comm_count})", callback_data=f"browse_{conf_id}") # This will be handled by browse_comments_action
            # Add comment button is now part of the paginated view's controls
            if message.from_user and message.from_user.id == conf_data['user_id']:
                builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{conf_id}")
                builder.adjust(1, 1)
            else:
                builder.adjust(1)
            await message.answer(txt, reply_markup=builder.as_markup())
        except (ValueError, IndexError): await message.answer("Invalid link.")
        except Exception as e: logging.error(f"Err deep link '{deep_link_args}': {e}", exc_info=True); await message.answer("Error link.")
    else: await message.answer("Welcome! /confess or /help.", reply_markup=ReplyKeyboardRemove())

@dp.message(Command("help"), StateFilter(None))
async def show_help(message: types.Message):
    help_text = (
        "<b>Welcome!</b>\n\n"
        "🔹 /confess - Submit a confession.\n"
        "🔹 /start - Welcome message / View specific confession via link.\n"
        "🔹 /help - This help.\n"
        "🔹 /privacy - Privacy info.\n\n"
        "Buttons:\n"
        "👍/👎: Like/Dislike.\n"
        "↪️ Reply: Reply to comment (Text, Sticker, GIF).\n"
        "⚠️ Report: Report comment.\n"
        "🤝 Request Contact: (Author only) Ask to contact commenter.\n\n"
        "Need to reach admin?"
    )
    kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin_start")]])
    if message.from_user and message.from_user.id == ADMIN_ID:
        help_text += "\n\n<b>Admin:</b>\n🔹 /id &lt;user_id&gt; - Get user info."
    await message.answer(help_text, parse_mode=ParseMode.HTML, reply_markup=kbd)

@dp.callback_query(F.data == "contact_admin_start", StateFilter(None))
async def start_contact_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ContactAdminForm.waiting_for_message)
    cancel_btn = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/cancel")]], resize_keyboard=True, one_time_keyboard=True)
    await callback_query.answer("Please send your message to the admin.")
    await callback_query.message.answer("Send message for admin.\n/cancel to abort.", reply_markup=cancel_btn)

@dp.message(Command("privacy"), StateFilter(None))
async def show_privacy(message: types.Message):
    url = "https://telegra.ph/Privacy-Policy-for-AAU-Confessions-Bot-04-27"
    txt = (
        "<b>Privacy Info</b>\n\n"
        "▪️ /confess: User ID stored, not shown to users. Contributes to 🏅.\n"
        "▪️ Comments (Text, Sticker, GIF): Anonymous. User ID stored for ID, points, reports; not public.\n"
        "▪️ Medal points (🏅) shown on comments.\n"
        "▪️ Author can request contact; commenter must approve username sharing.\n"
        "▪️ Reactions: Linked to User ID, affect points, not public who reacted.\n"
        "▪️ Reports: Linked to User ID, not public.\n"
        f"▪️ Data stored securely. Admin (<code>{ADMIN_ID}</code>) has access for moderation.\n\n"
        f'Full <a href="{url}">Privacy Policy</a>.'
    )
    await message.answer(txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("cancel"), StateFilter('*'))
async def cancel_any_state(message: types.Message, state: FSMContext):
    curr_state = await state.get_state()
    if curr_state is None: await message.answer("Nothing to cancel.", reply_markup=ReplyKeyboardRemove()); return
    logging.info(f"User {message.from_user.id} cancelling state {curr_state}")
    await state.clear(); await message.answer("Action cancelled.", reply_markup=ReplyKeyboardRemove())

@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def receive_admin_message(message: types.Message, state: FSMContext):
    uid = message.from_user.id; uinfo = message.from_user; mtxt = message.text
    if not uinfo: await message.answer("Cannot ID sender."); await state.clear(); return
    if len(mtxt) < 5: await message.answer("Too short. /cancel?"); return
    if len(mtxt) > 2000: await message.answer("Too long (max 2000). /cancel?"); return
    admin_msg = (f"<b>📬 Contact from User</b>\n<b>ID:</b> <code>{uid}</code>\n"
                 f"<b>User:</b> @{uinfo.username if uinfo.username else 'N/A'}\n"
                 f"<b>Name:</b> {html.quote(uinfo.first_name)}\n\n<b>Msg:</b>\n{html.quote(mtxt)}"
                 f"\n\n---\nReply to send to User ID <code>{uid}</code>.")
    try:
        await bot.send_message(ADMIN_ID, admin_msg, parse_mode=ParseMode.HTML)
        await message.answer("✅ Message sent to admin.", reply_markup=ReplyKeyboardRemove())
        logging.info(f"User {uid} sent msg to admin.")
    except Exception as e: logging.error(f"Fail fwd msg from {uid} to admin: {e}"); await message.answer("❌ Error.")
    finally: await state.clear()

@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def handle_admin_reply(message: types.Message, state: FSMContext):
    admin_curr_state = await state.get_state()
    if admin_curr_state is not None: # Let FSM handlers take precedence
        if not (AdminActions.waiting_for_rejection_reason.state == admin_curr_state or \
                AdminActions.waiting_for_comment_edit.state == admin_curr_state) : # if not these states, then allow reply.
            logging.debug(f"Admin {ADMIN_ID} in state {admin_curr_state}, FSM handles.")
            return

    replied_msg = message.reply_to_message
    if replied_msg and replied_msg.text and "⚠️ New Comment Report" in replied_msg.text:
        logging.info(f"Admin replied to report notification. Ignoring."); await message.reply("ℹ️ Reply to report no action."); return

    global bot_info;
    if not bot_info: logging.error("No bot_info for admin reply."); return
    admin_reply = message.text;
    if not replied_msg or not replied_msg.from_user or replied_msg.from_user.id != bot_info.id: return # Reply to non-bot msg

    target_uid = None; search_text = replied_msg.html_text or replied_msg.text
    if not search_text: return
    try:
        start_idx = search_text.find("User ID:</b> <code>")
        if start_idx != -1:
            uid_str = search_text[start_idx + len("User ID:</b> <code>"):].split("</code>", 1)[0]
            target_uid = int(uid_str.strip())
    except Exception: pass
    if target_uid:
        sent = await safe_send_message(target_uid, f"💬 <b>Admin Reply:</b>\n\n{html.quote(admin_reply or '')}")
        if sent: await message.reply("✅ Reply sent."); logging.info(f"Admin {ADMIN_ID} replied to user {target_uid}.")
        else: await message.reply("⚠️ Failed to send (user blocked?).")
    elif "Contact Request from User" in search_text:
        await message.reply("⚠️ Couldn't ID target user ID from replied message.")

@dp.message(Command("id"))
async def get_user_info_command(message: types.Message, command: CommandObject):
    if not message.from_user or message.from_user.id != ADMIN_ID: return
    if not command.args: await message.reply("Usage: /id <user_id>"); return
    try: target_uid = int(command.args.strip())
    except ValueError: await message.reply("Invalid User ID."); return
    logging.info(f"Admin {ADMIN_ID} req info for UID {target_uid}")
    parts = [f"ℹ️ <b>User Info ID:</b> <code>{target_uid}</code>\n"]; tg_fetched = False
    try:
        chat = await bot.get_chat(target_uid)
        parts.extend([f"<b>TG:</b> {html.quote(str(chat.type))}",
                      f"  - User: @{html.quote(chat.username or 'N/A')}",
                      f"  - Name: {html.quote(chat.first_name or 'N/A')}" + (f" {html.quote(chat.last_name)}" if chat.last_name else "")])
        tg_fetched = True
    except Exception as e: parts.append(f"⚠️ <b>TG:</b> Error: {html.quote(str(e))}")

    parts.append("\n<b>Bot History:</b>")
    try:
        async with db.acquire() as conn:
            pts = await get_user_points(target_uid)
            parts.append(f"  - Points: 🏅 {pts}")
            for table, label in [("confessions", "Confessions"), ("comments", "Comments"), ("reactions", "Reactions")]:
                stats = await conn.fetchrow(f"SELECT COUNT(*) n, MAX(created_at) ts FROM {table} WHERE user_id=$1", target_uid)
                ts_str = stats['ts'].strftime("%y-%m-%d %H:%M") if stats and stats['ts'] else "N/A"
                parts.append(f"  - {label}: {stats['n'] if stats else 0}" + (f" (Last: {ts_str})" if stats and stats['n'] > 0 else ""))
            # Simplified reports and contact requests counts
            for query, label_sfx in [
                ("SELECT COUNT(*) FROM contact_requests WHERE requester_user_id=$1", "Contact Reqs Sent"),
                ("SELECT COUNT(*) FROM contact_requests WHERE requested_user_id=$1", "Contact Reqs Recv"),
                ("SELECT COUNT(*) FROM reports WHERE reporter_user_id=$1", "Reports Made"),
                ("SELECT COUNT(*) FROM reports WHERE reported_user_id=$1", "Reports Recv (as commenter)")
            ]:
                count = await conn.fetchval(query, target_uid)
                parts.append(f"  - {label_sfx}: {count}")
    except Exception as e: parts.append(f"❌ <b>Bot History:</b> DB Error: {e}")
    await message.reply("\n".join(parts), parse_mode=ParseMode.HTML)

# --- Confession Submission ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    await state.update_data(selected_categories=[])
    await message.answer(
        f"Choose 1 to {MAX_CATEGORIES} categories. Click to select/deselect. Then 'Done'.",
        reply_markup=create_category_keyboard([])
    )
    await state.set_state(ConfessionForm.selecting_categories)

@dp.callback_query(StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
async def handle_category_selection(cb: types.CallbackQuery, state: FSMContext):
    action = cb.data.split("_", 1)[1]
    data = await state.get_data(); sel_cats: List[str] = data.get("selected_categories", [])
    if action == "cancel":
        await state.clear(); await cb.answer("Cancelled."); await cb.message.edit_text("Confession cancelled.", reply_markup=None); return
    if action == "done":
        if not sel_cats: await cb.answer(f"Select >=1 category.", show_alert=True); return
        if len(sel_cats) > MAX_CATEGORIES: await cb.answer(f"Max {MAX_CATEGORIES} cats.", show_alert=True); await cb.message.edit_reply_markup(reply_markup=create_category_keyboard(sel_cats)); return
        await state.set_state(ConfessionForm.waiting_for_text)
        tags = " ".join([f"#{html.quote(cat)}" for cat in sel_cats])
        await cb.message.edit_text(f"Cats: <b>{tags}</b>\n\nSend confession text.\n/cancel to stop.", reply_markup=None)
        await cb.answer(); return

    cat = action
    if cat in CATEGORIES:
        if cat in sel_cats: sel_cats.remove(cat)
        elif len(sel_cats) < MAX_CATEGORIES: sel_cats.append(cat)
        else: await cb.answer(f"Max {MAX_CATEGORIES} categories.", show_alert=True); return
        await state.update_data(selected_categories=sel_cats)
        await cb.message.edit_reply_markup(reply_markup=create_category_keyboard(sel_cats))
        await cb.answer(f"'{cat}' {'selected' if cat in sel_cats else 'deselected'}.")
    else: await cb.answer("Invalid category.", show_alert=True)

@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    conf_text = message.text; uid = message.from_user.id
    data = await state.get_data(); sel_cats: List[str] = data.get("selected_categories")
    if not sel_cats or not isinstance(sel_cats, list) or not sel_cats:
        await message.answer("⚠️ Cat info lost. /confess again."); await state.clear(); logging.error(f"State cats lost {uid}: {sel_cats}"); return
    if len(sel_cats) > MAX_CATEGORIES:
        await message.answer(f"⚠️ Too many cats. /confess again."); await state.clear(); logging.error(f"User {uid} too many cats: {sel_cats}"); return
    if len(conf_text) < 10: await message.answer("Too short (min 10 chars). /cancel?"); return
    if len(conf_text) > 3900: await message.answer(f"Too long (max ~3900). Has {len(conf_text)}. /cancel?"); return

    conf_id = None
    try:
        async with db.acquire() as conn, conn.transaction():
            conf_id = await conn.fetchval(
                "INSERT INTO confessions (text, user_id, categories, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                conf_text, uid, sel_cats)
            if not conf_id: raise Exception("Failed to get conf ID")
            await update_user_points(conn, uid, POINTS_PER_CONFESSION)
        tags = " ".join([f"#{html.quote(cat)}" for cat in sel_cats])
        kbd = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{conf_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{conf_id}")]])
        admin_msg = (f"<b>New Confession Review</b>\n<b>ID:</b> {conf_id}\n<b>Cats:</b> {tags}\n"
                     f"<b>User ID:</b> <code>{uid}</code>\n\n<b>Text:</b>\n{html.quote(conf_text)}")
        if len(admin_msg) > 4090: admin_msg = admin_msg[:4087] + "..."
        review_msg = await bot.send_message(ADMIN_ID, admin_msg, reply_markup=kbd, parse_mode=ParseMode.HTML)
        await state.update_data(admin_review_chat_id=review_msg.chat.id, admin_review_message_id=review_msg.message_id)
        await message.answer("✅ Submitted, pending review.")
        logging.info(f"Conf #{conf_id} (Cats: {', '.join(sel_cats)}) by {uid}")
    except Exception as e:
        logging.error(f"Error conf text from {uid} (ID {conf_id}): {e}", exc_info=True)
        await message.answer("Internal error submitting.")
    finally: await state.clear()

# --- Admin Confession Actions ---
def is_confession_action_callback(data: str) -> bool:
    if not isinstance(data, str): return False; parts = data.split("_")
    return len(parts) == 2 and parts[0] in ('approve', 'reject') and parts[1].isdigit()

@dp.callback_query(lambda c: is_confession_action_callback(c.data))
async def admin_action(cb: types.CallbackQuery, state: FSMContext):
    global bot_info;
    if not bot_info: await cb.answer("Internal err: No bot info.", show_alert=True); return
    if cb.from_user.id != ADMIN_ID: await cb.answer("Not authorized.", show_alert=True); return
    try: action, conf_id_str = cb.data.split("_", 1); conf_id = int(conf_id_str)
    except: await cb.answer("Invalid data.", show_alert=True); return

    async with db.acquire() as conn:
        conf_status = await conn.fetchval("SELECT status FROM confessions WHERE id = $1", conf_id)
        if not conf_status:
            await cb.answer("Conf not found.", show_alert=True)
            try: await cb.message.edit_text(cb.message.html_text + "\n\n-- Not Found --", reply_markup=None)
            except: pass; return
        if conf_status != 'pending':
            await cb.answer(f"Conf #{conf_id} already '{conf_status}'.", show_alert=True)
            try: await cb.message.edit_text(cb.message.html_text + f"\n\n-- Already {conf_status.capitalize()} --", reply_markup=None)
            except: pass; return

        if action == "approve":
            async with conn.transaction():
                conf = await conn.fetchrow("SELECT text, user_id, categories FROM confessions WHERE id = $1 AND status = 'pending' FOR UPDATE", conf_id)
                if not conf: await cb.answer("Conf status changed or not found.", show_alert=True); return
                uid, txt, cats = conf["user_id"], conf["text"], conf["categories"] or []
                final_status, channel_txt = "", ""
                try:
                    link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
                    tags = " ".join([f"#{html.quote(c)}" for c in cats]) if cats else "#Unknown"
                    channel_txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(txt)}\n\n{tags}"
                    ch_kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=link)]])
                    if len(channel_txt) > 4096:
                        logging.error(f"Conf {conf_id} too long ({len(channel_txt)}). Auto-reject."); await cb.answer("Err: Too long. Auto-reject.", show_alert=True)
                        await conn.execute("UPDATE confessions SET status='rejected', rejection_reason=$1 WHERE id=$2", "Content too long", conf_id)
                        await safe_send_message(uid, f"❌ Confession (#{conf_id} - {tags}) rejected: Too long.")
                        final_status = "Rejected (Too Long)"
                    else:
                        msg = await bot.send_message(CHANNEL_ID, channel_txt, reply_markup=ch_kbd)
                        await conn.execute("UPDATE confessions SET status='approved', message_id=$1 WHERE id=$2", msg.message_id, conf_id)
                        await safe_send_message(uid, f"✅ Confession (#{conf_id} - {tags}) approved!")
                        await cb.answer(f"Conf #{conf_id} approved.")
                        logging.info(f"Admin {cb.from_user.id} approved Conf #{conf_id}")
                        final_status = "Approved"
                    if final_status: await cb.message.edit_text(cb.message.html_text + f"\n\n-- Status: {final_status} --", reply_markup=None)
                except Exception as e:
                    logging.error(f"Error approval Conf {conf_id}: {e}", exc_info=True); await cb.answer(f"Error: {e}. Logs.", show_alert=True)
                    try: await cb.message.edit_text(cb.message.html_text + "\n\n-- Approval Failed! Logs. --", reply_markup=None)
                    except: pass
        elif action == "reject":
            await state.update_data(rejecting_conf_id=conf_id, admin_review_chat_id=cb.message.chat.id,
                                    admin_review_message_id=cb.message.message_id, original_admin_text=cb.message.html_text)
            await state.set_state(AdminActions.waiting_for_rejection_reason)
            reason_kbd = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="/skip")], [KeyboardButton(text="/cancel")]], resize_keyboard=True, one_time_keyboard=True)
            await cb.answer("❓ Reason for rejection", show_alert=False)
            await bot.send_message(cb.from_user.id, f"Reason for rejecting Conf #{conf_id}?\n/skip (no reason to user) or /cancel.", reply_markup=reason_kbd)

@dp.message(AdminActions.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id;
    if admin_id != ADMIN_ID: return
    data = await state.get_data()
    conf_id, adm_chat_id, adm_msg_id, orig_adm_txt = data.get("rejecting_conf_id"), data.get("admin_review_chat_id"), \
                                                    data.get("admin_review_message_id"), data.get("original_admin_text", "")
    if not all([conf_id, adm_chat_id, adm_msg_id]):
        await message.answer("Error: Context lost. Reject again.", reply_markup=ReplyKeyboardRemove()); await state.clear(); return

    reason, user_notify_txt, log_reason, final_status = None, "Your confession was rejected.", "(No reason)", "Rejected"
    if message.text.startswith("/"):
        cmd = message.text.split()[0]
        if cmd == "/skip": log_reason = "(Skipped reason)"; await message.answer("Skipping reason.", reply_markup=ReplyKeyboardRemove())
        elif cmd == "/cancel": await message.answer("Rejection cancelled.", reply_markup=ReplyKeyboardRemove()); await state.clear(); return
        else: await message.answer("Invalid cmd. Reason, /skip, or /cancel."); return
    else:
        reason = message.text.strip()
        if not reason: await message.answer("Reason empty. Reason, /skip, or /cancel."); return
        if len(reason) > 500: await message.answer("Reason too long (max 500). Shorten, /skip, /cancel."); return
        user_notify_txt = f"Your confession rejected:\n\n<i>{html.quote(reason)}</i>"
        log_reason, final_status = reason, "Rejected (Reason Provided)"
        await message.answer("Reason recorded.", reply_markup=ReplyKeyboardRemove())

    success = False
    async with db.acquire() as conn, conn.transaction():
        try:
            conf_data = await conn.fetchrow("SELECT user_id, categories, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id)
            if not conf_data: await message.answer("Error: Conf not found in DB.", reply_markup=ReplyKeyboardRemove()); await state.clear(); return
            if conf_data['status'] != 'pending':
                await message.answer(f"Error: Conf no longer pending (status: {conf_data['status']}).", reply_markup=ReplyKeyboardRemove())
                try: await bot.edit_message_text(adm_chat_id, adm_msg_id, orig_adm_txt + f"\n\n-- Already {conf_data['status'].capitalize()} --", reply_markup=None)
                except: pass; await state.clear(); return
            uid, cats = conf_data['user_id'], conf_data['categories'] or []
            tags = " ".join([f"#{html.quote(c)}" for c in cats]) if cats else "#Unknown"
            await conn.execute("UPDATE confessions SET status='rejected', rejection_reason=$1 WHERE id=$2", reason, conf_id)
            await safe_send_message(uid, f"❌ {user_notify_txt}\n(Conf ID: #{conf_id}, Cats: {tags})", parse_mode=ParseMode.HTML)
            try:
                admin_upd_txt = orig_adm_txt + f"\n\n-- Status: {final_status} --"
                if reason: admin_upd_txt += f"\nReason: {html.quote(reason)}"
                await bot.edit_message_text(adm_chat_id, adm_msg_id, admin_upd_txt, reply_markup=None)
            except: pass
            logging.info(f"Admin {admin_id} rejected Conf #{conf_id}. Reason: '{log_reason}'"); success = True
        except Exception as e: logging.error(f"DB err reject Conf {conf_id} by {admin_id}: {e}", exc_info=True); await message.answer(f"Error: {e}.", reply_markup=ReplyKeyboardRemove())
    if success: await message.answer(f"Conf #{conf_id} rejected.", reply_markup=ReplyKeyboardRemove())
    await state.clear()

# --- Commenting Flow & Pagination ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid browse cb: {callback_query.data}"); await callback_query.answer("Invalid.", show_alert=True); return
    await callback_query.answer("Loading comments...") # Answer first
    # show_comments_for_confession will delete callback_query.message if offset is 0 and comments exist
    await show_comments_for_confession(
        callback_query.from_user.id,
        conf_id,
        offset=0,
        source_message_for_controls=callback_query.message # This message will be deleted by show_comments
    )

@dp.callback_query(F.data.startswith("show_more_"))
async def handle_show_more_comments(callback_query: types.CallbackQuery):
    try:
        _, _, conf_id_str, offset_str = callback_query.data.split("_", 3)
        conf_id = int(conf_id_str)
        offset = int(offset_str)
    except (ValueError, IndexError, TypeError):
        logging.error(f"Invalid show_more cb data: {callback_query.data}")
        await callback_query.answer("Invalid data.", show_alert=True); return
    await callback_query.answer("Loading more comments...")
    await show_comments_for_confession(
        user_id=callback_query.from_user.id,
        confession_id=conf_id,
        offset=offset,
        source_message_for_controls=callback_query.message # This message (with "Show More") will be edited
    )

@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(cb: types.CallbackQuery, state: FSMContext):
    try: conf_id = int(cb.data.split("_", 1)[1])
    except: await cb.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id=$1 AND status='approved'", conf_id)
    if not exists: await cb.answer("Conf not available.", show_alert=True); try: await cb.message.edit_reply_markup(reply_markup=None); except: pass; return
    await state.update_data(confession_id=conf_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try: await safe_send_message(cb.from_user.id, f"📝 Commenting Conf #{conf_id}.\nSend text, sticker, or GIF. /cancel to abort."); await cb.answer()
    except: await cb.answer("Could not start. Try again.", show_alert=True); await state.clear()

@dp.message(CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
async def receive_comment(message: types.Message, state: FSMContext):
    uid = message.from_user.id; data = await state.get_data(); conf_id = data.get("confession_id")
    if not conf_id: await message.answer("⚠️ No conf context. Try again or /cancel."); logging.error(f"State no conf_id for {uid}"); return
    txt, sticker, anim, log_type = None, None, None, "Unknown"
    if message.text:
        txt = message.text.strip(); log_type = "Text"
        if not txt: await message.answer("Empty. Text or /cancel."); return
        if len(txt) > 1000: await message.answer(f"Too long (max 1000). Has {len(txt)}. /cancel?"); return
    elif message.sticker: sticker = message.sticker.file_id; log_type = "Sticker"
    elif message.animation: anim = message.animation.file_id; log_type = "GIF"
    else: await message.answer("Invalid. Text, sticker, GIF or /cancel."); return

    conf_owner_id, new_comm_id = None, None
    try:
        async with db.acquire() as conn, conn.transaction():
            conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id=$1 AND status='approved'", conf_id)
            if not conf_owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Conf not found/approved.")
            new_comm_id = await conn.fetchval(
                "INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id) "
                "VALUES ($1, $2, $3, $4, $5, NULL) RETURNING id", conf_id, uid, txt, sticker, anim)
            if not new_comm_id: raise Exception("Failed to get new comment ID.")
        await message.answer("💬 Comment added!"); logging.info(f"User {uid} added {log_type} comment (ID {new_comm_id}) to Conf #{conf_id}");
        await update_channel_post_button(conf_id)
        if conf_owner_id and conf_owner_id != uid and bot_info and bot_info.username:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
            preview = html.quote(txt[:150] + ('...' if txt and len(txt) > 150 else '')) if txt else ("[Sticker]" if sticker else "[GIF]")
            notif = f"💬 New comment on Conf #{conf_id}.\n<i>{preview}</i>\n<a href='{link}'>View comments.</a>"
            await safe_send_message(conf_owner_id, notif, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): logging.warning(f"No bot_info for author notif {conf_owner_id}")
        await show_comments_for_confession(uid, conf_id) # Show paginated view, will send new messages
    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
        specific_err = " (content type issue)" if "one_content_type" in str(e) else ""
        logging.error(f"Integrity err saving {log_type} cmt for conf {conf_id} by {uid}{specific_err}: {e}")
        await message.answer(f"❌ DB error{specific_err}. Try again.")
    except asyncpg.exceptions.ForeignKeyViolationError:
        logging.warning(f"User {uid} add {log_type} cmt to Conf #{conf_id}, but not found/approved.")
        await message.answer("⚠️ Cannot add. Conf removed/not approved. /cancel?")
    except Exception as e:
        logging.error(f"Unexpected err save {log_type} cmt for Conf #{conf_id} by {uid}: {e}", exc_info=True)
        await message.answer("❌ Unexpected error. Try again.")
    finally: await state.clear()

@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(cb: types.CallbackQuery, state: FSMContext):
    try: parent_id = int(cb.data.split("_", 1)[1])
    except: await cb.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        comm_data = await conn.fetchrow("SELECT confession_id, text, sticker_file_id, animation_file_id, user_id FROM comments WHERE id=$1", parent_id)
    if not comm_data: await cb.answer("Comment no longer exists.", show_alert=True); try: await cb.message.edit_reply_markup(reply_markup=None); except: pass; return
    conf_id, parent_uid = comm_data['confession_id'], comm_data['user_id']
    if cb.from_user.id == parent_uid: await cb.answer("Cannot reply to self.", show_alert=True); return

    preview = html.quote(comm_data['text'][:80] + ('...' if comm_data['text'] and len(comm_data['text']) > 80 else '')) if comm_data['text'] \
        else ("[Sticker]" if comm_data['sticker_file_id'] else "[GIF]")
    await state.update_data(confession_id=conf_id, parent_comment_id=parent_id)
    await state.set_state(CommentForm.waiting_for_reply)
    try:
        prompt = f"📝 Replying to:\n<i>\"{preview}\"</i>\n\nSend reply (text, sticker, GIF). /cancel to abort."
        await safe_send_message(cb.from_user.id, prompt); await cb.answer()
    except: await cb.answer("Could not start reply. Try again.", show_alert=True); await state.clear()

@dp.message(CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)
async def receive_reply(message: types.Message, state: FSMContext):
    uid = message.from_user.id; data = await state.get_data()
    conf_id, parent_id = data.get("confession_id"), data.get("parent_comment_id")
    if not conf_id or not parent_id: await message.answer("⚠️ Reply context lost. Try again or /cancel."); logging.error(f"State fields lost for {uid}: {data}"); return
    reply_txt, sticker, anim, log_type = None, None, None, "Unknown"
    if message.text:
        reply_txt = message.text.strip(); log_type = "Text Reply"
        if not reply_txt: await message.answer("Empty. Text or /cancel."); return
        if len(reply_txt) > 1000: await message.answer(f"Too long (max 1000). Has {len(reply_txt)}. /cancel?"); return
    elif message.sticker: sticker = message.sticker.file_id; log_type = "Sticker Reply"
    elif message.animation: anim = message.animation.file_id; log_type = "GIF Reply"
    else: await message.answer("Invalid. Text, sticker, GIF or /cancel."); return

    new_comm_id, parent_owner_id, conf_owner_id = None, None, None
    try:
        async with db.acquire() as conn, conn.transaction():
            parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id=$1 FOR UPDATE", parent_id)
            if not parent_data: await message.answer("⚠️ Original comment deleted. Reply not sent."); await state.clear(); return
            parent_owner_id = parent_data['user_id']
            if uid == parent_owner_id: await message.answer("Cannot reply to self."); await state.clear(); return
            conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id=$1 AND status='approved'", conf_id)
            if not conf_owner_id: await message.answer("⚠️ Confession removed/not approved. Reply not sent."); await state.clear(); return
            new_comm_id = await conn.fetchval(
                "INSERT INTO comments (confession_id, user_id, text, sticker_file_id, animation_file_id, parent_comment_id) "
                "VALUES ($1, $2, $3, $4, $5, $6) RETURNING id", conf_id, uid, reply_txt, sticker, anim, parent_id)
            if not new_comm_id: raise Exception("Failed to get new reply ID.")
        logging.info(f"User {uid} added {log_type} (ID {new_comm_id}) reply to cmt {parent_id} on Conf #{conf_id}")
        await update_channel_post_button(conf_id)
        await message.answer("↪️ Reply sent!")
        await show_comments_for_confession(uid, conf_id) # Show paginated view

        global bot_info
        if parent_owner_id and parent_owner_id != uid and bot_info and bot_info.username:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
            preview = html.quote(reply_txt[:150] + ('...' if reply_txt and len(reply_txt) > 150 else '')) if reply_txt \
                else ("[Sticker]" if sticker else "[GIF]")
            replier_pts = await get_user_points(uid); medal = f" 🏅{replier_pts} Aura" if replier_pts > -1000 else ""
            tag = "(Author)" if uid == conf_owner_id else "Anonymous"
            notif = f"↪️ ({tag}{medal}) replied to your comment on Conf #{conf_id}.\n<i>{preview}</i>\n<a href='{link}'>View reply.</a>"
            await safe_send_message(parent_owner_id, notif, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): logging.warning(f"No bot_info for parent author notif {parent_owner_id}")
    except asyncpg.exceptions.IntegrityConstraintViolationError as e:
        specific_err = " (content type issue)" if "one_content_type" in str(e) else ""
        logging.error(f"Integrity err saving {log_type} reply to {parent_id} by {uid}{specific_err}: {e}")
        await message.answer(f"❌ DB error{specific_err}. Try again.")
    except asyncpg.exceptions.ForeignKeyViolationError as e:
        logging.warning(f"FK violation reply by {uid} to {parent_id}: {e}")
        await message.answer("⚠️ Cannot add reply. Original cmt/conf gone.")
    except Exception as e:
        logging.error(f"Unexpected err save {log_type} reply to {parent_id} by {uid}: {e}", exc_info=True)
        await message.answer("❌ Unexpected error. Try again.")
    finally: await state.clear()

@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(cb: types.CallbackQuery):
    try: _, r_type, comm_id_str = cb.data.split("_", 2); comm_id = int(comm_id_str); uid = cb.from_user.id
    except: await cb.answer("Invalid reaction.", show_alert=True); return
    action, kbd, alert, delta = "none", None, None, 0; comm_uid, conf_owner_id = None, None
    async with db.acquire() as conn, conn.transaction():
        try:
            info = await conn.fetchrow("SELECT c.user_id comm_uid, co.user_id conf_owner_id FROM comments c JOIN confessions co ON c.confession_id=co.id WHERE c.id=$1", comm_id)
            if not info: raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")
            comm_uid, conf_owner_id = info['comm_uid'], info['conf_owner_id']
            if comm_uid == uid: await cb.answer("Cannot react to own comment.", show_alert=True); return
            existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id=$1 AND user_id=$2 FOR UPDATE", comm_id, uid)
            if existing:
                if existing == r_type: await conn.execute("DELETE FROM reactions WHERE comment_id=$1 AND user_id=$2", comm_id, uid); action, alert = f"Removed {r_type}", f"{r_type.capitalize()} removed"; delta = -POINTS_PER_LIKE_RECEIVED if r_type == 'like' else -POINTS_PER_DISLIKE_RECEIVED
                else: await conn.execute("UPDATE reactions SET reaction_type=$1, created_at=NOW() WHERE comment_id=$2 AND user_id=$3", r_type, comm_id, uid); action, alert = f"Changed to {r_type}", f"Reaction changed to {r_type}"; old_pts_eff = -POINTS_PER_LIKE_RECEIVED if existing == 'like' else -POINTS_PER_DISLIKE_RECEIVED; new_pts_eff = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED; delta = old_pts_eff + new_pts_eff
            else: await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, uid, r_type); action, alert = f"Added {r_type}", f"{r_type.capitalize()} added"; delta = POINTS_PER_LIKE_RECEIVED if r_type == 'like' else POINTS_PER_DISLIKE_RECEIVED
            if delta != 0 and comm_uid is not None: await update_user_points(conn, comm_uid, delta)
            kbd = await build_comment_keyboard(comm_id, comm_uid, uid, conf_owner_id)
        except asyncpg.exceptions.ForeignKeyViolationError: await cb.answer("Comment not found.", show_alert=True); try: await cb.message.edit_reply_markup(reply_markup=None); except: pass; return
        except Exception as e: logging.error(f"DB err reaction for cmt {comm_id} by {uid}: {e}", exc_info=True); await cb.answer("DB error.", show_alert=True); return
    if kbd and action != "none":
        try: await cb.message.edit_reply_markup(reply_markup=kbd); await cb.answer(alert)
        except TelegramBadRequest as e:
            err = str(e).lower()
            if "not modified" in err: await cb.answer(alert + " (No change)")
            elif "not found" in err: await cb.answer(alert + " (Display gone)")
            elif "too old" in err: await cb.answer(alert + " (Display stale)")
            else: await cb.answer("Error updating display.", show_alert=True)
        except Exception: await cb.answer("Unexpected err updating display.", show_alert=True)

# --- Report Comment ---
@dp.callback_query(F.data.startswith("report_confirm_"))
async def report_confirm_callback(cb: types.CallbackQuery):
    try: comm_id, reporter_uid = int(cb.data.split("_", 2)[2]), cb.from_user.id
    except: await cb.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM reports WHERE comment_id=$1 AND reporter_user_id=$2", comm_id, reporter_uid): await cb.answer("Already reported.", show_alert=True); return
        comm_data = await conn.fetchrow("SELECT text, sticker_file_id, animation_file_id, user_id FROM comments WHERE id=$1", comm_id)
    if not comm_data: await cb.answer("Comment not found.", show_alert=True); try: await cb.message.edit_reply_markup(reply_markup=None); except: pass; return
    if comm_data['user_id'] == reporter_uid: await cb.answer("Cannot report own comment.", show_alert=True); return
    snippet = html.quote(comm_data['text'][:100] + ('...' if comm_data['text'] and len(comm_data['text'])>100 else '')) if comm_data['text'] \
        else ("[Sticker]" if comm_data['sticker_file_id'] else "[GIF]")
    confirm_txt = f"Sure you want to report comment:\n<i>\"{snippet}\"</i>\nCannot be undone."
    kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yes, Report", callback_data=f"report_execute_{comm_id}"), InlineKeyboardButton(text="❌ No, Cancel", callback_data=f"report_cancel_{comm_id}")]])
    try: await safe_send_message(reporter_uid, confirm_txt, reply_markup=kbd); await cb.answer()
    except: await cb.answer("Could not start report. Try again.", show_alert=True)

@dp.callback_query(F.data.startswith("report_execute_"))
async def report_execute_callback(cb: types.CallbackQuery):
    try: comm_id, reporter_uid = int(cb.data.split("_", 2)[2]), cb.from_user.id
    except: await cb.answer("Invalid data.", show_alert=True); return
    reported_uid, conf_id, comm_txt, sticker, anim, report_id = None, None, None, None, None, None
    async with db.acquire() as conn, conn.transaction():
        try:
            comm_data = await conn.fetchrow("SELECT user_id, confession_id, text, sticker_file_id, animation_file_id FROM comments WHERE id=$1 FOR UPDATE", comm_id)
            if not comm_data: await cb.answer("Comment not found.", show_alert=True); try: await cb.message.edit_text("Report fail: Cmt gone.", reply_markup=None); except: pass; return
            reported_uid, conf_id, comm_txt, sticker, anim = comm_data['user_id'], comm_data['confession_id'], comm_data['text'], comm_data['sticker_file_id'], comm_data['animation_file_id']
            if reported_uid == reporter_uid: await cb.answer("Cannot report self.", show_alert=True); try: await cb.message.edit_text("Cancelled: Cannot report self.", reply_markup=None); except: pass; return
            if await conn.fetchval("SELECT 1 FROM reports WHERE comment_id=$1 AND reporter_user_id=$2", comm_id, reporter_uid): await cb.answer("Already reported.", show_alert=True); try: await cb.message.edit_text("Already reported.", reply_markup=None); except: pass; return
            report_id = await conn.fetchval("INSERT INTO reports (comment_id, reporter_user_id, reported_user_id, status) VALUES ($1, $2, $3, 'pending') RETURNING id", comm_id, reporter_uid, reported_uid)
            if not report_id: raise Exception("Failed to insert report.")
            logging.info(f"User {reporter_uid} reported cmt {comm_id} (by {reported_uid}). Report ID: {report_id}")
        except asyncpg.exceptions.UniqueViolationError: await cb.answer("Already reported.", show_alert=True); try: await cb.message.edit_text("Already reported.", reply_markup=None); except: pass; return
        except Exception as e: logging.error(f"DB err save report for cmt {comm_id} by {reporter_uid}: {e}", exc_info=True); await cb.answer("Error saving report.", show_alert=True); try: await cb.message.edit_text("Report fail DB err.", reply_markup=None); except: pass; return
    if report_id and reported_uid and conf_id and bot_info:
        snippet = (html.quote(comm_txt[:200] + ('...' if comm_txt and len(comm_txt)>200 else '')) if comm_txt else
                   (f"[Sticker: <code>{html.quote(sticker)}</code>]" if sticker else (f"[GIF: <code>{html.quote(anim)}</code>]" if anim else "[Unknown Content]")))
        content_desc = "Text Snippet" if comm_txt else ("Sticker" if sticker else ("GIF" if anim else "Content"))
        link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
        admin_notif = (f"⚠️ <b>New Comment Report (ID: {report_id})</b> ⚠️\n\n"
                       f"<b>Conf Link:</b> <a href='{link}'>View Conf #{conf_id}</a>\n"
                       f"<b>Cmt ID:</b> <code>{comm_id}</code>\n<b>Reported {content_desc}:</b>\n<i>{snippet}</i>\n\n"
                       f"<b>Reported UID:</b> <code>{reported_uid}</code> (/id <code>{reported_uid}</code>)\n"
                       f"<b>Reporter UID:</b> <code>{reporter_uid}</code> (/id <code>{reporter_uid}</code>)\n\nReview.")
        await safe_send_message(ADMIN_ID, admin_notif, disable_web_page_preview=True)
        try: await cb.message.edit_text("✅ Report submitted. Admin notified.", reply_markup=None); await cb.answer("Report sent.")
        except: await cb.answer("Report sent.") # Log if edit fails
    elif not bot_info: logging.error(f"Report {report_id} created, but no bot_info."); try: await cb.message.edit_text("✅ Report submitted (admin link issue).", reply_markup=None); await cb.answer("Report sent (admin link issue)."); except: pass

@dp.callback_query(F.data.startswith("report_cancel_"))
async def report_cancel_callback(cb: types.CallbackQuery):
    try: await cb.message.edit_text("Report cancelled.", reply_markup=None); await cb.answer("Report cancelled.")
    except: await cb.answer("Report cancelled.")

# --- Contact Request Flow ---
@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(cb: types.CallbackQuery):
    try: _, _, comm_id_str = cb.data.split("_", 2); comm_id = int(comm_id_str); req_uid = cb.from_user.id
    except: await cb.answer("Invalid req data.", show_alert=True); return
    async with db.acquire() as conn, conn.transaction():
        comm_data = await conn.fetchrow("SELECT c.user_id comm_uid, c.text comm_txt, c.sticker_file_id, c.animation_file_id, co.id conf_id, co.user_id conf_owner_id FROM comments c JOIN confessions co ON c.confession_id=co.id WHERE c.id=$1 AND co.status='approved'", comm_id)
        if not comm_data: await cb.answer("Cmt/Conf not found or not approved.", show_alert=True); return
        commenter_uid, conf_id, conf_owner_id = comm_data['comm_uid'], comm_data['conf_id'], comm_data['conf_owner_id']
        preview = (html.quote(comm_data['comm_txt'][:100] + ('...' if comm_data['comm_txt'] and len(comm_data['comm_txt'])>100 else '')) if comm_data['comm_txt']
                   else ("[Sticker]" if comm_data['sticker_file_id'] else "[GIF]"))
        if req_uid != conf_owner_id: await cb.answer("Only for own confessions.", show_alert=True); return
        if req_uid == commenter_uid: await cb.answer("Cannot contact self.", show_alert=True); return
        existing = await conn.fetchrow("SELECT id, status FROM contact_requests WHERE comment_id=$1 AND requester_user_id=$2", comm_id, req_uid)
        if existing and existing['status'] not in ('denied', 'failed_to_notify'): await cb.answer(f"Req already '{existing['status']}'.", show_alert=True); return

        req_id = None
        try:
            req_id = await conn.fetchval(
                "INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) VALUES ($1,$2,$3,$4,'pending') "
                "ON CONFLICT (comment_id, requester_user_id) DO UPDATE SET status='pending', updated_at=NOW() "
                "WHERE contact_requests.status IN ('denied', 'failed_to_notify', 'pending') RETURNING id",
                conf_id, comm_id, req_uid, commenter_uid)
            if not req_id: curr_status = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id=$1 AND requester_user_id=$2", comm_id, req_uid); await cb.answer(f"Req exists (Status: {curr_status or 'Unknown'}).", show_alert=True); return
        except Exception as e: logging.error(f"Failed insert/update contact req {req_uid} to {commenter_uid} for cmt {comm_id}: {e}", exc_info=True); await cb.answer("DB error.", show_alert=True); return

        notif_to_commenter = (f"🤝 Author of Conf #{conf_id} wants to contact you for comment:\n<i>\"{preview}\"</i>\n\nApprove sharing TG profile (username)?")
        kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve & Share Username", callback_data=f"approve_contact_{req_id}")], [InlineKeyboardButton(text="❌ Deny Request", callback_data=f"deny_contact_{req_id}")]])
        sent = await safe_send_message(commenter_uid, notif_to_commenter, reply_markup=kbd)
        if sent: await cb.answer("✅ Contact req sent.", show_alert=False); logging.info(f"Contact req ID {req_id} (cmt {comm_id}) from {req_uid} to {commenter_uid}.")
        else: await conn.execute("UPDATE contact_requests SET status='failed_to_notify' WHERE id=$1", req_id); await cb.answer("⚠️ Req saved, but could not notify commenter (blocked bot?).", show_alert=True)

def is_contact_response_callback(data: str) -> bool:
    if not isinstance(data, str): return False; parts = data.split("_"); return len(parts) == 3 and parts[0] in ('approve','deny') and parts[1]=='contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data))
async def handle_contact_response(cb: types.CallbackQuery):
    try: action, _, req_id_str = cb.data.split("_"); req_id = int(req_id_str); responder_uid = cb.from_user.id
    except: await cb.answer("Invalid req data.", show_alert=True); return
    new_db_status = 'approved' if action == 'approve' else 'denied'; edit_ui_status = ""
    async with db.acquire() as conn, conn.transaction():
        req_data = await conn.fetchrow("SELECT requester_user_id, requested_user_id, status, confession_id, comment_id FROM contact_requests WHERE id=$1 FOR UPDATE", req_id)
        if not req_data: await cb.answer("Req not found.", show_alert=True); try: await cb.message.delete(); except: pass; return
        if responder_uid != req_data['requested_user_id']: await cb.answer("Not for you.", show_alert=True); return
        if req_data['status'] not in ('pending', 'failed_to_notify'):
            status_disp = req_data['status'].replace('_',' ').capitalize()
            await cb.answer(f"Req already '{status_disp}'.", show_alert=True)
            try: if f"Status: {status_disp}" not in cb.message.html_text: await cb.message.edit_text(f"{cb.message.html_text}\n\n<b>Status: {status_disp}</b>", reply_markup=None);
            except: pass; return

        author_uid, conf_id, comm_id = req_data['requester_user_id'], req_data['confession_id'], req_data['comment_id']
        author_notif = ""
        if new_db_status == 'approved':
            uname = None; try: chat_info = await bot.get_chat(responder_uid); uname = chat_info.username; except: pass
            if uname:
                await conn.execute("UPDATE contact_requests SET status='approved', updated_at=NOW() WHERE id=$1", req_id)
                author_notif = f"✅ Contact Approved! For Conf #{conf_id} (Cmt ~{comm_id}), contact at: @{html.quote(uname)}"
                await cb.answer("Approved! Username shared."); edit_ui_status = 'Approved (Username Shared)'
            else:
                new_db_status = 'approved_no_username'
                await conn.execute("UPDATE contact_requests SET status=$1, updated_at=NOW() WHERE id=$2", new_db_status, req_id)
                author_notif = f"⚠️ Contact Approved (No Public Username). For Conf #{conf_id} (Cmt ~{comm_id}), commenter approved but has no public username."
                await cb.answer("Approved! No public username, author cannot contact.", show_alert=True); edit_ui_status = 'Approved (No Public Username)'
        else: # Denied
            await conn.execute("UPDATE contact_requests SET status='denied', updated_at=NOW() WHERE id=$1", req_id)
            author_notif = f"❌ Contact Denied. For Conf #{conf_id} (Cmt ~{comm_id}), commenter declined."
            await cb.answer("Denied. Not shared."); edit_ui_status = 'Denied'
        await safe_send_message(author_uid, author_notif)
        try: await cb.message.edit_text(f"{cb.message.html_text}\n\n<b>Status: {edit_ui_status}</b>", reply_markup=None)
        except: pass

@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(cb: types.CallbackQuery):
    try: _, _, conf_id_str = cb.data.split("_", 2); conf_id = int(conf_id_str); viewer_uid = cb.from_user.id
    except: await cb.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id=$1", conf_id)
        if not conf_owner_id: await cb.answer("Conf not found.", show_alert=True); return
        if viewer_uid != conf_owner_id: await cb.answer("Only for own confessions.", show_alert=True); return
        reqs_data = await conn.fetch(
            "SELECT cr.id req_id, cr.comment_id, cr.status, cr.updated_at, c.text cmt_txt, c.sticker_file_id, c.animation_file_id, cr.requested_user_id "
            "FROM contact_requests cr JOIN comments c ON cr.comment_id=c.id WHERE cr.confession_id=$1 AND cr.requester_user_id=$2 ORDER BY cr.updated_at DESC",
            conf_id, viewer_uid)
    if not reqs_data: await cb.answer("No contact reqs for this conf.", show_alert=False); return
    parts = [f"<b>Contact Reqs Status for Conf #{conf_id}</b>\n"]
    for req in reqs_data:
        preview = (html.quote(req['cmt_txt'][:60] + ('...' if req['cmt_txt'] and len(req['cmt_txt']) > 60 else '')) if req['cmt_txt']
                   else ("[Sticker]" if req['sticker_file_id'] else "[GIF]"))
        status_disp = req['status'].replace('_',' ').capitalize(); ts = req['updated_at'].strftime("%y-%m-%d %H:%M")
        emoji = {"pending":"❓", "approved":"✅", "denied":"❌", "approved_no_username":"⚠️", "failed_to_notify":"🚫"}.get(req['status'], "❓")
        entry = (f"🔹 <b>Req to Commenter (UID: <code>{req['requested_user_id']}</code>)</b>\n"
                 f"   <i>Cmt: \"{preview}\"</i>\n   <b>Status:</b> {emoji} {status_disp}\n   <b>Last Upd:</b> {ts}")
        if req['status'] == 'approved':
            try: commenter_info = await bot.get_chat(req['requested_user_id']); entry += f"\n   <b>Contact:</b> @{html.quote(commenter_info.username)}" if commenter_info.username else "\n   <b>Contact:</b> (Approved, no public username)"
            except: entry += "\n   <b>Contact:</b> (Error fetching username)"
        elif req['status'] == 'approved_no_username': entry += "\n   <b>Contact:</b> (Approved, no public username)"
        elif req['status'] == 'failed_to_notify': entry += "\n   <i>Note: Could not deliver req to commenter.</i>"
        parts.append(entry)
    final_txt = "\n\n".join(parts);
    if len(final_txt) > 4096: final_txt = final_txt[:4090] + "\n\n...(truncated)"
    await safe_send_message(viewer_uid, final_txt, disable_web_page_preview=True); await cb.answer()

# --- Admin Comment Edit Handlers ---
@dp.callback_query(F.data.startswith("admin_edit_comment_"), F.from_user.id == ADMIN_ID)
async def admin_start_comment_edit(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        comment_id = int(callback_query.data.split("_", 3)[3])
    except (ValueError, IndexError):
        await callback_query.answer("Invalid comment ID.", show_alert=True); return

    async with db.acquire() as conn:
        comment_data = await conn.fetchrow(
            "SELECT text, sticker_file_id, animation_file_id, user_id, confession_id FROM comments WHERE id = $1", comment_id
        )
    if not comment_data:
        await callback_query.answer("Comment not found.", show_alert=True); return

    preview = ""
    if comment_data['text']: preview = f"Current text: \"{html.quote(comment_data['text'][:200])}" + ("..." if len(comment_data['text']) > 200 else "") + "\""
    elif comment_data['sticker_file_id']: preview = f"Current content: [Sticker ID: {comment_data['sticker_file_id']}]"
    elif comment_data['animation_file_id']: preview = f"Current content: [GIF ID: {comment_data['animation_file_id']}]"

    await state.update_data(
        editing_comment_id=comment_id,
        original_comment_confession_id=comment_data['confession_id']
    )
    await state.set_state(AdminActions.waiting_for_comment_edit)

    edit_options_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/confirm_current_content_as_text")],
            [KeyboardButton(text="/remove_content")],
            [KeyboardButton(text="/cancel_edit")]
        ], resize_keyboard=True, one_time_keyboard=True )
    await callback_query.answer("Preparing to edit comment...")
    await bot.send_message(
        ADMIN_ID,
        f"Editing comment ID {comment_id} on Confession #{comment_data['confession_id']}.\n{preview}\n\n"
        "Send the new content (text, sticker, or GIF).\n"
        "Or use special commands below:",
        reply_markup=edit_options_kb
    )

@dp.message(AdminActions.waiting_for_comment_edit, F.from_user.id == ADMIN_ID) # Catches text, sticker, animation
async def admin_process_comment_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    comment_id = data.get("editing_comment_id")
    conf_id = data.get("original_comment_confession_id")

    if not comment_id: # Should not happen if state is correct
        await message.answer("Error: Editing context lost. Please /cancel_edit and try again.", reply_markup=ReplyKeyboardRemove())
        await state.clear(); return

    new_text, new_sticker_id, new_animation_id = None, None, None
    update_description = ""

    if message.text:
        if message.text == "/cancel_edit":
            await state.clear()
            await message.answer("Comment edit cancelled.", reply_markup=ReplyKeyboardRemove()); return
        elif message.text == "/remove_content":
            new_text = "[Content Removed by Admin]"
            update_description = "content removed and replaced with placeholder text"
        elif message.text == "/confirm_current_content_as_text":
            async with db.acquire() as conn:
                original_content = await conn.fetchrow("SELECT sticker_file_id, animation_file_id FROM comments WHERE id=$1", comment_id)
            if original_content and original_content['sticker_file_id']:
                new_text = f"[Sticker ID: {original_content['sticker_file_id']}]"
                update_description = "sticker content converted to its file_id as text"
            elif original_content and original_content['animation_file_id']:
                new_text = f"[Animation ID: {original_content['animation_file_id']}]"
                update_description = "GIF content converted to its file_id as text"
            else: # Original was text or comment gone
                await message.answer("Original content was text, or comment data not found. Send new text, or /cancel_edit.", reply_markup=ReplyKeyboardRemove())
                return # Stay in state
        else:
            new_text = message.text
            update_description = "text content updated"
            if len(new_text) > 1000:
                 await message.answer("New text is too long (max 1000 chars). Please shorten it or use a command.", reply_markup=ReplyKeyboardRemove()); return
    elif message.sticker:
        new_sticker_id = message.sticker.file_id
        update_description = "content updated to new sticker"
    elif message.animation:
        new_animation_id = message.animation.file_id
        update_description = "content updated to new GIF"
    else: # Should not be reached if filters are F.text | F.sticker | F.animation
        await message.answer("Unsupported content type. Send text, sticker, GIF, or use a command.", reply_markup=ReplyKeyboardRemove())
        return

    try:
        async with db.acquire() as conn:
            # Ensure only one content type is set by nullifying others
            await conn.execute(
                """UPDATE comments
                   SET text = $1, sticker_file_id = $2, animation_file_id = $3,
                       edited_at = CURRENT_TIMESTAMP, edited_by_admin_id = $4
                   WHERE id = $5""",
                new_text, new_sticker_id, new_animation_id, ADMIN_ID, comment_id
            )
        logging.info(f"Admin {ADMIN_ID} edited comment {comment_id} (Confession {conf_id}): {update_description}")
        await message.answer(f"Comment ID {comment_id} successfully updated: {update_description}.", reply_markup=ReplyKeyboardRemove())
        await message.answer(f"To see changes for confession #{conf_id}, please browse its comments again (e.g., /start view_{conf_id}).", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logging.error(f"Failed to update comment {comment_id} by admin {ADMIN_ID}: {e}", exc_info=True)
        await message.answer(f"Error updating comment: {e}. Please try again or /cancel_edit.", reply_markup=ReplyKeyboardRemove())
    finally:
        if not message.text or message.text not in ["/confirm_current_content_as_text", "/remove_content"]: # Don't clear state if a command failed and needs retry
             if not (message.text and (len(message.text)>1000 and new_text)): # Don't clear if text too long
                await state.clear()


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    logging.debug(f"Non-command text from {message.from_user.id} outside state: '{message.text[:50]}...'")
    await message.reply("Hi! 👋 /confess or /help.")

# --- Main Execution ---
async def main():
    bot_task, server_task = None, None
    try:
        await setup()
        if db and bot_info:
            cmds = [ types.BotCommand(command="start", description="Start/View confession"),
                     types.BotCommand(command="confess", description="Submit confession"),
                     types.BotCommand(command="help", description="Show help"),
                     types.BotCommand(command="privacy", description="Privacy info"),
                     types.BotCommand(command="cancel", description="Cancel action")]
            admin_cmds = cmds + [types.BotCommand(command="id", description="ADMIN: Get user info")]
            await bot.set_my_commands(cmds)
            try: await bot.set_my_commands(admin_cmds, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
            except: logging.warning(f"Could not set admin cmds for {ADMIN_ID}")

            logging.info("Registering handlers...")
            # Commands
            dp.message.register(start, Command("start"))
            dp.message.register(show_help, Command("help"), StateFilter(None))
            dp.message.register(show_privacy, Command("privacy"), StateFilter(None))
            dp.message.register(get_user_info_command, Command("id"))
            dp.message.register(start_confession, Command("confess"), StateFilter(None))
            dp.message.register(cancel_any_state, Command("cancel"), StateFilter('*'))
            # Confession FSM
            dp.callback_query.register(handle_category_selection, StateFilter(ConfessionForm.selecting_categories), F.data.startswith("category_"))
            dp.message.register(receive_confession_text, ConfessionForm.waiting_for_text, F.text)
            # Commenting FSM
            dp.message.register(receive_comment, CommentForm.waiting_for_comment, F.text | F.sticker | F.animation)
            dp.message.register(receive_reply, CommentForm.waiting_for_reply, F.text | F.sticker | F.animation)
            # Contact Admin FSM
            dp.callback_query.register(start_contact_admin_callback, F.data == "contact_admin_start", StateFilter(None))
            dp.message.register(receive_admin_message, ContactAdminForm.waiting_for_message, F.text)
            # Admin Actions FSM (Rejection, Comment Edit)
            dp.message.register(receive_rejection_reason, AdminActions.waiting_for_rejection_reason, F.text)
            dp.message.register(admin_process_comment_edit, AdminActions.waiting_for_comment_edit, F.from_user.id == ADMIN_ID) # Catches text, sticker, animation
            # Callback Queries (Non-FSM)
            dp.callback_query.register(admin_action, lambda c: is_confession_action_callback(c.data))
            dp.callback_query.register(browse_comments_action, F.data.startswith("browse_"))
            dp.callback_query.register(handle_show_more_comments, F.data.startswith("show_more_")) # Pagination
            dp.callback_query.register(add_comment_prompt, F.data.startswith("add_"))
            dp.callback_query.register(reply_comment_prompt, F.data.startswith("reply_"))
            dp.callback_query.register(handle_reaction, F.data.startswith("react_"))
            dp.callback_query.register(report_confirm_callback, F.data.startswith("report_confirm_"))
            dp.callback_query.register(report_execute_callback, F.data.startswith("report_execute_"))
            dp.callback_query.register(report_cancel_callback, F.data.startswith("report_cancel_"))
            dp.callback_query.register(handle_request_contact, F.data.startswith("req_contact_"))
            dp.callback_query.register(handle_contact_response, lambda c: is_contact_response_callback(c.data))
            dp.callback_query.register(view_contact_requests, F.data.startswith("view_reqs_"))
            dp.callback_query.register(admin_start_comment_edit, F.data.startswith("admin_edit_comment_"), F.from_user.id == ADMIN_ID) # Admin edit comment
            # Messages (Non-FSM, Non-Command)
            dp.message.register(handle_admin_reply, F.from_user.id == ADMIN_ID, F.reply_to_message)
            dp.message.register(handle_text_without_state, StateFilter(None), F.text & ~F.text.startswith('/')) # Fallback
            logging.info("Handler registration complete.")

            tasks = []
            bot_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True), name="BotPolling")
            tasks.append(bot_task)
            if HTTP_PORT_STR:
                server_task = asyncio.create_task(start_dummy_server(), name="HttpServer")
                tasks.append(server_task)
                logging.info("Starting bot polling and HTTP server...")
            else: logging.info("Starting bot polling...")
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending: task.cancel()
            for task in done:
                if task.exception(): logging.error(f"Task {task.get_name()} failed: {task.exception()}", exc_info=task.exception())
                else: logging.info(f"Task {task.get_name()} completed.")
        else: logging.critical("FATAL: DB or bot_info missing. Cannot start.")
    except ValueError as ve: logging.critical(f"Config Error: {ve}", exc_info=True)
    except Exception as e: logging.critical(f"Fatal error in main setup/loop: {e}", exc_info=True)
    finally:
        logging.info("Shutting down...")
        if bot_task and not bot_task.done(): bot_task.cancel()
        if server_task and not server_task.done(): server_task.cancel()
        # await asyncio.gather(bot_task, server_task, return_exceptions=True) # Wait for tasks to actually cancel
        if bot and bot.session and not bot.session.closed: await bot.session.close(); logging.info("Bot session closed.")
        if db: await db.close(); logging.info("DB pool closed.")
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as main_err: logging.critical(f"Critical error in asyncio.run: {main_err}", exc_info=True)

