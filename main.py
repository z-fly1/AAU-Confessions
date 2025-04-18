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

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) # Ensure ADMIN_ID is integer
CHANNEL_ID = os.getenv("CHANNEL_ID")
DATABASE_URL = os.getenv("DATABASE_URL")

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

        # --- *** NEW: Create Contact Requests Table *** ---
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
        # --- *** END NEW SECTION *** ---

        # Consider adding a trigger for updated_at on contact_requests table if needed
        # (See previous explanation for trigger code - run manually or add here if permissions allow)

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

# --- *** MODIFIED: build_comment_keyboard *** ---
async def build_comment_keyboard(
    comment_id: int,
    commenter_user_id: int,          # ID of the person who wrote *this* comment
    viewer_user_id: int,           # ID of the person *viewing* the comments
    confession_owner_id: int       # ID of the person who wrote the original confession
):
    """Builds the keyboard for a comment, conditionally adding 'Request Contact'."""
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")

    # --- Add Request Contact Button Conditionally ---
    if viewer_user_id == confession_owner_id and viewer_user_id != commenter_user_id:
        # Only show if the viewer is the author AND it's not their own comment
        # Check if a request is already pending/approved to disable button? (Optional enhancement)
        # For simplicity now, always show if author is viewing someone else's comment.
        # The handler `handle_request_contact` will perform the actual check.
        builder.button(text="🤝 Request Contact", callback_data=f"req_contact_{comment_id}")
        builder.adjust(3, 1) # Reactions/Reply row, Request Contact row below
    else:
        builder.adjust(3) # All buttons in one row

    return builder.as_markup()
# --- *** END MODIFICATION *** ---


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
             # Consider marking message_id as NULL in DB?
             # async with db.acquire() as conn_inner:
             #     await conn_inner.execute("UPDATE confessions SET message_id = NULL WHERE id = $1", confession_id)
        else:
            logging.error(f"Failed to edit channel post {channel_message_id} for confession {confession_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating channel post button for confession {confession_id}: {e}", exc_info=True)


# --- *** MODIFIED: show_comments_for_confession *** ---
async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: Optional[types.Message] = None):
    """Fetches and sends comments, assigning sequence numbers.
       Adds "(Author)" tag for comments by the original confessor.
       Adds "(You)" tag for comments by the browsing user (if not the author).
       Reply prefix refers to the parent's sequential number.
       Allows Author to request contact info.
    """
    confession_owner_id: Optional[int] = None

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

        confession_owner_id = confession_data['user_id'] # Store confession owner ID

        comments = await conn.fetch(
            """
            SELECT id, user_id, text, parent_comment_id, created_at
            FROM comments
            WHERE confession_id = $1
            ORDER BY created_at ASC
            """,
            confession_id
        )

    # --- Prepare comment display ---
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
            commenter_user_id = c['user_id'] # Get commenter's ID
            comment_text = html.quote(c['text'])
            timestamp = c['created_at'].strftime("%Y-%m-%d %H:%M")

            reply_prefix = ""
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_id_to_sequence:
                parent_sequence_num = comment_id_to_sequence[c['parent_comment_id']]
                reply_prefix = f"↪️ <i>Replying to #{parent_sequence_num}</i>\n"
            elif c['parent_comment_id']:
                reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"

            # Author/You Tag Logic
            author_tag = ""
            if commenter_user_id == confession_owner_id:
                author_tag = "(Author)"
            elif commenter_user_id == user_id: # user_id is the viewer's ID
                author_tag = "(You)"
            else:
                author_tag = "Anonymous" # Default/Fallback
            display_tag = f" {author_tag}" if author_tag else " Anonymous" # Ensure 'Anonymous' if no other tag

            comment_metadata = f"<i>#{current_sequence_num}{display_tag} | {timestamp}</i>"

            # <<< CHANGE: Pass IDs to build_comment_keyboard >>>
            keyboard = await build_comment_keyboard(
                comment_id=comment_id,
                commenter_user_id=commenter_user_id,
                viewer_user_id=user_id, # Pass the ID of the person viewing
                confession_owner_id=confession_owner_id # Pass the confession owner ID
            )

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
                    # Use safe_send_message here too
                    await safe_send_message(user_id, f"⚠️ Error displaying comment #{current_sequence_num}.")
                except Exception:
                    pass # Avoid further errors if user blocked etc.

    # --- Send the "Add Comment" button ---
    add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])
    end_text = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_text += "You can add your own comment below:"

    try:
        if message_to_edit and not comments:
            # Edit the initial message if it was passed and no comments were found
            await message_to_edit.edit_text(end_text, reply_markup=add_comment_button)
        elif message_to_edit and comments:
             # If comments were found, we sent them individually. Send the final prompt as a new message.
             await safe_send_message(user_id, end_text, reply_markup=add_comment_button)
             # Optionally delete the original "Loading..." message if message_to_edit was provided
             try:
                 await message_to_edit.delete()
             except Exception:
                 pass # Ignore if deletion fails
        else:
            # No message_to_edit provided, send the final prompt as a new message
            await safe_send_message(user_id, end_text, reply_markup=add_comment_button)
    except Exception as e:
        logging.warning(f"Could not send/edit final 'Add Comment' prompt to user {user_id}: {e}")
# --- *** END MODIFICATION *** ---

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
                    "SELECT text, category, status, user_id FROM confessions WHERE id = $1", # Fetch user_id too
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
            confession_owner_id = confession_data['user_id'] # Get owner ID

            text_to_show = f"<b>Confession #{confession_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}\n---"

            builder = InlineKeyboardBuilder()
            builder.button(text="➕ Add Comment", callback_data=f"add_{confession_id}")
            builder.button(text=f"💬 Browse Comments ({comment_count})", callback_data=f"browse_{confession_id}")

            # Conditionally add "View My Requests" button if the viewer is the author
            if message.from_user.id == confession_owner_id:
                 builder.button(text="✉️ View Contact Requests", callback_data=f"view_reqs_{confession_id}")
                 builder.adjust(1, 1, 1) # Stack all buttons
            else:
                 builder.adjust(1, 1) # Stack Add/Browse

            await message.answer(text_to_show, reply_markup=builder.as_markup())

            # Optionally, directly show comments if requested via deep link?
            # await show_comments_for_confession(message.from_user.id, confession_id)

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
        # Edit the message to confirm category and ask for text
        await callback_query.message.edit_text(f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")
    except Exception as e:
        logging.warning(f"Could not edit category message: {e}")
        # Fallback: Send a new message if editing fails
        await callback_query.answer() # Ack button press
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

    # Basic validation
    if len(confession_text) < 10:
        await message.answer("Your confession seems a bit short. Please provide more detail (at least 10 characters).")
        return
    if len(confession_text) > 3900: # Leave ~196 chars for formatting/header/footer
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
            f"Category: {category}\n"
            f"User ID: {user_id}\n\n" # Keep user ID for admin reference
            f"{html.quote(confession_text)}"
        )
        # Ensure admin message isn't too long either
        if len(admin_msg) > 4090:
            admin_msg = admin_msg[:4087] + "..."

        await bot.send_message(
            ADMIN_ID,
            admin_msg,
            reply_markup=approve_keyboard
        )
        await message.answer("✅ Your confession has been submitted for review by the admin.")
        logging.info(f"Confession {confession_id} (Category: {category}) submitted by user {user_id}")

    except Exception as e:
        logging.error(f"Error in receive_confession_text from user {user_id}: {e}", exc_info=True)
        await message.answer("An internal error occurred while submitting your confession. Please try again later.")
    finally:
        await state.clear()


# --- Admin Actions ---

@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def admin_action(callback_query: types.CallbackQuery):
    global bot_info
    if not bot_info:
        logging.error("Bot info not available for admin action.")
        await callback_query.answer("Internal error: Bot username missing.", show_alert=True)
        return

    if callback_query.from_user.id != ADMIN_ID:
        await callback_query.answer("You are not authorized for this action.", show_alert=True)
        return

    try:
        action_data = callback_query.data.split("_", 1)
        action = action_data[0]
        confession_id = int(action_data[1])
    except (ValueError, IndexError):
        logging.error(f"Invalid admin callback data format: {callback_query.data}")
        await callback_query.answer("Invalid action data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction(): # Use transaction
            # Fetch confession details FOR UPDATE to lock the row
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
                try: await callback_query.message.edit_reply_markup(reply_markup=None) # Remove buttons
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

                    # Check length before posting
                    if len(text_to_post) > 4096:
                         logging.error(f"Confession {confession_db_id} text too long ({len(text_to_post)}) even after formatting. Cannot post.")
                         await callback_query.answer("Error: Confession text too long to post.", show_alert=True)
                         # Optionally reject it automatically?
                         # await conn.execute("UPDATE confessions SET status = 'rejected' WHERE id = $1", confession_db_id)
                         # await safe_send_message(user_id, f"❌ Your confession (#{confession_db_id} - #{category}) could not be posted because it was too long.")
                         return # Stop processing

                    channel_message = await bot.send_message(
                        CHANNEL_ID,
                        text_to_post,
                        reply_markup=initial_markup,
                        parse_mode=ParseMode.HTML
                    )

                    await conn.execute(
                        "UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2",
                        channel_message.message_id, confession_db_id
                    )
                    await safe_send_message(user_id, f"✅ Your confession (#{confession_db_id} - #{category}) has been approved and posted!")
                    await callback_query.answer(f"Confession {confession_db_id} approved & posted.")
                    logging.info(f"Admin {callback_query.from_user.id} approved confession {confession_db_id}")

                else: # action == "reject"
                    await conn.execute(
                        "UPDATE confessions SET status = 'rejected' WHERE id = $1",
                        confession_db_id
                    )
                    await safe_send_message(user_id, f"❌ Your confession (#{confession_db_id} - #{category}) was rejected by the admin.")
                    await callback_query.answer(f"Confession {confession_db_id} rejected.")
                    logging.info(f"Admin {callback_query.from_user.id} rejected confession {confession_db_id}")

                # Edit the admin message to show status and remove buttons
                try:
                    final_admin_text = callback_query.message.html_text + f"\n\n-- Status: {action.capitalize()}ed --"
                    await callback_query.message.edit_text(final_admin_text, reply_markup=None, parse_mode=ParseMode.HTML)
                except Exception as e:
                     logging.warning(f"Could not edit admin action message for confession {confession_id}: {e}")

            except TelegramForbiddenError:
                 logging.error(f"Bot is likely blocked or lacks permissions in channel {CHANNEL_ID} or with user {user_id}.")
                 await callback_query.answer("Error: Check bot permissions or if user blocked.", show_alert=True)
                 raise # Reraise to ensure transaction rollback
            except TelegramBadRequest as e:
                 logging.error(f"Telegram API error processing admin action ({action}) for confession {confession_id}: {e}", exc_info=True)
                 await callback_query.answer(f"Telegram Error: {e}. Action might fail.", show_alert=True)
                 raise # Reraise to ensure transaction rollback
            except Exception as e:
                logging.error(f"Error processing admin action ({action}) for confession {confession_id}: {e}", exc_info=True)
                await callback_query.answer(f"An error occurred: {e}. Action might fail.", show_alert=True)
                raise # Reraise to ensure transaction rollback


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

    # Answer immediately to acknowledge, then load comments
    await callback_query.answer("Loading comments...")
    # Pass the original message to edit *if* no comments are found
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

    # Check if confession exists and is approved before prompting
    async with db.acquire() as conn:
        confession_exists = await conn.fetchval("SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'", confession_id)

    if not confession_exists:
         await callback_query.answer("Cannot add comment: Confession not found or not approved.", show_alert=True)
         # Optionally edit the message that contained the button
         try: await callback_query.message.delete()
         except Exception: pass
         return

    await state.update_data(confession_id=confession_id, parent_comment_id=None) # Ensure parent_comment_id is None
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        await safe_send_message(callback_query.from_user.id, f"📝 Please send your comment for Confession #{confession_id}:")
        await callback_query.answer() # Acknowledge button press silently
    except Exception as e:
        logging.warning(f"Could not send comment prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for comment.", show_alert=True)
        except Exception: pass # Avoid error loop

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
                 confession_owner_id = await conn.fetchval(
                     "SELECT user_id FROM confessions WHERE id = $1 AND status = 'approved'",
                     confession_id
                 )
                 if not confession_owner_id:
                     raise asyncpg.exceptions.ForeignKeyViolationError("Confession not found or not approved.")

                 new_comment_id = await conn.fetchval( # Get the new comment ID
                     """
                     INSERT INTO comments (confession_id, user_id, text, parent_comment_id)
                     VALUES ($1, $2, $3, NULL) RETURNING id
                     """,
                     confession_id, user_id, comment_text
                 )
            # Transaction successful

            await message.answer("💬 Your comment has been added!")
            logging.info(f"User {user_id} added direct comment {new_comment_id} to confession {confession_id}")

            await update_channel_post_button(confession_id) # Update count

            # Notify original confessor (if not the commenter)
            if confession_owner_id and confession_owner_id != user_id:
                 logging.info(f"Notifying user {confession_owner_id} about new comment {new_comment_id} on confession {confession_id}")
                 deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                 notification_text = (
                     f"💬 Someone commented on your confession #{confession_id}.\n\n"
                     f"Comment: {html.quote(comment_text[:150])}{'...' if len(comment_text) > 150 else ''}\n\n"
                     f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                 )
                 await safe_send_message(confession_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

            # Show comments again AFTER adding the comment
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

    message_id_to_reply_to = callback_query.message.message_id # Get the ID of the message containing the button

    async with db.acquire() as conn:
        # Check if parent comment exists and get its confession ID
        comment_data = await conn.fetchrow(
            "SELECT confession_id, text FROM comments WHERE id = $1",
            parent_comment_id
        )

    if not comment_data:
         await callback_query.answer("Cannot reply: Original comment not found (maybe deleted?).", show_alert=True)
         # Optionally edit the message that contained the button
         try: await callback_query.message.edit_reply_markup(reply_markup=None)
         except Exception: pass
         return

    confession_id = comment_data['confession_id']
    parent_comment_text_preview = html.quote(comment_data['text'][:80]) + ('...' if len(comment_data['text']) > 80 else '')

    # Store necessary data in state
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
        # Send prompt as a new message
        await safe_send_message(
            callback_query.from_user.id,
            prompt_text,
            parse_mode=ParseMode.HTML
        )
        await callback_query.answer() # Acknowledge the button press silently
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
    message_id_to_reply_to = data.get("message_id_to_reply_to") # Retrieve the target message ID

    if not confession_id or not parent_comment_id or not message_id_to_reply_to:
        await message.answer("⚠️ Error: Could not determine what to reply to. Please try starting the reply process again.")
        await state.clear()
        logging.error(f"State data missing required fields for user {user_id} in receive_reply: {data}")
        return

    # Validation
    if len(reply_text) < 1:
        await message.answer("Your reply cannot be empty.")
        return
    if len(reply_text) > 1000:
        await message.answer(f"Your reply is too long (max 1000 chars). It has {len(reply_text)} chars.")
        return

    new_comment_id = None
    parent_comment_owner_id = None
    confession_owner_id = None # Need this too for tagging

    try:
        async with db.acquire() as conn:
            async with conn.transaction():
                # Check parent comment existence and get its owner ID FOR UPDATE
                parent_data = await conn.fetchrow(
                    "SELECT user_id FROM comments WHERE id = $1 FOR UPDATE",
                    parent_comment_id
                )
                if not parent_data:
                    # This case should be rare if checked before prompt, but handle defensively
                    await message.answer("⚠️ Sorry, the comment you were replying to seems to have been deleted just now.")
                    await state.clear()
                    return
                parent_comment_owner_id = parent_data['user_id']

                # Also get confession owner ID
                confession_owner_id = await conn.fetchval(
                    "SELECT user_id FROM confessions WHERE id = $1", confession_id
                )
                if not confession_owner_id:
                     # Should not happen if checks are done earlier, but for safety
                     raise Exception("Confession not found during reply save")


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

        await update_channel_post_button(confession_id) # Update count

        # Send the reply using native Telegram reply mechanism
        try:
            # Determine tag for the new reply message
            reply_author_tag = ""
            if user_id == confession_owner_id:
                reply_author_tag = "(Author)"
            elif user_id == parent_comment_owner_id: # check if replying to own comment's thread
                 reply_author_tag = "(Commenter)" # Or just use (You)? Let's use (You) for consistency
                 reply_author_tag = "(You)"
            else:
                 reply_author_tag = "(You)" # The user sending the reply sees it as (You)

            display_tag = f" {reply_author_tag}" if reply_author_tag else " Anonymous"

            # Format the text shown in the reply message itself
            reply_message_text = (
                f"💬 {html.quote(reply_text)}\n\n"
                f"<i>Reply #{new_comment_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>" # Use new comment ID? Or sequence? Let's use DB ID for now.
            )
            # Build keyboard for the new reply message
            # Need to pass correct IDs for the new reply's keyboard context
            reply_keyboard = await build_comment_keyboard(
                comment_id=new_comment_id,
                commenter_user_id=user_id, # The replier is the commenter for this new message
                viewer_user_id=user_id,    # The replier is also viewing it immediately
                confession_owner_id=confession_owner_id
            )

            sent_reply_message = await bot.send_message(
                chat_id=user_id,
                text=reply_message_text,
                reply_to_message_id=message_id_to_reply_to, # Use the stored message ID for native reply
                reply_markup=reply_keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            # Optionally confirm non-natively (usually not needed)
            # await message.answer("Reply sent!", disable_notification=True)

            # Notify parent comment owner (if not the replier)
            if parent_comment_owner_id and parent_comment_owner_id != user_id:
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
                 logging.warning(f"Could not send native reply for comment {new_comment_id} (replying to {parent_comment_id}) - original message {message_id_to_reply_to} deleted? Sending as normal message.")
                 # Fallback: Send the reply without the reply_to parameter
                 # Rebuild keyboard just in case context changes slightly
                 reply_keyboard = await build_comment_keyboard(
                     comment_id=new_comment_id, commenter_user_id=user_id,
                     viewer_user_id=user_id, confession_owner_id=confession_owner_id
                 )
                 reply_message_text = ( # Reformat slightly for non-native reply
                     f"↪️ Replying to comment #{parent_comment_id}\n" # Indicate parent comment ID
                     f"💬 {html.quote(reply_text)}\n\n"
                     f"<i>Reply #{new_comment_id}{display_tag} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>"
                 )
                 await safe_send_message(
                     user_id,
                     reply_message_text,
                     reply_markup=reply_keyboard,
                     parse_mode=ParseMode.HTML
                 )
                 # Still try to notify parent owner
                 if parent_comment_owner_id and parent_comment_owner_id != user_id:
                      # (Same notification logic as above)
                      logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} (original msg deleted) to their comment {parent_comment_id}")
                      deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                      notification_text = (
                          f"↪️ Someone replied to your comment on confession #{confession_id} (original message might be deleted).\n\n"
                          f"Reply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n"
                          f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                      )
                      await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

             else:
                 logging.error(f"Telegram error sending native reply for comment {new_comment_id} by user {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an error displaying it as a direct reply.")
        except Exception as e:
             logging.error(f"Unexpected error sending native reply for comment {new_comment_id} by user {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an internal error displaying it.")

        # --- Remove automatic refresh ---
        # We don't call show_comments_for_confession here anymore.
        # The native reply appears instantly. Refreshing would lose the native look and context.
        # The user can manually browse again if needed.

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

    action_taken = "none"
    new_keyboard = None
    alert_message = None # Specific message for callback answer

    async with db.acquire() as conn:
        async with conn.transaction():
            try:
                # Fetch comment owner, viewer, confession owner for keyboard rebuild
                comment_info = await conn.fetchrow(
                    """
                    SELECT c.user_id as commenter_id, co.user_id as confession_owner_id
                    FROM comments c
                    JOIN confessions co ON c.confession_id = co.id
                    WHERE c.id = $1
                    """, comment_id
                )
                if not comment_info:
                    raise asyncpg.exceptions.ForeignKeyViolationError("Comment not found")

                commenter_id = comment_info['commenter_id']
                confession_owner_id = comment_info['confession_owner_id']
                viewer_id = user_id # The user reacting is the viewer

                # Lock the specific reaction row if it exists
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
                # Pass all necessary IDs for potential 'Request Contact' button
                new_keyboard = await build_comment_keyboard(
                    comment_id=comment_id,
                    commenter_user_id=commenter_id,
                    viewer_user_id=viewer_id,
                    confession_owner_id=confession_owner_id
                )
                logging.info(f"User {user_id} action '{action_taken}' on comment {comment_id}. Keyboard rebuilt.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                logging.warning(f"FK violation during reaction update for comment {comment_id} user {user_id} (comment likely deleted)")
                await callback_query.answer("Comment not found.", show_alert=True)
                # Try to remove the keyboard from the message if possible
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
            await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
            await callback_query.answer(alert_message)
            logging.info(f"Successfully updated markup for comment {comment_id} after action: {action_taken}")

        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.info(f"Markup for comment {comment_id} not modified (likely counts unchanged). Action: {action_taken}.")
                await callback_query.answer(alert_message + " (No visual change)") # Give feedback
            elif "message to edit not found" in err_str:
                logging.warning(f"Message to edit not found for reaction update on comment {comment_id}. Action: {action_taken}.")
                await callback_query.answer(alert_message + " (Counts updated, view not)", show_alert=False)
            elif "query is too old" in err_str:
                 logging.warning(f"Query too old for reaction update on comment {comment_id}. Action: {action_taken}.")
                 await callback_query.answer(alert_message + " (Counts updated, view might be stale)", show_alert=False)
            else:
                logging.error(f"Telegram error updating reaction markup for comment {comment_id}: {e}")
                await callback_query.answer("Error updating reaction display.", show_alert=True)
        except Exception as e:
             logging.error(f"Unexpected error updating reaction markup for comment {comment_id}: {e}", exc_info=True)
             await callback_query.answer("Error updating reaction display.", show_alert=True)
    elif action_taken != "none": # DB success but keyboard build failed (unlikely)
         logging.error(f"Action {action_taken} for comment {comment_id} done in DB, but failed to build new keyboard.")
         await callback_query.answer("Reaction processed (internal error).", show_alert=True)
    # else: No DB action was taken, .answer() was called in the exception handlers


# --- *** NEW: Contact Request Flow Handlers *** ---

@dp.callback_query(F.data.startswith("req_contact_"))
async def handle_request_contact(callback_query: types.CallbackQuery):
    """Handles the author clicking 'Request Contact' on a comment."""
    try:
        comment_id_str = callback_query.data.split("_", 2)[2] # req_contact_{comment_id}
        comment_id = int(comment_id_str)
        requester_user_id = callback_query.from_user.id # This is the author initiating
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for request contact: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction(): # Use transaction for checks and insert
            # Fetch comment details: commenter ID and confession ID/author ID
            comment_data = await conn.fetchrow(
                """
                SELECT c.user_id AS commenter_id, c.text AS comment_text,
                       co.id AS confession_id, co.user_id AS confession_owner_id
                FROM comments c
                JOIN confessions co ON c.confession_id = co.id
                WHERE c.id = $1 AND co.status = 'approved'
                """,
                comment_id
            )

            if not comment_data:
                await callback_query.answer("Comment or confession not found/accessible.", show_alert=True)
                return

            commenter_id = comment_data['commenter_id']
            confession_id = comment_data['confession_id']
            confession_owner_id = comment_data['confession_owner_id']
            comment_text_preview = html.quote(comment_data['comment_text'][:100]) + ('...' if len(comment_data['comment_text']) > 100 else '')

            # --- Security Checks ---
            if requester_user_id != confession_owner_id:
                logging.warning(f"User {requester_user_id} tried to request contact for comment {comment_id} but is not the confession owner ({confession_owner_id}).")
                await callback_query.answer("You can only request contact for comments on your own confessions.", show_alert=True)
                return
            if requester_user_id == commenter_id:
                 await callback_query.answer("You cannot request contact with yourself.", show_alert=True)
                 return

            # --- Check if commenter has a public username SET IN TELEGRAM PROFILE ---
            try:
                # Fetch chat info to check username existence
                commenter_chat = await bot.get_chat(commenter_id)
                if not commenter_chat or not commenter_chat.username:
                     await callback_query.answer("This user does not have a public Telegram username set in their profile, so contact cannot be requested.", show_alert=True)
                     logging.info(f"Author {requester_user_id} request for comment {comment_id} aborted: commenter {commenter_id} has no username.")
                     return
            except Exception as e:
                 # Handle cases like user deactivated, bot blocked by commenter, etc.
                 logging.warning(f"Could not fetch commenter {commenter_id} chat info: {e}. Assuming no contact possible.")
                 await callback_query.answer("Could not retrieve commenter info. They may have deactivated their account or blocked the bot.", show_alert=True)
                 return

            # --- Check for existing PENDING or APPROVED request for this specific comment ---
            # Avoid spamming requests if one is already active or completed successfully.
            existing_request = await conn.fetchval(
                """SELECT status FROM contact_requests
                   WHERE comment_id = $1 AND requester_user_id = $2 AND status IN ('pending', 'approved')
                """, comment_id, requester_user_id)

            if existing_request:
                if existing_request == 'pending':
                    await callback_query.answer("A contact request for this comment is already pending.", show_alert=True)
                elif existing_request == 'approved':
                    await callback_query.answer("Contact request for this comment was already approved.", show_alert=True)
                return

            # --- Create the request record in DB ---
            try:
                request_id = await conn.fetchval(
                    """
                    INSERT INTO contact_requests (confession_id, comment_id, requester_user_id, requested_user_id, status)
                    VALUES ($1, $2, $3, $4, 'pending')
                    ON CONFLICT (comment_id, requester_user_id) DO NOTHING -- Safely handle race conditions
                    RETURNING id
                    """,
                    confession_id, comment_id, requester_user_id, commenter_id
                )
                if not request_id: # Could happen if ON CONFLICT triggers
                     existing_status = await conn.fetchval(
                         "SELECT status FROM contact_requests WHERE comment_id = $1 AND requester_user_id = $2",
                         comment_id, requester_user_id
                     )
                     await callback_query.answer(f"A request already exists (Status: {existing_status}).", show_alert=True)
                     return

            except Exception as insert_err:
                logging.error(f"Failed to insert contact request from {requester_user_id} to {commenter_id} for comment {comment_id}: {insert_err}", exc_info=True)
                await callback_query.answer("Failed to save contact request. Please try again.", show_alert=True)
                return # Rollback transaction

            # --- Notify Commenter ---
            notification_text = (
                f"🤝 The author of Confession #{confession_id} would like to contact you regarding your comment:\n\n"
                f"<i>{comment_text_preview}</i>\n\n"
                f"Do you approve sharing your Telegram username (@{html.quote(commenter_chat.username)}) with them?"
            )
            approval_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_contact_{request_id}"),
                    InlineKeyboardButton(text="❌ Deny", callback_data=f"deny_contact_{request_id}")
                ]
            ])

            # Send the notification to the COMMENTER
            sent_to_commenter = await safe_send_message(
                commenter_id,
                notification_text,
                reply_markup=approval_keyboard,
                parse_mode=ParseMode.HTML
            )

            if sent_to_commenter:
                await callback_query.answer("✅ Contact request sent to the commenter.", show_alert=False)
                logging.info(f"Contact request {request_id} (comment {comment_id}) sent from author {requester_user_id} to commenter {commenter_id}.")
            else:
                # If sending failed (e.g., commenter blocked bot), we need to rollback the request
                logging.warning(f"Failed to send contact request {request_id} notification to commenter {commenter_id} (likely blocked bot). Rolling back DB entry.")
                await callback_query.answer("⚠️ Could not send request. The commenter might have blocked the bot.", show_alert=True)
                # Raising an exception forces the transaction to rollback
                raise Exception(f"Failed to notify commenter {commenter_id}, rolling back request {request_id}.")


@dp.callback_query(F.data.startswith("approve_contact_") | F.data.startswith("deny_contact_"))
async def handle_contact_response(callback_query: types.CallbackQuery):
    """Handles the commenter approving or denying a contact request."""
    try:
        # Correctly parse: action is the first part, request_id is the last
        parts = callback_query.data.split("_") # approve_contact_{id} or deny_contact_{id}
        action = parts[0] # 'approve' or 'deny'
        request_id = int(parts[-1])
        responder_user_id = callback_query.from_user.id # This is the commenter responding
    except (ValueError, IndexError, TypeError):
        logging.error(f"Invalid callback data format for contact response: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True)
        return

    new_status = 'approved' if action == 'approve' else 'denied'

    async with db.acquire() as conn:
        async with conn.transaction():
            # Fetch request details and lock the row
            request_data = await conn.fetchrow(
                """SELECT id, requester_user_id, requested_user_id, status, confession_id, comment_id
                   FROM contact_requests
                   WHERE id = $1 FOR UPDATE""",
                request_id
            )

            if not request_data:
                await callback_query.answer("This contact request was not found (maybe deleted or expired?).", show_alert=True)
                try: await callback_query.message.delete() # Clean up button message
                except Exception: pass
                return

            # --- Security Checks ---
            if responder_user_id != request_data['requested_user_id']:
                logging.warning(f"User {responder_user_id} tried to respond to contact request {request_id} intended for {request_data['requested_user_id']}.")
                # Don't reveal it wasn't for them, just say not found or invalid.
                await callback_query.answer("Invalid request.", show_alert=True)
                return

            if request_data['status'] != 'pending':
                await callback_query.answer(f"This request was already {request_data['status']}.", show_alert=True)
                try:
                    # Try removing buttons from the original message
                    await callback_query.message.edit_reply_markup(reply_markup=None)
                except Exception: pass
                return

            # --- Process Response ---
            author_notification_text = ""
            requester_user_id = request_data['requester_user_id'] # The author who made the request
            confession_id = request_data['confession_id']
            comment_id = request_data['comment_id'] # Get comment ID for context

            if new_status == 'approved':
                # Double-check username existence AT THE TIME OF APPROVAL
                # Use callback_query.from_user which represents the commenter clicking the button
                commenter_username = callback_query.from_user.username
                if commenter_username:
                    await conn.execute("UPDATE contact_requests SET status = 'approved', updated_at = CURRENT_TIMESTAMP WHERE id = $1", request_id)
                    author_notification_text = (
                        f"✅ Contact Approved!\n\n"
                        f"Your request to contact the user who commented on Confession #{confession_id} (Comment ID approx: {comment_id}) was APPROVED.\n\n" # Give context
                        f"You can contact them at: @{html.quote(commenter_username)}"
                    )
                    await callback_query.answer("Request approved. Your username has been shared with the author.")
                    logging.info(f"Contact request {request_id} approved by {responder_user_id}. Username @{commenter_username} sent to author {requester_user_id}.")
                    new_status_for_edit = 'Approved (Username Shared)' # For editing the commenter's message
                else:
                    # Approved, but commenter removed username between request and approval
                    new_status = 'approved_no_username' # Use the specific status in DB
                    await conn.execute("UPDATE contact_requests SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2", new_status, request_id)
                    author_notification_text = (
                         f"⚠️ Contact Approved (No Username)\n\n"
                         f"Your request to contact the user who commented on Confession #{confession_id} (Comment ID approx: {comment_id}) was APPROVED, but they currently do not have a public Telegram username set in their profile."
                    )
                    # Notify the commenter clearly
                    await callback_query.answer("Request approved, but you have no public username set. The author cannot contact you via username.", show_alert=True)
                    logging.info(f"Contact request {request_id} approved by {responder_user_id}, but they have no username. Notified author {requester_user_id}.")
                    new_status_for_edit = 'Approved (No Username)'

            else: # new_status == 'denied'
                 await conn.execute("UPDATE contact_requests SET status = 'denied', updated_at = CURRENT_TIMESTAMP WHERE id = $1", request_id)
                 author_notification_text = (
                     f"❌ Contact Denied\n\n"
                     f"Your request to contact the user who commented on Confession #{confession_id} (Comment ID approx: {comment_id}) was DENIED."
                 )
                 await callback_query.answer("Request denied. Your username was not shared.")
                 logging.info(f"Contact request {request_id} denied by {responder_user_id}. Notified author {requester_user_id}.")
                 new_status_for_edit = 'Denied'


            # --- Notify Author ---
            await safe_send_message(requester_user_id, author_notification_text, parse_mode=ParseMode.HTML)

            # --- Edit Commenter's original notification message ---
            # (The message with the Approve/Deny buttons)
            try:
                # Get the original text and append the final status
                original_text = callback_query.message.html_text
                final_text = f"{original_text}\n\n<b>Status: {new_status_for_edit}</b>"
                await callback_query.message.edit_text(final_text, reply_markup=None, parse_mode=ParseMode.HTML)
            except TelegramBadRequest as e:
                 if "message is not modified" in str(e).lower(): pass # Ignore if already edited somehow
                 else: raise # Reraise other Telegram errors
            except Exception as e:
                # Log error but don't fail the whole process if editing fails
                logging.warning(f"Could not edit commenter's ({responder_user_id}) notification message {callback_query.message.message_id} for request {request_id}: {e}")


@dp.callback_query(F.data.startswith("view_reqs_"))
async def view_contact_requests(callback_query: types.CallbackQuery):
    """Allows the confession author to view the status of their requests for a specific confession."""
    try:
        confession_id_str = callback_query.data.split("_", 2)[2] # view_reqs_{confession_id}
        confession_id = int(confession_id_str)
        viewer_user_id = callback_query.from_user.id
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for view requests: {callback_query.data}")
        await callback_query.answer("Invalid request data.", show_alert=True)
        return

    async with db.acquire() as conn:
        # Verify viewer is the author
        confession_owner_id = await conn.fetchval(
            "SELECT user_id FROM confessions WHERE id = $1", confession_id
        )
        if not confession_owner_id:
            await callback_query.answer("Confession not found.", show_alert=True)
            return
        if viewer_user_id != confession_owner_id:
             await callback_query.answer("You can only view requests for your own confessions.", show_alert=True)
             return

        # Fetch all requests for this confession made by the author
        requests = await conn.fetch(
            """
            SELECT cr.comment_id, cr.requested_user_id, cr.status, cr.updated_at, c.text as comment_text
            FROM contact_requests cr
            JOIN comments c ON cr.comment_id = c.id -- Join to get comment text for context
            WHERE cr.confession_id = $1 AND cr.requester_user_id = $2
            ORDER BY cr.updated_at DESC
            """,
            confession_id, viewer_user_id
        )

    if not requests:
        await callback_query.answer("You haven't made any contact requests for this confession.", show_alert=False)
        return

    response_text = f"<b>Contact Requests Status for Confession #{confession_id}</b>\n\n"
    for req in requests:
        comment_preview = html.quote(req['comment_text'][:50]) + ('...' if len(req['comment_text']) > 50 else '')
        status_text = req['status'].replace('_', ' ').capitalize()
        updated_time = req['updated_at'].strftime("%Y-%m-%d %H:%M")
        response_text += (
            f"• Comment starting: \"<i>{comment_preview}</i>\"\n"
            # f"  Requested User ID: {req['requested_user_id']}\n" # Maybe hide commenter ID?
            f"  Status: <b>{status_text}</b>\n"
            f"  Last Update: {updated_time}\n\n"
        )

    # Check if the message is too long
    if len(response_text) > 4096:
        response_text = response_text[:4090] + "\n\n...(list truncated)"

    # Send as a new message, or edit the previous one? Let's send a new one.
    await safe_send_message(viewer_user_id, response_text, parse_mode=ParseMode.HTML)
    await callback_query.answer() # Acknowledge the button press

# --- *** END NEW HANDLERS *** ---


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    # Politely guide the user
    await message.reply(
        "Hi there! 👋\n"
        "To share something anonymously, please use the /confess command.\n"
        "If you clicked a link to view a confession, please use the buttons provided under that confession's message."
    )

# --- Main Execution ---
async def main():
    # Perform environment variable check before setup
    missing_vars = []
    if not DATABASE_URL: missing_vars.append("DATABASE_URL")
    if not BOT_TOKEN: missing_vars.append("BOT_TOKEN")
    if not ADMIN_ID: missing_vars.append("ADMIN_ID")
    if not CHANNEL_ID: missing_vars.append("CHANNEL_ID")

    if missing_vars:
        logging.critical(f"FATAL: Missing environment variables: {', '.join(missing_vars)}")
        print(f"FATAL: Missing environment variables: {', '.join(missing_vars)}")
        return # Stop execution

    try:
        # Validate ADMIN_ID format early
        global ADMIN_ID
        ADMIN_ID = int(ADMIN_ID)
    except ValueError:
        logging.critical("FATAL: ADMIN_ID environment variable must be an integer.")
        print("FATAL: ADMIN_ID environment variable must be an integer.")
        return


    try:
        await setup() # Setup database and get bot info
        if db and bot_info:
            logging.info("Starting bot polling...")
            # Register handlers implicitly if defined before this point
            await dp.start_polling(bot, skip_updates=True) # Skip old updates on restart
        else:
            logging.critical("Database connection or bot info missing after setup. Bot cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during bot setup or polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        # Ensure bot session is closed properly
        if bot and bot.session:
            session = bot.session
            if session and not session.closed:
                 await session.close()
                 logging.info("Bot session closed.")
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
        # Catch any unexpected error during asyncio.run itself
        logging.critical(f"Critical error in main execution loop: {main_err}", exc_info=True)
        print(f"Critical error: {main_err}")


# --- END OF FILE main.py ---