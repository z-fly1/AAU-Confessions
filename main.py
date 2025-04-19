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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove # Added ReplyKeyboardRemove
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

class ContactAdminForm(StatesGroup): # State for contacting admin
    waiting_for_message = State()

# --- Database ---
db = None
async def create_db_pool():
    # ... (database pool creation remains the same) ...
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
    # ... (database table creation remains the same) ...
    global db, bot_info
    db = await create_db_pool()
    bot_info = await bot.get_me()
    logging.info(f"Bot started: @{bot_info.username}")

    async with db.acquire() as conn:
        # --- Create Confessions Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY, text TEXT NOT NULL, user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending', message_id BIGINT, category VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP );
        """)
        logging.info("Checked/Created 'confessions' table.")
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='category') THEN ALTER TABLE confessions ADD COLUMN category VARCHAR(50); END IF;
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE confessions ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE confessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                COMMENT ON COLUMN confessions.category IS 'Category chosen by the user'; COMMENT ON COLUMN confessions.message_id IS 'Message ID of the post in the channel';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'confessions'.")
        # --- Create Comments Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments ( id SERIAL PRIMARY KEY, confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL, text TEXT NOT NULL, parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP );
        """)
        logging.info("Checked/Created 'comments' table.")
        await conn.execute("""
             DO $$ BEGIN
                 IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='parent_comment_id') THEN ALTER TABLE comments ADD COLUMN parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL; END IF;
                 IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='comments' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE comments ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE comments ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                 COMMENT ON COLUMN comments.parent_comment_id IS 'ID of the comment this is a reply to';
            END $$;
        """)
        logging.info("Checked/Applied ALTER/COMMENT statements for 'comments'.")
        # --- Create Reactions Table ---
        await conn.execute("""
             CREATE TABLE IF NOT EXISTS reactions ( id SERIAL PRIMARY KEY, comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
                 user_id BIGINT NOT NULL, reaction_type VARCHAR(10) NOT NULL, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 UNIQUE(comment_id, user_id) );
             COMMENT ON TABLE reactions IS 'Stores likes and dislikes for comments';
        """)
        logging.info("Checked/Created 'reactions' table.")
        # --- Create Contact Requests Table ---
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_requests ( id SERIAL PRIMARY KEY, confession_id INTEGER NOT NULL REFERENCES confessions(id) ON DELETE CASCADE,
                comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE, requester_user_id BIGINT NOT NULL, requested_user_id BIGINT NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending', created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (comment_id, requester_user_id) );
            COMMENT ON TABLE contact_requests IS 'Stores requests from confession authors to contact commenters.'; COMMENT ON COLUMN contact_requests.requester_user_id IS 'User ID of the confession author making the request.';
            COMMENT ON COLUMN contact_requests.requested_user_id IS 'User ID of the commenter being asked for contact.'; COMMENT ON COLUMN contact_requests.status IS 'pending, approved, denied, approved_no_username';
        """)
        logging.info("Checked/Created 'contact_requests' table.")
        logging.info("Database tables setup complete.")


# --- Helper Functions ---

def create_category_keyboard():
    # ... (remains the same) ...
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2)
    return builder.as_markup()

async def get_comment_reactions(comment_id: int):
    # ... (remains the same) ...
    async with db.acquire() as conn:
        counts = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(CASE WHEN reaction_type = 'like' THEN 1 ELSE 0 END), 0) AS likes,
                   COALESCE(SUM(CASE WHEN reaction_type = 'dislike' THEN 1 ELSE 0 END), 0) AS dislikes
            FROM reactions WHERE comment_id = $1 """, comment_id )
    return counts['likes'] if counts else 0, counts['dislikes'] if counts else 0

async def build_comment_keyboard(comment_id: int, commenter_user_id: int, viewer_user_id: int, confession_owner_id: int ):
    # ... (remains the same) ...
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")
    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(3, 1)
    else: builder.adjust(3)
    return builder.as_markup()

async def safe_send_message(user_id: int, text: str, **kwargs):
    # ... (remains the same) ...
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
    # ... (remains the same) ...
    global bot_info; await asyncio.sleep(0.1) # Small delay might help prevent race conditions
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
        elif "message to edit not found" in str(e).lower(): logging.warning(f"Msg {ch_msg_id} not found in {CHANNEL_ID} (conf {confession_id}). Maybe deleted?")
        else: logging.error(f"Failed edit channel post {ch_msg_id} for conf {confession_id}: {e}")
    except Exception as e: logging.error(f"Unexpected err updating btn for conf {confession_id}: {e}", exc_info=True)

async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None):
    # ... (remains largely the same, ensure build_comment_keyboard is called correctly) ...
    confession_owner_id: Optional[int] = None
    async with db.acquire() as conn:
        conf_data = await conn.fetchrow("SELECT status, user_id FROM confessions WHERE id = $1", confession_id)
        if not conf_data or conf_data['status'] != 'approved':
            err_txt = f"Confession #{confession_id} not found or not approved."; print(err_txt)
            try:
                if message_to_edit: await message_to_edit.edit_text(err_txt, reply_markup=None)
                else: await safe_send_message(user_id, err_txt)
            except Exception as e: logging.warning(f"Could not send/edit 'conf not found' to {user_id}: {e}")
            return
        confession_owner_id = conf_data['user_id']
        comments = await conn.fetch("SELECT id, user_id, text, parent_comment_id, created_at FROM comments WHERE confession_id = $1 ORDER BY created_at ASC", confession_id)

    sent_msg_ids = {}; comment_id_to_seq = {}; counter = 0
    if not comments: comments_html = "<i>No comments yet. Be the first!</i>\n"
    else:
        comments_html = f"--- Comments for Confession #{confession_id} ---\n\n"; temp_map = {}
        for c_data in comments: counter += 1; db_id = c_data['id']; comment_id_to_seq[db_id] = counter; temp_map[db_id] = c_data
        for c in comments:
            comm_id = c['id']; seq_num = comment_id_to_seq[comm_id]; commenter_uid = c['user_id']; comm_text = html.quote(c['text']); ts = c['created_at'].strftime("%Y-%m-%d %H:%M")
            reply_prefix = ""
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_id_to_seq: reply_prefix = f"↪️ <i>Replying to #{comment_id_to_seq[c['parent_comment_id']]}</i>\n"
            elif c['parent_comment_id']: reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"
            tag = "(Author)" if commenter_uid == confession_owner_id else ("(You)" if commenter_uid == user_id else "Anonymous")
            display_tag = f" {tag}" if tag else " Anonymous"
            metadata = f"<i>#{seq_num}{display_tag} | {ts}</i>"
            keyboard = await build_comment_keyboard(comment_id=comm_id, commenter_user_id=commenter_uid, viewer_user_id=user_id, confession_owner_id=confession_owner_id)
            full_text = f"{reply_prefix}💬 {comm_text}\n\n{metadata}"
            try: sent_msg = await bot.send_message(user_id, full_text, reply_markup=keyboard, parse_mode=ParseMode.HTML, disable_web_page_preview=True); sent_msg_ids[comm_id] = sent_msg.message_id
            except Exception as e: logging.warning(f"Could not send comment #{seq_num} (DB ID: {comm_id}) to {user_id}: {e}"); await safe_send_message(user_id, f"⚠️ Error displaying comment #{seq_num}.")

    add_comm_btn = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]])
    end_txt = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_txt += "You can add your own comment below:"
    try:
        if message_to_edit and not comments: await message_to_edit.edit_text(end_txt, reply_markup=add_comm_btn)
        elif message_to_edit and comments: await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn)
        else: await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn)
    except Exception as e: logging.warning(f"Could not send/edit final 'Add Comment' prompt to {user_id}: {e}")


# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, command: CommandObject | None = None):
    # ... (start handler remains the same) ...
    deep_link_args = command.args if command else None
    if deep_link_args and deep_link_args.startswith("view_"):
        try:
            conf_id = int(deep_link_args.split("_", 1)[1])
            logging.info(f"User {message.from_user.id} started via deep link for conf {conf_id}")
            async with db.acquire() as conn:
                conf_data = await conn.fetchrow("SELECT text, category, status, user_id FROM confessions WHERE id = $1", conf_id)
                comm_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", conf_id) or 0
            if not conf_data or conf_data['status'] != 'approved': await message.answer(f"Confession #{conf_id} not found or not approved."); return
            txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(conf_data['text'])}\n\n#{conf_data['category']}\n---"
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{conf_id}")
            builder.button(text=f"💬 Browse Comments ({comm_count})", callback_data=f"browse_{conf_id}")
            if message.from_user.id == conf_data['user_id']: builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{conf_id}"); builder.adjust(1, 1, 1)
            else: builder.adjust(1, 1)
            await message.answer(txt, reply_markup=builder.as_markup())
        except (ValueError, IndexError): logging.warning(f"Invalid deep link from {message.from_user.id}: {deep_link_args}"); await message.answer("Invalid link.")
        except Exception as e: logging.error(f"Err handling deep link '{deep_link_args}' for {message.from_user.id}: {e}", exc_info=True); await message.answer("Error processing link.")
    else: await message.answer("Welcome! Use /confess to share anonymously.")

# --- *** MODIFIED: Help Command Handler *** ---
@dp.message(Command("help"), StateFilter(None))
async def show_help(message: types.Message):
    help_text = (
        "<b>Welcome to the Confession Bot!</b>\n\n"
        "Here's how to use the bot:\n"
        "🔹 /confess - Start the process to submit a new anonymous confession.\n"
        "🔹 /start - Show the welcome message or view a specific confession.\n"
        # Removed /contact_admin command from text
        "🔹 /help - Display this help message.\n"
        "🔹 /privacy - View information about data privacy.\n\n"
        "Confessions approved by the admin will be posted in the channel. You can then interact using the buttons.\n\n"
        "Need to reach the admin directly?"
    )
    # Create Inline Keyboard with Contact Admin button
    contact_admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin_start")]
        ]
    )
    await message.answer(help_text, parse_mode=ParseMode.HTML, reply_markup=contact_admin_keyboard) # Add keyboard here

# --- *** NEW: Callback Handler to Start Contact Admin Flow *** ---
@dp.callback_query(F.data == "contact_admin_start", StateFilter(None))
async def start_contact_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    """Initiates the process for a user to send a message to the admin via button."""
    await state.set_state(ContactAdminForm.waiting_for_message)
    # Optional: Add a cancel keyboard
    cancel_button = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="/cancel")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    # Answer the callback first
    await callback_query.answer("Please send your message to the admin.")
    # Send a new message prompting for the text
    await callback_query.message.answer( # Send prompt in chat
        "Okay, please send the message you want to forward to the admin.\n"
        "The admin will see your message but not your direct profile initially.\n"
        "Type /cancel to abort.",
        reply_markup=cancel_button # Optional: Add cancel button
    )
    # Optionally edit the help message to remove the button after clicking
    # try:
    #     await callback_query.message.edit_reply_markup(reply_markup=None)
    # except Exception:
    #     pass # Ignore if editing fails

# --- Privacy Command Handler ---
@dp.message(Command("privacy"), StateFilter(None))
async def show_privacy(message: types.Message):
    # ... (privacy handler remains the same, including the link) ...
    privacy_policy_url = "https://telegra.ph/Privacy-Policy-for-AAU-Confession-Bot-04-16"
    privacy_text = (
        "<b>Privacy Information</b>\n\n"
        "Your privacy is important:\n"
        "▪️ When you /confess, your Telegram User ID is stored but never shown to other users. Only the admin sees it during review.\n"
        "▪️ Comments are posted anonymously. Your User ID is stored with the comment.\n"
        "▪️ The confession author can request to contact a commenter. You (the commenter) must explicitly 'Approve' sharing your @username (if set).\n"
        "▪️ Reactions are linked to your User ID but not publicly displayed.\n"
        f"▪️ All data is stored in a secure database.\n"
        f"▪️ The bot admin (User ID: {ADMIN_ID}) manages the review process and has access to stored User IDs for moderation.\n\n"
        f'For more details, please read our full <a href="{privacy_policy_url}">Privacy Policy</a>.'
    )
    await message.answer(privacy_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- *** REMOVED: Old /contact_admin Command Handler *** ---
# (The function `contact_admin_command` is deleted)

# --- Handler to Cancel Contact Admin (Remains the same) ---
@dp.message(Command("cancel"), StateFilter(ContactAdminForm.waiting_for_message))
async def cancel_contact_admin(message: types.Message, state: FSMContext):
    """Allows the user to cancel sending a message to the admin."""
    current_state = await state.get_state()
    if current_state is None: return # Do nothing if no state
    logging.info(f"User {message.from_user.id} cancelling state {current_state}")
    await state.clear()
    await message.answer("Action cancelled.", reply_markup=ReplyKeyboardRemove()) # Remove custom keyboard

# --- Handler to Receive User Message for Admin (Remains the same) ---
@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def receive_admin_message(message: types.Message, state: FSMContext):
    """Receives the user's message and forwards it to the admin."""
    # ... (logic remains the same) ...
    user_id = message.from_user.id; user_info = message.from_user; message_text = message.text
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

# --- Handler for Admin Replies to User Messages (Remains the same) ---
# --- Handler for Admin Replies to User Messages ---
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message) # Trigger only for admin replies
async def handle_admin_reply(message: types.Message):
    """Handles admin replies to messages forwarded via contact admin flow."""
    global bot_info # Needed for bot ID check

    if not bot_info:
        logging.error("Cannot handle admin reply: Bot info not loaded.")
        return

    admin_reply_text = message.text
    replied_to_message = message.reply_to_message

    # 1. Check if replying to a message FROM THE BOT
    if replied_to_message.from_user.id != bot_info.id:
        logging.debug("Admin replied to a non-bot message. Ignoring.")
        return

    # 2. --- REFINED Extraction Logic ---
    target_user_id = None
    # Prioritize html_text as the original message used HTML
    original_html_text = replied_to_message.html_text
    original_plain_text = replied_to_message.text # Fallback

    # Use html_text if available, otherwise fall back to plain text
    text_to_search = original_html_text if original_html_text else original_plain_text

    if not text_to_search:
        logging.warning("Admin replied to a bot message with no text content. Cannot extract User ID.")
        return

    # Define more specific markers based on the sent format in receive_admin_message
    id_marker_start = "<b>User ID:</b> <code>" # More specific start marker
    id_marker_end = "</code>"                 # End marker remains the same

    try:
        start_index = text_to_search.find(id_marker_start)
        if start_index != -1:
            # Calculate the index *after* the start marker
            start_parse_index = start_index + len(id_marker_start)
            # Find the end marker *after* the parsing start index
            end_index = text_to_search.find(id_marker_end, start_parse_index)
            if end_index != -1:
                # Extract the string between the markers
                user_id_str = text_to_search[start_parse_index:end_index]
                # Convert to integer
                target_user_id = int(user_id_str.strip())
                logging.info(f"Extracted target User ID {target_user_id} from admin reply context.")
            else:
                # Log if end marker wasn't found after start marker
                logging.warning(f"Found start marker '{id_marker_start}' but not end marker '{id_marker_end}' after it in replied text.")
        else:
            # Log if start marker wasn't found at all
            logging.warning(f"Could not find start marker '{id_marker_start}' in replied message text.")
            # For debugging, log the text being searched
            logging.debug(f"Searched text content:\n{text_to_search}")


    except ValueError:
        # Log if conversion to int failed
        logging.warning(f"Could not convert extracted User ID string '{user_id_str}' to integer.")
        target_user_id = None # Ensure it's None if conversion fails
    except Exception as e:
        # Log any other unexpected errors during parsing
        logging.error(f"Error parsing User ID from admin reply context: {e}", exc_info=True)
        target_user_id = None # Ensure it's None on error

    # 3. Send Reply if User ID Found
    if target_user_id:
        try:
            sent = await safe_send_message(
                target_user_id,
                f"💬 <b>Admin Reply:</b>\n\n{html.quote(admin_reply_text)}",
                parse_mode=ParseMode.HTML
            )
            if sent:
                await message.reply("✅ Reply sent to the user.") # Confirm to admin
                logging.info(f"Admin {message.from_user.id} replied to user {target_user_id}.")
            else:
                await message.reply("⚠️ Failed to send reply. User may have blocked the bot.")
                logging.warning(f"Failed to send admin reply to user {target_user_id} (likely blocked).")
        except Exception as e:
            logging.error(f"Error sending admin reply to user {target_user_id}: {e}")
            await message.reply(f"❌ Error sending reply: {e}")
    else:
        # Failed to extract ID
        # Check if it *looked* like a contact forward attempt to give better feedback
        # Use a simpler check on the search text
        if "Contact Request from User" in text_to_search and "<b>User ID:</b>" in text_to_search:
             await message.reply("⚠️ Couldn't identify the target user ID from the message you replied to. Was the format changed? Please use the User ID provided.")
             logging.warning(f"Admin {message.from_user.id} replied to a potential contact message, but User ID couldn't be extracted.")
        else:
             # Admin replied to some other message from the bot, ignore silently.
             logging.debug("Admin replied to a generic bot message or format was unrecognizable. Ignoring.")


# --- Confession Submission Flow ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    # ... (remains the same) ...
    await message.answer("Choose a category:", reply_markup=create_category_keyboard()); await state.set_state(ConfessionForm.waiting_for_category)

@dp.callback_query(StateFilter(ConfessionForm.waiting_for_category), F.data.startswith("category_"))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (remains the same) ...
    category = callback_query.data.split("_", 1)[1]
    if category not in CATEGORIES: await callback_query.answer("Invalid category.", show_alert=True); return
    await state.update_data(category=category); await state.set_state(ConfessionForm.waiting_for_text)
    try: await callback_query.message.edit_text(f"Category: <b>{category}</b>\n\nSend your confession text.")
    except Exception as e: logging.warning(f"Could not edit category msg: {e}"); await callback_query.answer(); await safe_send_message(callback_query.from_user.id, f"Category: <b>{category}</b>\n\nSend confession text.")

@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    # ... (remains the same) ...
    conf_text = message.text; user_id = message.from_user.id; state_data = await state.get_data(); category = state_data.get("category")
    if not category: await message.answer("⚠️ Error: Category missing. Start again /confess."); await state.clear(); logging.error(f"State missing category for {user_id}"); return
    if len(conf_text) < 10: await message.answer("Confession too short (min 10 chars)."); return
    if len(conf_text) > 3900: await message.answer(f"Confession too long (max ~3900 chars). Has {len(conf_text)} chars."); return
    try:
        async with db.acquire() as conn: conf_id = await conn.fetchval("INSERT INTO confessions (text, user_id, category, status) VALUES ($1, $2, $3, 'pending') RETURNING id", conf_text, user_id, category)
        if not conf_id: await message.answer("Error submitting. Try again."); logging.error("Failed get conf_id"); await state.clear(); return
        kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{conf_id}")], [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{conf_id}")]])
        admin_msg = f"New Confession (ID: {conf_id})\nCat: {category}\nUser: {user_id}\n\n{html.quote(conf_text)}"
        if len(admin_msg) > 4090: admin_msg = admin_msg[:4087] + "..."
        await bot.send_message(ADMIN_ID, admin_msg, reply_markup=kbd)
        await message.answer("✅ Confession submitted for review.")
        logging.info(f"Confession {conf_id} ({category}) submitted by {user_id}")
    except Exception as e: logging.error(f"Error receive_confession_text from {user_id}: {e}", exc_info=True); await message.answer("Internal error submitting.")
    finally: await state.clear()

# --- Admin Action Filters and Handlers ---
def is_confession_action_callback(data: str) -> bool:
    # ... (remains the same) ...
    if not isinstance(data, str): return False
    parts = data.split("_")
    return len(parts) == 2 and parts[0] in ('approve', 'reject') and parts[1].isdigit()

@dp.callback_query(lambda c: is_confession_action_callback(c.data))
async def admin_action(callback_query: types.CallbackQuery):
    # ... (admin_action logic remains the same) ...
    global bot_info; # ... (rest of admin action handler) ...
    if not bot_info: logging.error("Bot info missing for admin action."); await callback_query.answer("Internal error.", show_alert=True); return
    if callback_query.from_user.id != ADMIN_ID: await callback_query.answer("Not authorized.", show_alert=True); return
    try: action, conf_id_str = callback_query.data.split("_", 1); conf_id = int(conf_id_str)
    except (ValueError, IndexError): logging.error(f"Invalid admin cb data: {callback_query.data}"); await callback_query.answer("Invalid data format.", show_alert=True); return
    async with db.acquire() as conn:
        async with conn.transaction():
            conf = await conn.fetchrow("SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id)
            if not conf: await callback_query.answer("Confession not found.", show_alert=True); await callback_query.message.delete(); return
            if conf['status'] != 'pending': await callback_query.answer(f"Confession #{conf_id} already {conf['status']}.", show_alert=True); await callback_query.message.edit_reply_markup(reply_markup=None); return
            user_id = conf["user_id"]; conf_text = conf["text"]; db_id = conf["id"]; category = conf["category"]
            try:
                if action == "approve":
                    link = f"https://t.me/{bot_info.username}?start=view_{db_id}"; txt = f"<b>Confession #{db_id}</b>\n\n{html.quote(conf_text)}\n\n#{category}"; kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=link)]])
                    if len(txt) > 4096: logging.error(f"Conf {db_id} too long ({len(txt)})."); await callback_query.answer("Error: Text too long.", show_alert=True); return
                    msg = await bot.send_message(CHANNEL_ID, txt, reply_markup=kbd, parse_mode=ParseMode.HTML)
                    await conn.execute("UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2", msg.message_id, db_id)
                    await safe_send_message(user_id, f"✅ Your confession (#{db_id} - #{category}) was approved!"); await callback_query.answer(f"Conf {db_id} approved."); logging.info(f"Admin {callback_query.from_user.id} approved {db_id}")
                else: # reject
                    await conn.execute("UPDATE confessions SET status = 'rejected' WHERE id = $1", db_id)
                    await safe_send_message(user_id, f"❌ Your confession (#{db_id} - #{category}) was rejected."); await callback_query.answer(f"Conf {db_id} rejected."); logging.info(f"Admin {callback_query.from_user.id} rejected {db_id}")
                final_admin_txt = callback_query.message.html_text + f"\n\n-- Status: {action.capitalize()}ed --"; await callback_query.message.edit_text(final_admin_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except TelegramForbiddenError: logging.error(f"Bot permissions error channel/user {CHANNEL_ID}/{user_id}."); await callback_query.answer("Error: Check bot permissions.", show_alert=True); raise
            except TelegramBadRequest as e: logging.error(f"TG API error admin action ({action}) for {conf_id}: {e}", exc_info=True); await callback_query.answer(f"TG Error: {e}.", show_alert=True); raise
            except Exception as e: logging.error(f"Error admin action ({action}) for {conf_id}: {e}", exc_info=True); await callback_query.answer(f"Error: {e}.", show_alert=True); raise

# --- Commenting Flow Handlers ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    # ... (remains the same) ...
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError): logging.error(f"Invalid browse cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    await callback_query.answer("Loading comments...")
    await show_comments_for_confession(callback_query.from_user.id, conf_id, message_to_edit=callback_query.message)

@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (remains the same) ...
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError): logging.error(f"Invalid add cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn: conf_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
    if not conf_exists: await callback_query.answer("Confession not found/approved.", show_alert=True); await callback_query.message.delete(); return
    await state.update_data(confession_id=conf_id, parent_comment_id=None); await state.set_state(CommentForm.waiting_for_comment)
    try: await safe_send_message(callback_query.from_user.id, f"📝 Send comment for Confession #{conf_id}:"); await callback_query.answer()
    except Exception as e: logging.warning(f"Could not send comment prompt to {callback_query.from_user.id}: {e}"); await callback_query.answer("Could not ask for comment.", show_alert=True)

@dp.message(CommentForm.waiting_for_comment, F.text)
async def receive_comment(message: types.Message, state: FSMContext):
    # ... (remains the same) ...
    comm_text = message.text; user_id = message.from_user.id; data = await state.get_data(); conf_id = data.get("confession_id")
    if not conf_id: await message.answer("⚠️ Error: No confession context. Start again."); await state.clear(); logging.error(f"State missing conf_id for {user_id}"); return
    if len(comm_text) < 2: await message.answer("Comment too short (min 2 chars)."); return
    if len(comm_text) > 1000: await message.answer(f"Comment too long (max 1000 chars). Has {len(comm_text)}."); return
    conf_owner_id = None; new_comm_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                 conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", conf_id);
                 if not conf_owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found/approved.")
                 new_comm_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, NULL) RETURNING id", conf_id, user_id, comm_text)
        await message.answer("💬 Comment added!"); logging.info(f"User {user_id} added comment {new_comm_id} to conf {conf_id}"); await update_channel_post_button(conf_id)
        if conf_owner_id and conf_owner_id != user_id: logging.info(f"Notifying {conf_owner_id} of comment {new_comm_id} on {conf_id}"); link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"; notif = (f"💬 Comment on your confession #{conf_id}.\n\nComment: {html.quote(comm_text[:150])}{'...' if len(comm_text) > 150 else ''}\n\n<a href='{link}'>View comments.</a>"); await safe_send_message(conf_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await show_comments_for_confession(user_id, conf_id)
    except asyncpg.exceptions.ForeignKeyViolationError: logging.warning(f"Attempt add comment to non-existent/unapproved conf {conf_id} by {user_id}"); await message.answer("⚠️ Cannot add comment. Confession removed/unapproved.")
    except Exception as e: logging.error(f"Error saving comment for conf {conf_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Internal error saving comment.")
    finally: await state.clear()

# --- Reply Flow Handlers ---
@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (remains the same) ...
    try: parent_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError): logging.error(f"Invalid reply cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    msg_id_reply_to = callback_query.message.message_id 
    async with db.acquire() as conn: comm_data = await conn.fetchrow("SELECT confession_id, text FROM comments WHERE id = $1", parent_id)
    if not comm_data: await callback_query.answer("Original comment not found.", show_alert=True); await callback_query.message.edit_reply_markup(reply_markup=None); return
    conf_id = comm_data['confession_id']; preview = html.quote(comm_data['text'][:80]) + ('...' if len(comm_data['text']) > 80 else '')
    await state.update_data(confession_id=conf_id, parent_comment_id=parent_id, message_id_to_reply_to=msg_id_reply_to); await state.set_state(CommentForm.waiting_for_reply)
    try: prompt = (f"📝 Replying to comment:\n<i>{preview}</i>\n\nPlease send reply:"); await safe_send_message(callback_query.from_user.id, prompt, parse_mode=ParseMode.HTML); await callback_query.answer()
    except Exception as e: logging.warning(f"Could not send reply prompt to {callback_query.from_user.id}: {e}"); await callback_query.answer("Could not ask for reply.", show_alert=True)

@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    # ... (remains the same) ...
    reply_text = message.text; user_id = message.from_user.id; data = await state.get_data(); conf_id = data.get("confession_id"); parent_id = data.get("parent_comment_id"); msg_id_reply_to = data.get("message_id_to_reply_to")
    if not conf_id or not parent_id or not msg_id_reply_to: await message.answer("⚠️ Error: Reply context lost. Try again."); await state.clear(); logging.error(f"State missing fields for {user_id} in receive_reply: {data}"); return
    if len(reply_text) < 1: await message.answer("Reply cannot be empty."); return
    if len(reply_text) > 1000: await message.answer(f"Reply too long (max 1000 chars). Has {len(reply_text)}."); return
    new_comm_id = None; parent_owner_id = None; conf_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1 FOR UPDATE", parent_id);
                if not parent_data: await message.answer("⚠️ Original comment deleted."); await state.clear(); return
                parent_owner_id = parent_data['user_id']; conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id);
                if not conf_owner_id: raise Exception("Confession not found during reply save")
                new_comm_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, $4) RETURNING id", conf_id, user_id, reply_text, parent_id)
        logging.info(f"User {user_id} added reply {new_comm_id} to comment {parent_id} on conf {conf_id}"); await update_channel_post_button(conf_id)
        try:
            tag = "(Author)" if user_id == conf_owner_id else ("(You)" if user_id == parent_owner_id else "(You)")
            display_tag = f" {tag}" if tag else " Anonymous"
            reply_msg_txt = (f"💬 {html.quote(reply_text)}\n\n<i>Reply #{new_comm_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>")
            reply_kbd = await build_comment_keyboard(comment_id=new_comm_id, commenter_user_id=user_id, viewer_user_id=user_id, confession_owner_id=conf_owner_id)
            await bot.send_message(user_id, reply_msg_txt, reply_to_message_id=msg_id_reply_to, reply_markup=reply_kbd, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            if parent_owner_id and parent_owner_id != user_id: logging.info(f"Notifying {parent_owner_id} of reply {new_comm_id} to their comment {parent_id}"); link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"; notif = (f"↪️ Reply to your comment on confession #{conf_id}.\n\nReply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n<a href='{link}'>View comments.</a>"); await safe_send_message(parent_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except TelegramBadRequest as e:
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Native reply failed for {new_comm_id} (to {parent_id}) - msg {msg_id_reply_to} deleted?"); reply_kbd = await build_comment_keyboard(new_comm_id, user_id, user_id, conf_owner_id); reply_msg_txt = (f"↪️ Replying to comment #{parent_id}\n💬 {html.quote(reply_text)}\n\n<i>Reply #{new_comm_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"); await safe_send_message(user_id, reply_msg_txt, reply_markup=reply_kbd, parse_mode=ParseMode.HTML)
                 if parent_owner_id and parent_owner_id != user_id: logging.info(f"Notifying {parent_owner_id} of reply {new_comm_id} (orig msg deleted)"); link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"; notif = (f"↪️ Reply to your comment on confession #{conf_id} (orig msg deleted).\n\nReply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n<a href='{link}'>View comments.</a>"); await safe_send_message(parent_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
             else: logging.error(f"TG error sending native reply {new_comm_id} by {user_id}: {e}", exc_info=True); await message.answer(f"❌ Reply saved (ID: {new_comm_id}), but error displaying it.")
        except Exception as e: logging.error(f"Unexpected error sending native reply {new_comm_id} by {user_id}: {e}", exc_info=True); await message.answer(f"❌ Reply saved (ID: {new_comm_id}), but internal error displaying.")
    except asyncpg.exceptions.ForeignKeyViolationError: logging.warning(f"FK violation reply to comment {parent_id} by {user_id}"); await message.answer("⚠️ Cannot add reply. Original comment removed.")
    except Exception as e: logging.error(f"Error saving reply DB transaction for {parent_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Internal error saving reply.")
    finally: await state.clear()

# --- Reaction Handling ---
# --- Reaction Handling ---
@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    try:
        # Use simpler unpacking if '_' variables are unused
        action_prefix, r_type, comm_id_str = callback_query.data.split("_", 2)
        comm_id = int(comm_id_str)
        user_id = callback_query.from_user.id
        if r_type not in ['like', 'dislike']: raise ValueError("Invalid reaction type")
    except (ValueError, IndexError, TypeError): # Added TypeError for robustness
        logging.error(f"Invalid react cb: {callback_query.data}")
        await callback_query.answer("Invalid reaction.", show_alert=True)
        return

    action = "none"; kbd = None; alert = None
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                info = await conn.fetchrow("SELECT c.user_id as comm_uid, co.user_id as conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comm_id)
                if not info:
                    raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")
                comm_uid = info['comm_uid']; conf_owner_id = info['conf_owner_id']; viewer_id = user_id

                existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", comm_id, user_id)
                if existing:
                    if existing == r_type:
                        await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id)
                        action = f"Removed {r_type}"; alert = f"{r_type.capitalize()} removed"
                    else:
                        await conn.execute("UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", r_type, comm_id, user_id)
                        action = f"Changed to {r_type}"; alert = f"Reaction changed to {r_type}"
                else:
                    await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, user_id, r_type)
                    action = f"Added {r_type}"; alert = f"{r_type.capitalize()} added"

                kbd = await build_comment_keyboard(comm_id, comm_uid, viewer_id, conf_owner_id)
                logging.info(f"User {user_id} action '{action}' on comment {comm_id}. Kbd rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK viol reaction update comm {comm_id} user {user_id}")
                await callback_query.answer("Comment not found.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None) # Try remove buttons
                except Exception: pass
                return # Exit transaction
            except Exception as db_err:
                logging.error(f"DB error reaction proc comm {comm_id} by {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("DB Error processing reaction.", show_alert=True)
                return # Exit transaction

    # --- Outside transaction ---
    if kbd and action != "none":
        try:
            await callback_query.message.edit_reply_markup(reply_markup=kbd)
            await callback_query.answer(alert)
            logging.info(f"Updated markup comm {comm_id} after {action}")
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.info(f"Markup {comm_id} not modified.")
                await callback_query.answer(alert + " (No visual change)")
            elif "message to edit not found" in err_str:
                logging.warning(f"Msg not found react update {comm_id}.")
                await callback_query.answer(alert + " (Counts updated, view not)", show_alert=False)
            elif "query is too old" in err_str:
                logging.warning(f"Query old react update {comm_id}.")
                await callback_query.answer(alert + " (Counts updated, view stale)", show_alert=False)
            else:
                logging.error(f"TG error update react markup {comm_id}: {e}")
                await callback_query.answer("Error updating display.", show_alert=True)
        except Exception as e:
            # --- FIX: Separate statements onto different lines ---
            logging.error(f"Unexpected error update react markup {comm_id}: {e}", exc_info=True)
            await callback_query.answer("Error updating display.", show_alert=True)
            # --- END FIX ---
    elif action != "none": # DB success, kbd build failed
        logging.error(f"Action {action} comm {comm_id} DB done, kbd build failed.")
        await callback_query.answer("Reaction processed (internal error).", show_alert=True)
    # else: DB action failed or no action needed, already answered.
# --- Contact Request Flow Handlers ---
# --- Contact Request Flow Handlers ---
@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    """Handles the author clicking 'Request Contact' on a comment."""
    try:
        comm_id = int(callback_query.data.split("_", 2)[2])
        req_uid = callback_query.from_user.id
    except (ValueError, IndexError):
        logging.error(f"Invalid req contact cb: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            # --- Fetch data ---
            comm_data = await conn.fetchrow(
                "SELECT c.user_id comm_uid, c.text comm_txt, co.id conf_id, co.user_id conf_owner_id "
                "FROM comments c JOIN confessions co ON c.confession_id = co.id "
                "WHERE c.id = $1 AND co.status = 'approved'",
                comm_id
            )
            if not comm_data:
                await callback_query.answer("Comment/confession not found.", show_alert=True)
                return

            comm_uid = comm_data['comm_uid']
            conf_id = comm_data['conf_id']
            conf_owner_id = comm_data['conf_owner_id']
            preview = html.quote(comm_data['comm_txt'][:100]) + ('...' if len(comm_data['comm_txt']) > 100 else '')

            # --- Security Checks ---
            if req_uid != conf_owner_id:
                logging.warning(f"User {req_uid} tried req contact comm {comm_id} but not owner {conf_owner_id}.")
                await callback_query.answer("Only for your confessions.", show_alert=True)
                return
            if req_uid == comm_uid:
                await callback_query.answer("Cannot request contact self.", show_alert=True)
                return

            # --- Check Username ---
            comm_chat = None # Define before try block
            try:
                comm_chat = await bot.get_chat(comm_uid)
                if not comm_chat or not comm_chat.username: # Check both chat object and username attribute
                     await callback_query.answer("User has no public username.", show_alert=True)
                     logging.info(f"Author {req_uid} req comm {comm_id} aborted: commenter {comm_uid} no username.")
                     return
            except Exception as e:
                logging.warning(f"Could not fetch {comm_uid} chat: {e}.")
                await callback_query.answer("Could not get commenter info.", show_alert=True)
                return # Exit if we can't confirm username

            # --- Check Existing Request ---
            existing = await conn.fetchval(
                "SELECT status FROM contact_requests "
                "WHERE comment_id = $1 AND requester_user_id = $2 AND status IN ('pending', 'approved')",
                comm_id, req_uid
            )
            if existing:
                await callback_query.answer(f"Request already {existing}.", show_alert=True)
                return

            # --- Insert Request ---
            request_id = None # Define before try block
            try:
                request_id = await conn.fetchval(
                    "INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) "
                    "VALUES ($1, $2, $3, $4, 'pending') "
                    "ON CONFLICT (comment_id, requester_user_id) DO NOTHING RETURNING id", # Handles race condition/duplicate click
                    conf_id, comm_id, req_uid, comm_uid
                )
                if not request_id: # Could happen if ON CONFLICT triggers due to near-simultaneous clicks
                    existing_s = await conn.fetchval( # Re-check status if insert didn't return ID
                        "SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2",
                        comm_id, req_uid
                    )
                    # Add a more specific log message
                    logging.warning(f"Contact request insert for comm {comm_id} by {req_uid} returned no ID (ON CONFLICT triggered?). Existing status: {existing_s}")
                    await callback_query.answer(f"Request already exists (Status: {existing_s or 'Unknown'}).", show_alert=True)
                    return # Exit transaction if conflict occurred

            # --- *** ADDED except block *** ---
            except Exception as insert_err:
                logging.error(f"Failed insert contact req {req_uid} to {comm_uid} for comm {comm_id}: {insert_err}", exc_info=True)
                await callback_query.answer("Failed save request. Please try again.", show_alert=True)
                # No need to explicitly raise here, returning will cause transaction rollback if error occurs
                return # Exit transaction on other insert errors
            # --- *** END ADDED block *** ---

            # --- Notify Commenter (Only runs if insert was successful and returned ID) ---
            # Ensure comm_chat is available here (it should be due to checks above)
            if not comm_chat or not comm_chat.username:
                 logging.error(f"Reached notification stage for req {request_id} but commenter chat/username missing unexpectedly.")
                 await callback_query.answer("Internal error: Could not verify commenter username.", show_alert=True)
                 raise Exception(f"Comm chat/username missing for req {request_id}") # Force rollback

            notification_text = (
                f"🤝 Author of Confession #{conf_id} wants to contact you regarding your comment:\n\n"
                f"<i>{preview}</i>\n\n"
                f"Do you approve sharing your Telegram username (@{html.quote(comm_chat.username)}) with them?"
            )
            approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_contact_{request_id}")],
                [InlineKeyboardButton(text="❌ Deny", callback_data=f"deny_contact_{request_id}")]
            ])

            sent = await safe_send_message(comm_uid, notification_text, reply_markup=approval_keyboard, parse_mode=ParseMode.HTML)

            if sent:
                await callback_query.answer("✅ Contact request sent.", show_alert=False)
                logging.info(f"Contact req {request_id} (comm {comm_id}) sent from {req_uid} to {comm_uid}.")
            else:
                logging.warning(f"Failed send contact req {request_id} notification to {comm_uid}. Rolling back.")
                await callback_query.answer("⚠️ Could not send request (user blocked?).", show_alert=True)
                raise Exception(f"Failed notify commenter {comm_uid}, rolling back req {request_id}")

def is_contact_response_callback(data: str) -> bool:
    # ... (remains the same) ...
    if not isinstance(data, str): return False
    parts = data.split("_")
    return len(parts) == 3 and parts[0] in ('approve', 'deny') and parts[1] == 'contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data))
async def handle_contact_response(callback_query: types.CallbackQuery):
    # ... (remains the same) ...
    try: action, _, req_id_str = callback_query.data.split("_"); req_id = int(req_id_str); resp_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid contact resp cb: {callback_query.data}"); await callback_query.answer("Invalid request data.", show_alert=True); return
    db_status = 'approved' if action == 'approve' else 'denied'
    async with db.acquire() as conn:
        async with conn.transaction():
            req_data = await conn.fetchrow("SELECT id, requester_user_id, requested_user_id, status, confession_id, comment_id FROM contact_requests WHERE id = $1 FOR UPDATE", req_id)
            if not req_data: await callback_query.answer("Request not found.", show_alert=True); await callback_query.message.delete(); return
            if resp_uid != req_data['requested_user_id']: logging.warning(f"User {resp_uid} tried respond req {req_id} for {req_data['requested_user_id']}."); await callback_query.answer("Invalid request.", show_alert=True); return
            if req_data['status'] != 'pending': await callback_query.answer(f"Request already {req_data['status']}.", show_alert=True); await callback_query.message.edit_reply_markup(reply_markup=None); return
            author_notif = ""; req_uid = req_data['requester_user_id']; conf_id = req_data['confession_id']; comm_id = req_data['comment_id']; edit_status = ""
            if db_status == 'approved':
                comm_uname = callback_query.from_user.username
                if comm_uname:
                    await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id)
                    author_notif = (f"✅ Contact Approved!\n\nReq for Confession #{conf_id} (Comment approx ID: {comm_id}) APPROVED.\n\nContact: @{html.quote(comm_uname)}")
                    await callback_query.answer("Approved. Username shared."); logging.info(f"Req {req_id} approved by {resp_uid}. Uname @{comm_uname} sent to {req_uid}."); edit_status = 'Approved (Username Shared)'
                else: db_status = 'approved_no_username'; await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", db_status, req_id); author_notif = (f"⚠️ Contact Approved (No Username)\n\nReq Confession #{conf_id} (Comment ID {comm_id}) APPROVED, but user lacks public username."); await callback_query.answer("Approved, but no public username.", show_alert=True); logging.info(f"Req {req_id} approved by {resp_uid}, but no username. Notified {req_uid}."); edit_status = 'Approved (No Username)'
            else: await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id); author_notif = (f"❌ Contact Denied\n\nReq Confession #{conf_id} (Comment ID {comm_id}) DENIED."); await callback_query.answer("Denied. Username not shared."); logging.info(f"Req {req_id} denied by {resp_uid}. Notified {req_uid}."); edit_status = 'Denied'
            await safe_send_message(req_uid, author_notif, parse_mode=ParseMode.HTML)
            try: orig_txt = callback_query.message.html_text; final_txt = f"{orig_txt}\n\n<b>Status: {edit_status}</b>"; await callback_query.message.edit_text(final_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except TelegramBadRequest as e:
                 if "message is not modified" not in str(e).lower(): raise
            except Exception as e: logging.warning(f"Could not edit commenter's ({resp_uid}) notif msg {callback_query.message.message_id} req {req_id}: {e}")

@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    # ... (remains the same) ...
    try: conf_id = int(callback_query.data.split("_", 2)[2]); viewer_uid = callback_query.from_user.id
    except (ValueError, IndexError): logging.error(f"Invalid view reqs cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn: conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id);
    if not conf_owner_id: await callback_query.answer("Confession not found.", show_alert=True); return
    if viewer_uid != conf_owner_id: await callback_query.answer("Only for your confessions.", show_alert=True); return
    async with db.acquire() as conn: reqs = await conn.fetch("SELECT cr.comment_id, cr.status, cr.updated_at, c.text as comment_text FROM contact_requests cr JOIN comments c ON cr.comment_id = c.id WHERE cr.confession_id = $1 AND cr.requester_user_id = $2 ORDER BY cr.updated_at DESC", conf_id, viewer_uid)
    if not reqs: await callback_query.answer("No contact requests made for this confession.", show_alert=False); return
    resp_txt = f"<b>Contact Requests Status for Confession #{conf_id}</b>\n\n";
    for req in reqs: preview = html.quote(req['comment_text'][:50]) + ('...' if len(req['comment_text']) > 50 else ''); status = req['status'].replace('_', ' ').capitalize(); updated = req['updated_at'].strftime("%Y-%m-%d %H:%M"); resp_txt += (f"• Comment: \"<i>{preview}</i>\"\n  Status: <b>{status}</b>\n  Update: {updated}\n\n")
    if len(resp_txt) > 4096: resp_txt = resp_txt[:4090] + "\n\n...(truncated)"
    await safe_send_message(viewer_uid, resp_txt, parse_mode=ParseMode.HTML); await callback_query.answer()


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    # ... (remains the same) ...
    await message.reply("Hi there! 👋\nUse /confess to share anonymously.\nUse /help for commands.")


# --- Main Execution ---
async def main():
    # ... (main function remains the same) ...
    try:
        await setup()
        if db and bot_info: logging.info("Starting bot polling..."); await dp.start_polling(bot, skip_updates=True)
        else: logging.critical("DB connection or bot info missing. Cannot start.")
    except Exception as e: logging.critical(f"Fatal error setup/polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...");
        if bot and bot.session and not bot.session.closed: await bot.session.close(); logging.info("Bot session closed.")
        if db: logging.info("Closing database pool..."); await db.close(); logging.info("Database pool closed.")
        logging.info("Bot stopped.")

if __name__ == "__main__":
    # Environment variables loaded/validated at start
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("Bot stopped by user.")
    except Exception as main_err: logging.critical(f"Critical error main loop: {main_err}", exc_info=True); print(f"Critical error: {main_err}")

# --- END OF FILE main.py ---
