# --- START OF FILE main.py ---

import logging
import asyncpg
import os
import asyncio
import re # Import regular expressions for parsing admin replies
from aiogram import Bot, Dispatcher, types, F, html # Import html for formatting
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, StateFilter, BaseFilter # Import BaseFilter
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder # Use builder for dynamic keyboards
from datetime import datetime # For formatting timestamps
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional

# --- Constants ---
CATEGORIES = [
    "Relationship", "Education", "Family", "School", "Friendship",
    "Religion", "Entertainment", "Information", "Sexual Assault", "Other"
]
PRIVACY_POLICY_URL = "https://telegra.ph/Privacy-Policy-for-AAU-Confession-Bot-04-16"

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_STR = os.getenv("ADMIN_ID")
ADMIN_ID = int(ADMIN_ID_STR) if ADMIN_ID_STR and ADMIN_ID_STR.isdigit() else None
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Check essential variables
if not BOT_TOKEN:
    logging.critical("FATAL: BOT_TOKEN environment variable not set!")
    exit()
if not ADMIN_ID:
    logging.critical("FATAL: ADMIN_ID environment variable not set or not a valid integer!")
    exit()
if not CHANNEL_ID:
    logging.critical("FATAL: CHANNEL_ID environment variable not set!")
    exit()
if not DATABASE_URL:
    logging.critical("FATAL: DATABASE_URL environment variable not set!")
    exit()


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

class ContactAdminForm(StatesGroup):
    waiting_for_message = State()

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
        # Create/Check Confessions Table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY, text TEXT NOT NULL, user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending', message_id BIGINT,
                category VARCHAR(50), created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
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
        logging.info("Checked/Created/Altered 'confessions' table.")

        # Create/Check Comments Table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY, confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL, text TEXT NOT NULL,
                parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
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
        logging.info("Checked/Created/Altered 'comments' table.")

        # Create/Check Reactions Table
        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions (
                 id SERIAL PRIMARY KEY, comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL, reaction_type VARCHAR(10) NOT NULL,
                 created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id)
             );
             COMMENT ON TABLE reactions IS 'Stores likes and dislikes for comments';
        """)
        logging.info("Checked/Created 'reactions' table.")
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
            FROM reactions WHERE comment_id = $1
            """, comment_id
        )
    return counts['likes'] if counts else 0, counts['dislikes'] if counts else 0

# REMOVED: build_comment_keyboard - no longer used with combined view/command reactions

async def safe_send_message(user_id: int, text: str, **kwargs):
    """Safely send a message, handling common errors like user blocking."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        logging.warning(f"Could not send message to user {user_id}: {e}")
    except TelegramRetryAfter as e:
        logging.warning(f"Flood control exceeded for user {user_id}. Retrying after {e.retry_after}s")
        await asyncio.sleep(e.retry_after)
        try:
            await bot.send_message(user_id, text, **kwargs)
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
            "SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'", confession_id
        )
        comment_count = await conn.fetchval(
            "SELECT COUNT(*) FROM comments WHERE confession_id = $1", confession_id
        ) or 0

    if not confession_data or not confession_data['message_id']:
        logging.warning(f"Could not find approved confession or channel message_id for {confession_id} to update button.")
        return

    channel_message_id = confession_data['message_id']
    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
    new_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💬 View / Add Comments ({comment_count})", url=deep_link_url)]
    ])

    try:
        await bot.edit_message_reply_markup(CHANNEL_ID, channel_message_id, reply_markup=new_markup)
        logging.info(f"Updated comment count ({comment_count}) on channel post for confession {confession_id}")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logging.info(f"Channel button for {confession_id} already up-to-date ({comment_count}).")
        elif "message to edit not found" in str(e).lower():
             logging.warning(f"Cannot update channel button: Message {channel_message_id} not found in {CHANNEL_ID} (confession {confession_id}).")
        else:
            logging.error(f"Failed to edit channel post {channel_message_id} for {confession_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating channel button for {confession_id}: {e}", exc_info=True)

# --- MODIFIED: Function to format comments ---
async def format_comments_html(user_id: int, confession_id: int, confession_owner_id: int) -> str:
    """Fetches and formats comments into an HTML string.
       Adds sequence numbers, (Author)/(You) tags, reply prefixes, and reaction counts.
    """
    async with db.acquire() as conn:
        comments = await conn.fetch(
            """
            SELECT id, user_id, text, parent_comment_id, created_at
            FROM comments WHERE confession_id = $1 ORDER BY created_at ASC
            """, confession_id
        )

    if not comments:
        return "<i>No comments yet. Be the first!</i>\n"

    comments_html_parts = []
    comment_id_to_sequence = {}
    comment_counter = 0

    # First pass: Assign sequence numbers
    temp_comment_map = {}
    for c_data in comments:
        comment_counter += 1
        db_comment_id = c_data['id']
        comment_id_to_sequence[db_comment_id] = comment_counter
        temp_comment_map[db_comment_id] = c_data # Store data for second pass

    # Second pass: Generate HTML for each comment
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

        # Author/You tag logic
        author_tag = ""
        if commenter_user_id == confession_owner_id:
            author_tag = "(Author)"
        elif commenter_user_id == user_id:
            author_tag = "(You)"

        display_tag = f" {author_tag}" if author_tag else ""
        user_display = f"User{display_tag}" if display_tag else "Anonymous"
        comment_metadata = f"<i>#{current_sequence_num} {user_display} | {timestamp}</i>"

        # Add like/dislike counts directly in the text
        likes, dislikes = await get_comment_reactions(comment_id)
        reaction_text = f"[👍{likes} 👎{dislikes}]"

        # Combine parts for this comment
        full_comment_text = f"{reply_prefix}💬 {comment_text}\n{comment_metadata} {reaction_text}"
        comments_html_parts.append(full_comment_text)

    # Join all comment parts
    return "\n\n".join(comments_html_parts)


# --- Filter for Admin Replies ---
class IsAdminReplyFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        global bot_info
        if not bot_info: return False
        return (
            message.reply_to_message is not None and
            message.from_user.id == ADMIN_ID and
            message.reply_to_message.from_user.id == bot_info.id
        )


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
                    "SELECT text, category, status FROM confessions WHERE id = $1", confession_id
                )
                # No need for comment count initially

            if not confession_data or confession_data['status'] != 'approved':
                await message.answer(f"Confession #{confession_id} could not be found or hasn't been approved.")
                return

            confession_text = confession_data['text']
            category = confession_data['category']
            text_to_show = f"<b>Confession #{confession_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}\n---"

            # Buttons to load comments or add a new one
            builder = InlineKeyboardBuilder()
            builder.button(text=f"💬 Load / Refresh Comments", callback_data=f"browse_{confession_id}")
            builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
            builder.adjust(1)

            await message.answer(text_to_show, reply_markup=builder.as_markup())

        except (ValueError, IndexError):
            logging.warning(f"Invalid deep link payload: {deep_link_args}")
            await message.answer("Invalid link format. Use /start or /confess.")
        except Exception as e:
             logging.error(f"Error handling deep link '{deep_link_args}': {e}", exc_info=True)
             await message.answer("An error occurred processing the link.")
    else:
        await message.answer(
            "Welcome! Use /confess to share anonymously.\n"
            "Reviewed before posting.\n\nUse /help for more options."
        )

@dp.message(Command("privacy"))
async def privacy_policy(message: types.Message):
    """Handles the /privacy command."""
    privacy_text = (
        f"🔒 <b>Privacy Policy</b>\n\n"
        f"Your Telegram User ID is stored for notifications and identifying your comments/confessions (to admin or as 'Author'/'You'). It's shown to the admin upon submission or direct contact.\n\n"
        f"<b>Never</b> displayed publicly otherwise.\n\n"
        f"Full policy: {html.link('View Privacy Policy', PRIVACY_POLICY_URL)}"
    )
    await message.answer(privacy_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@dp.message(Command("help"))
async def show_help(message: types.Message):
    """Handles the /help command."""
    help_text = (
        "ℹ️ <b>Help & Commands</b>\n\n"
        "• /confess - Start submitting a confession.\n"
        "• /privacy - View privacy info.\n"
        "• /start - Welcome message or deep link processing.\n"
        "• /reply <code>ConfessionID Comment#</code> - Reply to a specific comment.\n"
        "   <i>(Example: /reply 123 5)</i>\n"
        "• /like <code>ConfessionID Comment#</code> - Like a comment.\n"
        "• /dislike <code>ConfessionID Comment#</code> - Dislike a comment.\n\n"
        "Need to contact the admin directly?"
    )
    contact_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin")]
    ])
    await message.answer(help_text, reply_markup=contact_keyboard)

@dp.callback_query(F.data == "contact_admin", StateFilter(None))
async def prompt_admin_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Asks the user for the message to send to the admin."""
    await state.set_state(ContactAdminForm.waiting_for_message)
    prompt_text = ("Okay, please send the message you'd like to forward to the admin.\n\n"
                   "<i>Note: The admin will see your Telegram profile and ID to reply.</i>")
    try:
        await callback_query.message.edit_text(prompt_text, reply_markup=None)
    except TelegramBadRequest:
         await safe_send_message(callback_query.from_user.id, prompt_text)
    await callback_query.answer()

@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def forward_to_admin(message: types.Message, state: FSMContext):
    """Forwards the user's message to the admin."""
    user = message.from_user
    user_message_text = message.text

    if len(user_message_text) < 5: await message.reply("Message too short."); return
    if len(user_message_text) > 2000: await message.reply("Message too long."); return

    user_mention_link = user.mention_html(user.full_name)
    admin_notification_text = (
        f"✉️ <b>Direct Message from User</b>\n\n"
        f"<b>From:</b> {user_mention_link} (ID: <code>{user.id}</code>)\n"
        f"------------------------------------\n"
        f"{html.quote(user_message_text)}\n"
        f"------------------------------------\n"
        f"✍️ <b>Reply to this message directly</b> to respond."
    )

    try:
        await bot.send_message(ADMIN_ID, admin_notification_text, parse_mode=ParseMode.HTML)
        await message.answer("✅ Your message sent to the admin.")
        logging.info(f"User {user.id} sent direct message to admin.")
    except Exception as e:
        logging.error(f"Failed to forward message from {user.id} to admin: {e}", exc_info=True)
        await message.answer("❌ Error sending message. Please try again later.")
    finally:
        await state.clear()

@dp.message(IsAdminReplyFilter(), F.text)
async def handle_admin_reply(message: types.Message):
    """Handles the admin replying to a user's forwarded message."""
    admin_reply_text = message.text
    original_bot_message = message.reply_to_message

    target_user_id = None
    match = re.search(r"\(ID:\s*<code>(\d+)</code>\)", original_bot_message.html_text, re.IGNORECASE)
    if match:
        target_user_id = int(match.group(1))
        logging.info(f"Admin ({message.from_user.id}) replying to user {target_user_id}")
    else:
        logging.error(f"Could not parse target user ID from admin reply msg: {original_bot_message.html_text}")
        await message.reply("⚠️ Error: Couldn't identify user ID. Reply *directly* to the bot's message containing '(ID: <code>...</code>)'.")
        return

    user_notification_text = f"ℹ️ <b>Reply from Admin:</b>\n\n{html.quote(admin_reply_text)}"
    sent_successfully = await safe_send_message(target_user_id, user_notification_text, parse_mode=ParseMode.HTML)

    if sent_successfully:
        await message.reply("✅ Reply sent to the user.")
    else:
        await message.reply(f"❌ Failed to send reply to user {target_user_id} (maybe blocked).")


# --- Confession Submission Flow ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    await message.answer("Please choose a category:", reply_markup=create_category_keyboard())
    await state.set_state(ConfessionForm.waiting_for_category)

@dp.callback_query(StateFilter(ConfessionForm.waiting_for_category), F.data.startswith("category_"))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split("_", 1)[1]
    if category not in CATEGORIES:
        await callback_query.answer("Invalid category.", show_alert=True); return

    await state.update_data(category=category)
    await state.set_state(ConfessionForm.waiting_for_text)
    prompt_text = f"Category: <b>{category}</b>\n\nSend your confession text:"
    try:
        await callback_query.message.edit_text(prompt_text)
    except Exception as e:
        logging.warning(f"Could not edit category msg: {e}")
        await safe_send_message(callback_query.from_user.id, prompt_text)
    await callback_query.answer()


@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    confession_text = message.text
    user = message.from_user
    user_id = user.id
    state_data = await state.get_data()
    category = state_data.get("category")

    if not category:
        await message.answer("⚠️ Category missing. Start again with /confess."); await state.clear(); return
    if len(confession_text) < 10: await message.answer("Confession too short."); return
    if len(confession_text) > 3900: await message.answer("Confession too long."); return

    try:
        async with db.acquire() as conn:
            confession_id = await conn.fetchval(
                "INSERT INTO confessions (text, user_id, category, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                confession_text, user_id, category
            )
        if not confession_id: raise Exception("Failed to get confession ID after insert.")

        approve_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{confession_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{confession_id}")]
        ])
        user_mention_link = user.mention_html(user.full_name)
        admin_notification_text = (
            f"<b>New Confession #{confession_id}</b>\n\n"
            f"<b>Category:</b> {category}\n"
            f"<b>From:</b> {user_mention_link} (ID: <code>{user_id}</code>)\n"
            f"------------------------------------\n"
            f"{html.quote(confession_text)}"
        )
        await bot.send_message(ADMIN_ID, admin_notification_text, reply_markup=approve_keyboard, parse_mode=ParseMode.HTML)
        await message.answer("✅ Confession submitted for review.")
        logging.info(f"Confession {confession_id} ({category}) submitted by {user_id} ({user.username or ''})")

    except Exception as e:
        logging.error(f"Error receiving confession from {user_id}: {e}", exc_info=True)
        await message.answer("❌ Error submitting confession. Please try again later.")
    finally:
        await state.clear()


# --- Admin Actions ---
@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def admin_action(callback_query: types.CallbackQuery):
    global bot_info
    if not bot_info:
        await callback_query.answer("Error: Bot info missing.", show_alert=True)
        return
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("Unauthorized.", show_alert=True)
        return

    try:
        action, confession_id_str = callback_query.data.split("_", 1)
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        await callback_query.answer("Invalid data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            confession = await conn.fetchrow(
                "SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE", confession_id
            )

            # --- FIX START ---
            if not confession:
                await callback_query.answer("Not found.", show_alert=True)
                try:
                    await callback_query.message.delete()
                except Exception:
                    pass # Ignore if message already deleted
                return # Exit the function

            if confession['status'] != 'pending':
                await callback_query.answer(f"Already {confession['status']}.", show_alert=True)
                try:
                    await callback_query.message.delete()
                except Exception:
                    pass # Ignore if message already deleted
                return # Exit the function
            # --- FIX END ---

            user_id, text, db_id, category = confession["user_id"], confession["text"], confession["id"], confession["category"]

            try:
                if action == "approve":
                    deep_link = f"https://t.me/{bot_info.username}?start=view_{db_id}"
                    post_text = f"<b>Confession #{db_id}</b>\n\n{html.quote(text)}\n\n#{category}"
                    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=deep_link)]])
                    msg = await bot.send_message(CHANNEL_ID, post_text, reply_markup=markup, parse_mode=ParseMode.HTML)
                    await conn.execute("UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2", msg.message_id, db_id)
                    await safe_send_message(user_id, f"✅ Your confession (#{db_id} - #{category}) approved & posted!")
                    await callback_query.answer(f"Confession {db_id} approved.")
                    logging.info(f"Admin approved confession {db_id}")
                else: # reject
                    await conn.execute("UPDATE confessions SET status = 'rejected' WHERE id = $1", db_id)
                    await safe_send_message(user_id, f"❌ Your confession (#{db_id} - #{category}) was rejected.")
                    await callback_query.answer(f"Confession {db_id} rejected.")
                    logging.info(f"Admin rejected confession {db_id}")

                # Clean up admin msg (outside the if/else for approval/rejection)
                try:
                    await callback_query.message.delete()
                except Exception as del_e:
                    logging.warning(f"Could not delete admin action message for {db_id}: {del_e}")

            except TelegramForbiddenError:
                await callback_query.answer("Error: Check bot permissions/user block.", show_alert=True)
                raise # Reraise to ensure transaction rollback
            except Exception as e:
                logging.error(f"Error processing admin action {action} for {db_id}: {e}", exc_info=True)
                await callback_query.answer(f"Error: {e}", show_alert=True)
                raise # Reraise to ensure transaction rollback


# --- Commenting Flow ---

# --- MODIFIED: browse_comments_action ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try:
        confession_id_str = callback_query.data.split("_", 1)[1]
        confession_id = int(confession_id_str)
        user_id = callback_query.from_user.id
    except (ValueError, IndexError): await callback_query.answer("Invalid data.", show_alert=True); return

    await callback_query.answer("Loading comments...")

    async with db.acquire() as conn:
        confession_data = await conn.fetchrow(
            "SELECT text, category, status, user_id FROM confessions WHERE id = $1", confession_id
        )

    if not confession_data or confession_data['status'] != 'approved':
        err_text = f"Confession #{confession_id} not found or not approved."
        try: await callback_query.message.edit_text(err_text, reply_markup=None)
        except Exception: await safe_send_message(user_id, err_text)
        return

    confession_text, category, owner_id = confession_data['text'], confession_data['category'], confession_data['user_id']
    header_html = f"<b>Confession #{confession_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}\n--- Comments ---\n\n"
    comments_html = await format_comments_html(user_id, confession_id, owner_id)
    combined_text = header_html + comments_html

    builder = InlineKeyboardBuilder()
    builder.button(text=f"🔄 Refresh Comments", callback_data=f"browse_{confession_id}")
    builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
    builder.adjust(1)
    new_markup = builder.as_markup()

    try:
        if len(combined_text) > 4090:
            logging.warning(f"Combined text too long for confession {confession_id}. Sending simplified.")
            simplified_text = header_html + "\n<i>Comments truncated due to length. Use /like, /dislike, /reply commands.</i>"
            await callback_query.message.edit_text(simplified_text, reply_markup=new_markup, parse_mode=ParseMode.HTML)
        else:
            await callback_query.message.edit_text(combined_text, reply_markup=new_markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower(): await callback_query.answer("Comments up to date.")
        else: logging.error(f"Failed editing msg for {confession_id}: {e}"); await callback_query.answer("Error displaying.", show_alert=True)
    except Exception as e:
        logging.error(f"Error displaying comments for {confession_id}: {e}", exc_info=True); await callback_query.answer("Internal error.", show_alert=True)


@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    try: confession_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError): await callback_query.answer("Invalid data.", show_alert=True); return

    async with db.acquire() as conn: exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id=$1 AND status='approved'", confession_id)
    if not exists: await callback_query.answer("Confession not found/approved.", show_alert=True); try:await callback_query.message.edit_reply_markup(None);except Exception:pass; return

    await state.update_data(confession_id=confession_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    await safe_send_message(callback_query.from_user.id, f"📝 Send comment for Confession #{confession_id}:")
    await callback_query.answer()


# --- MODIFIED: receive_comment ---
@dp.message(CommentForm.waiting_for_comment, F.text)
async def receive_comment(message: types.Message, state: FSMContext):
    comment_text, user_id = message.text, message.from_user.id
    data = await state.get_data(); confession_id = data.get("confession_id")
    if not confession_id: await message.answer("⚠️ Error: Confession ID missing. Try again."); await state.clear(); return
    if len(comment_text) < 2: await message.answer("Comment too short."); return
    if len(comment_text) > 1000: await message.answer("Comment too long."); return

    owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                 owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id=$1 AND status='approved'", confession_id)
                 if not owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found/approved.")
                 await conn.execute("INSERT INTO comments (confession_id, user_id, text) VALUES ($1, $2, $3)", confession_id, user_id, comment_text)
        await message.answer("💬 Comment added! Refresh view to see.") # MODIFIED Confirmation
        logging.info(f"User {user_id} added comment to {confession_id}")
        await update_channel_post_button(confession_id)
        if owner_id and owner_id != user_id:
             link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
             preview = html.quote(comment_text[:150]) + ('...' if len(comment_text)>150 else '')
             notify_text = f"💬 Comment on your confession #{confession_id}:\n\n{preview}\n\n<a href='{link}'>View comments.</a>"
             await safe_send_message(owner_id, notify_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        # --- REMOVED automatic refresh ---
    except asyncpg.exceptions.ForeignKeyViolationError: await message.answer("⚠️ Cannot add comment: Confession removed/unapproved.")
    except Exception as e: logging.error(f"Error saving comment for {confession_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Error saving comment.")
    finally: await state.clear()


# --- Reply Flow ---

# NEW: /reply command handler
@dp.message(Command("reply"), StateFilter(None))
async def reply_command_prompt(message: types.Message, command: CommandObject, state: FSMContext):
    """Handles /reply <confession_id> <comment_sequence_number>"""
    args = command.args
    if not args: await message.reply("Usage: `/reply <ConfessionID> <Comment#>`"); return
    parts = args.split();
    if len(parts) != 2: await message.reply("Format: `/reply <ConfessionID> <Comment#>`"); return

    try: confession_id, target_seq_num = int(parts[0]), int(parts[1]); assert target_seq_num > 0
    except (ValueError, AssertionError): await message.reply("Invalid ID/Comment#. Must be positive numbers."); return

    parent_comment_data = None
    async with db.acquire() as conn:
        comments = await conn.fetch("SELECT id, text FROM comments WHERE confession_id=$1 ORDER BY created_at ASC", confession_id)
        if not comments or target_seq_num > len(comments): await message.reply(f"Comment #{target_seq_num} not found in Confession #{confession_id}."); return
        parent_comment_data = comments[target_seq_num - 1]
        parent_id = parent_comment_data['id']
        preview = html.quote(parent_comment_data['text'][:80]) + ('...' if len(parent_comment_data['text']) > 80 else '')

    await state.update_data(confession_id=confession_id, parent_comment_id=parent_id)
    await state.set_state(CommentForm.waiting_for_reply)
    prompt = f"📝 Replying to Comment #{target_seq_num} (Confession #{confession_id}):\n<i>{preview}</i>\n\nSend your reply:"
    await safe_send_message(message.from_user.id, prompt, parse_mode=ParseMode.HTML)


# --- MODIFIED: receive_reply ---
@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    reply_text, user_id = message.text, message.from_user.id
    data = await state.get_data(); confession_id, parent_id = data.get("confession_id"), data.get("parent_comment_id")
    if not confession_id or not parent_id: await message.answer("⚠️ Error: Reply context lost. Use /reply again."); await state.clear(); return
    if len(reply_text) < 1: await message.answer("Reply too short."); return
    if len(reply_text) > 1000: await message.answer("Reply too long."); return

    parent_owner_id, new_id = None, None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id=$1 FOR UPDATE", parent_id)
                if not parent_data: await message.answer("⚠️ Original comment deleted."); await state.clear(); return
                parent_owner_id = parent_data['user_id']
                new_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1,$2,$3,$4) RETURNING id", confession_id, user_id, reply_text, parent_id)
        logging.info(f"User {user_id} added reply {new_id} to {parent_id} (confession {confession_id}) via /reply")
        await update_channel_post_button(confession_id)
        await message.answer(f"↪️ Reply sent! Refresh view to see.")
        if parent_owner_id and parent_owner_id != user_id:
             link = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
             preview = html.quote(reply_text[:150]) + ('...' if len(reply_text)>150 else '')
             notify_text = f"↪️ Reply to your comment (Confession #{confession_id}):\n\n{preview}\n\n<a href='{link}'>View comments.</a>"
             await safe_send_message(parent_owner_id, notify_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        # --- REMOVED automatic refresh ---
    except asyncpg.exceptions.ForeignKeyViolationError: await message.answer("⚠️ Cannot reply: Confession/comment removed.")
    except Exception as e: logging.error(f"Error saving reply {parent_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Error saving reply.")
    finally: await state.clear()


# --- Reaction Handling ---

# NEW: /like and /dislike command handlers
async def process_reaction_command(message: types.Message, command: CommandObject, reaction_type: str):
    """Handles /like or /dislike <confession_id> <comment_sequence_number>"""
    args = command.args
    if not args: await message.reply(f"Usage: `/{reaction_type} <ConfessionID> <Comment#>`"); return
    parts = args.split()
    if len(parts) != 2: await message.reply(f"Format: `/{reaction_type} <ConfessionID> <Comment#>`"); return

    try: confession_id, target_seq_num = int(parts[0]), int(parts[1]); assert target_seq_num > 0
    except (ValueError, AssertionError): await message.reply("Invalid ID/Comment#. Must be positive numbers."); return

    user_id = message.from_user.id
    target_comment_id, alert_message = None, None

    async with db.acquire() as conn:
        async with conn.transaction():
            comments = await conn.fetch("SELECT id FROM comments WHERE confession_id=$1 ORDER BY created_at ASC", confession_id)
            if not comments or target_seq_num > len(comments): await message.reply(f"Comment #{target_seq_num} not found in Confession #{confession_id}."); return
            target_comment_id = comments[target_seq_num - 1]['id']

            try:
                existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id=$1 AND user_id=$2 FOR UPDATE", target_comment_id, user_id)
                if existing:
                    if existing == reaction_type:
                        await conn.execute("DELETE FROM reactions WHERE comment_id=$1 AND user_id=$2", target_comment_id, user_id)
                        alert_message = f"{reaction_type.capitalize()} removed from Comment #{target_seq_num}."
                    else:
                        await conn.execute("UPDATE reactions SET reaction_type=$1, created_at=CURRENT_TIMESTAMP WHERE comment_id=$2 AND user_id=$3", reaction_type, target_comment_id, user_id)
                        alert_message = f"Reaction changed to {reaction_type} for Comment #{target_seq_num}."
                else:
                    await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", target_comment_id, user_id, reaction_type)
                    alert_message = f"{reaction_type.capitalize()} added to Comment #{target_seq_num}."
                logging.info(f"User {user_id} {reaction_type} comment {target_comment_id} (Seq: {target_seq_num}) via command.")
            except Exception as db_err: logging.error(f"DB error reaction cmd {target_comment_id} by {user_id}: {db_err}", exc_info=True); await message.reply("Error processing reaction (database)."); return # Rolls back

    if alert_message: await message.reply(alert_message + " Refresh view to see counts.")


@dp.message(Command("like"), StateFilter(None))
async def like_command(message: types.Message, command: CommandObject):
    await process_reaction_command(message, command, "like")

@dp.message(Command("dislike"), StateFilter(None))
async def dislike_command(message: types.Message, command: CommandObject):
    await process_reaction_command(message, command, "dislike")


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply("Use commands like /confess or /help.")

# --- Main Execution ---
async def main():
    try:
        await setup()
        if db and bot_info:
            await bot.delete_webhook(drop_pending_updates=True) # Clear old updates
            await dp.start_polling(bot)
        else:
            logging.critical("DB connection or bot info missing. Cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during startup/polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        session = bot.session
        if session and not session.closed: await session.close()
        if db: logging.info("Closing database pool..."); await db.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as global_error:
        logging.critical(f"Unhandled exception during main execution: {global_error}", exc_info=True)

# --- END OF FILE main.py ---