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
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton # Added Reply Keyboards
from aiogram.utils.keyboard import InlineKeyboardBuilder # Use builder for dynamic keyboards
from datetime import datetime # For formatting timestamps
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

# --- Constants ---
CATEGORIES = [
    "Relationship", "Education", "Family", "School", "Friendship",
    "Religion", "Entertainment", "Information", "Sexual Assault", "Other" # Added Other
]

# Load environment variables
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
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
    waiting_for_reply = State() # New state for replies

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
        # (Keep the existing ALTER/COMMENT blocks as they are safe)
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
        # (Keep the existing ALTER/COMMENT blocks)
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
    # ... (remains the same) ...
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2) # Adjust layout, e.g., 2 buttons per row
    return builder.as_markup()

async def get_comment_reactions(comment_id: int):
    # ... (remains the same) ...
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
    # ... (remains the same) ...
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
        # Optional: Mark user as inactive in DB here if needed
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
             # Consider removing message_id from DB?
             # async with db.acquire() as conn_inner:
             #     await conn_inner.execute("UPDATE confessions SET message_id = NULL WHERE id = $1", confession_id)
        else:
            logging.error(f"Failed to edit channel post {channel_message_id} for confession {confession_id}: {e}")
    except Exception as e:
        logging.error(f"Unexpected error updating channel post button for confession {confession_id}: {e}", exc_info=True)

async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: types.Message | None = None):
    """Fetches and sends comments (separately) for a specific confession.
       Assigns a sequential number (#1, #2, ...) to each comment in the current view.
       Reply prefix refers to the parent's sequential number (e.g., 'Replying to #3').
       Indicates user's own comments with '(You)'.
    """
    async with db.acquire() as conn:
        confession_exists = await conn.fetchval(
            "SELECT 1 FROM confessions WHERE id = $1 AND status = 'approved'",
            confession_id
        )
        if not confession_exists:
            error_text = f"Confession #{confession_id} could not be found or hasn't been approved."
            try:
                if message_to_edit:
                     await message_to_edit.edit_text(error_text, reply_markup=None)
                else:
                    await safe_send_message(user_id, error_text)
            except Exception as e:
                 logging.warning(f"Could not send/edit 'confession not found' to user {user_id}: {e}")
            return

        # Fetch all comments for the confession, ordered as they should appear
        comments = await conn.fetch(
            """
            SELECT id, user_id, text, parent_comment_id, created_at
            FROM comments
            WHERE confession_id = $1
            ORDER BY created_at ASC
            """,
            confession_id
        )

    # --- Prepare comment display with sequential numbering ---
    sent_message_ids = {} # Store sent message IDs (optional)
    comment_id_to_sequence = {} # Map DB comment ID to its temporary sequence number in this view
    comment_counter = 0 # Initialize the sequence counter

    if not comments:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
    else:
        comments_html = f"--- Comments for Confession #{confession_id} ---\n\n"

        # First pass: Assign sequence numbers to all comments in this view
        temp_comment_map = {} # Temporary map to hold comment data for prefix generation
        for c_data in comments:
            comment_counter += 1
            db_comment_id = c_data['id']
            comment_id_to_sequence[db_comment_id] = comment_counter
            temp_comment_map[db_comment_id] = c_data # Store data for later lookup

        # Second pass: Generate and send messages using the sequence numbers
        for c in comments:
            comment_id = c['id']
            current_sequence_num = comment_id_to_sequence[comment_id] # Get assigned number
            commenter_user_id = c['user_id']
            comment_text = html.quote(c['text'])
            timestamp = c['created_at'].strftime("%Y-%m-%d %H:%M") # Keep timestamp for info

            reply_prefix = ""
            # <<< --- START REPLY PREFIX CHANGE (using sequence number) --- >>>
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_id_to_sequence:
                # Find the sequence number of the parent comment
                parent_sequence_num = comment_id_to_sequence[c['parent_comment_id']]
                reply_prefix = f"↪️ <i>Replying to #{parent_sequence_num}</i>\n"
            elif c['parent_comment_id']:
                # Fallback if parent isn't in the current view (e.g., deleted)
                 reply_prefix = f"↪️ <i>Replying to deleted comment</i>\n"
            # <<< --- END REPLY PREFIX CHANGE --- >>>

            # Determine display name for the *current* comment's author
            author_display = "(You)" if commenter_user_id == user_id else "" # Simpler "(You)" indicator
            # Format metadata including the sequence number
            comment_metadata = f"<i>#{current_sequence_num} {author_display} | {timestamp}</i>"

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
                except Exception: pass

    # --- Send the "Add Comment" button ---
    add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])
    end_text = f"--- End of comments for Confession #{confession_id} ---\n" if comments else comments_html
    end_text += "You can add your own comment below:"

    try:
        if message_to_edit and not comments:
            await message_to_edit.edit_text(end_text, reply_markup=add_comment_button)
        else:
             await safe_send_message(user_id, end_text, reply_markup=add_comment_button)
    except Exception as e:
         logging.warning(f"Could not send/edit final 'Add Comment' prompt to user {user_id}: {e}")

# --- Rest of the file remains the same ---
# (Replace the existing `show_comments_for_confession` function with this one)
# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, command: CommandObject | None = None):
    # ... (deep link logic remains the same) ...
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
            "It will be reviewed by an admin before posting."
        )

# --- Confession Submission Flow ---
# ... (start_confession, category_chosen remain the same) ...
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
    # ... (validation remains the same) ...
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

        await bot.send_message(
            ADMIN_ID,
            f"New Confession (ID: {confession_id}, Category: {category}, User: {user_id}):\n\n{html.quote(confession_text)}", # Added user ID for admin context
            reply_markup=approve_keyboard
            )
        await message.answer("✅ Your confession has been submitted for review.")
        logging.info(f"Confession {confession_id} (Category: {category}) submitted by user {user_id}")

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

                    # <<< CHANGE: Post with initial comment count (0) >>>
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
                 # Transaction will rollback automatically
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
         return

    await state.update_data(confession_id=confession_id, parent_comment_id=None)
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        # Send a new message for the prompt
        await safe_send_message(callback_query.from_user.id, f"📝 Please send your comment for Confession #{confession_id}:")
        await callback_query.answer() # Acknowledge button press silently
    except Exception as e:
        # This catch might be redundant if safe_send_message handles it, but keep for safety
        logging.warning(f"Could not send comment prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for comment.", show_alert=True)
        except Exception: pass


@dp.message(CommentForm.waiting_for_comment, F.text)
async def receive_comment(message: types.Message, state: FSMContext):
    comment_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")
    commenter_username = message.from_user.username # Get username for notification

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

            # <<< CHANGE: Update channel post button count >>>
            await update_channel_post_button(confession_id)

            # <<< CHANGE: Notify original confessor >>>
            if confession_owner_id and confession_owner_id != user_id: # Don't notify if commenting on own confession
                 logging.info(f"Notifying user {confession_owner_id} about new comment on confession {confession_id}")
                 deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}"
                 notification_text = (
                     f"💬 Someone commented on your confession #{confession_id}.\n\n"
                     f"{html.quote(comment_text[:150])}{'...' if len(comment_text) > 150 else ''}\n\n" # Preview comment
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
    # This is crucial for sending the native reply *initially*
    message_id_to_reply_to = callback_query.message.message_id

    async with db.acquire() as conn:
        comment_data = await conn.fetchrow(
            "SELECT confession_id, text FROM comments WHERE id = $1",
            parent_comment_id
        )

    if not comment_data:
         await callback_query.answer("Cannot reply: Original comment not found.", show_alert=True)
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

        # <<< CHANGE: Update channel post button count >>>
        await update_channel_post_button(confession_id)

        # Send the reply using native Telegram reply mechanism
        try:
            reply_keyboard = await build_comment_keyboard(new_comment_id)
            sent_reply_message = await bot.send_message(
                chat_id=user_id,
                # Format nicely, maybe indicating it's a reply, though native UI does this
                text=f"💬 {html.quote(reply_text)}\n\n<i>#anonymous_user | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>",
                reply_to_message_id=message_id_to_reply_to, # Native reply
                reply_markup=reply_keyboard,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            # Optionally confirm to the user non-natively
            # await message.answer("Reply sent!", disable_notification=True)

            # <<< CHANGE: Notify parent comment owner >>>
            if parent_comment_owner_id and parent_comment_owner_id != user_id: # Don't notify if replying to own comment
                logging.info(f"Notifying user {parent_comment_owner_id} about reply {new_comment_id} to their comment {parent_comment_id}")
                deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_id}" # Link to whole confession
                # Note: Linking directly to the *reply* is harder as we don't have a stable message_id for it across views.
                notification_text = (
                    f"↪️ Someone replied to your comment on confession #{confession_id}.\n\n"
                    f"Reply: {html.quote(reply_text[:150])}{'...' if len(reply_text) > 150 else ''}\n\n"
                    f"<a href='{deep_link_url}'>Click here to view the comments.</a>"
                )
                await safe_send_message(parent_comment_owner_id, notification_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

        except TelegramBadRequest as e:
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Could not send native reply for comment {parent_comment_id} - original message deleted? Sending reply as normal message.")
                 # Send the reply without the reply_to parameter
                 reply_keyboard = await build_comment_keyboard(new_comment_id) # Rebuild just in case
                 await safe_send_message(
                     user_id,
                     f"⚠️ Reply saved (ID: {new_comment_id}), but couldn't link it visually (original message might be deleted).\n\n💬 {html.quote(reply_text)}\n\n<i>#anonymous_user | {datetime.now().strftime('%Y-%m-%d %H:%M')}</i>",
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
                 logging.error(f"Telegram error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an error displaying it as a direct reply.")
        except Exception as e:
             logging.error(f"Unexpected error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an internal error displaying it.")

        # --- Remove automatic refresh ---
        # We don't call show_comments_for_confession here anymore.
        # The native reply appears instantly. Refreshing would lose the native look.

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
            if new_keyboard.inline_keyboard == current_markup.inline_keyboard: # Compare keyboard content
                logging.warning(f"Skipping message edit for comment {comment_id}: keyboard content unchanged. Action: {action_taken}.")
                # Answer with the specific action message
                await callback_query.answer(alert_message + " (No visual change)")
            else:
                await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
                # Answer with the specific action message on success
                await callback_query.answer(alert_message)
                logging.info(f"Successfully updated markup for comment {comment_id} after action: {action_taken}")

        except TelegramBadRequest as e:
            err_str = str(e).lower()
            if "message is not modified" in err_str:
                logging.warning(f"Got 'message is not modified' for comment {comment_id} despite content check (race condition?). Action: {action_taken}.")
                await callback_query.answer(alert_message + " (Counts updated)") # Feedback that DB is fine
            elif "message to edit not found" in err_str:
                logging.warning(f"Message to edit not found for reaction update on comment {comment_id}. Action: {action_taken}.")
                await callback_query.answer(alert_message + " (Counts updated, view not)")
            # Add other specific error checks if needed (e.g., "message can't be edited")
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


# --- Fallback Handler ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply("Please use the /confess command to submit a confession or interact using the buttons.")

# --- Main Execution ---
async def main():
    try:
        await setup()
        if db and bot_info:
            await dp.start_polling(bot, skip_updates=True) # Consider skipping old updates on restart
        else:
            logging.critical("Database connection or bot info missing. Bot cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during bot startup or polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        # Ensure bot session is closed properly
        # Check if bot.session exists and is not already closed
        session = bot.session
        if session and not session.closed:
             await session.close()
        if db:
            logging.info("Closing database pool...")
            await db.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    # (Environment variable checks remain the same)
    if not DATABASE_URL: print("FATAL: DATABASE_URL environment variable not set!")
    elif not BOT_TOKEN: print("FATAL: BOT_TOKEN environment variable not set!")
    elif not ADMIN_ID: print("FATAL: ADMIN_ID environment variable not set!")
    elif not CHANNEL_ID: print("FATAL: CHANNEL_ID environment variable not set!")
    else:
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")

# --- END OF FILE main.py ---