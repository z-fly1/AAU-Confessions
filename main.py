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
from aiogram.exceptions import TelegramBadRequest

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
        # Modify confessions table (ensure category exists)
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='confessions' AND column_name='category') THEN
                    ALTER TABLE confessions ADD COLUMN category VARCHAR(50);
                    COMMENT ON COLUMN confessions.category IS 'Category chosen by the user';
                END IF;
            END $$;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                text TEXT NOT NULL,
                user_id BIGINT NOT NULL,
                status VARCHAR(10) DEFAULT 'pending', -- pending, approved, rejected
                message_id BIGINT,
                category VARCHAR(50), -- Added category column
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP -- Use TIMESTAMPTZ
            );
        """)

        # Modify comments table (ensure parent_comment_id and TIMESTAMPTZ exist)
        await conn.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                               WHERE table_name='comments' AND column_name='parent_comment_id') THEN
                    ALTER TABLE comments ADD COLUMN parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL;
                    COMMENT ON COLUMN comments.parent_comment_id IS 'ID of the comment this is a reply to';
                END IF;
                 -- Ensure created_at uses TIMESTAMPTZ (might already exist, but good practice to check)
                 IF EXISTS (SELECT 1 FROM information_schema.columns
                            WHERE table_name='comments' AND column_name='created_at' AND data_type != 'timestamp with time zone') THEN
                     ALTER TABLE comments ALTER COLUMN created_at TYPE TIMESTAMP WITH TIME ZONE USING created_at AT TIME ZONE 'UTC';
                     ALTER TABLE comments ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;
                 END IF;
            END $$;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS comments (
                id SERIAL PRIMARY KEY,
                confession_id INTEGER REFERENCES confessions(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                parent_comment_id INTEGER REFERENCES comments(id) ON DELETE SET NULL, -- Added for replies
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP -- Use TIMESTAMPTZ
            );
        """)

        # Create reactions table (ensure UNIQUE constraint and TIMESTAMPTZ)
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
        logging.info("Database tables checked/created/updated.")


# --- Helper Functions ---

def create_category_keyboard():
    builder = InlineKeyboardBuilder()
    for category in CATEGORIES:
        builder.button(text=category, callback_data=f"category_{category}")
    builder.adjust(2) # Adjust layout, e.g., 2 buttons per row
    return builder.as_markup()

async def get_comment_reactions(comment_id: int):
    """Fetches like and dislike counts for a comment."""
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
    # Handle case where fetchrow returns None if no reactions exist
    return counts['likes'] if counts else 0, counts['dislikes'] if counts else 0


async def build_comment_keyboard(comment_id: int):
    """Builds the inline keyboard for a comment with like/dislike/reply."""
    likes, dislikes = await get_comment_reactions(comment_id)
    builder = InlineKeyboardBuilder()
    builder.button(text=f"👍 {likes}", callback_data=f"react_like_{comment_id}")
    builder.button(text=f"👎 {dislikes}", callback_data=f"react_dislike_{comment_id}")
    builder.button(text="↪️ Reply", callback_data=f"reply_{comment_id}")
    builder.adjust(3) # All buttons in one row
    return builder.as_markup()

# --- Helper Functions ---

# ... (create_category_keyboard, get_comment_reactions, build_comment_keyboard remain the same) ...

async def show_comments_for_confession(user_id: int, confession_id: int, message_to_edit: types.Message | None = None):
    """Fetches and sends comments (separately) for a specific confession.
       Displays '#anonymous_user' instead of comment ID.
       Can optionally edit a previous message instead of sending new ones."""
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
                    await bot.send_message(user_id, error_text)
            except Exception as e:
                 logging.warning(f"Could not send/edit 'confession not found' to user {user_id}: {e}")
            return

        # Fetch comments, including parent ID, ordered chronologically
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
    comments_html = ""
    if not comments:
        comments_html = "<i>No comments yet. Be the first!</i>\n"
         # Send the "Add Comment" button even if no comments exist
        add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
        ])
        try:
            await bot.send_message(
                user_id,
                f"--- Comments for Confession #{confession_id} ---\n{comments_html}\nYou can add your own comment below:",
                reply_markup=add_comment_button
            )
        except Exception as e:
            logging.warning(f"Could not send initial 'Add Comment' button to user {user_id}: {e}")
        return # Stop processing if no comments

    else:
        comment_map = {c['id']: c for c in comments} # For easy lookup when formatting replies
        # Simple hierarchical display (only one level deep for prefix)
        for c in comments:
            comment_id = c['id'] # Keep the real ID for keyboard data
            comment_text = html.quote(c['text']) # Escape HTML in user text
            timestamp = c['created_at'].strftime("%Y-%m-%d %H:%M") # Format timestamp

            reply_prefix = ""
            if c['parent_comment_id'] and c['parent_comment_id'] in comment_map:
                # Display '#anonymous_user' for the parent reference
                reply_prefix = f"↪️ <i>Replying to #anonymous_user</i>\n" # <--- CHANGED

            # Display '#anonymous_user' instead of Comment #ID
            comment_metadata = f"<i>#anonymous_user | {timestamp}</i>" # <--- CHANGED

            # Build keyboard for this comment (uses the REAL comment_id)
            keyboard = await build_comment_keyboard(comment_id)

            # Send each comment as a separate message
            full_comment_text = f"{reply_prefix}💬 {comment_text}\n\n{comment_metadata}"
            try:
                 await bot.send_message(
                     user_id,
                     full_comment_text,
                     reply_markup=keyboard,
                     parse_mode=ParseMode.HTML
                 )
            except Exception as e:
                logging.warning(f"Could not send comment {comment_id} to user {user_id}: {e}")
                try:
                     await bot.send_message(user_id, f"⚠️ Error displaying comment for #anonymous_user. Details: {e}") # Use #anonymous_user in error too
                except Exception: pass # Ignore if even error fails

    # --- Send the "Add Comment" button after all comments ---
    add_comment_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Comment", callback_data=f"add_{confession_id}")]
    ])
    try:
        # Send a final message with the add comment button
        await bot.send_message(
            user_id,
            f"--- End of comments for Confession #{confession_id} ---\nYou can add your own comment below:",
            reply_markup=add_comment_button
        )
    except Exception as e:
         logging.warning(f"Could not send final 'Add Comment' button to user {user_id}: {e}")


# --- Handlers ---

@dp.message(Command("start"))
async def start(message: types.Message, command: CommandObject | None = None):
    deep_link_args = command.args if command else None

    if deep_link_args and deep_link_args.startswith("view_"):
        try:
            confession_id_str = deep_link_args.split("_", 1)[1]
            confession_id = int(confession_id_str)
            logging.info(f"User {message.from_user.id} started bot via deep link for confession {confession_id}")

            # *** NEW: Fetch confession and comment count for deep link view ***
            async with db.acquire() as conn:
                confession_data = await conn.fetchrow(
                    "SELECT text, category, status FROM confessions WHERE id = $1",
                    confession_id
                )
                comment_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM comments WHERE confession_id = $1",
                    confession_id
                ) or 0 # Ensure count is 0 if query returns None

            if not confession_data or confession_data['status'] != 'approved':
                await message.answer(f"Confession #{confession_id} could not be found or hasn't been approved.")
                return

            confession_text = confession_data['text']
            category = confession_data['category']

            # Format text similar to channel post
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

@dp.message(Command("confess"), StateFilter(None)) # Trigger confession flow only with command and no active state
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

    # Edit the original message or send a new one
    try:
        await callback_query.message.edit_text(f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")
    except Exception as e:
        logging.warning(f"Could not edit category message: {e}")
        await callback_query.answer() # Ack button press
        await bot.send_message(callback_query.from_user.id, f"Category selected: <b>{category}</b>\n\nNow, please send me the text of your confession.")

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
        await message.answer("Your confession seems a bit short. Please provide more detail.")
        return # Keep state active for retry
    if len(confession_text) > 3900: # Leave space for category tag and formatting
        await message.answer("Your confession is too long (max ~3900 chars). Please shorten it.")
        return # Keep state active

    try:
        async with db.acquire() as conn:
            confession_id = await conn.fetchval(
                "INSERT INTO confessions (text, user_id, category, status) VALUES ($1, $2, $3, 'pending') RETURNING id",
                confession_text, user_id, category
            )

        if not confession_id:
            await message.answer("Sorry, there was an error submitting your confession. Please try again.")
            logging.error("Failed to get confession_id after insert.")
            await state.clear() # Clear state on failure
            return

        approve_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Approve", callback_data=f"approve_{confession_id}")],
            [InlineKeyboardButton(text="❌ Reject", callback_data=f"reject_{confession_id}")]
        ])

        # Show admin the text *without* category tag initially
        await bot.send_message(
            ADMIN_ID,
            f"New Confession (ID: {confession_id}, Category: {category}):\n\n{html.quote(confession_text)}", # Use html.quote for safety
            reply_markup=approve_keyboard
            )
        await message.answer("✅ Your confession has been submitted for review.")
        logging.info(f"Confession {confession_id} (Category: {category}) submitted by user {user_id}")

    except Exception as e:
        logging.error(f"Error in receive_confession_text from user {user_id}: {e}", exc_info=True)
        await message.answer("An internal error occurred while submitting your confession. Please try again later.")
    finally:
        await state.clear() # Clear state after processing


# --- Admin Actions ---

@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def admin_action(callback_query: types.CallbackQuery):
    try:
        action, confession_id_str = callback_query.data.split("_", 1)
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid admin callback data format: {callback_query.data}")
        await callback_query.answer("Invalid action data.", show_alert=True)
        return

    # Ensure bot_info is available before proceeding with approval
    if action == "approve" and not bot_info:
        logging.error("Bot info not available for deep link generation during approval.")
        await callback_query.answer("Internal error: Bot username missing.", show_alert=True)
        return

    async with db.acquire() as conn:
        async with conn.transaction():
            confession = await conn.fetchrow(
                # Fetch category as well
                "SELECT id, text, user_id, category, status FROM confessions WHERE id = $1 FOR UPDATE",
                confession_id
            )

            if not confession:
                await callback_query.answer("Confession not found.", show_alert=True)
                try: await callback_query.message.delete()
                except Exception: pass
                return
            
            if confession['status'] != 'pending':
                 await callback_query.answer("Confession already processed.", show_alert=True)
                 try: await callback_query.message.delete()
                 except Exception: pass
                 return

            user_id = confession["user_id"]
            confession_text = confession["text"]
            confession_db_id = confession["id"]
            category = confession["category"] # Get category

            try:
                if action == "approve":
                    deep_link_url = f"https://t.me/{bot_info.username}?start=view_{confession_db_id}"
                    logging.info(f"Generated deep link: {deep_link_url}")

                    # Add category hashtag to the text before posting
                    text_to_post = f"<b>Confession #{confession_db_id}</b>\n\n{html.quote(confession_text)}\n\n#{category}"

                    channel_message = await bot.send_message(
                        CHANNEL_ID,
                        text_to_post,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            # Link text changed slightly for clarity
                            [InlineKeyboardButton(text="💬 View / Add Comments", url=deep_link_url)]
                        ]),
                        parse_mode=ParseMode.HTML
                    )

                    await conn.execute(
                        "UPDATE confessions SET status = 'approved', message_id = $1 WHERE id = $2",
                        channel_message.message_id, confession_db_id
                    )
                    await bot.send_message(user_id, f"✅ Your confession (#{confession_db_id} - #{category}) has been approved and posted!")
                    await callback_query.answer(f"Confession {confession_db_id} approved.")
                    logging.info(f"Admin {callback_query.from_user.id} approved confession {confession_db_id}")

                else: # action == "reject"
                    await conn.execute(
                        "UPDATE confessions SET status = 'rejected' WHERE id = $1",
                        confession_db_id
                    )
                    await bot.send_message(user_id, f"❌ Your confession (#{confession_db_id} - #{category}) was rejected.")
                    await callback_query.answer(f"Confession {confession_db_id} rejected.")
                    logging.info(f"Admin {callback_query.from_user.id} rejected confession {confession_db_id}")

                await callback_query.message.delete()

            except Exception as e:
                logging.error(f"Error processing admin action ({action}) for confession {confession_id}: {e}", exc_info=True)
                # Try to inform admin, rollback happens automatically due to context manager
                await callback_query.answer(f"An error occurred: {e}. Action may not have completed.", show_alert=True)
                # Raise the exception to ensure transaction rollback if not already handled by context manager exit
                raise


# --- Commenting Flow ---

# *** NEW: Handler for the "Browse Comments" button ***
@dp.callback_query(F.data.startswith("browse_"))
async def browse_comments_action(callback_query: types.CallbackQuery):
    try:
        confession_id_str = callback_query.data.split("_", 1)[1]
        confession_id = int(confession_id_str)
    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for browse: {callback_query.data}")
        await callback_query.answer("Invalid data for browsing comments.", show_alert=True)
        return

    await callback_query.answer("Loading comments...") # Acknowledge button press
    # Call the existing function to show comments
    # Pass the original message so it *could* be edited if needed (though show_comments sends new msgs now)
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

    await state.update_data(confession_id=confession_id, parent_comment_id=None) # Explicitly set parent to None
    await state.set_state(CommentForm.waiting_for_comment)
    try:
        # Edit the previous message (e.g., the confession view or the comment list)
        # await callback_query.message.edit_text(f"📝 Please send your comment for Confession #{confession_id}:", reply_markup=None)
        # OR send a new message (often simpler)
        await bot.send_message(callback_query.from_user.id, f"📝 Please send your comment for Confession #{confession_id}:")
        await callback_query.answer() # Acknowledge button press silently
    except Exception as e:
        logging.warning(f"Could not send/edit comment prompt to user {callback_query.from_user.id}: {e}")
        # Attempt to answer callback even if message fails
        try: await callback_query.answer("Could not ask for comment. Please send your comment directly.", show_alert=True)
        except Exception: pass
        # Don't clear state here, user might still send the comment

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
        await message.answer("Your comment is too short.")
        return # Keep state active
    if len(comment_text) > 1000:
        await message.answer("Your comment is too long (max 1000 chars). Please shorten it.")
        return # Keep state active

    try:
        async with db.acquire() as conn:
            await conn.execute(
                # Insert NULL for parent_comment_id for direct comments
                "INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, NULL)",
                confession_id, user_id, comment_text
            )
        await message.answer("💬 Your comment has been added!")
        logging.info(f"User {user_id} added direct comment to confession {confession_id}")

        # Show comments again after adding
        await show_comments_for_confession(user_id, confession_id)

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"Attempt to add comment to non-existent/deleted confession {confession_id} by user {user_id}")
         await message.answer("⚠️ Sorry, could not add comment. The original confession might have been removed.")
    except Exception as e:
        logging.error(f"Error saving direct comment for confession {confession_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an internal error saving your comment.")
    finally:
        await state.clear()

# --- Reply Flow ---

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

    # Need to find the confession_id associated with this comment
    async with db.acquire() as conn:
        comment_data = await conn.fetchrow(
            "SELECT confession_id, text FROM comments WHERE id = $1", # Fetch text for context if needed
            parent_comment_id
        )

    if not comment_data:
         await callback_query.answer("Cannot reply: Original comment not found.", show_alert=True)
         return

    confession_id = comment_data['confession_id']
    # parent_comment_text = comment_data['text'] # Optional: use for prompt context

    # Store confession_id, parent_comment_id, AND the message_id to reply to
    await state.update_data(
        confession_id=confession_id,
        parent_comment_id=parent_comment_id,
        message_id_to_reply_to=message_id_to_reply_to # Store the target message ID
    )
    await state.set_state(CommentForm.waiting_for_reply) # Use the new state
    try:
        # Send a new message prompting for the reply
        await bot.send_message(
            callback_query.from_user.id,
            f"📝 Replying to comment #{parent_comment_id}. Please send your reply:"
            # Optional: Quote the original comment text briefly here if desired
            # f"📝 Replying to comment #{parent_comment_id}:\n"
            # f"<i>{html.quote(parent_comment_text[:100])}...</i>\n\n" # Example snippet
            # f"Please send your reply:"
        )
        await callback_query.answer() # Acknowledge the button press
    except Exception as e:
        logging.warning(f"Could not send reply prompt to user {callback_query.from_user.id}: {e}")
        try: await callback_query.answer("Could not ask for reply. Please send your reply directly.", show_alert=True)
        except Exception: pass
        # Don't clear state here

@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    reply_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")
    parent_comment_id = data.get("parent_comment_id")
    # Retrieve the message ID we need to reply to
    message_id_to_reply_to = data.get("message_id_to_reply_to")

    if not confession_id or not parent_comment_id or not message_id_to_reply_to: # Check all needed data
        await message.answer("⚠️ Error: Could not determine which confession, comment, or message to reply to. Please try starting the reply process again.")
        await state.clear()
        logging.error(f"State data missing required fields for user {user_id} in receive_reply: {data}")
        return

    # Basic validation
    if len(reply_text) < 1: # Replies can be short
        await message.answer("Your reply is too short.")
        return # Keep state active
    if len(reply_text) > 1000:
        await message.answer("Your reply is too long (max 1000 chars). Please shorten it.")
        return # Keep state active

    new_comment_id = None
    try:
        async with db.acquire() as conn:
            async with conn.transaction(): # Use transaction
                # Check if parent comment still exists before inserting reply
                parent_exists = await conn.fetchval("SELECT 1 FROM comments WHERE id = $1 FOR UPDATE", parent_comment_id) # Lock parent
                if not parent_exists:
                    await message.answer("⚠️ Sorry, the comment you were replying to seems to have been deleted.")
                    await state.clear()
                    return

                new_comment_id = await conn.fetchval( # Get the new comment ID
                    """
                    INSERT INTO comments (confession_id, user_id, text, parent_comment_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    confession_id, user_id, reply_text, parent_comment_id
                )
        # DB Insert successful

        logging.info(f"User {user_id} added reply (ID: {new_comment_id}) to comment {parent_comment_id} on confession {confession_id}")

        # Now send the reply as a native Telegram reply
        try:
            # Build keyboard for the NEW reply message itself
            reply_keyboard = await build_comment_keyboard(new_comment_id)

            await bot.send_message(
                chat_id=user_id,
                text=f"💬 {html.quote(reply_text)}", # Just send the reply text, maybe prefix with icon
                reply_to_message_id=message_id_to_reply_to, # THIS MAKES IT A NATIVE REPLY
                reply_markup=reply_keyboard, # Add like/dislike/reply to the reply itself
                parse_mode=ParseMode.HTML # Ensure HTML parsing
            )
            # Optional: Send a silent confirmation *after* the reply if you want
            # await message.answer("Reply sent.", disable_notification=True)

        except TelegramBadRequest as e:
             # Handle cases where the message being replied to might be deleted between prompt and send
             if "reply message not found" in str(e).lower():
                 logging.warning(f"Could not send native reply for comment {parent_comment_id} - original message deleted? Sending as normal message.")
                 await message.answer(f"⚠️ Reply saved (ID: {new_comment_id}), but couldn't link it visually to the original comment (it might have been deleted).\n\n💬 {html.quote(reply_text)}") # Send without reply_to
             else:
                 logging.error(f"Telegram error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
                 await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an error displaying it as a direct reply.\n\n💬 {html.quote(reply_text)}") # Send without reply_to
        except Exception as e:
             logging.error(f"Unexpected error sending native reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
             await message.answer(f"❌ Reply saved (ID: {new_comment_id}), but there was an internal error displaying it.\n\n💬 {html.quote(reply_text)}") # Send without reply_to

        # --- IMPORTANT: Remove the automatic refresh ---
        # await show_comments_for_confession(user_id, confession_id)

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"FK violation adding reply to comment {parent_comment_id} by user {user_id}")
         await message.answer("⚠️ Sorry, could not add reply. The original confession or parent comment might have been removed.")
    except Exception as e:
        logging.error(f"Error saving reply DB transaction for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an internal error saving your reply.")
    finally:
        await state.clear() # Clear state after processing

@dp.message(CommentForm.waiting_for_reply, F.text)
async def receive_reply(message: types.Message, state: FSMContext):
    reply_text = message.text
    user_id = message.from_user.id
    data = await state.get_data()
    confession_id = data.get("confession_id")
    parent_comment_id = data.get("parent_comment_id")

    if not confession_id or not parent_comment_id:
        await message.answer("⚠️ Error: Could not determine which confession or comment to reply to. Please try starting the reply process again.")
        await state.clear()
        logging.error(f"State data missing confession_id or parent_comment_id for user {user_id} in receive_reply")
        return

    # Basic validation
    if len(reply_text) < 1: # Replies can be short
        await message.answer("Your reply is too short.")
        return # Keep state active
    if len(reply_text) > 1000:
        await message.answer("Your reply is too long (max 1000 chars). Please shorten it.")
        return # Keep state active

    try:
        async with db.acquire() as conn:
            # Check if parent comment still exists before inserting reply
            parent_exists = await conn.fetchval("SELECT 1 FROM comments WHERE id = $1", parent_comment_id)
            if not parent_exists:
                await message.answer("⚠️ Sorry, the comment you were replying to seems to have been deleted.")
                await state.clear()
                return

            await conn.execute(
                "INSERT INTO comments (confession_id, user_id, text, parent_comment_id) VALUES ($1, $2, $3, $4)",
                confession_id, user_id, reply_text, parent_comment_id
            )
        await message.answer("💬 Your reply has been added!")
        logging.info(f"User {user_id} added reply to comment {parent_comment_id} on confession {confession_id}")

        # Show comments again
        await show_comments_for_confession(user_id, confession_id)

    except asyncpg.exceptions.ForeignKeyViolationError:
         logging.warning(f"FK violation adding reply to comment {parent_comment_id} by user {user_id}")
         await message.answer("⚠️ Sorry, could not add reply. The original confession or parent comment might have been removed.")
    except Exception as e:
        logging.error(f"Error saving reply for comment {parent_comment_id} by user {user_id}: {e}", exc_info=True)
        await message.answer("❌ Sorry, there was an internal error saving your reply.")
    finally:
        await state.clear()


# --- Reaction Handling ---

# --- Reaction Handling ---

@dp.callback_query(F.data.startswith("react_"))
async def handle_reaction(callback_query: types.CallbackQuery):
    try:
        _, reaction_type, comment_id_str = callback_query.data.split("_", 2)
        comment_id = int(comment_id_str)
        user_id = callback_query.from_user.id

        if reaction_type not in ['like', 'dislike']:
            raise ValueError("Invalid reaction type")

    except (ValueError, IndexError):
        logging.error(f"Invalid callback data format for reaction: {callback_query.data}")
        await callback_query.answer("Invalid reaction data.", show_alert=True)
        return

    action_taken = "none" # Keep track of the logical action
    new_keyboard = None    # Initialize new_keyboard

    async with db.acquire() as conn:
        async with conn.transaction(): # Use transaction for atomicity
            try:
                # Check existing reaction
                existing_reaction = await conn.fetchval(
                    "SELECT reaction_type FROM reactions WHERE comment_id = $1 AND user_id = $2 FOR UPDATE", # Add FOR UPDATE for locking
                    comment_id, user_id
                )

                if existing_reaction:
                    if existing_reaction == reaction_type:
                        # User clicked the same reaction again - remove it
                        await conn.execute(
                            "DELETE FROM reactions WHERE comment_id = $1 AND user_id = $2",
                            comment_id, user_id
                        )
                        action_taken = f"Removed {reaction_type}"
                    else:
                        # User changed reaction - update it
                        await conn.execute(
                            "UPDATE reactions SET reaction_type = $1, created_at = CURRENT_TIMESTAMP WHERE comment_id = $2 AND user_id = $3", # Update timestamp too
                            reaction_type, comment_id, user_id
                        )
                        action_taken = f"Changed to {reaction_type}"
                else:
                    # No existing reaction - insert new one
                    await conn.execute(
                        "INSERT INTO reactions (comment_id, user_id, reaction_type) VALUES ($1, $2, $3)",
                        comment_id, user_id, reaction_type
                    )
                    action_taken = f"Added {reaction_type}"

                # Get updated counts and build the new keyboard AFTER DB change
                new_keyboard = await build_comment_keyboard(comment_id)
                logging.info(f"User {user_id} attempted {action_taken} on comment {comment_id}. New keyboard generated.")

            except asyncpg.exceptions.ForeignKeyViolationError:
                # This might happen if the comment gets deleted between reading and reacting
                logging.warning(f"FK violation during reaction DB update for comment {comment_id} user {user_id} (comment likely deleted)")
                await callback_query.answer("Comment not found.", show_alert=True)
                return # Exit early, transaction will rollback
            except asyncpg.exceptions.UniqueViolationError:
                 # Should ideally not happen due to the logic above, but handle just in case
                 logging.warning(f"Unique violation error on reaction DB update for comment {comment_id} user {user_id}")
                 await callback_query.answer("Reaction already exists (concurrent issue?).", show_alert=True)
                 return # Exit early, transaction will rollback
            except Exception as db_err:
                logging.error(f"Database error during reaction processing for comment {comment_id} by user {user_id}: {db_err}", exc_info=True)
                await callback_query.answer("Error processing reaction (database).", show_alert=True)
                return # Exit early, transaction will rollback

        # --- DB Transaction successful, now try to update Telegram message ---
        if new_keyboard and action_taken != "none":
            try:
                current_markup = callback_query.message.reply_markup
                if new_keyboard == current_markup:
                    # Keyboard is identical, likely means no change or a race condition resolved identically
                    logging.warning(f"Skipping message edit for comment {comment_id}: keyboard unchanged.")
                    await callback_query.answer(action_taken + " (No visual change)") # Still answer
                else:
                    await callback_query.message.edit_reply_markup(reply_markup=new_keyboard)
                    await callback_query.answer(action_taken) # Give feedback on success
                    logging.info(f"Successfully updated markup for comment {comment_id} after action: {action_taken}")

            except TelegramBadRequest as e:
                err_str = str(e).lower()
                # Handle specific Telegram errors after successful DB commit
                if "message is not modified" in err_str:
                    # This case is less likely now with the explicit check, but handles race conditions
                    logging.warning(f"Got 'message is not modified' error for comment {comment_id} even after check.")
                    await callback_query.answer(action_taken + " (Counts updated)")
                elif "message to edit not found" in err_str:
                    logging.warning(f"Message to edit not found for reaction update on comment {comment_id}")
                    await callback_query.answer(action_taken + " (Counts updated, view not)")
                elif "message can't be edited" in err_str:
                    logging.warning(f"Message can't be edited for reaction update on comment {comment_id}")
                    await callback_query.answer(action_taken + " (Counts updated, view not)")
                elif "query is too old" in err_str:
                    logging.warning(f"Query too old for reaction update on comment {comment_id}")
                    # DB change is already committed, inform user counts are updated
                    await callback_query.answer(action_taken + " (Counts updated, view might be stale)", show_alert=False) # Non-alert feedback
                else:
                    # Other Telegram errors
                    logging.error(f"Telegram error updating reaction markup for comment {comment_id}: {e}", exc_info=False) # Don't need full trace for common errors
                    await callback_query.answer("Error updating reaction display.", show_alert=True)
            except Exception as e:
                 # Catch other unexpected errors during message edit
                 logging.error(f"Unexpected error updating reaction markup for comment {comment_id}: {e}", exc_info=True)
                 await callback_query.answer("Error updating reaction display.", show_alert=True)
        elif action_taken != "none": # DB succeeded, but keyboard build failed? Should not happen.
             logging.error(f"Action {action_taken} for comment {comment_id} done in DB, but failed to build new keyboard.")
             await callback_query.answer("Reaction processed (internal error).", show_alert=True)
        else: # No action was taken in DB (e.g., error occurred)
             logging.debug(f"No DB action taken for comment {comment_id}, callback not answered yet.")
             # The specific error handlers above should have called .answer()

# --- Fallback Handler for Text (When no state is active) ---
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def handle_text_without_state(message: types.Message):
    await message.reply("Please use the /confess command to submit a confession or interact using the buttons.")

# --- Main Execution ---
async def main():
    try:
        await setup()
        if db and bot_info:
            await dp.start_polling(bot)
        else:
            logging.critical("Database connection or bot info missing. Bot cannot start.")
    except Exception as e:
        logging.critical(f"Fatal error during bot startup or polling: {e}", exc_info=True)
    finally:
        logging.info("Closing bot session...")
        if bot and bot.session:
             await bot.session.close()
        if db:
            logging.info("Closing database pool...")
            await db.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    if not DATABASE_URL:
        print("FATAL: DATABASE_URL environment variable not set!")
    elif not BOT_TOKEN:
        print("FATAL: BOT_TOKEN environment variable not set!")
    elif not ADMIN_ID:
        print("FATAL: ADMIN_ID environment variable not set!")
    elif not CHANNEL_ID:
        print("FATAL: CHANNEL_ID environment variable not set!")
    else:
        asyncio.run(main())

# Placeholder for graceful shutdown logic if needed
# async def shutdown(signal, loop): ...