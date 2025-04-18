# --- START OF FILE main.py ---

import logging
import asyncpg
import os
import asyncio
from aiogram import Bot, Dispatcher, types, F, html # Import html for formatting
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional

# --- Constants ---
CATEGORIES = [
    "Relationship", "Education", "Family", "School", "Friendship",
    "Religion", "Entertainment", "Information", "Sexual Assault", "Other"
]

# Load environment variables at the top level
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID") # Load as string first for validation
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

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
    waiting_for_category = State()
    waiting_for_text = State()

class CommentForm(StatesGroup):
    waiting_for_comment = State()
    waiting_for_reply = State()

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
        # --- Create Confessions Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending', -- pending, approved, rejected
                message_id BIGINT, -- Channel message ID
                category VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logging.info("Checked/Created 'confessions' table.")
        # --- Conditionally ALTER/COMMENT confessions table ---
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='category') THEN
                    ALTER TABLE confessions ADD COLUMN category VARCHAR(50); END IF;
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN
                     ALTER TABLE confessions ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC';
                     ALTER TABLE confessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                COMMENT ON COLUMN confessions.category IS 'Category chosen by the user';
                COMMENT ON COLUMN confessions.message_id IS 'Message ID of the post in the channel';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'confessions'.")

        # --- Create Comments Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL, -- Important for replies
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        logging.info("Checked/Created 'comments' table.")
        # --- Conditionally ALTER/COMMENT comments table ---
        await conn.execute("""
             DO $$ BEGIN
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='parent_comment_id') THEN
                     ALTER TABLE comments ADD COLUMN parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL; END IF;
                 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN
                     ALTER TABLE comments ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC';
                     ALTER TABLE comments ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                 COMMENT ON COLUMN comments.parent_comment_id IS 'ID of the comment this is a reply to';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'comments'.")

        # --- Create Reactions Table ---
        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions (
                 id SERIAL PRIMARY KEY,
                 comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL,
                 reaction_type VARCHAR(10) NOT NULL, -- 'like' or 'dislike'
                 created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id) -- Allow only one reaction per user per comment
             );
             COMMENT ON TABLE reactions IS 'Stores likes and dislikes for comments';
        """)
        logging.info("Checked/Created 'reactions' table.")

        # --- Create Contact Requests Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
                requester_user_id BIGINT NOT NULL, -- Confession Author ID
                requested_user_id BIGINT NOT NULL, -- Commenter ID
                status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, approved, denied, approved_no_username
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, requester_user_id) -- Prevent author spamming requests for the same comment
            );
            COMMENT ON TABLE contact_requests IS 'Stores requests from confession authors to contact commenters.';
            COMMENT ON COLUMN contact_requests.requester_user_id IS 'User ID of the confession author making the request.';
            COMMENT ON COLUMN contact_requests.requested_user_id IS 'User ID of the commenter being asked for contact.';
            COMMENT ON COLUMN contact_requests.status IS 'pending, approved, denied, approved_no_username';
        """)
        logging.info("Checked/Created 'contact_requests' table.")
        # Optional: Add trigger for updated_at on contact_requests table if needed

        logging.info("Database tables setup complete.")


# --- Helper Functions ---

def create_category_keyboard():
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2)
    return builder.as_markup()

async def get_comment_reactions(comment_id: int):
    async with db.acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(CASE WHEN reaction_type = 'like' THEN 1 ELSE 0 END), 0) AS likes,
                COALESCE(SUM(CASE WHEN reaction_type = 'dislike' THEN 1 ELSE 0 END), 0) AS dislikes
            FROM reactions
            WHERE comment_id = $1
            """,
            comment_id
        )
    return counts['likes'] if counts else 0, counts['dislikes'] if counts else 0

async def build_comment_keyboard(
    comment_id: int,
    commenter_user_id: int,
    viewer_user_id: int,
    confession_owner_id: int
):
    """Builds the keyboard for a comment, conditionally adding 'Request Contact'."""
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")

    # Add Request Contact Button Conditionally
    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(3, 1) # Reactions/Reply row, Request Contact row below
    else:
        builder.adjust(3) # All buttons in one row

    return builder.as_markup()

async def safe_send_message(user_id: int, text: str, **kwargs):
    """Safely send a message, handling common errors like user blocking."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        if "bot was blocked by the user" in str(e) or "user is deactivated" in str(e) or "chat not found" in str(e):
             logging.warning(f"Could not send message to user {user_id}: Blocked or deactivated. {e}")
        else:
             logging.warning(f"Telegram API error sending to {user_id}: {e}")
    except TelegramRetryAfter as e:
        logging.warning(f"Flood control exceeded for user {user_id}. Retrying after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        try:
            await bot.send_message(user_id, text, **kwargs) # Retry once
            return True
        except Exception as e_inner:
            logging.error(f"Failed to send message to user {user_id} after retry: {e_inner}")
    except Exception as e:
        logging.error(f"Unexpected error sending message to user {user_id}: {e}", exc_info=True)
    return False

async def update_channel_post_button(confession_id: int):
    """Fetches comment count and updates the button on the channel post."""
    global bot_info
    if not bot_info:
        logging.error(f"Cannot update channel post for {confession_id}: Bot info not available.")
        return

    async with db.acquire() as conn:
        confession_data = await conn.fetchrow(
            "SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'",
            confession_id
        )
        comment_count = await conn.fetchval(
            "SELECT COUNT(*) FROM comments WHERE confession_id = $1",
            confession_id
        ) or 0

    if not confession_data or not confession_data['message_id']:
        logging.debug(f"No approved confession or message_id for confession {confession_id} to update button.")
        return

    channel_message_id = confession_data['message_id']
    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
    new_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💬 View / Add Comments ({comment_count})", url=deep_link_url)]
    ])

    try:
        await bot.edit_message_reply_markup(
            chat_id=CHANNEL_ID,
            message_id=channel_message_id,
            reply_markup=new_markup
        )
        logging.info(f"Updated comment count ({comment_count}) on channel post for confession {confession_id}")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logging.info(f"Channel post button for confession {confession_id} already up-to-date ({comment_count}).")
        elif "message to edit not found" in str(e).lower():
             logging.warning(f"Cannot update channel post button: Message {channel_message_id} not found in channel {CHANNEL_ID} (confession {confession_id}). Maybe deleted?")
        else:
            logging.error(f"Failed to edit channel post {channel_message_id} for confession {confession_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating channel post button for confession {confession_id}: {e}", exc_info=True)

async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None):
    """Fetches and sends comments, assigning sequence numbers and adding context tags/buttons."""
    confession_owner_id: Optional[int] = None

    async with db.acquire() as conn:
        confession_data = await conn.fetchrow(
            "SELECT status, user_id FROM confessions WHERE id = $1",
            confession_id
        )
        if not confession_data or confession_data['status'] != 'approved':
            error_text = f"Confession #{confession_id} could not be found or hasn't been approved."
            try:
                if message_to_edit: await message_to_edit.edit_text(error_text, reply_markup=None)
                else: await safe_send_message(user_id, error_text)
            except Exception as e: logging.warning(f"Could not send/edit 'confession not found' to user {user_id}: {e}")
            return

        confession_owner_id = confession_data['user_id']
        comments = await conn.fetch(
            "SELECT id, user_id, text, parent_comment_id, created_at FROM comments WHERE confession_id = $1 ORDER BY created_at ASC",
            confession_id
        )

    sent_message_ids = {}
    comment_id_to_sequence = {}
    comment_counter = 0

    if not comments:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
    else:
        comments_html = f"--- Comments for Confession #{confession_id} ---\n\n"
        temp_comment_map = {}
        for c_data in comments:
            comment_counter += 1
            db_comment_id = c_data['id']
            comment_id_to_sequence[db_comment_id] = comment_counter
            temp_comment_map[db_comment_id] = c_data

        for c in comments:
            comment_id = c['id']
            current_sequence_num = comment_id_to_sequence[comment_id]
            commenter_user_id = c['user_id']
            comment_text = html.quote(c['text'])
            timestamp = c['created_at'].strftime("%Y-%m-%d %H:%M")

            reply_prefix = ""
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_id_to_sequence:
                parent_sequence_num = comment_id_to_sequence[c['parent_comment_id']]
                reply_prefix = f"↪️ <i>Replying to #{parent_sequence_num}</i>\n"
            elif c['parent_comment_id']:
                reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"

            author_tag = ""
            if commenter_user_id == confession_owner_id: author_tag = "(Author)"
            elif commenter_user_id == user_id: author_tag = "(You)"
            else: author_tag = "Anonymous"
            display_tag = f" {author_tag}" if author_tag else " Anonymous"

            comment_metadata = f"<i>#{current_sequence_num}{display_tag} | {timestamp}</i>"
            keyboard = await build_comment_keyboard(
                comment_id=comment_id,
                commenter_user_id=commenter_user_id,
                viewer_user_id=user_id,
                confession_owner_id=confession_owner_id
            )
            full_comment_text = f"{reply_prefix}💬 {comment_text}\n\n{comment_metadata}"

            try:
                sent_msg = await bot.send_message(
                    user_id, full_comment_text, reply_markup=keyboard,
                    parse_mode=ParseMode.HTML, disable_web_page_preview=True
                )
                sent_message_ids[comment_id] = sent_msg.message_id
            except Exception as e:
                logging.warning(f"Could not send comment #{current_sequence_num} (DB ID: {comment_id}) to user {user_id}: {e}")
                try: await safe_send_message(user_id, f"⚠️ Error displaying comment #{current_sequence_num}.")
                except Exception: pass

    add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])
    end_text = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_text += "You can add your own comment below:"

    try:
        if message_to_edit and not comments:
            await message_to_edit.edit_text(end_text, reply_markup=add_comment_button)
        elif message_to_edit and comments:
             await safe_send_message(user_id, end_text, reply_markup=add_comment_button)
        else:
            await safe_send_message(user_id, end_text, reply_markup=add_comment_button)
    except Exception as e:
        logging.warning(f"Could not send/edit final 'Add Comment' prompt to user {user_id}: {e}")

# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, command: CommandObject | None = None):
    deep_link_args = command.args if command else None

    if deep_link_args and deep_link_args.startswith("view_"):
        try:
            confession_id_str = deep_link_args.split("_", 1)[1]
            confession_id = int(confession_id_str)
            logging.info(f"User {message.from_user.id} started bot via deep link for confession {confession_id}")

            async with db.acquire() as conn:
                confession_data = await conn.fetchrow(
                    "SELECT text, category, status, user_id FROM confessions WHERE id = $1",
                    confession_id
                )
                comment_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM comments WHERE confession_id = $1",
                    confession_id
                ) or 0

            if not confession_data or confession_data['status'] != 'approved':
                await message.answer(f"Confession #{confession_id} could not be found or hasn't been approved.")
                return

            confession_text = confession_data['text']
            category = confession_data['category']
            confession_owner_id = confession_data['user_id']

            text_to_show = f"<b>Confession #{confession_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}\n---"
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
            builder.button(text=f"💬 Browse Comments ({comment_count})", callback_data=f"browse_{confession_id}")

            if message.from_user.id == confession_owner_id:
                 builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{confession_id}")
                 builder.adjust(1, 1, 1)
            else:
                 builder.adjust(1, 1)

            await message.answer(text_to_show, reply_markup=builder.as_markup())

        except (ValueError, IndexError):
            logging.warning(f"Invalid deep link payload from user {message.from_user.id}: {deep_link_args}")
            await message.answer("Invalid link format. Send /start for general info or /confess to submit.")
        except Exception as e:
             logging.error(f"Error handling deep link '{deep_link_args}' for user {message.from_user.id}: {e}", exc_info=True)
             await message.answer("An error occurred while processing the link. Please try again.")
    else:
        await message.answer(
            "Welcome! Use the /confess command to share your confession anonymously.\n"
            "It will be reviewed by an admin before posting."
        )

# --- Confession Submission Flow ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    await message.answer("Please choose a category for your confession:", reply_markup=create_category_keyboard())
    await state.set_state(ConfessionForm.waiting_for_category)

@dp.callback_query(StateFilter(ConfessionForm.waiting_for_category), F.data.startswith("category_"))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split("_", 1)[1]
    if category not in CATEGORIES:
        await callback_query.answer("Invalid category selected.", show_alert=True)
        return

    await state.update_data(category=category)
    await state.set_state(ConfessionForm.waiting_for_text)
    try:
        await callback_query.message.edit_text(f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")
    except Exception as e:
        logging.warning(f"Could not edit category message: {e}")
        await callback_query.answer()
        await safe_send_message(callback_query.from_user.id, f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")

@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    confession_text = message.text
    user_id = message.from_user.id
    state_data = await state.get_data()
    category = state_data.get("category")

    if not category:
        await message.answer("⚠️ Error: Category information missing. Please start again with /confess.")
        await state.clear()
        logging.error(f"State data missing category for user {user_id} in receive_confession_text")
        return

    if len(confession_text) < 10:
        await message.answer("Your confession seems a bit short. Please provide more detail (at least 10 characters).")
        return
    if len(confession_text) > 3900:
        await message.answer(f"Your confession is too long (max ~3900 chars). It has {len(confession_text)} characters. Please shorten it.")
        return

    try:
        async with db.acquire() as conn:
            confession_id = await conn.fetchval(
                "INSERT INTO confessions (text, user_id, category, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                confession_text, user_id, category
            )
        if not confession_id:
            await message.answer("Sorry, there was an error submitting your confession. Please try again.")
            logging.error("Failed to get confession_id after insert.")
            await state.clear()
            return

        approve_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{confession_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{confession_id}")]
        ])
        admin_msg = (
            f"New Confession (ID: {confession_id})\n"
            f"Category: {category}\nUser ID: {user_id}\n\n"
            f"{html.quote(confession_text)}"
        )
        if len(admin_msg) > 4090: admin_msg = admin_msg[:4087] + "..."

        await bot.send_message(ADMIN_ID, admin_msg, reply_markup=approve_keyboard)
        await message.answer("✅ Your confession has been submitted for review by the admin.")
        logging.info(f"Confession {confession_id} (Category: {category}) submitted by user {user_id}")

    except Exception as e:
        logging.error(f"Error in receive_confession_text from user {user_id}: {e}", exc_info=True)
        await message.answer("An internal error occurred while submitting your confession. Please try again later.")
    finally:
        await state.clear()


# --- *** MODIFIED: Admin Actions Filter *** ---

# Filter function to check for confession approve/reject format
def is_confession_action_callback(data: str) -> bool:
    if not isinstance(data, str): return False # Ensure data is a string
    parts = data.split("_")
    # Should be exactly 2 parts: ('approve' or 'reject') and (digits)
    return len(parts) == 2 and parts[0] in ('approve', 'reject') and parts[1].isdigit()

@dp.callback_query(lambda c: is_confession_action_callback(c.data)) # Use the specific filter function
async def admin_action(callback_query: types.CallbackQuery):
    global bot_info
    if not bot_info:
        logging.error("Bot info not available for admin action.")
        await callback_query.answer("Internal error: Bot username missing.", show_alert=True)
        return

    # Check authorization using the global ADMIN_ID
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized for this action.", show_alert=True)
        return

    # Parsing should be safe due to the filter
    try:
        action, confession_id_str = callback_query.data.split("_", 1)
        confession_id = int(confession_id_str)
    except (ValueError, IndexError): # Keep as safety net
        logging.error(f"Invalid admin callback data format despite filter: {callback_query.data}")
        await callback_query.answer("Invalid action data format.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            confession = await conn.fetchrow(
                "SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE",
                confession_id
            )
            if not confession:
                await callback_query.answer("Confession not found (maybe already handled?).", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass
                return
            if confession['status'] != 'pending':
                await callback_query.answer(f"Confession #{confession_id} already {confession['status']}.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return

            user_id = confession["user_id"]
            confession_text = confession["text"]
            confession_db_id = confession["id"]
            category = confession["category"]

            try:
                if action == "approve":
                    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_db_id}"
                    text_to_post = f"<b>Confession #{confession_db_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}"
                    initial_markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💬 View / Add Comments (0)", url=deep_link_url)]
                    ])
                    if len(text_to_post) > 4096:
                         logging.error(f"Confession {confession_db_id} text too long ({len(text_to_post)}). Cannot post.")
                         await callback_query.answer("Error: Confession text too long to post.", show_alert=True)
                         # Optionally reject automatically here
                         return
                    channel_message = await bot.send_message(CHANNEL_ID, text_to_post, reply_markup=initial_markup, parse_mode=ParseMode.HTML)
                    await conn.execute(
                        "UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2",
                        channel_message.message_id, confession_db_id
                    )
                    await safe_send_message(user_id, f"✅ Your confession (#{confession_db_id} - #{category}) has been approved and posted!")
                    await callback_query.answer(f"Confession {confession_db_id} approved & posted.")
                    logging.info(f"Admin {callback_query.from_user.id} approved confession {confession_db_id}")
                else: # action == "reject"
                    await conn.execute("UPDATE confessions SET status = 'rejected' WHERE id = $1", confession_db_id)
                    await safe_send_message(user_id, f"❌ Your confession (#{confession_db_id} - #{category}) was rejected by the admin.")
                    await callback_query.answer(f"Confession {confession_db_id} rejected.")
                    logging.info(f"Admin {callback_query.from_user.id} rejected confession {confession_db_id}")

                try:
                    final_admin_text = callback_query.message.html_text + f"\n\n-- Status: {action.capitalize()}ed --"
                    await callback_query.message.edit_text(final_admin_text, reply_markup=None, parse_mode=ParseMode.HTML)
                except Exception as e:
                     logging.warning(f"Could not edit admin action message for confession {confession_id}: {e}")

            except TelegramForbiddenError:
                 logging.error(f"Bot permissions error in channel {CHANNEL_ID} or with user {user_id}.")
                 await callback_query.answer("Error: Check bot permissions or if user blocked.", show_alert=True)
                 raise
            except TelegramBadRequest as e:
                 logging.error(f"Telegram API error processing admin action ({action}) for {confession_id}: {e}", exc_info=True)
                 await callback_query.answer(f"Telegram Error: {e}. Action might fail.", show_alert=True)
                 raise
            except Exception as e:
                logging.error(f"Error processing admin action ({action}) for {confession_id}: {e}", exc_info=True)
                await callback_query.answer(f"An error occurred: {e}. Action might fail.", show_alert=True)
                raise
# --- *** END MODIFIED SECTION *** ---


# --- Commenting Flow ---

@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try:
        confession_id_str = callback_query.data.split("_", 1)[1]
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for browse: {callback_query.data}")
        await callback_query.answer("Invalid data for browsing comments.", show_alert=True)
        return
    await callback_query.answer("Loading comments...")
    await show_comments_for_confession(callback_query.from_user.id, confession_id, message_to_edit=callback_query.message)

@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        confession_id_str = callback_query.data.split("_", 1)[1]
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for add: {callback_query.data}")
        await callback_query.answer("Invalid data for adding comment.", show_alert=True)
        return

    async with db.acquire() as conn:
        confession_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)
    if not confession_exists:
         await callback_query.answer("Cannot add comment: Confession not found or not approved.", show_alert=True)
         try: await callback_query.message.delete()
         except Exception: pass
         return

    await state.update_data(confession_id=confession_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        await safe_send_message(callback_query.from_user.id, f"📝 Please send your comment for Confession #{confession_id}:")
        await callback_query.answer()
    except Exception as e:
        logging.warning(f"Could not send comment prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for comment.", show_alert=True)
        except Exception: pass

@dp.message(CommentForm.waiting_for_comment, F.text)
async def receive_comment(message: types.Message, state: FSMContext):
    comment_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")

    if not confession_id:
        await message.answer("⚠️ Error: Could not determine which confession to comment on. Please try starting again.")
        await state.clear()
        logging.error(f"State data missing confession_id for user {user_id} in receive_comment")
        return
    if len(comment_text) < 2:
        await message.answer("Your comment is too short (min 2 chars).")
        return
    if len(comment_text) > 1000:
        await message.answer(f"Your comment is too long (max 1000 chars). It has {len(comment_text)} chars.")
        return

    confession_owner_id = None
    new_comment_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                 confession_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)
                 if not confession_owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found or not approved.")
                 new_comment_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, NULL) RETURNING id", confession_id, user_id, comment_text)

            await message.answer("💬 Your comment has been added!")
            logging.info(f"User {user_id} added direct comment {new_comment_id} to confession {confession_id}")
            await update_channel_post_button(confession_id)

            if confession_owner_id and confession_owner_id != user_id:
                 logging.info(f"Notifying user {confession_owner_id} about new comment {new_comment_id} on confession {confession_id}")
                 deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                 notification_text = (f"💬 Someone commented on your confession #{confession_id}.\n\nComment: {html.quote(comment_text[:150])}{'...' if len(comment_text) > 150 else ''}\n\n<a href='{deep_link_url}'>Click here to view the comments.</a>")
                 await safe_send_message(confession_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

            await show_comments_for_confession(user_id, confession_id)

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"Attempt to add comment to non-existent/deleted/unapproved confession {confession_id} by user {user_id}")
         await message.answer("⚠️ Sorry, could not add comment. The confession might have been removed or is no longer approved.")
    except Exception as e:
        logging.error(f"Error saving direct comment for confession {confession_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an internal error saving your comment.")
    finally:
        await state.clear()


# --- Reply Flow ---

@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try:
        parent_comment_id_str = callback_query.data.split("_", 1)[1]
        parent_comment_id = int(parent_comment_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for reply: {callback_query.data}")
        await callback_query.answer("Invalid data for replying.", show_alert=True)
        return

    message_id_to_reply_to = callback_query.message.message_id
    async with db.acquire() as conn:
        comment_data = await conn.fetchrow("SELECT confession_id, text FROM comments WHERE id = $1", parent_comment_id)
    if not comment_data:
         await callback_query.answer("Cannot reply: Original comment not found (maybe deleted?).", show_alert=True)
         try: await callback_query.message.edit_reply_markup(reply_markup=None)
         except Exception: pass
         return

    confession_id = comment_data['confession_id']
    parent_comment_text_preview = html.quote(comment_data['text'][:80]) + ('...' if len(comment_data['text']) > 80 else '')
    await state.update_data(confession_id=confession_id, parent_comment_id=parent_comment_id, message_id_to_reply_to=message_id_to_reply_to)
    await state.set_state(CommentForm.waiting_for_reply)
    try:
        prompt_text = (f"📝 Replying to comment:\n<i>{parent_comment_text_preview}</i>\n\nPlease send your reply:")
        await safe_send_message(callback_query.from_user.id, prompt_text, parse_mode=ParseMode.HTML)
        await callback_query.answer()
    except Exception as e:
        logging.warning(f"Could not send reply prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for reply.", show_alert=True)
        except Exception: pass

@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    reply_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")
    parent_comment_id = data.get("parent_comment_id")
    message_id_to_reply_to = data.get("message_id_to_reply_to")

    if not confession_id or not parent_comment_id or not message_id_to_reply_to:
        await message.answer("⚠️ Error: Could not determine what to reply to. Please try starting the reply process again.")
        await state.clear()
        logging.error(f"State data missing required fields for user {user_id} in receive_reply: {data}")
        return
    if len(reply_text) < 1:
        await message.answer("Your reply cannot be empty.")
        return
    if len(reply_text) > 1000:
        await message.answer(f"Your reply is too long (max 1000 chars). It has {len(reply_text)} chars.")
        return

    new_comment_id = None
    parent_comment_owner_id = None
    confession_owner_id = None

    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1 FOR UPDATE", parent_comment_id)
                if not parent_data:
                    await message.answer("⚠️ Sorry, the comment you were replying to seems to have been deleted just now.")
                    await state.clear()
                    return
                parent_comment_owner_id = parent_data['user_id']
                confession_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", confession_id)
                if not confession_owner_id: raise Exception("Confession not found during reply save")
                new_comment_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, $4) RETURNING id", confession_id, user_id, reply_text, parent_comment_id)

        logging.info(f"User {user_id} added reply (ID: {new_comment_id}) to comment {parent_comment_id} on confession {confession_id}")
        await update_channel_post_button(confession_id)

        try:
            reply_author_tag = ""
            if user_id == confession_owner_id: reply_author_tag = "(Author)"
            elif user_id == parent_comment_owner_id: reply_author_tag = "(You)" # Replying to own comment thread
            else: reply_author_tag = "(You)" # Default for the sender
            display_tag = f" {reply_author_tag}" if reply_author_tag else " Anonymous"

            reply_message_text = (f"💬 {html.quote(reply_text)}\n\n<i>Reply #{new_comment_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
            reply_keyboard = await build_comment_keyboard(comment_id=new_comment_id, commenter_user_id=user_id, viewer_user_id=user_id, confession_owner_id=confession_owner_id)

            sent_reply_message = await bot.send_message(chat_id=user_id, text=reply_message_text, reply_to_message_id=message_id_to_reply_to, reply_markup=reply_keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

            if parent_comment_owner_id and parent_comment_owner_id != user_id:
                logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} to their comment {parent_comment_id}")
                deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                notification_text = (f"↪️ Someone replied to your comment on confession #{confession_id}.\n\nReply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n<a href='{deep_link_url}'>Click here to view the comments.</a>")
                await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        except TelegramBadRequest as e:
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Could not send native reply for comment {new_comment_id} - original message {message_id_to_reply_to} deleted? Sending as normal message.")
                 reply_keyboard = await build_comment_keyboard(comment_id=new_comment_id, commenter_user_id=user_id, viewer_user_id=user_id, confession_owner_id=confession_owner_id)
                 reply_message_text = (f"↪️ Replying to comment #{parent_comment_id}\n💬 {html.quote(reply_text)}\n\n<i>Reply #{new_comment_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
                 await safe_send_message(user_id, reply_message_text, reply_markup=reply_keyboard, parse_mode=ParseMode.HTML)
                 if parent_comment_owner_id and parent_comment_owner_id != user_id:
                      logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} (original msg deleted) to their comment {parent_comment_id}")
                      deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                      notification_text = (f"↪️ Someone replied to your comment on confession #{confession_id} (original message might be deleted).\n\nReply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n<a href='{deep_link_url}'>Click here to view the comments.</a>")
                      await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
             else:
                 logging.error(f"Telegram error sending native reply for comment {new_comment_id} by user {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an error displaying it as a direct reply.")
        except Exception as e:
             logging.error(f"Unexpected error sending native reply for comment {new_comment_id} by user {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an internal error displaying it.")

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"FK violation adding reply to comment {parent_comment_id} by user {user_id} (parent likely deleted)")
         await message.answer("⚠️ Sorry, could not add reply. The original comment might have been removed.")
    except Exception as e:
        logging.error(f"Error saving reply DB transaction for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an internal error saving your reply.")
    finally:
        await state.clear()


# --- Reaction Handling ---

@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    try:
        _, reaction_type, comment_id_str = callback_query.data.split("_", 2)
        comment_id = int(comment_id_str)
        user_id = callback_query.from_user.id
        if reaction_type not in ['like', 'dislike']: raise ValueError("Invalid reaction type")
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for reaction: {callback_query.data}")
        await callback_query.answer("Invalid reaction data.", show_alert=True)
        return

    action_taken = "none"; new_keyboard = None; alert_message = None
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                comment_info = await conn.fetchrow("SELECT c.user_id as commenter_id, co.user_id as confession_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comment_id)
                if not comment_info: raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")
                commenter_id = comment_info['commenter_id']; confession_owner_id = comment_info['confession_owner_id']; viewer_id = user_id

                existing_reaction = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", comment_id, user_id)
                if existing_reaction:
                    if existing_reaction == reaction_type:
                        await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comment_id, user_id)
                        action_taken = f"Removed {reaction_type}"; alert_message = f"{reaction_type.capitalize()} removed"
                    else:
                        await conn.execute("UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", reaction_type, comment_id, user_id)
                        action_taken = f"Changed to {reaction_type}"; alert_message = f"Reaction changed to {reaction_type}"
                else:
                    await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comment_id, user_id, reaction_type)
                    action_taken = f"Added {reaction_type}"; alert_message = f"{reaction_type.capitalize()} added"

                new_keyboard = await build_comment_keyboard(comment_id=comment_id, commenter_user_id=commenter_id, viewer_user_id=viewer_id, confession_owner_id=confession_owner_id)
                logging.info(f"User {user_id} action '{action_taken}' on comment {comment_id}. Keyboard rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK violation during reaction update for comment {comment_id} user {user_id}")
                await callback_query.answer("Comment not found.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return
            except Exception as db_err:
                logging.error(f"Database error during reaction processing for comment {comment_id} by user {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("Error processing reaction (database).", show_alert=True)
                return

    if new_keyboard and action_taken != "none":
        try:
            await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
            await callback_query.answer(alert_message)
            logging.info(f"Successfully updated markup for comment {comment_id} after action: {action_taken}")
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.info(f"Markup for comment {comment_id} not modified. Action: {action_taken}.")
                await callback_query.answer(alert_message + " (No visual change)")
            elif "message to edit not found" in err_str:
                logging.warning(f"Message to edit not found for reaction update on {comment_id}. Action: {action_taken}.")
                await callback_query.answer(alert_message + " (Counts updated, view not)", show_alert=False)
            elif "query is too old" in err_str:
                 logging.warning(f"Query too old for reaction update on {comment_id}. Action: {action_taken}.")
                 await callback_query.answer(alert_message + " (Counts updated, view might be stale)", show_alert=False)
            else:
                logging.error(f"Telegram error updating reaction markup for {comment_id}: {e}")
                await callback_query.answer("Error updating reaction display.", show_alert=True)
        except Exception as e:
             logging.error(f"Unexpected error updating reaction markup for {comment_id}: {e}", exc_info=True)
             await callback_query.answer("Error updating reaction display.", show_alert=True)
    elif action_taken != "none":
         logging.error(f"Action {action_taken} for comment {comment_id} done in DB, but failed to build keyboard.")
         await callback_query.answer("Reaction processed (internal error).", show_alert=True)


# --- Contact Request Flow Handlers ---

@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    """Handles the author clicking 'Request Contact' on a comment."""
    try:
        comment_id_str = callback_query.data.split("_", 2)[2]
        comment_id = int(comment_id_str)
        requester_user_id = callback_query.from_user.id
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for request contact: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            comment_data = await conn.fetchrow("SELECT c.user_id AS commenter_id, c.text AS comment_text, co.id AS confession_id, co.user_id AS confession_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1 AND co.status = 'approved'", comment_id)
            if not comment_data:
                await callback_query.answer("Comment or confession not found/accessible.", show_alert=True); return
            commenter_id = comment_data['commenter_id']; confession_id = comment_data['confession_id']; confession_owner_id = comment_data['confession_owner_id']; comment_text_preview = html.quote(comment_data['comment_text'][:100]) + ('...' if len(comment_data['comment_text']) > 100 else '')

            if requester_user_id != confession_owner_id:
                logging.warning(f"User {requester_user_id} tried req contact for comment {comment_id} but isn't owner {confession_owner_id}.")
                await callback_query.answer("You can only request contact for comments on your own confessions.", show_alert=True); return
            if requester_user_id == commenter_id:
                 await callback_query.answer("You cannot request contact with yourself.", show_alert=True); return

            try:
                commenter_chat = await bot.get_chat(commenter_id)
                if not commenter_chat or not commenter_chat.username:
                     await callback_query.answer("This user does not have a public Telegram username set, so contact cannot be requested.", show_alert=True)
                     logging.info(f"Author {requester_user_id} req for comment {comment_id} aborted: commenter {commenter_id} has no username."); return
            except Exception as e:
                 logging.warning(f"Could not fetch commenter {commenter_id} chat info: {e}.")
                 await callback_query.answer("Could not retrieve commenter info (maybe deactivated/blocked bot).", show_alert=True); return

            existing_request = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2 AND status IN ('pending', 'approved')", comment_id, requester_user_id)
            if existing_request:
                await callback_query.answer(f"A contact request for this comment is already {existing_request}.", show_alert=True); return

            try:
                request_id = await conn.fetchval("INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) VALUES ($1, $2, $3, $4, 'pending') ON CONFLICT (comment_id, requester_user_id) DO NOTHING RETURNING id", confession_id, comment_id, requester_user_id, commenter_id)
                if not request_id:
                     existing_status = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2", comment_id, requester_user_id)
                     await callback_query.answer(f"A request already exists (Status: {existing_status}).", show_alert=True); return
            except Exception as insert_err:
                logging.error(f"Failed insert contact request from {requester_user_id} to {commenter_id} for comment {comment_id}: {insert_err}", exc_info=True)
                await callback_query.answer("Failed to save contact request. Try again.", show_alert=True); return

            notification_text = (f"🤝 The author of Confession #{confession_id} would like to contact you regarding your comment:\n\n<i>{comment_text_preview}</i>\n\nDo you approve sharing your Telegram username (@{html.quote(commenter_chat.username)}) with them?")
            approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_contact_{request_id}"), InlineKeyboardButton(text="❌ Deny", callback_data=f"deny_contact_{request_id}") ]])
            sent_to_commenter = await safe_send_message(commenter_id, notification_text, reply_markup=approval_keyboard, parse_mode=ParseMode.HTML)

            if sent_to_commenter:
                await callback_query.answer("✅ Contact request sent to the commenter.", show_alert=False)
                logging.info(f"Contact request {request_id} (comment {comment_id}) sent from author {requester_user_id} to commenter {commenter_id}.")
            else:
                logging.warning(f"Failed send contact request {request_id} notification to commenter {commenter_id}. Rolling back.")
                await callback_query.answer("⚠️ Could not send request. Commenter might have blocked the bot.", show_alert=True)
                raise Exception(f"Failed to notify commenter {commenter_id}, rolling back request {request_id}.")

# --- *** MODIFIED: Contact Response Filter *** ---
# Filter function to check for contact approve/deny format
def is_contact_response_callback(data: str) -> bool:
    if not isinstance(data, str): return False # Ensure data is a string
    parts = data.split("_")
    # Should be exactly 3 parts: ('approve' or 'deny'), 'contact', and (digits)
    return len(parts) == 3 and parts[0] in ('approve', 'deny') and parts[1] == 'contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data)) # Use the specific filter function
async def handle_contact_response(callback_query: types.CallbackQuery):
    """Handles the commenter approving or denying a contact request."""
    try:
        # Parsing should be safe due to the filter
        action, _, request_id_str = callback_query.data.split("_")
        request_id = int(request_id_str)
        responder_user_id = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): # Keep as safety net
        logging.error(f"Invalid callback data format for contact response despite filter: {callback_query.data}")
        await callback_query.answer("Invalid request data format.", show_alert=True)
        return

    new_status_db = 'approved' if action == 'approve' else 'denied' # Status for DB

    async with db.acquire() as conn:
        async with conn.transaction():
            request_data = await conn.fetchrow("SELECT id, requester_user_id, requested_user_id, status, confession_id, comment_id FROM contact_requests WHERE id = $1 FOR UPDATE", request_id)
            if not request_data:
                await callback_query.answer("This contact request was not found.", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass; return
            if responder_user_id != request_data['requested_user_id']:
                logging.warning(f"User {responder_user_id} tried respond to contact request {request_id} for {request_data['requested_user_id']}.")
                await callback_query.answer("Invalid request.", show_alert=True); return
            if request_data['status'] != 'pending':
                await callback_query.answer(f"This request was already {request_data['status']}.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass; return

            author_notification_text = ""; requester_user_id = request_data['requester_user_id']; confession_id = request_data['confession_id']; comment_id = request_data['comment_id']
            new_status_for_edit = "" # Status text for editing message

            if new_status_db == 'approved':
                commenter_username = callback_query.from_user.username
                if commenter_username:
                    await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", request_id)
                    author_notification_text = (f"✅ Contact Approved!\n\nRequest for Confession #{confession_id} (Comment approx ID: {comment_id}) was APPROVED.\n\nContact user at: @{html.quote(commenter_username)}")
                    await callback_query.answer("Request approved. Your username has been shared.")
                    logging.info(f"Contact request {request_id} approved by {responder_user_id}. Username @{commenter_username} sent to {requester_user_id}.")
                    new_status_for_edit = 'Approved (Username Shared)'
                else:
                    new_status_db = 'approved_no_username' # Update DB status
                    await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", new_status_db, request_id)
                    author_notification_text = (f"⚠️ Contact Approved (No Username)\n\nRequest for Confession #{confession_id} (Comment approx ID: {comment_id}) was APPROVED, but user currently lacks a public username.")
                    await callback_query.answer("Request approved, but you have no public username. Author cannot contact you via username.", show_alert=True)
                    logging.info(f"Contact request {request_id} approved by {responder_user_id}, but no username. Notified author {requester_user_id}.")
                    new_status_for_edit = 'Approved (No Username)'
            else: # denied
                 await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", request_id)
                 author_notification_text = (f"❌ Contact Denied\n\nRequest for Confession #{confession_id} (Comment approx ID: {comment_id}) was DENIED.")
                 await callback_query.answer("Request denied. Username not shared.")
                 logging.info(f"Contact request {request_id} denied by {responder_user_id}. Notified author {requester_user_id}.")
                 new_status_for_edit = 'Denied'

            await safe_send_message(requester_user_id, author_notification_text, parse_mode=ParseMode.HTML)

            try:
                original_text = callback_query.message.html_text
                final_text = f"{original_text}\n\n<b>Status: {new_status_for_edit}</b>"
                await callback_query.message.edit_text(final_text, reply_markup=None, parse_mode=ParseMode.HTML)
            except TelegramBadRequest as e:
                 if "message is not modified" not in str(e).lower(): raise # Reraise other errors
            except Exception as e:
                logging.warning(f"Could not edit commenter's ({responder_user_id}) notification message {callback_query.message.message_id} for req {request_id}: {e}")
# --- *** END MODIFIED SECTION *** ---

@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    """Allows the confession author to view the status of their requests for a specific confession."""
    try:
        confession_id_str = callback_query.data.split("_", 2)[2]
        confession_id = int(confession_id_str)
        viewer_user_id = callback_query.from_user.id
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for view requests: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True); return

    async with db.acquire() as conn:
        confession_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", confession_id)
        if not confession_owner_id: await callback_query.answer("Confession not found.", show_alert=True); return
        if viewer_user_id != confession_owner_id: await callback_query.answer("You can only view requests for your own confessions.", show_alert=True); return

        requests = await conn.fetch("SELECT cr.comment_id, cr.status, cr.updated_at, c.text as comment_text FROM contact_requests cr JOIN comments c ON cr.comment_id = c.id WHERE cr.confession_id = $1 AND cr.requester_user_id = $2 ORDER BY cr.updated_at DESC", confession_id, viewer_user_id)

    if not requests:
        await callback_query.answer("You haven't made any contact requests for this confession.", show_alert=False); return

    response_text = f"<b>Contact Requests Status for Confession #{confession_id}</b>\n\n"
    for req in requests:
        comment_preview = html.quote(req['comment_text'][:50]) + ('...' if len(req['comment_text']) > 50 else '')
        status_text = req['status'].replace('_', ' ').capitalize()
        updated_time = req['updated_at'].strftime("%Y-%m-%d %H:%M")
        response_text += (f"• Comment starting: \"<i>{comment_preview}</i>\"\n  Status: <b>{status_text}</b>\n  Last Update: {updated_time}\n\n")

    if len(response_text) > 4096: response_text = response_text[:4090] + "\n\n...(list truncated)"
    await safe_send_message(viewer_user_id, response_text, parse_mode=ParseMode.HTML)
    await callback_query.answer()


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply("Hi there! 👋\nTo share something anonymously, please use the /confess command.\nIf you clicked a link, please use the buttons provided.")

# --- Main Execution ---
async def main():
    # ADMIN_ID is already validated and converted to int at the top level
    try:
        await setup() # Setup database and get bot info
        if db and bot_info:
            logging.info("Starting bot polling...")
            await dp.start_polling(bot, skip_updates=True)
        else:
            logging.critical("Database connection or bot info missing after setup. Bot cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during bot setup or polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        if bot and bot.session:
            session = bot.session
            if session and not session.closed:
                 await session.close(); logging.info("Bot session closed.")
        if db:
            logging.info("Closing database pool..."); await db.close(); logging.info("Database pool closed.")
        logging.info("Bot stopped.")

if __name__ == "__main__":
    # Environment variables are loaded and validated at the beginning of the script
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as main_err:
        logging.critical(f"Critical error in main execution loop: {main_err}", exc_info=True)
        print(f"Critical error: {main_err}")

# --- END OF FILE main.py ---