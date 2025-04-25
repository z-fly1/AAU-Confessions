# --- START OF FILE main.py ---

# --- START OF FILE main[V2.0.1.8] - Stable.py --- # Added admin-only User ID view in comments

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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional, Tuple

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

# --- *** ADDITION: New FSM State for Admin Rejection Reason *** ---
class AdminActions(StatesGroup):
    waiting_for_rejection_reason = State()
# --- *** END ADDITION *** ---


# --- Database ---
# ... (Database setup code remains the same) ...
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
                id SERIAL PRIMARY KEY, text TEXT NOT NULL, user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending', message_id BIGINT, category VARCHAR(50),
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                -- *** ADDITION: Optional column for rejection reason ***
                rejection_reason TEXT NULL
            );
        """)
        logging.info("Checked/Created 'confessions' table.")
        await conn.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='category') THEN ALTER TABLE confessions ADD COLUMN category VARCHAR(50); END IF;
                IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN ALTER TABLE confessions ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC'; ALTER TABLE confessions ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP; END IF;
                COMMENT ON COLUMN confessions.category IS 'Category chosen by the user'; COMMENT ON COLUMN confessions.message_id IS 'Message ID of the post in the channel';
                -- *** ADDITION: Add rejection_reason column if not exists ***
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='confessions' AND column_name='rejection_reason') THEN ALTER TABLE confessions ADD COLUMN rejection_reason TEXT NULL; END IF;
                COMMENT ON COLUMN confessions.rejection_reason IS 'Reason provided by admin upon rejection (optional)';
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
# ... (Most helper functions remain the same) ...
def create_category_keyboard():
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2)
    return builder.as_markup()

async def get_comment_reactions(comment_id: int) -> Tuple[int, int]: # Added type hint
    likes, dislikes = 0, 0 # Default values
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


async def build_comment_keyboard(comment_id: int, commenter_user_id: int, viewer_user_id: int, confession_owner_id: int ):
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")
    # Allow author to request contact UNLESS it's their own comment
    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(3, 1) # Adjust layout: 3 reaction/reply buttons, 1 contact button below
    else:
        builder.adjust(3) # Just the 3 buttons in one row
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


# --- MODIFIED: show_comments_for_confession ---
# ... (show_comments_for_confession remains the same, still shows admin UIDs) ...
async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None):
    """
    Displays comments for a given confession.
    If the viewer (user_id) is the ADMIN_ID, includes the commenter's User ID.
    """
    confession_owner_id: Optional[int] = None
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
        comments = await conn.fetch(
            "SELECT id, user_id, text, parent_comment_id, created_at FROM comments WHERE confession_id = $1 ORDER BY created_at ASC",
            confession_id
        )

    sent_msg_ids = {}; comment_id_to_seq = {}; counter = 0
    comments_html = ""

    if not comments:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
    else:
        comments_html = f"--- Comments for Confession #{confession_id} ---\n\n"
        temp_map = {}

        for c_data in comments:
            counter += 1
            db_id = c_data['id']
            comment_id_to_seq[db_id] = counter
            temp_map[db_id] = c_data

        for c in comments:
            comm_id = c['id']
            seq_num = comment_id_to_seq[comm_id]
            commenter_uid = c['user_id'] # <<< The ID of the person who wrote this comment
            comm_text = html.quote(c['text'])
            ts = c['created_at'].strftime("%Y-%m-%d %H:%M")

            reply_prefix = ""
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_id_to_seq:
                reply_prefix = f"↪️ <i>Replying to #{comment_id_to_seq[c['parent_comment_id']]}</i>\n"
            elif c['parent_comment_id']:
                reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"

            tag = ""
            if confession_owner_id is not None:
                if commenter_uid == confession_owner_id: tag = "(Author)"
                elif commenter_uid == user_id: tag = "(You)" # The viewer is the commenter
                else: tag = "Anonymous"
            else: tag = "Anonymous"
            display_tag = f" {tag}" if tag else " Anonymous"

            # --- Admin-only User ID display ---
            admin_info = ""
            # Check if the person VIEWING the comments is the admin
            if user_id == ADMIN_ID:
                admin_info = f" [UID: <code>{commenter_uid}</code>]"
            # --- END Admin-only User ID display ---

            # Include admin_info in the metadata string
            metadata = f"<i>#{seq_num}{display_tag} | {ts}{admin_info}</i>"

            keyboard = await build_comment_keyboard(
                comment_id=comm_id,
                commenter_user_id=commenter_uid,
                viewer_user_id=user_id, # Pass the viewer's ID
                confession_owner_id=confession_owner_id or 0
            )

            full_text = f"{reply_prefix}💬 {comm_text}\n\n{metadata}"
            try:
                sent_msg = await bot.send_message(
                    user_id, # Send to the user requesting the view
                    full_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                sent_msg_ids[comm_id] = sent_msg.message_id
            except Exception as e:
                logging.warning(f"Could not send comment #{seq_num} (DB ID: {comm_id}) to {user_id}: {e}")
                await safe_send_message(user_id, f"⚠️ Error displaying comment #{seq_num}.")

    add_comm_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])

    end_txt = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_txt += "\nYou can add your own comment below:"

    try:
        if message_to_edit and not comments:
            await message_to_edit.edit_text(end_txt, reply_markup=add_comm_btn, parse_mode=ParseMode.HTML)
        elif message_to_edit and comments:
            # Comments were sent as new messages, so send the end prompt as a new message too
            await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn, parse_mode=ParseMode.HTML)
            # Try to remove the original "Loading..." text if possible
            try:
                 if message_to_edit.text.startswith("<i>Loading comments"):
                      await message_to_edit.edit_text(f"Finished loading comments for Confession #{confession_id}.", reply_markup=None)
            except Exception: pass # Ignore if editing fails
        else:
             # Not editing, just send the end prompt
             await safe_send_message(user_id, end_txt, reply_markup=add_comm_btn, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as e:
         if "message is not modified" not in str(e).lower():
             logging.warning(f"Could not send/edit final 'Add Comment' prompt to {user_id} for {confession_id}: {e}")
    except Exception as e:
        logging.warning(f"Could not send/edit final 'Add Comment' prompt to {user_id} for {confession_id}: {e}")


# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, command: CommandObject | None = None, state: FSMContext): # Added state
    # --- *** ADDITION: Clear state on /start *** ---
    await state.clear() # Clear any lingering state when user types /start
    # --- *** END ADDITION *** ---

    deep_link_args = command.args if command else None
    if deep_link_args and deep_link_args.startswith("view_"):
        try:
            conf_id = int(deep_link_args.split("_", 1)[1])
            logging.info(f"User {message.from_user.id} started via deep link for conf {conf_id}")
            async with db.acquire() as conn:
                conf_data = await conn.fetchrow("SELECT text, category, status, user_id FROM confessions WHERE id = $1", conf_id)
                comm_count = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1", conf_id) or 0

            if not conf_data or conf_data['status'] != 'approved':
                await message.answer(f"Confession #{conf_id} not found or not approved.")
                return
            txt = f"<b>Confession #{conf_id}</b>\n\n{html.quote(conf_data['text'])}\n\n#{conf_data['category']}\n---"
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{conf_id}")
            builder.button(text=f"💬 Browse Comments ({comm_count})", callback_data=f"browse_{conf_id}")
            # Check if the current user is the author of the confession
            if message.from_user and message.from_user.id == conf_data['user_id']:
                builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{conf_id}")
                builder.adjust(1, 1, 1) # Adjust layout for author
            else:
                builder.adjust(1, 1) # Standard layout
            await message.answer(txt, reply_markup=builder.as_markup())
        except (ValueError, IndexError): logging.warning(f"Invalid deep link from {message.from_user.id}: {deep_link_args}"); await message.answer("Invalid link.")
        except Exception as e: logging.error(f"Err handling deep link '{deep_link_args}' for {message.from_user.id}: {e}", exc_info=True); await message.answer("Error processing link.")
    else: await message.answer("Welcome! Use /confess to share anonymously or /help for more info.", reply_markup=ReplyKeyboardRemove())

# --- Help Command Handler ---
@dp.message(Command("help"), StateFilter(None))
async def show_help(message: types.Message):
    # ... (Help text remains the same) ...
    help_text = (
        "<b>Welcome to the Confession Bot!</b>\n\n"
        "Here's how to use the bot:\n"
        "🔹 /confess - Start the process to submit a new anonymous confession.\n"
        "🔹 /start - Show the welcome message.\n"
        "🔹 /start view_&lt;id&gt; - View a specific confession and its comments.\n"
        "🔹 /help - Display this help message.\n"
        "🔹 /privacy - View information about data privacy.\n\n"
        "Confessions approved by the admin will be posted in the channel. You can then interact using the buttons.\n\n"
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
            "🔹 /id &lt;user_id&gt; - Get info about a specific user."
        )
    await message.answer(help_text, parse_mode=ParseMode.HTML, reply_markup=contact_admin_keyboard)

# --- Callback Handler to Start Contact Admin Flow ---
@dp.callback_query(F.data == "contact_admin_start", StateFilter(None))
async def start_contact_admin_callback(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (Contact Admin start remains the same) ...
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

# --- Privacy Command Handler ---
@dp.message(Command("privacy"), StateFilter(None))
async def show_privacy(message: types.Message):
    # ... (Privacy text remains the same) ...
    privacy_policy_url = "https://telegra.ph/Privacy-Policy-for-AAU-Confession-Bot-04-16" # Replace with your actual URL
    privacy_text = (
        "<b>Privacy Information</b>\n\n"
        "Your privacy is important:\n"
        "▪️ When you /confess, your Telegram User ID is stored but never shown to other users. \n"
        "▪️ Comments are posted anonymously. Your User ID is stored with the comment for identification purposes (e.g., for the author to request contact, for admin review) but is not displayed publicly to regular users or authors.\n"
        "▪️ The confession author can request to contact a commenter. You (the commenter) must explicitly 'Approve' sharing your @username (if set). If you approve without a username, the author is notified you approved but have no public username.\n"
        "▪️ Reactions (likes/dislikes) are linked to your User ID but not publicly displayed which user reacted.\n"
        f"▪️ All data is stored in a secure database hosted potentially outside your region.\n"
        f"▪️ The bot admin (User ID: <code>{ADMIN_ID}</code>) manages the review process and has access to stored User IDs for moderation and operational purposes.\n\n"
        f'For more details, please read our full <a href="{privacy_policy_url}">Privacy Policy</a>.'
    )
    await message.answer(privacy_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- Handler to Cancel Any State ---
@dp.message(Command("cancel"), StateFilter('*')) # More generic cancel
async def cancel_any_state(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Nothing to cancel.", reply_markup=ReplyKeyboardRemove())
        return
    logging.info(f"User {message.from_user.id} cancelling state {current_state}")
    await state.clear()
    await message.answer("Action cancelled.", reply_markup=ReplyKeyboardRemove())

# --- Contact Admin Message Handling ---
@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def receive_admin_message(message: types.Message, state: FSMContext):
    # ... (Receiving contact admin message remains the same) ...
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

# --- Handler for Admin Replies to User Messages ---
@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message)
async def handle_admin_reply(message: types.Message, state: FSMContext): # Added state
    # --- *** ADDITION: Check if admin is in a specific state first *** ---
    current_admin_state = await state.get_state()
    if current_admin_state is not None:
        # If admin is in a state (like waiting for rejection reason),
        # let that state's handler process the message, not this reply handler.
        logging.debug(f"Admin {ADMIN_ID} sent a reply, but is in state {current_admin_state}. Letting state handler process.")
        return
    # --- *** END ADDITION *** ---

    # ... (Rest of the admin reply logic remains the same) ...
    global bot_info
    if not bot_info: logging.error("Cannot handle admin reply: Bot info not loaded."); return
    admin_reply_text = message.text; replied_to_message = message.reply_to_message
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
        if "Contact Request from User" in text_to_search and "<b>User ID:</b>" in text_to_search: await message.reply("⚠️ Couldn't identify the target user ID from the message you replied to. Was the format changed? Please check the User ID provided in the original request."); logging.warning(f"Admin {message.from_user.id} replied to a potential contact message, but User ID couldn't be extracted.")
        else: logging.debug("Admin replied to a generic bot message, or format was unrecognizable, or it wasn't a contact forward reply. Ignoring.")

# --- Admin /id Command Handler ---
@dp.message(Command("id"))
async def get_user_info_command(message: types.Message, command: CommandObject):
    # ... (Admin /id command remains the same) ...
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
    except Exception as e: info_parts.append("❌ <b>Bot Interaction History:</b> Error fetching database info."); logging.error(f"Error fetching DB info for user {target_user_id}: {e}", exc_info=True)
    final_message = "\n".join(info_parts); await message.reply(final_message, parse_mode=ParseMode.HTML)

# --- Confession Submission Flow ---
@dp.message(Command("confess"), StateFilter(None))
async def start_confession(message: types.Message, state: FSMContext):
    await message.answer("Please choose a category for your confession:", reply_markup=create_category_keyboard()); await state.set_state(ConfessionForm.waiting_for_category)

@dp.callback_query(StateFilter(ConfessionForm.waiting_for_category), F.data.startswith("category_"))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    category = callback_query.data.split("_", 1)[1]
    if category not in CATEGORIES: await callback_query.answer("Invalid category selected.", show_alert=True); return
    await state.update_data(category=category); await state.set_state(ConfessionForm.waiting_for_text)
    try: await callback_query.message.edit_text(f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.\n\nType /cancel to stop.")
    except Exception as e: logging.warning(f"Could not edit category message for {callback_query.from_user.id}: {e}"); await callback_query.answer(); await safe_send_message(callback_query.from_user.id, f"Category: <b>{category}</b>\n\nSend confession text or /cancel.")

@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    conf_text = message.text; user_id = message.from_user.id; state_data = await state.get_data(); category = state_data.get("category")
    if not category: await message.answer("⚠️ Error: Category information was lost. Please start again with /confess."); await state.clear(); logging.error(f"State missing category for user {user_id} in receive_confession_text"); return
    if len(conf_text) < 10: await message.answer("Your confession is too short. Please provide at least 10 characters, or type /cancel."); return
    if len(conf_text) > 3900: await message.answer(f"Your confession is too long (max ~3900 characters). It currently has {len(conf_text)} characters. Please shorten it, or type /cancel."); return
    conf_id = None
    try:
        async with db.acquire() as conn: conf_id = await conn.fetchval("INSERT INTO confessions (text, user_id, category, status) VALUES ($1, $2, $3, 'pending') RETURNING id", conf_text, user_id, category)
        if not conf_id: await message.answer("There was an error submitting your confession. Please try again."); logging.error(f"Failed to get confession ID after insert for user {user_id}"); await state.clear(); return

        # Store the original message details in state for later editing
        await state.update_data(admin_review_message_id=message.message_id) # Store admin message ID

        kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{conf_id}")], [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{conf_id}")]])
        admin_msg_text = f"<b>New Confession Review</b>\n<b>ID:</b> {conf_id}\n<b>Category:</b> {html.quote(category)}\n<b>User ID:</b> <code>{user_id}</code>\n\n<b>Text:</b>\n{html.quote(conf_text)}"
        if len(admin_msg_text) > 4090: admin_msg_text = admin_msg_text[:4087] + "..."

        # Send the review message to the admin
        admin_review_msg = await bot.send_message(ADMIN_ID, admin_msg_text, reply_markup=kbd, parse_mode=ParseMode.HTML)

        # Store admin review message info for potential future edits (e.g., after rejection reason)
        await state.update_data(admin_review_chat_id=admin_review_msg.chat.id, admin_review_message_id=admin_review_msg.message_id)

        await message.answer("✅ Your confession has been submitted successfully and is pending review.")
        logging.info(f"Confession #{conf_id} (Category: {category}) submitted by User ID {user_id}")
    except Exception as e: logging.error(f"Error processing confession text from user {user_id} (Conf ID might be {conf_id}): {e}", exc_info=True); await message.answer("An internal error occurred while submitting your confession.")
    finally: await state.clear() # Clear user state, admin state is handled separately


# --- *** MODIFIED: Admin Action Handler *** ---
def is_confession_action_callback(data: str) -> bool:
    if not isinstance(data, str): return False
    parts = data.split("_")
    return len(parts) == 2 and parts[0] in ('approve', 'reject') and parts[1].isdigit()

@dp.callback_query(lambda c: is_confession_action_callback(c.data))
async def admin_action(callback_query: types.CallbackQuery, state: FSMContext): # Added state
    global bot_info
    if not bot_info:
        logging.error("Bot info missing for admin action.")
        await callback_query.answer("Internal error: Bot info not loaded.", show_alert=True)
        return
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized to perform this action.", show_alert=True)
        return

    try:
        action, conf_id_str = callback_query.data.split("_", 1)
        conf_id = int(conf_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid admin action callback data received: {callback_query.data}")
        await callback_query.answer("Invalid data format in callback.", show_alert=True)
        return

    async with db.acquire() as conn:
        # Check status before potentially locking row (less blocking)
        conf_status = await conn.fetchval("SELECT status FROM confessions WHERE id = $1", conf_id)

        if not conf_status:
            logging.warning(f"Admin {callback_query.from_user.id} tried action on non-existent Confession ID {conf_id}")
            await callback_query.answer("Confession not found (it might have been deleted or processed already).", show_alert=True)
            try: await callback_query.message.delete(); logging.info(f"Admin review message for non-existent conf {conf_id} deleted.")
            except Exception as e: logging.warning(f"Could not delete admin review message for non-existent conf {conf_id}: {e}")
            return

        if conf_status != 'pending':
            await callback_query.answer(f"This confession (#{conf_id}) has already been '{conf_status}'.", show_alert=True)
            try: # Try to update the message to show it was already processed
                final_admin_txt = callback_query.message.html_text + f"\n\n-- Already {conf_status.capitalize()} --"
                await callback_query.message.edit_text(final_admin_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except Exception: pass
            return

        # --- Handle Approval Directly ---
        if action == "approve":
            async with conn.transaction(): # Transaction for approve logic
                # Fetch full data and lock row
                conf = await conn.fetchrow("SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id)
                if not conf or conf['status'] != 'pending': # Re-check status after lock
                    await callback_query.answer("Confession status changed unexpectedly.", show_alert=True); return

                user_id = conf["user_id"]; conf_text = conf["text"]; db_id = conf["id"]; category = conf["category"]
                final_status = ""; channel_post_text = "" # Initialize

                try:
                    link = f"https://t.me/{bot_info.username}?start=view_{db_id}"
                    channel_post_text = f"<b>Confession #{db_id}</b>\n\n{html.quote(conf_text)}\n\n#{html.quote(category)}"
                    channel_kbd = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 View / Add Comments (0)", url=link)]])

                    if len(channel_post_text) > 4096:
                        logging.error(f"Confession {db_id} text too long for Telegram ({len(channel_post_text)} chars). Auto-rejecting.")
                        await callback_query.answer("Error: Text exceeds limit. Auto-rejecting.", show_alert=True)
                        await conn.execute("UPDATE confessions SET status = 'rejected', rejection_reason = $1 WHERE id = $2", "Content too long for Telegram.", db_id)
                        await safe_send_message(user_id, f"❌ Your confession (#{db_id} - #{category}) was rejected: Content too long.")
                        final_status = "Rejected (Too Long)"
                    else:
                        msg = await bot.send_message(CHANNEL_ID, channel_post_text, reply_markup=channel_kbd, parse_mode=ParseMode.HTML)
                        await conn.execute("UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2", msg.message_id, db_id)
                        await safe_send_message(user_id, f"✅ Your confession (#{db_id} - #{category}) has been approved and posted!")
                        await callback_query.answer(f"Confession #{db_id} approved.")
                        logging.info(f"Admin {callback_query.from_user.id} approved Confession #{db_id}")
                        final_status = "Approved"

                    # Update admin message (common for both auto-reject and approve)
                    if final_status:
                        final_admin_txt = callback_query.message.html_text + f"\n\n-- Status: {final_status} --"
                        await callback_query.message.edit_text(final_admin_txt, reply_markup=None, parse_mode=ParseMode.HTML)

                except (TelegramForbiddenError, TelegramBadRequest, Exception) as e:
                    logging.error(f"Error during approval process for Confession {conf_id}: {e}", exc_info=True)
                    await callback_query.answer(f"Error during approval: {e}. Check logs.", show_alert=True)
                    # Transaction will rollback automatically on exception
                    # Optionally try to update the admin message to indicate failure
                    try:
                         fail_txt = callback_query.message.html_text + "\n\n-- Approval Failed! --"
                         await callback_query.message.edit_text(fail_txt, reply_markup=None)
                    except Exception: pass


        # --- Handle Rejection: Ask for Reason ---
        elif action == "reject":
            # Store necessary info in FSM state for the next step
            await state.update_data(
                rejecting_conf_id=conf_id,
                admin_review_chat_id=callback_query.message.chat.id,
                admin_review_message_id=callback_query.message.message_id
            )
            await state.set_state(AdminActions.waiting_for_rejection_reason)

            # Ask admin for the reason
            reason_keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text="/skip")], # Option to skip reason
                    [KeyboardButton(text="/cancel")] # Option to cancel rejection
                ],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await callback_query.answer("❓ Provide rejection reason", show_alert=False)
            await bot.send_message(
                callback_query.from_user.id,
                f"Please provide the reason for rejecting Confession #{conf_id}.\n"
                "Send the reason as a message, or use:\n"
                "/skip - Reject without providing a specific reason.\n"
                "/cancel - Cancel the rejection process.",
                reply_markup=reason_keyboard
            )
            logging.info(f"Admin {callback_query.from_user.id} initiated rejection for Confession #{conf_id}, waiting for reason.")

# --- *** ADDITION: Handler for Admin Rejection Reason *** ---
@dp.message(AdminActions.waiting_for_rejection_reason, F.text)
async def receive_rejection_reason(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    if admin_id != ADMIN_ID: # Should not happen due to state filter, but double-check
        logging.warning(f"Non-admin {admin_id} tried sending message in admin state.")
        return

    data = await state.get_data()
    conf_id = data.get("rejecting_conf_id")
    admin_review_chat_id = data.get("admin_review_chat_id")
    admin_review_message_id = data.get("admin_review_message_id")

    if not conf_id or not admin_review_chat_id or not admin_review_message_id:
        logging.error(f"Admin {admin_id} sent rejection reason, but state data is missing: {data}")
        await message.answer("Error: Could not find the confession context. Please try rejecting again.", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return

    reason = None # Default to no reason
    reason_text_for_user = "Your confession was rejected by the admin." # Default message
    reason_text_for_log = "(No reason provided)"
    final_status_text = "Rejected"

    if message.text == "/skip":
        reason = None # Explicitly null for DB
        reason_text_for_log = "(Skipped reason)"
        await message.answer("Skipping reason. Confession will be rejected.", reply_markup=ReplyKeyboardRemove())
    elif message.text == "/cancel":
        # Handled by the global cancel handler
        return
    else:
        reason = message.text # Store the provided reason
        if len(reason) > 500: # Optional: Limit reason length
            await message.answer("Reason too long (max 500 chars). Please shorten it or /skip /cancel.")
            return
        reason_text_for_user = f"Your confession was rejected for the following reason:\n\n<i>{html.quote(reason)}</i>"
        reason_text_for_log = reason
        final_status_text = f"Rejected (Reason: {html.quote(reason[:50])}{'...' if len(reason)>50 else ''})"
        await message.answer("Reason recorded. Rejecting confession...", reply_markup=ReplyKeyboardRemove())

    # --- Perform the rejection ---
    success = False
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                # Fetch user_id and category for notification, check status again
                conf_data = await conn.fetchrow("SELECT user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE", conf_id)
                if not conf_data:
                    logging.warning(f"Admin {admin_id} processing rejection reason for conf {conf_id}, but confession disappeared.")
                    await message.answer("Error: Confession not found.", reply_markup=ReplyKeyboardRemove())
                    await state.clear()
                    return
                if conf_data['status'] != 'pending':
                    logging.warning(f"Admin {admin_id} processing rejection reason for conf {conf_id}, but status is already {conf_data['status']}.")
                    await message.answer(f"Error: Confession already {conf_data['status']}.", reply_markup=ReplyKeyboardRemove())
                     # Try to update original admin message anyway if possible
                    try:
                        original_admin_msg = await bot.edit_message_text(
                            chat_id=admin_review_chat_id,
                            message_id=admin_review_message_id,
                            text=f"Review for Confession #{conf_id}\n\n-- Already {conf_data['status'].capitalize()} --",
                            reply_markup=None # Remove buttons
                        )
                    except Exception: pass # Ignore if editing fails
                    await state.clear()
                    return

                user_id = conf_data['user_id']
                category = conf_data['category']

                # Update database
                await conn.execute(
                    "UPDATE confessions SET status = 'rejected', rejection_reason = $1 WHERE id = $2",
                    reason, conf_id
                )

                # Notify user
                user_notification = f"❌ {reason_text_for_user}\n(Confession ID: #{conf_id}, Category: #{category})"
                await safe_send_message(user_id, user_notification, parse_mode=ParseMode.HTML)

                # Update the original admin review message
                try:
                    original_admin_msg_content = await bot.get_chat_history(admin_review_chat_id, limit=1, offset= -admin_review_message_id) # A way to get text? Seems complex.
                    # Let's rebuild the essential info + status
                    admin_update_text = (
                        f"<b>Confession Review (ID: {conf_id})</b>\n"
                        f"<i>Category: {html.quote(category)}</i>\n\n"
                        f"-- Status: {final_status_text} --"
                        # Maybe add original text snippet here if easily fetchable or passed via state?
                    )
                    # Alternative: Edit existing text + append status (might be messy if original text was long)
                    # admin_update_text = data.get('original_admin_text', '') + f"\n\n-- Status: {final_status_text} --" # Needs original text in state

                    await bot.edit_message_text(
                        chat_id=admin_review_chat_id,
                        message_id=admin_review_message_id,
                        text=admin_update_text, # Use the rebuilt or appended text
                        reply_markup=None, # Remove buttons
                        parse_mode=ParseMode.HTML
                    )
                except TelegramBadRequest as e:
                     if "message not found" in str(e).lower():
                         logging.warning(f"Could not find original admin msg {admin_review_message_id} to update after rejection.")
                     else:
                         logging.error(f"Error updating original admin message {admin_review_message_id} after rejection: {e}")
                except Exception as e:
                    logging.error(f"Unexpected error updating original admin message {admin_review_message_id} after rejection: {e}")

                logging.info(f"Admin {admin_id} rejected Confession #{conf_id}. Reason: {reason_text_for_log}")
                success = True

            except Exception as e:
                logging.error(f"Error during rejection DB/notification process for Confession {conf_id}: {e}", exc_info=True)
                await message.answer(f"An error occurred during rejection: {e}", reply_markup=ReplyKeyboardRemove())
                # Transaction rolls back

    if success:
        await message.answer(f"Confession #{conf_id} has been rejected.", reply_markup=ReplyKeyboardRemove())

    await state.clear() # Clear state regardless of success/failure after processing


# --- Commenting Flow Handlers ---
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    # ... (Browse comments remains the same) ...
    try: conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid browse comments callback data: {callback_query.data}"); await callback_query.answer("Invalid data format.", show_alert=True); return
    await callback_query.answer("Loading comments...")
    # Call the modified function, passing the viewer's ID
    await show_comments_for_confession(callback_query.from_user.id, conf_id)


@dp.callback_query(F.data.startswith("add_"))
async def add_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (Add comment prompt remains the same) ...
    try:
        conf_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError):
        logging.error(f"Invalid add comment callback data: {callback_query.data}")
        await callback_query.answer("Invalid data format.", show_alert=True)
        return
    async with db.acquire() as conn:
        conf_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", conf_id)
        if not conf_exists:
            logging.warning(f"User {callback_query.from_user.id} tried to add comment to non-existent/unapproved conf {conf_id}.")
            await callback_query.answer("This confession is no longer available for commenting.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None); logging.info(f"Removed 'Add Comment' button for conf {conf_id} after finding it unavailable.")
            except Exception as e: logging.warning(f"Could not remove 'Add Comment' button for unavailable conf {conf_id}: {e}")
            return
    await state.update_data(confession_id=conf_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        await safe_send_message(callback_query.from_user.id, f"📝 You are adding a comment to Confession #{conf_id}.\nPlease send your comment text now, or type /cancel.")
        await callback_query.answer()
    except Exception as e:
        logging.warning(f"Could not send 'add comment' prompt to user {callback_query.from_user.id} for conf {conf_id}: {e}")
        await callback_query.answer("Could not start the commenting process. Please try again.", show_alert=True)
        await state.clear()

@dp.message(CommentForm.waiting_for_comment, F.text)
async def receive_comment(message: types.Message, state: FSMContext):
    # ... (Receiving comment remains the same) ...
    comm_text = message.text; user_id = message.from_user.id; data = await state.get_data(); conf_id = data.get("confession_id")
    if not conf_id: await message.answer("⚠️ Error: No confession context. Start again."); await state.clear(); logging.error(f"State missing conf_id for {user_id}"); return
    if len(comm_text) < 2: await message.answer("Comment too short (min 2 chars), or /cancel."); return
    if len(comm_text) > 1000: await message.answer(f"Comment too long (max 1000 chars). Has {len(comm_text)}. Shorten or /cancel."); return
    conf_owner_id = None; new_comm_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                 conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'", conf_id);
                 if not conf_owner_id: raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found/approved.")
                 new_comm_id = await conn.fetchval("INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, NULL) RETURNING id", conf_id, user_id, comm_text)
                 if not new_comm_id: raise Exception("Failed get new comment ID.")
        await message.answer("💬 Comment added!"); logging.info(f"User {user_id} added comment {new_comm_id} to conf {conf_id}"); await update_channel_post_button(conf_id)
        if conf_owner_id and conf_owner_id != user_id and bot_info and bot_info.username:
            link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"; preview = html.quote(comm_text[:150]) + ('...' if len(comm_text) > 150 else ''); notif = (f"💬 Comment on your confession #{conf_id}.\n\n<i>{preview}</i>\n\n<a href='{link}'>View comments.</a>"); await safe_send_message(conf_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username): logging.warning(f"Cannot gen notif link for author {conf_owner_id} - bot_info missing.")
        await show_comments_for_confession(user_id, conf_id)
    except asyncpg.exceptions.ForeignKeyViolationError: logging.warning(f"Attempt add comment to non-existent/unapproved conf {conf_id} by {user_id}"); await message.answer("⚠️ Cannot add comment. Confession removed/unapproved.")
    except Exception as e: logging.error(f"Error saving comment for conf {conf_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Internal error saving comment.")
    finally: await state.clear()

# --- Reply Flow Handlers ---
@dp.callback_query(F.data.startswith("reply_"))
async def reply_comment_prompt(callback_query: types.CallbackQuery, state: FSMContext):
    # ... (Reply prompt remains the same) ...
    try: parent_id = int(callback_query.data.split("_", 1)[1])
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid reply cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    msg_id_reply_to = callback_query.message.message_id
    async with db.acquire() as conn:
        comm_data = await conn.fetchrow("SELECT confession_id, text FROM comments WHERE id = $1", parent_id)
        if not comm_data:
            logging.warning(f"User {callback_query.from_user.id} tried to reply to non-existent parent comment {parent_id}.")
            await callback_query.answer("The comment you tried to reply to no longer exists.", show_alert=True)
            try: await callback_query.message.edit_reply_markup(reply_markup=None); logging.info(f"Removed buttons from message for deleted parent comment {parent_id}.")
            except Exception as e: logging.warning(f"Could not remove buttons for deleted parent comment {parent_id}: {e}")
            return
    conf_id = comm_data['confession_id']; preview = html.quote(comm_data['text'][:80]) + ('...' if len(comm_data['text']) > 80 else '')
    await state.update_data(confession_id=conf_id, parent_comment_id=parent_id, message_id_to_reply_to=msg_id_reply_to); await state.set_state(CommentForm.waiting_for_reply)
    try: prompt = (f"📝 Replying to comment:\n<i>{preview}</i>\n\nPlease send reply or /cancel."); await safe_send_message(callback_query.from_user.id, prompt, parse_mode=ParseMode.HTML); await callback_query.answer()
    except Exception as e: logging.warning(f"Could not send reply prompt to {callback_query.from_user.id}: {e}"); await callback_query.answer("Could not ask for reply.", show_alert=True); await state.clear()


@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    # ... (Receiving reply remains the same) ...
    reply_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    conf_id = data.get("confession_id")
    parent_id = data.get("parent_comment_id")
    msg_id_reply_to = data.get("message_id_to_reply_to")

    if not conf_id or not parent_id or not msg_id_reply_to:
        await message.answer("⚠️ Error: Reply context lost. Try again."); await state.clear();
        logging.error(f"State missing fields for {user_id} in receive_reply: {data}"); return
    if len(reply_text) < 1: await message.answer("Reply cannot be empty, or /cancel."); return
    if len(reply_text) > 1000: await message.answer(f"Reply too long (max 1000 chars). Has {len(reply_text)}. Shorten or /cancel."); return

    new_comm_id = None; parent_owner_id = None; conf_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                parent_data = await conn.fetchrow("SELECT user_id FROM comments WHERE id = $1 FOR UPDATE", parent_id);
                if not parent_data: await message.answer("⚠️ Original comment deleted."); await state.clear(); return
                parent_owner_id = parent_data['user_id']
                conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id);
                if not conf_owner_id: raise Exception("Confession not found during reply save")

                new_comm_id = await conn.fetchval(
                    "INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, $4) RETURNING id",
                    conf_id, user_id, reply_text, parent_id
                )
                if not new_comm_id: raise Exception("Failed get new reply ID.")

        logging.info(f"User {user_id} added reply {new_comm_id} to comment {parent_id} on conf {conf_id}")
        await update_channel_post_button(conf_id) # Update count

        # --- Send Confirmation back to the Replier ---
        try:
            async with db.acquire() as conn:
                new_reply_seq = await conn.fetchval("SELECT COUNT(*) FROM comments WHERE confession_id = $1 AND created_at <= (SELECT created_at FROM comments WHERE id = $2)", conf_id, new_comm_id) or new_comm_id # Fallback
            tag = ""
            if conf_owner_id is not None:
                 if user_id == conf_owner_id: tag = "(Author)"
                 elif user_id == message.from_user.id: tag = "(You)"
                 else: tag = "Anonymous"
            else: tag = "Anonymous"
            display_tag = f" {tag}" if tag else " Anonymous"
            admin_reply_info = ""
            if user_id == ADMIN_ID:
                 admin_reply_info = f" [UID: <code>{user_id}</code>]"

            reply_msg_txt = (
                f"💬 {html.quote(reply_text)}\n\n"
                f"<i>Reply #{new_reply_seq}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}{admin_reply_info}</i>"
            )
            reply_kbd = await build_comment_keyboard(
                 comment_id=new_comm_id,
                 commenter_user_id=user_id,
                 viewer_user_id=user_id,
                 confession_owner_id=conf_owner_id or 0
            )
            await bot.send_message(
                user_id, reply_msg_txt, reply_to_message_id=msg_id_reply_to, reply_markup=reply_kbd,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True, allow_sending_without_reply=True
            )
            await message.answer("↪️ Reply sent!")

        except TelegramBadRequest as e:
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Native reply failed for new comment {new_comm_id} (msg {msg_id_reply_to} deleted?). Sending update via show_comments.");
                 await message.answer("↪️ Reply sent! (Original message was deleted, reloading comment view)")
                 await show_comments_for_confession(user_id, conf_id) # Show full view as fallback
             else:
                 logging.error(f"TG error sending native reply confirmation {new_comm_id} by {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comm_id}), but error displaying confirmation.")
        except Exception as e:
             logging.error(f"Unexpected error sending native reply confirmation {new_comm_id} by {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comm_id}), but internal error displaying confirmation.")

        # --- Notify Parent Comment Author ---
        global bot_info
        if parent_owner_id and parent_owner_id != user_id and bot_info and bot_info.username:
             logging.info(f"Notifying parent author {parent_owner_id} of reply {new_comm_id} to their comment {parent_id}")
             link = f"https://t.me/{bot_info.username}?start=view_{conf_id}"
             preview = html.quote(reply_text[:150]) + ('...' if len(reply_text) > 150 else '')
             notif = (f"↪️ Reply to your comment on confession #{conf_id}.\n\n<i>{preview}</i>\n\n<a href='{link}'>View comments.</a>");
             await safe_send_message(parent_owner_id, notif, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        elif not (bot_info and bot_info.username):
              logging.warning(f"Cannot gen notif link for parent author {parent_owner_id} - bot_info missing.")

    except asyncpg.exceptions.ForeignKeyViolationError: logging.warning(f"FK violation reply to comment {parent_id} by {user_id}"); await message.answer("⚠️ Cannot add reply. Original comment removed.")
    except Exception as e: logging.error(f"Error saving reply DB transaction for {parent_id} by {user_id}: {e}", exc_info=True); await message.answer("❌ Internal error saving reply.")
    finally: await state.clear()


# --- Reaction Handling ---
@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    # ... (Reaction handling remains the same) ...
    try:
        _, r_type, comm_id_str = callback_query.data.split("_", 2)
        comm_id = int(comm_id_str)
        user_id = callback_query.from_user.id
        if r_type not in ['like', 'dislike']:
            raise ValueError("Invalid reaction type")
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid react cb: {callback_query.data}"); await callback_query.answer("Invalid reaction.", show_alert=True); return
    action = "none"; kbd = None; alert = None
    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                info = await conn.fetchrow("SELECT c.user_id as comm_uid, co.user_id as conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1", comm_id)
                if not info: raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")
                comm_uid = info['comm_uid']; conf_owner_id = info['conf_owner_id']; viewer_id = user_id
                existing = await conn.fetchval("SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", comm_id, user_id)
                if existing:
                    if existing == r_type: await conn.execute("DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2", comm_id, user_id); action = f"Removed {r_type}"; alert = f"{r_type.capitalize()} removed"
                    else: await conn.execute("UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", r_type, comm_id, user_id); action = f"Changed to {r_type}"; alert = f"Reaction changed to {r_type}"
                else: await conn.execute("INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)", comm_id, user_id, r_type); action = f"Added {r_type}"; alert = f"{r_type.capitalize()} added"
                kbd = await build_comment_keyboard(comm_id, comm_uid, viewer_id, conf_owner_id); logging.info(f"User {user_id} action '{action}' on comment {comm_id}. Kbd rebuilt.")
            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK viol reaction update comm {comm_id} user {user_id}")
                await callback_query.answer("Comment not found.", show_alert=True)
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return
            except Exception as db_err: logging.error(f"DB error reaction proc comm {comm_id} by {user_id}: {db_err}", exc_info=True); await callback_query.answer("DB Error processing reaction.", show_alert=True); return
    if kbd and action != "none":
        try: await callback_query.message.edit_reply_markup(reply_markup=kbd); await callback_query.answer(alert); logging.info(f"Updated markup comm {comm_id} after {action}")
        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str: logging.info(f"Markup {comm_id} not modified."); await callback_query.answer(alert + " (No visual change)")
            elif "message to edit not found" in err_str: logging.warning(f"Msg not found react update {comm_id}."); await callback_query.answer(alert + " (Counts updated, view not)", show_alert=False)
            elif "query is too old" in err_str: logging.warning(f"Query old react update {comm_id}."); await callback_query.answer(alert + " (Counts updated, view stale)", show_alert=False)
            else: logging.error(f"TG error update react markup {comm_id}: {e}"); await callback_query.answer("Error updating display.", show_alert=True)
        except Exception as e: logging.error(f"Unexpected error update react markup {comm_id}: {e}", exc_info=True); await callback_query.answer("Error updating display.", show_alert=True)
    elif action != "none": logging.error(f"Action {action} comm {comm_id} DB done, kbd build failed."); await callback_query.answer("Reaction processed (internal error).", show_alert=True)


# --- Contact Request Flow Handlers ---
@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    # ... (Request contact remains the same) ...
    try: _, _, comm_id_str = callback_query.data.split("_", 2); comm_id = int(comm_id_str); req_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid req contact cb: {callback_query.data}"); await callback_query.answer("Invalid request data.", show_alert=True); return
    async with db.acquire() as conn:
        async with conn.transaction():
            comm_data = await conn.fetchrow("SELECT c.user_id comm_uid, c.text comm_txt, co.id conf_id, co.user_id conf_owner_id FROM comments c JOIN confessions co ON c.confession_id = co.id WHERE c.id = $1 AND co.status = 'approved'", comm_id)
            if not comm_data: await callback_query.answer("Comment/confession not found.", show_alert=True); return
            comm_uid = comm_data['comm_uid']; conf_id = comm_data['conf_id']; conf_owner_id = comm_data['conf_owner_id']; preview = html.quote(comm_data['comm_txt'][:100]) + ('...' if len(comm_data['comm_txt']) > 100 else '')
            if req_uid != conf_owner_id: logging.warning(f"User {req_uid} tried req contact comm {comm_id} but not owner {conf_owner_id}."); await callback_query.answer("Only for your confessions.", show_alert=True); return
            if req_uid == comm_uid: await callback_query.answer("Cannot request contact self.", show_alert=True); return
            comm_chat = None; comm_username = None
            try:
                comm_chat = await bot.get_chat(comm_uid)
                comm_username = comm_chat.username
                if not comm_username: await callback_query.answer("User has no public username.", show_alert=True); logging.info(f"Author {req_uid} req comm {comm_id} aborted: commenter {comm_uid} no username."); return
            except TelegramBadRequest as e: logging.warning(f"Could not fetch {comm_uid} chat: {e}."); await callback_query.answer(f"Could not get commenter info: {e}.", show_alert=True); return
            except Exception as e: logging.error(f"Unexpected err fetching {comm_uid} chat: {e}", exc_info=True); await callback_query.answer("Could not get commenter info.", show_alert=True); return
            existing = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2 AND status IN ('pending', 'approved', 'approved_no_username')", comm_id, req_uid)
            if existing: await callback_query.answer(f"Request already {existing}.", show_alert=True); return
            request_id = None
            try:
                request_id = await conn.fetchval("INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status) VALUES ($1, $2, $3, $4, 'pending') ON CONFLICT (comment_id, requester_user_id) DO UPDATE SET status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE contact_requests.status = 'denied' RETURNING id", conf_id, comm_id, req_uid, comm_uid)
                if not request_id: existing_s = await conn.fetchval("SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2", comm_id, req_uid); logging.warning(f"Contact request insert for comm {comm_id} by {req_uid} returned no ID (ON CONFLICT?). Existing status: {existing_s}"); await callback_query.answer(f"Request already exists (Status: {existing_s or 'Unknown'}).", show_alert=True); return
            except Exception as insert_err: logging.error(f"Failed insert contact req {req_uid} to {comm_uid} for comm {comm_id}: {insert_err}", exc_info=True); await callback_query.answer("Failed save request.", show_alert=True); return
            notification_text = (f"🤝 Author of Confession #{conf_id} wants to contact you re your comment:\n\n<i>{preview}</i>\n\nDo you approve sharing @{html.quote(comm_username)}?"); approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_contact_{request_id}")], [InlineKeyboardButton(text="❌ Deny", callback_data=f"deny_contact_{request_id}")]]); sent = await safe_send_message(comm_uid, notification_text, reply_markup=approval_keyboard, parse_mode=ParseMode.HTML)
            if sent: await callback_query.answer("✅ Contact request sent.", show_alert=False); logging.info(f"Contact req {request_id} (comm {comm_id}) sent from {req_uid} to {comm_uid}.")
            else: logging.warning(f"Failed send contact req {request_id} notification to {comm_uid}. Rolling back."); await callback_query.answer("⚠️ Could not send request (user blocked?).", show_alert=True); raise Exception(f"Failed notify commenter {comm_uid}, rolling back req {request_id}")

def is_contact_response_callback(data: str) -> bool:
    if not isinstance(data, str): return False; parts = data.split("_"); return len(parts) == 3 and parts[0] in ('approve', 'deny') and parts[1] == 'contact' and parts[2].isdigit()

@dp.callback_query(lambda c: is_contact_response_callback(c.data))
async def handle_contact_response(callback_query: types.CallbackQuery):
    # ... (Contact response handling remains the same) ...
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
                comm_uname = callback_query.from_user.username
                if comm_uname: await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id); author_notif = (f"✅ Contact Approved!\n\nReq Confession #{conf_id} (Comment ~{comm_id}) APPROVED.\n\nContact: @{html.quote(comm_uname)}"); await callback_query.answer("Approved. Username shared."); logging.info(f"Req {req_id} approved by {resp_uid}. Uname @{comm_uname} sent to {req_uid}."); edit_status = 'Approved (Username Shared)'
                else: db_status = 'approved_no_username'; await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", db_status, req_id); author_notif = (f"⚠️ Contact Approved (No Username)\n\nReq Confession #{conf_id} (Comment ~{comm_id}) APPROVED, but user lacks public username."); await callback_query.answer("Approved, but no public username.", show_alert=True); logging.info(f"Req {req_id} approved by {resp_uid}, no username. Notified {req_uid}."); edit_status = 'Approved (No Username)'
            else: await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", req_id); author_notif = (f"❌ Contact Denied\n\nReq Confession #{conf_id} (Comment ~{comm_id}) DENIED."); await callback_query.answer("Denied. Username not shared."); logging.info(f"Req {req_id} denied by {resp_uid}. Notified {req_uid}."); edit_status = 'Denied'
            await safe_send_message(req_uid, author_notif, parse_mode=ParseMode.HTML)
            try: orig_txt = callback_query.message.html_text; final_txt = f"{orig_txt}\n\n<b>Status: {edit_status}</b>"; await callback_query.message.edit_text(final_txt, reply_markup=None, parse_mode=ParseMode.HTML)
            except TelegramBadRequest as e:
                 if "message is not modified" not in str(e).lower() and "message to edit not found" not in str(e).lower(): logging.warning(f"Could not edit commenter's ({resp_uid}) notif msg {callback_query.message.message_id} req {req_id}: {e}")
            except Exception as e: logging.warning(f"Could not edit commenter's ({resp_uid}) notif msg {callback_query.message.message_id} req {req_id}: {e}")

@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    # ... (View contact requests remains the same) ...
    try: _, _, conf_id_str = callback_query.data.split("_", 2); conf_id = int(conf_id_str); viewer_uid = callback_query.from_user.id
    except (ValueError, IndexError, TypeError): logging.error(f"Invalid view reqs cb: {callback_query.data}"); await callback_query.answer("Invalid data.", show_alert=True); return
    async with db.acquire() as conn:
        conf_owner_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id)
    if not conf_owner_id: await callback_query.answer("Confession not found.", show_alert=True); return
    if viewer_uid != conf_owner_id: await callback_query.answer("Only for your confessions.", show_alert=True); return
    async with db.acquire() as conn:
        reqs = await conn.fetch("SELECT cr.comment_id, cr.status, cr.updated_at, c.text as comment_text, cr.requested_user_id FROM contact_requests cr JOIN comments c ON cr.comment_id = c.id WHERE cr.confession_id = $1 AND cr.requester_user_id = $2 ORDER BY cr.updated_at DESC", conf_id, viewer_uid)
    if not reqs: await callback_query.answer("No contact requests made for this confession.", show_alert=False); return
    resp_parts = [f"<b>Contact Requests Status for Confession #{conf_id}</b>\n"];
    for req in reqs:
        preview = html.quote(req['comment_text'][:60]) + ('...' if len(req['comment_text']) > 60 else '')
        status = req['status'].replace('_', ' ').capitalize()
        updated = req['updated_at'].strftime("%Y-%m-%d %H:%M")
        req_uid = req['requested_user_id']
        status_emoji = {"pending": "❓", "approved": "✅", "denied": "❌", "approved_no_username": "⚠️"}.get(req['status'], "❓")
        resp_parts.append(f"🔹 <b>To Commenter ID:</b> <code>{req_uid}</code>\n   <i>Comment: \"{preview}\"</i>\n   <b>Status:</b> {status_emoji} {status}\n   <b>Last Update:</b> {updated}")
        if req['status'] == 'approved':
            try: req_user_chat = await bot.get_chat(req_uid); resp_parts.append(f"   <b>Username:</b> @{html.quote(req_user_chat.username)}" if req_user_chat and req_user_chat.username else "   <b>Username:</b> (Not Available)")
            except Exception: resp_parts.append(f"   <b>Username:</b> (Error fetching)")
    resp_txt = "\n\n".join(resp_parts);
    if len(resp_txt) > 4096: resp_txt = resp_txt[:4090] + "\n\n...(truncated)"
    await safe_send_message(viewer_uid, resp_txt, parse_mode=ParseMode.HTML, disable_web_page_preview=True); await callback_query.answer()


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    logging.debug(f"Received non-command text from user {message.from_user.id} outside of any state: '{message.text[:50]}...'")
    await message.reply("Hi there! 👋\nUse /confess to share something anonymously.\nUse /help to see all available commands.")


# --- Main Execution ---
async def main():
    try:
        await setup()
        if db and bot_info:
            commands_list = [
                types.BotCommand(command="start", description="Start the bot / View confession"),
                types.BotCommand(command="confess", description="Submit an anonymous confession"),
                types.BotCommand(command="help", description="Show help and commands"),
                types.BotCommand(command="privacy", description="View privacy information"),
                types.BotCommand(command="cancel", description="Cancel current action"),
            ]
            admin_commands_list = commands_list + [
                types.BotCommand(command="id", description="ADMIN: Get user info by ID"),
                # No specific command needed for rejection reason, it's part of the flow
            ]
            await bot.set_my_commands(commands_list) # Set default commands for everyone
            try: # Attempt setting admin-specific commands
                 await bot.set_my_commands(admin_commands_list, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
                 logging.info(f"Admin-specific commands set for ADMIN_ID {ADMIN_ID}.")
            except Exception as e: logging.warning(f"Could not set admin-specific commands: {e}")

            logging.info("Starting bot polling...")
            # --- *** ADDITION: Register new handlers *** ---
            # Ensure the new state handler is registered BEFORE the generic text handler
            dp.message.register(receive_rejection_reason, AdminActions.waiting_for_rejection_reason, F.text)
            # Re-register fallback handler to ensure it's last for text messages without state
            dp.message.register(handle_text_without_state, StateFilter(None), F.text & ~F.text.startswith('/'))
            # --- *** END ADDITION *** ---

            await dp.start_polling(bot, skip_updates=True) # skip_updates might miss things if bot offline long
        else: logging.critical("FATAL: DB connection or bot info missing. Cannot start.")
    except Exception as e: logging.critical(f"Fatal error setup/polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...");
        if bot and bot.session and not bot.session.closed: await bot.session.close(); logging.info("Bot session closed.")
        if db: logging.info("Closing database pool..."); await db.close(); logging.info("Database pool closed.")
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: logging.info("Bot stopped by user (KeyboardInterrupt).")
    except Exception as main_err: logging.critical(f"Critical error in main loop: {main_err}", exc_info=True); print(f"Critical error: {main_err}")

# --- END OF FILE main.py ---

# --- END OF FILE main[V2.0.1.8] - Stable.py ---