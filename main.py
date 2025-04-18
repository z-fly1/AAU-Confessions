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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton # Added Reply Keyboards
from aiogram.utils.keyboard import InlineKeyboardBuilder # Use builder for dynamic keyboards
from datetime import datetime # For formatting timestamps
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from typing import Optional

# --- Constants ---
CATEGORIES = [
    "Relationship", "Education", "Family", "School", "Friendship",
    "Religion", "Entertainment", "Information", "Sexual Assault", "Other" # Added Other
]
PRIVACY_POLICY_URL = "https://telegra.ph/Privacy-Policy-for-AAU-Confession-Bot-04-16"

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None # Handle potential missing ADMIN_ID
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Check essential variables
if not BOT_TOKEN:
    logging.critical("FATAL: BOT_TOKEN environment variable not set!")
    exit()
if not ADMIN_ID:
    logging.critical("FATAL: ADMIN_ID environment variable not set or not an integer!")
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
    waiting_for_reply = State() # New state for replies

# New FSM State for contacting admin
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
        # --- Create Confessions Table FIRST ---
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

        # --- Create Comments Table FIRST ---
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

        logging.info("Database tables setup complete.")


# --- Helper Functions ---

def create_category_keyboard():
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2) # Adjust layout, e.g., 2 buttons per row
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

async def build_comment_keyboard(comment_id: int):
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")
    builder.adjust(3) # All buttons in one row
    return builder.as_markup()

async def safe_send_message(user_id: int, text: str, **kwargs):
    """Safely send a message, handling common errors like user blocking."""
    try:
        await bot.send_message(user_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        # User blocked the bot, deactivated account, or chat not found
        logging.warning(f"Could not send message to user {user_id}: {e}")
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
    global bot_info # Ensure bot_info is accessible
    if not bot_info:
        logging.error(f"Cannot update channel post for {confession_id}: Bot info not available.")
        return

    async with db.acquire() as conn:
        # Get channel message ID and current comment count
        confession_data = await conn.fetchrow(
            "SELECT message_id FROM confessions WHERE id = $1 AND status = 'approved'",
            confession_id
        )
        comment_count = await conn.fetchval(
            "SELECT COUNT(*) FROM comments WHERE confession_id = $1",
            confession_id
        ) or 0 # Default to 0 if no comments

    if not confession_data or not confession_data['message_id']:
        logging.warning(f"Could not find approved confession or channel message_id for confession {confession_id} to update button.")
        return

    channel_message_id = confession_data['message_id']
    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"

    # Build the new keyboard
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
    """Fetches and sends comments, assigning sequence numbers.
       Adds "(Author)" tag for comments by the original confessor.
       Adds "(You)" tag for comments by the browsing user (if not the author).
       Reply prefix refers to the parent's sequential number.
    """
    confession_owner_id: Optional[int] = None # Variable to store the confession owner's ID

    async with db.acquire() as conn:
        # Fetch confession status AND owner_id
        confession_data = await conn.fetchrow(
            "SELECT status, user_id FROM confessions WHERE id = $1",
            confession_id
        )

        if not confession_data or confession_data['status'] != 'approved':
            error_text = f"Confession #{confession_id} could not be found or hasn't been approved."
            try:
                if message_to_edit:
                    await message_to_edit.edit_text(error_text, reply_markup=None)
                else:
                    await safe_send_message(user_id, error_text)
            except Exception as e:
                logging.warning(f"Could not send/edit 'confession not found' to user {user_id}: {e}")
            return

        # Store the owner ID if the confession is valid
        confession_owner_id = confession_data['user_id']

        # Fetch all approved comments for the confession
        comments = await conn.fetch(
            """
            SELECT id, user_id, text, parent_comment_id, created_at
            FROM comments
            WHERE confession_id = $1
            ORDER BY created_at ASC
            """,
            confession_id
        )

    # --- Prepare comment display with sequential numbering and author tag ---
    sent_message_ids = {}
    comment_id_to_sequence = {}
    comment_counter = 0

    if not comments:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
    else:
        comments_html = f"--- Comments for Confession #{confession_id} ---\n\n"

        # First pass: Assign sequence numbers
        temp_comment_map = {}
        for c_data in comments:
            comment_counter += 1
            db_comment_id = c_data['id']
            comment_id_to_sequence[db_comment_id] = comment_counter
            temp_comment_map[db_comment_id] = c_data

        # Second pass: Generate and send messages
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
            else:
                author_tag = "Anonymous"
            display_tag = f" {author_tag}" if author_tag != "Anonymous" else ""

            # Format metadata including sequence number and the tag
            comment_metadata = f"<i>#{current_sequence_num}{display_tag} | {timestamp}</i>"

            # Build keyboard
            keyboard = await build_comment_keyboard(comment_id)

            # Construct the full text
            full_comment_text = f"{reply_prefix}💬 {comment_text}\n\n{comment_metadata}"

            try:
                sent_msg = await bot.send_message(
                    user_id,
                    full_comment_text,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True
                )
                sent_message_ids[comment_id] = sent_msg.message_id
            except Exception as e:
                logging.warning(f"Could not send comment #{current_sequence_num} (DB ID: {comment_id}) to user {user_id}: {e}")
                try:
                    await safe_send_message(user_id, f"⚠️ Error displaying comment #{current_sequence_num}. Details: {e}")
                except Exception:
                    pass

    # --- Send the "Add Comment" button ---
    add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])
    end_text = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_text += "You can add your own comment below:"

    try:
        if message_to_edit and not comments:
            # If we started with a message to edit and found no comments, edit it.
            await message_to_edit.edit_text(end_text, reply_markup=add_comment_button, parse_mode=ParseMode.HTML)
        elif message_to_edit and comments:
             # If we started with a message to edit but FOUND comments (which were sent above), send a new message for the final prompt.
             await safe_send_message(user_id, end_text, reply_markup=add_comment_button, parse_mode=ParseMode.HTML)
             # Optionally delete the original message_to_edit (like the "Loading..." message)
        else:
            # If we didn't have an initial message to edit, just send the final prompt.
            await safe_send_message(user_id, end_text, reply_markup=add_comment_button, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.warning(f"Could not send/edit final 'Add Comment' prompt to user {user_id}: {e}")


# --- Filter for Admin Replies ---
# This filter checks if a message is a reply FROM the admin TO a message sent BY THIS bot
class IsAdminReplyFilter(BaseFilter):
    async def __call__(self, message: types.Message) -> bool:
        global bot_info # Need bot's ID
        if not bot_info: return False # Cannot check if bot info not loaded
        return (
            message.reply_to_message is not None and
            message.from_user.id == ADMIN_ID and
            message.reply_to_message.from_user.id == bot_info.id # Check if replied-to message is from our bot
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

            # Fetch confession and comment count for deep link view
            async with db.acquire() as conn:
                confession_data = await conn.fetchrow(
                    "SELECT text, category, status FROM confessions WHERE id = $1",
                    confession_id
                )
                comment_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM comments WHERE confession_id = $1",
                    confession_id
                ) or 0 # Ensure count is 0

            if not confession_data or confession_data['status'] != 'approved':
                await message.answer(f"Confession #{confession_id} could not be found or hasn't been approved.")
                return

            confession_text = confession_data['text']
            category = confession_data['category']

            text_to_show = f"<b>Confession #{confession_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}\n---"

            # Build keyboard with Add and Browse (with count)
            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
            builder.button(text=f"💬 Browse Comments ({comment_count})", callback_data=f"browse_{confession_id}")
            builder.adjust(1) # Stack buttons vertically

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
            "It will be reviewed by an admin before posting.\n\n"
            "Use /help for more options."
        )

# --- /privacy Handler ---
@dp.message(Command("privacy"))
async def privacy_policy(message: types.Message):
    """Handles the /privacy command."""
    privacy_text = (
        f"🔒 <b>Privacy Policy</b>\n\n"
        f"This bot is designed for anonymous confessions and comments within the AAU community. "
        f"Your Telegram User ID is stored when you submit a confession or comment to enable functionality like notifications and identifying your comments.\n\n"
        f"Your User ID is:\n"
        f"- Shown to the admin <b>only</b> when your confession is submitted for review or when you contact the admin via /help.\n"
        f"- <b>Never</b> displayed publicly in the channel or alongside comments (unless you comment on your own confession, where it will be marked as 'Author').\n"
        f"- Used to notify you if your confession is approved/rejected or if someone comments/replies to you.\n\n"
        f"We do not collect any other personal data.\n\n"
        f"For full details, please read our privacy policy: {html.link('View Privacy Policy', PRIVACY_POLICY_URL)}" # Use html.link for cleaner link
    )
    await message.answer(privacy_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# --- /help Handler ---
@dp.message(Command("help"))
async def show_help(message: types.Message):
    """Handles the /help command and offers contact option."""
    help_text = (
        "ℹ️ <b>Help & Information</b>\n\n"
        "Here's how to use the bot:\n"
        "• Use /confess to start submitting a new confession anonymously.\n"
        "• Use /privacy to view the privacy policy.\n"
        "• Use /start to see the welcome message or process a deep link.\n"
        "• Follow the prompts after using commands or interacting with buttons.\n\n"
        "Need to report an issue or contact the admin directly?"
    )
    contact_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✉️ Contact Admin", callback_data="contact_admin")]
    ])
    await message.answer(help_text, reply_markup=contact_keyboard)

# --- Callback for Contact Admin Button ---
@dp.callback_query(F.data == "contact_admin", StateFilter(None)) # Ensure no other state is active
async def prompt_admin_message(callback_query: types.CallbackQuery, state: FSMContext):
    """Asks the user for the message they want to send to the admin."""
    await state.set_state(ContactAdminForm.waiting_for_message)
    try:
        await callback_query.message.edit_text( # Edit the help message
            "Okay, please send the message you'd like to forward to the admin.\n\n"
            "<i>Note: The admin will see your Telegram profile and ID to be able to reply.</i>",
            reply_markup=None # Remove the button
        )
    except TelegramBadRequest as e:
         logging.warning(f"Could not edit help message to prompt for admin contact: {e}")
         # If editing fails (e.g., message too old), send a new message
         await safe_send_message(
             callback_query.from_user.id,
             "Okay, please send the message you'd like to forward to the admin.\n\n"
            "<i>Note: The admin will see your Telegram profile and ID to be able to reply.</i>"
         )
    await callback_query.answer() # Acknowledge button press

# --- Handler for User's Message to Admin ---
@dp.message(ContactAdminForm.waiting_for_message, F.text)
async def forward_to_admin(message: types.Message, state: FSMContext):
    """Forwards the user's message to the admin."""
    user = message.from_user
    user_message_text = message.text

    # Validation
    if len(user_message_text) < 5:
        await message.reply("Your message seems a bit short. Please provide more detail.")
        return
    if len(user_message_text) > 2000:
        await message.reply("Your message is too long (max 2000 chars). Please shorten it.")
        return

    user_mention_link = user.mention_html(user.full_name) # Use user's name for link text

    admin_notification_text = (
        f"✉️ <b>Direct Message from User</b>\n\n"
        f"<b>From:</b> {user_mention_link} (ID: <code>{user.id}</code>)\n"
        f"------------------------------------\n"
        f"{html.quote(user_message_text)}\n"
        f"------------------------------------\n"
        f"✍️ <b>Reply to this message directly</b> to send your response back to the user."
    )

    try:
        # Send to Admin
        await bot.send_message(
            ADMIN_ID,
            admin_notification_text,
            parse_mode=ParseMode.HTML
        )
        # Confirm to User
        await message.answer("✅ Your message has been sent to the admin. They will reply here if needed.")
        logging.info(f"User {user.id} ({user.username or 'no username'}) sent a direct message to admin.")

    except Exception as e:
        logging.error(f"Failed to forward message from user {user.id} to admin: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an error sending your message to the admin. Please try again later.")
    finally:
        await state.clear() # Clear the state after sending or error

# --- Handler for Admin's Reply ---
@dp.message(IsAdminReplyFilter(), F.text) # Use the custom filter
async def handle_admin_reply(message: types.Message):
    """Handles the admin replying to a user's forwarded message."""
    admin_reply_text = message.text
    original_bot_message = message.reply_to_message # The message sent *by the bot* to the admin

    # --- Extract the original User ID from the bot's message text ---
    target_user_id = None
    # Use regex to find the User ID reliably
    # Updated regex to handle potential variations in formatting (e.g., bold tags around ID)
    match = re.search(r"\(ID:\s*<code>(\d+)</code>\)", original_bot_message.html_text, re.IGNORECASE)
    if match:
        target_user_id = int(match.group(1))
        logging.info(f"Admin ({message.from_user.id}) replying to user {target_user_id}")
    else:
        logging.error(f"Could not parse target user ID from admin reply. Original bot message text: {original_bot_message.html_text}")
        await message.reply("⚠️ Error: Couldn't identify the original user ID from the message you replied to. Please ensure you reply *directly* to the bot's message containing '(ID: <code>123...</code>)'.")
        return

    # --- Send the admin's reply to the target user ---
    user_notification_text = f"ℹ️ <b>Reply from Admin:</b>\n\n{html.quote(admin_reply_text)}"

    sent_successfully = await safe_send_message(
        target_user_id,
        user_notification_text,
        parse_mode=ParseMode.HTML
    )

    if sent_successfully:
        await message.reply("✅ Your reply has been sent to the user.") # Confirm to admin
        logging.info(f"Admin reply successfully sent to user {target_user_id}")
    else:
        await message.reply(f"❌ Failed to send reply to user {target_user_id}. They might have blocked the bot.")
        logging.warning(f"Failed to send admin reply to user {target_user_id}")


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
        await callback_query.answer() # Ack button press
        await safe_send_message(callback_query.from_user.id, f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")


@dp.message(ConfessionForm.waiting_for_text, F.text)
async def receive_confession_text(message: types.Message, state: FSMContext):
    confession_text = message.text
    user = message.from_user # Get the full user object
    user_id = user.id
    state_data = await state.get_data()
    category = state_data.get("category")

    if not category:
        await message.answer("⚠️ Error: Category information missing. Please start again with /confess.")
        await state.clear()
        logging.error(f"State data missing category for user {user_id} in receive_confession_text")
        return

    if len(confession_text) < 10:
        await message.answer("Your confession seems a bit short. Please provide more detail.")
        return
    if len(confession_text) > 3900: # Leave space for formatting
        await message.answer("Your confession is too long (max ~3900 chars). Please shorten it.")
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

        # Generate user mention link (HTML)
        user_mention_link = user.mention_html(user.full_name) # Or use user.first_name

        # Prepare the message text for the admin, including the link
        admin_notification_text = (
            f"<b>New Confession #{confession_id}</b>\n\n"
            f"<b>Category:</b> {category}\n"
            f"<b>Submitted by:</b> {user_mention_link} (ID: <code>{user_id}</code>)\n" # Link + ID
            f"------------------------------------\n"
            f"{html.quote(confession_text)}" # Ensure confession text is escaped
        )

        await bot.send_message(
            ADMIN_ID,
            admin_notification_text, # Use the new text with the link
            reply_markup=approve_keyboard,
            parse_mode=ParseMode.HTML # Ensure HTML parsing is active
        )

        await message.answer("✅ Your confession has been submitted for review.")
        logging.info(f"Confession {confession_id} (Category: {category}) submitted by user {user_id} ({user.username or 'no username'})")

    except Exception as e:
        logging.error(f"Error in receive_confession_text from user {user_id}: {e}", exc_info=True)
        await message.answer("An internal error occurred while submitting your confession. Please try again later.")
    finally:
        await state.clear()


# --- Admin Actions ---

@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def admin_action(callback_query: types.CallbackQuery):
    # Ensure bot_info is available
    global bot_info
    if not bot_info:
        logging.error("Bot info not available for admin action.")
        await callback_query.answer("Internal error: Bot username missing.", show_alert=True)
        return

    # Ensure sender is the admin
    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized to perform this action.", show_alert=True)
        logging.warning(f"Unauthorized attempt to moderate confession by user {callback_query.from_user.id}")
        return

    try:
        action, confession_id_str = callback_query.data.split("_", 1)
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid admin callback data format: {callback_query.data}")
        await callback_query.answer("Invalid action data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            # Fetch confession details FOR UPDATE to lock the row
            confession = await conn.fetchrow(
                "SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE",
                confession_id
            )

            if not confession:
                await callback_query.answer("Confession not found.", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass
                return

            if confession['status'] != 'pending':
                await callback_query.answer(f"Confession already {confession['status']}.", show_alert=True)
                try: await callback_query.message.delete() # Clean up admin message
                except Exception: pass
                return

            user_id = confession["user_id"]
            confession_text = confession["text"]
            confession_db_id = confession["id"]
            category = confession["category"]

            try:
                if action == "approve":
                    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_db_id}"
                    logging.info(f"Generated deep link for {confession_db_id}: {deep_link_url}")

                    text_to_post = f"<b>Confession #{confession_db_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}"

                    # Post with initial comment count (0)
                    initial_markup = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="💬 View / Add Comments (0)", url=deep_link_url)]
                    ])

                    channel_message = await bot.send_message(
                        CHANNEL_ID,
                        text_to_post,
                        reply_markup=initial_markup, # Use initial markup
                        parse_mode=ParseMode.HTML
                    )

                    await conn.execute(
                        "UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2",
                        channel_message.message_id, confession_db_id
                    )
                    # Use safe_send_message for user notification
                    await safe_send_message(user_id, f"✅ Your confession (#{confession_db_id} - #{category}) has been approved and posted!")
                    await callback_query.answer(f"Confession {confession_db_id} approved.")
                    logging.info(f"Admin {callback_query.from_user.id} approved confession {confession_db_id}")

                else: # action == "reject"
                    await conn.execute(
                        "UPDATE confessions SET status = 'rejected' WHERE id = $1",
                        confession_db_id
                    )
                    # Use safe_send_message for user notification
                    await safe_send_message(user_id, f"❌ Your confession (#{confession_db_id} - #{category}) was rejected.")
                    await callback_query.answer(f"Confession {confession_db_id} rejected.")
                    logging.info(f"Admin {callback_query.from_user.id} rejected confession {confession_db_id}")

                # Delete the admin approval/rejection message
                try:
                    await callback_query.message.delete()
                except Exception as e:
                     logging.warning(f"Could not delete admin action message for confession {confession_id}: {e}")

            except TelegramForbiddenError:
                 logging.error(f"Bot is likely blocked or lacks permissions in channel {CHANNEL_ID} or with user {user_id}.")
                 await callback_query.answer("Error: Check bot permissions or if user blocked.", show_alert=True)
                 raise # Reraise to ensure rollback
            except Exception as e:
                logging.error(f"Error processing admin action ({action}) for confession {confession_id}: {e}", exc_info=True)
                await callback_query.answer(f"An error occurred: {e}. Action may not have completed.", show_alert=True)
                raise # Reraise to ensure rollback


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
    # Pass the message to potentially edit if no comments are found initially
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
         # Optionally edit the message the button was attached to
         try: await callback_query.message.edit_reply_markup(reply_markup=None)
         except Exception: pass
         return

    await state.update_data(confession_id=confession_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        # Send a new message for the prompt
        await safe_send_message(callback_query.from_user.id, f"📝 Please send your comment for Confession #{confession_id}:")
        await callback_query.answer() # Acknowledge button press silently
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

    # Validation
    if len(comment_text) < 2:
        await message.answer("Your comment is too short.")
        return
    if len(comment_text) > 1000:
        await message.answer("Your comment is too long (max 1000 chars).")
        return

    confession_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction(): # Use transaction
                 # Fetch confession owner ID *before* inserting comment
                 confession_owner_id = await conn.fetchval(
                     "SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'",
                     confession_id
                 )
                 if not confession_owner_id:
                     # Confession might have been deleted/rejected between prompt and send
                     raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found or not approved.")

                 await conn.execute(
                     "INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, NULL)",
                     confession_id, user_id, comment_text
                 )
            # --- Transaction successful ---
            await message.answer("💬 Your comment has been added!")
            logging.info(f"User {user_id} added direct comment to confession {confession_id}")

            # Update channel post button count
            await update_channel_post_button(confession_id)

            # Notify original confessor
            if confession_owner_id and confession_owner_id != user_id: # Don't notify if commenting on own confession
                 logging.info(f"Notifying user {confession_owner_id} about new comment on confession {confession_id}")
                 deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                 notification_text = (
                     f"💬 Someone commented on your confession #{confession_id}.\n\n"
                     f"Comment: {html.quote(comment_text[:150])}{'...' if len(comment_text) > 150 else ''}\n\n" # Preview comment
                     f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                 )
                 await safe_send_message(confession_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

            # Show comments again after adding
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

    # Get the message ID of the comment message the user wants to reply to
    message_id_to_reply_to = callback_query.message.message_id

    async with db.acquire() as conn:
        comment_data = await conn.fetchrow(
            "SELECT confession_id, text, user_id FROM comments WHERE id = $1", # Also get user_id to check if replying to self
            parent_comment_id
        )

    if not comment_data:
         await callback_query.answer("Cannot reply: Original comment not found.", show_alert=True)
         # Optionally edit the message the button was attached to
         try: await callback_query.message.edit_reply_markup(reply_markup=None)
         except Exception: pass
         return

    confession_id = comment_data['confession_id']
    parent_comment_text_preview = html.quote(comment_data['text'][:80]) + ('...' if len(comment_data['text']) > 80 else '')

    await state.update_data(
        confession_id=confession_id,
        parent_comment_id=parent_comment_id,
        message_id_to_reply_to=message_id_to_reply_to # Store the target message ID
    )
    await state.set_state(CommentForm.waiting_for_reply)
    try:
        prompt_text = (
            f"📝 Replying to comment:\n"
            f"<i>{parent_comment_text_preview}</i>\n\n"
            f"Please send your reply:"
        )
        # Send prompt as a new message, don't edit the comment list
        await safe_send_message(
            callback_query.from_user.id,
            prompt_text,
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer() # Acknowledge the button press
    except Exception as e:
        logging.warning(f"Could not send reply prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for reply.", show_alert=True)
        except Exception: pass
        await state.clear() # Clear state if prompt fails


@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    reply_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")
    parent_comment_id = data.get("parent_comment_id")
    message_id_to_reply_to = data.get("message_id_to_reply_to")

    if not confession_id or not parent_comment_id or not message_id_to_reply_to:
        await message.answer("⚠️ Error: Could not determine what to reply to. Please try again.")
        await state.clear()
        logging.error(f"State data missing required fields for user {user_id} in receive_reply: {data}")
        return

    # Validation
    if len(reply_text) < 1:
        await message.answer("Your reply is too short.")
        return
    if len(reply_text) > 1000:
        await message.answer("Your reply is too long (max 1000 chars).")
        return

    new_comment_id = None
    parent_comment_owner_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                # Check parent comment existence and get its owner ID FOR UPDATE
                parent_data = await conn.fetchrow(
                    "SELECT user_id FROM comments WHERE id = $1 FOR UPDATE",
                    parent_comment_id
                )
                if not parent_data:
                    await message.answer("⚠️ Sorry, the comment you were replying to seems to have been deleted.")
                    await state.clear()
                    return
                parent_comment_owner_id = parent_data['user_id']

                new_comment_id = await conn.fetchval(
                    """
                    INSERT INTO comments (confession_id, user_id, text, parent_comment_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    confession_id, user_id, reply_text, parent_comment_id
                )
        # --- DB Insert successful ---
        logging.info(f"User {user_id} added reply (ID: {new_comment_id}) to comment {parent_comment_id} on confession {confession_id}")

        # Update channel post button count
        await update_channel_post_button(confession_id)

        # Send the reply using native Telegram reply mechanism
        try:
            reply_keyboard = await build_comment_keyboard(new_comment_id)
            timestamp_now = datetime.now().strftime('%Y-%m-%d %H:%M') # Timestamp for the reply message

            # Determine the tag for the reply message itself
            # Get confession owner ID again (could be cached, but safer to fetch)
            confession_owner_id = await db.fetchval("SELECT user_id FROM confessions WHERE id = $1", confession_id)
            reply_author_tag = ""
            if user_id == confession_owner_id:
                 reply_author_tag = "(Author)"
            # No "(You)" needed here as it's sent *to* the user

            display_tag = f" {reply_author_tag}" if reply_author_tag else " Anonymous"

            # Format the reply text shown in the user's chat
            reply_message_text = f"💬 {html.quote(reply_text)}\n\n<i>#{display_tag} | {timestamp_now}</i>" # Add tag

            sent_reply_message = await bot.send_message(
                chat_id=user_id,
                text=reply_message_text,
                reply_to_message_id=message_id_to_reply_to, # Native reply
                reply_markup=reply_keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                allow_sending_without_reply=True # Allow sending even if original msg deleted
            )
            await message.answer("↪️ Reply sent!", disable_notification=True) # Subtle confirmation

            # Notify parent comment owner
            if parent_comment_owner_id and parent_comment_owner_id != user_id: # Don't notify if replying to own comment
                logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} to their comment {parent_comment_id}")
                deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}" # Link to whole confession
                notification_text = (
                    f"↪️ Someone replied to your comment on confession #{confession_id}.\n\n"
                    f"Reply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n"
                    f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                )
                await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        except TelegramBadRequest as e:
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Could not send native reply for comment {parent_comment_id} - original message deleted? Sent reply normally (allow_sending_without_reply=True).")
                 # Confirmation was already sent, notification below will proceed
             else:
                 logging.error(f"Telegram error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an error displaying it as a direct reply.")
             # Still try to notify parent owner even if visual reply fails
             if parent_comment_owner_id and parent_comment_owner_id != user_id:
                  logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} (original msg deleted/reply error) to comment {parent_comment_id}")
                  deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                  notification_text = (
                      f"↪️ Someone replied to your comment on confession #{confession_id} (visual link might be missing).\n\n"
                      f"Reply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n"
                      f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                  )
                  await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        except Exception as e:
             logging.error(f"Unexpected error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an internal error displaying it.")

        # --- Remove automatic refresh ---
        # We don't call show_comments_for_confession here anymore.

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"FK violation adding reply to comment {parent_comment_id} by user {user_id}")
         await message.answer("⚠️ Sorry, could not add reply. The original confession or parent comment might have been removed.")
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

    action_taken = "none"
    new_keyboard = None
    alert_message = None # Specific message for callback answer

    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                # Lock the specific reaction row if it exists, or check comment existence
                comment_exists = await conn.fetchval("SELECT 1 FROM comments WHERE id = $1", comment_id)
                if not comment_exists:
                    raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")

                existing_reaction = await conn.fetchval(
                    "SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE",
                    comment_id, user_id
                )

                if existing_reaction:
                    if existing_reaction == reaction_type:
                        await conn.execute(
                            "DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2",
                            comment_id, user_id
                        )
                        action_taken = f"Removed {reaction_type}"
                        alert_message = f"{reaction_type.capitalize()} removed"
                    else:
                        await conn.execute(
                            "UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3",
                            reaction_type, comment_id, user_id
                        )
                        action_taken = f"Changed to {reaction_type}"
                        alert_message = f"Reaction changed to {reaction_type}"
                else:
                    await conn.execute(
                        "INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)",
                        comment_id, user_id, reaction_type
                    )
                    action_taken = f"Added {reaction_type}"
                    alert_message = f"{reaction_type.capitalize()} added"

                # Get updated counts and build keyboard AFTER DB change
                new_keyboard = await build_comment_keyboard(comment_id)
                logging.info(f"User {user_id} action '{action_taken}' on comment {comment_id}. Keyboard rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK violation during reaction update for comment {comment_id} user {user_id} (comment likely deleted)")
                await callback_query.answer("Comment not found.", show_alert=True)
                # Edit the message to remove buttons if comment is gone
                try: await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return # Exit early, transaction rolled back
            except Exception as db_err:
                logging.error(f"Database error during reaction processing for comment {comment_id} by user {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("Error processing reaction (database).", show_alert=True)
                return # Exit early, transaction rolled back

    # --- DB Transaction successful, now try to update Telegram message ---
    if new_keyboard and action_taken != "none":
        try:
            # Fetch current markup directly before editing
            current_markup = callback_query.message.reply_markup
            # Compare keyboard content (important!)
            if current_markup and new_keyboard.inline_keyboard == current_markup.inline_keyboard:
                logging.info(f"Skipping message edit for comment {comment_id}: keyboard content unchanged. Action: {action_taken}.")
                await callback_query.answer(alert_message) # Still provide feedback
            else:
                await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
                await callback_query.answer(alert_message) # Answer on successful edit
                logging.info(f"Successfully updated markup for comment {comment_id} after action: {action_taken}")

        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.warning(f"Got 'message is not modified' for comment {comment_id} despite content check (race condition?). Action: {action_taken}.")
                await callback_query.answer(alert_message) # Feedback that DB is fine
            elif "message to edit not found" in err_str:
                logging.warning(f"Message to edit not found for reaction update on comment {comment_id}. Action: {action_taken}.")
                # Cannot answer query if message is gone
            elif "query is too old" in err_str:
                 logging.warning(f"Query too old for reaction update on comment {comment_id}. Action: {action_taken}.")
                 # Cannot answer query if too old
            else:
                logging.error(f"Telegram error updating reaction markup for comment {comment_id}: {e}")
                await callback_query.answer("Error updating reaction display.", show_alert=True)
        except Exception as e:
             logging.error(f"Unexpected error updating reaction markup for comment {comment_id}: {e}", exc_info=True)
             await callback_query.answer("Error updating reaction display.", show_alert=True)
    elif action_taken != "none": # DB success but keyboard build failed (unlikely)
         logging.error(f"Action {action_taken} for comment {comment_id} done in DB, but failed to build new keyboard.")
         await callback_query.answer("Reaction processed (internal error).", show_alert=True)
    # else: No DB action was taken or comment not found


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply("Please use a command like /confess or /help, or interact using the buttons.")

# --- Main Execution ---
async def main():
    try:
        await setup()
        if db and bot_info:
            # Skip old updates to prevent processing stale commands/callbacks on restart
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        else:
            logging.critical("Database connection or bot info missing. Bot cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during bot startup or polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        session = bot.session
        if session and not session.closed:
             await session.close()
        if db:
            logging.info("Closing database pool...")
            await db.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user.")
    except Exception as global_error:
        # Catch errors during asyncio.run() itself, e.g., initial setup failures
        logging.critical(f"Unhandled exception during main execution: {global_error}", exc_info=True)


# --- END OF FILE main.py ---
