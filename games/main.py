import os
import logging
import asyncio
import random
from typing import Optional
from dotenv import load_dotenv

from telegram import Update, ChatMember, ChatMemberUpdated
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatType, ChatMemberStatus
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType, ChatMemberStatus

from datetime import datetime
from datetime import timedelta

from game_manager import GameManager, GameState, GameSession
from story_builder import StoryBuilderGame
from guess_the_imposter import GuessTheImposterGame
from guess_the_logo import GuessTheLogoGame
from guess_the_movie import GuessTheMovieGame
from guess_the_flag import GuessTheFlagGame
from guessmoji import GuessMojiGame

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Priority User ID constant
PRIORITY_USER_ID = 8103840368

# Initialize game manager
game_manager = GameManager()


# Allowed Group IDs
ALLOWED_CHAT_IDS = [
    -1003170577690,  # @aau_confessions
    -1003696845309,  # Testing Group
]


# Quirky response messages
QUIRKY_RESPONSES = [
    "Error: I don’t feel like it.",
    "I’ll pass.",
    "Cool. Command acknowledged. Ignored.",
    "This action has been declined",
    "Denied. But nicely",
    "Request rejected successfully.",
    
]


async def check_bot_is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Check if the bot has admin privileges in the chat.
    
    Args:
        update: Telegram update object
        context: Callback context
        
    Returns:
        True if bot is admin, False otherwise
    """
    chat = update.effective_chat
    if chat.type == ChatType.PRIVATE:
        return True
    
    try:
        bot_member = await chat.get_member(context.bot.id)
        return bot_member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False


async def my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot being added to or removed from a chat."""
    result = extract_status_change(update.my_chat_member)
    if result is None:
        return
    
    was_member, is_member = result
    chat = update.effective_chat
    
    # Bot was just added to a group
    if not was_member and is_member and chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        logger.info(f"Bot added to chat {chat.id}: {chat.title}")
        
        # Check Allowed Group
        if chat.id not in ALLOWED_CHAT_IDS:
            await context.bot.send_message(
                chat_id=chat.id,
                text=" <b>Womp Womp</b>\n\n"
                     "I am exclusive to the @aau_confessions group!",
                parse_mode="HTML"
            )
            return

        # Check if bot is admin
        is_admin = await check_bot_is_admin(update, context)
        
        if not is_admin:
            await context.bot.send_message(
                chat_id=chat.id,
                text="⚠️ <b>Admin Privileges Required</b>\n\n"
                     "I need admin privileges to manage games properly. "
                     "Please make me an admin and then use /start to begin!",
                parse_mode="HTML"
            )
        else:
            await context.bot.send_message(
                chat_id=chat.id,
                text="✅ <b>Bot Ready!</b>\n\n"
                     "I'm ready to host games! Use /start to begin.",
                parse_mode="HTML"
            )


def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[tuple[bool, bool]]:
    """Extract status change from ChatMemberUpdated."""
    status_change = chat_member_update.difference().get("status")
    if status_change is None:
        return None
    
    old_is_member = chat_member_update.old_chat_member.status in [
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    ]
    new_is_member = chat_member_update.new_chat_member.status in [
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.ADMINISTRATOR,
    ]
    
    return old_is_member, new_is_member


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command to initiate a game."""
    chat = update.effective_chat
    
    # Only work in groups
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "👋 Hi! I'm a game bot for @aau_confessions Group.\n\n"
            "I also work in other groups.Add me to a group and make me an admin to start playing games!"
        )
        return
    
    # Check Allowed Group
    if chat.id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            "⚠️ <b>Womp Womp</b>\n\n"
            "I only work in the @aau_confessions group!",
            parse_mode="HTML"
        )
        return

    # Check if bot is admin
    is_admin = await check_bot_is_admin(update, context)
    if not is_admin:
        await update.message.reply_text(
            "⚠️ I need admin privileges to host games. "
            "Please make me an admin first!"
        )
        return
    
    # Check if there's already an active game
    if game_manager.has_active_game(chat.id):
        await update.message.reply_text(random.choice(QUIRKY_RESPONSES))
        return
    
    # Create new game session
    session = game_manager.create_game(chat.id)
    
    await update.message.reply_text(
        "🎮 <b>Welcome to Game Bot!</b>\n\n"
        "Please select a game by sending its code:\n\n"
        "<b>1</b> - Word Unscramble Game\n"
        "<b>2</b> - Story Builder Game\n"
        "<b>3</b> - Guess the Imposter\n"
        "<b>4</b> - Guess the Logo\n"
        "<b>5</b> - GuessMoji Game\n"
        "<b>6</b> - Guess the Movie\n"
        "<b>7</b> - Guess the Flag\n\n"
        "Send the game code to continue...",
        parse_mode="HTML"
    )


async def start_game_after_delay(chat_id: int, context: ContextTypes.DEFAULT_TYPE, delay: int) -> None:
    """Wait for the specified delay, then start the game if enough players joined.
    
    Args:
        chat_id: Telegram chat ID
        context: Callback context
        delay: Initial delay in seconds before starting the game
    """
    session = game_manager.get_game(chat_id)
    if not session:
        return

    # Set initial deadline
    session.joining_deadline = datetime.now() + timedelta(seconds=delay)
    
    # Loop until deadline is reached
    while datetime.now() < session.joining_deadline:
        # Check if game was cancelled or state changed
        if session.state != GameState.JOINING:
            return
        
        # Wait a bit before checking again
        await asyncio.sleep(1)
    
    # Double check state after loop
    if session.state != GameState.JOINING:
        return
    
    # Check if enough players joined
    if session.get_player_count() < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="❌ Not enough players joined. Game cancelled.\n"
                 "Use /start to try again.",
            parse_mode="HTML"
        )
        game_manager.remove_game(chat_id)
        return
    
    # Start the game
    if session.start_game():
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎮 <b>Game Starting!</b>\n\n"
                 f"👥 Players: {session.get_player_count()}\n"
                 f"Get ready!",
            parse_mode="HTML"
        )
        
        # Handle different game types
        if session.game_code == "1":
            # Word Unscramble
            await start_round(chat_id, context)
        elif session.game_code == "2":
            # Story Builder
            start_story_game(chat_id, context, session)
        elif session.game_code == "3":
            # Guess the Imposter
            await start_imposter_game(chat_id, context, session)
        elif session.game_code == "4":
            # Guess the Logo
            await start_logo_game(chat_id, context, session)
        elif session.game_code == "5":
            # GuessMoji
            await start_guessmoji_round(chat_id, context)
        elif session.game_code == "6":
            # Guess the Movie
            await start_movie_game(chat_id, context, session)
        elif session.game_code == "7":
            # Guess the Flag
            await start_flag_game(chat_id, context, session)


def start_story_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the story builder game."""
    start_text = session.game.start_game()
    current_player_id = session.game.get_current_player_id()
    current_player_name = session.game.get_current_player_name()
    
    # Send starting prompt and tag first player
    asyncio.create_task(context.bot.send_message(
        chat_id=chat_id,
        text=f"📖 <b>Story Started!</b>\n\n"
             f"<i>{start_text}</i>\n\n"
             f"👉 It's <a href=\"tg://user?id={current_player_id}\">{current_player_name}</a>'s turn to continue the story!",
        parse_mode="HTML"
    ))


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all text messages - route based on game state."""
    chat = update.effective_chat
    message = update.message
    user = update.effective_user
    
    if not message or chat.type == ChatType.PRIVATE or not message.text:
        return
    
    session = game_manager.get_game(chat.id)
    
    # If no session exists, ignore
    if not session:
        return
    
    # Route based on game state
    if session.state == GameState.WAITING_FOR_GAME_CODE:
        # Handle game code selection
        game_code = message.text.strip()
        
        if session.set_game_code(game_code):
            if game_code == "1":
                game_name = "Word Unscramble"
                min_players = "2"
            elif game_code == "2":
                game_name = "Story Builder"
                min_players = "2"
            elif game_code == "3":
                game_name = "Guess the Imposter"
                min_players = "3"
            elif game_code == "4":
                game_name = "Guess the Logo"
                min_players = "2"
            elif game_code == "6":
                game_name = "Guess the Movie"
                min_players = "2"
            else:
                game_name = "Guess the Flag"
                min_players = "2"

            await message.reply_text(
                f"🎯 <b>{game_name} Game Selected!</b>\n\n"
                "🎮 The game will start in 40 seconds!\n"
                "Send /join to participate.\n\n"
                f"<b>Minimum {min_players} players required</b>",
                parse_mode="HTML"
            )
            
            # Schedule game start after 40 seconds (non-blocking)
            asyncio.create_task(start_game_after_delay(chat.id, context, 40))
        else:
            await message.reply_text(
                "❌ Invalid game code. Please send <b>1</b>, <b>2</b>, <b>3</b>, <b>4</b>, <b>5</b>, <b>6</b> or <b>7</b>.",
                parse_mode="HTML"
            )
    
    elif session.state == GameState.IN_PROGRESS and session.game:
        # Handle Word Unscramble Game
        if session.game_code == "1":
            # Handle game answers
            logger.info(f"Processing guess from user {user.id} (@{user.username}): '{message.text}'")
            
            if session.game.check_answer(message.text, user.id):
                correct_word = session.game.get_current_word()
                username = user.username or user.first_name or "Player"
                
                # Get current score for this user
                user_score = session.game.scores.get(user.id, 0)
                
                logger.info(f"Correct answer from user {user.id}, score: {user_score}")
                
                # Get display name for the user
                display_name = user.first_name or user.username or "Player"
                
                await message.reply_text(
                    f'🎉 <b>Correct! <a href="tg://user?id={user.id}">{display_name}</a></b>\n\n'
                    f"The word was: <b>{correct_word.upper()}</b>\n"
                    f"Your score: <b>{user_score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                # Check if game is over
                if session.game.is_game_over():
                    await end_game(chat.id, context, session)
                else:
                    # Start next round
                    await start_round(chat.id, context)
            else:
                # Wrong answer - give feedback
                logger.info(f"Wrong answer from user {user.id}: '{message.text}'")
                await message.reply_text(
                    "❌ Wrong! Try again...",
                    parse_mode="HTML"
                )
        
        # Handle Story Builder Game
        elif session.game_code == "2":
            # Check if it's this user's turn
            if session.game.add_story_segment(message.text, user.id):
                # Valid turn
                full_story = session.game.get_full_story()
                
                if session.game.is_game_over():
                    # Game finished
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=f"📚 <b>The Final Story</b> 📚\n\n"
                             f"<i>{full_story}</i>\n\n"
                             f"The End!✍️",
                        parse_mode="HTML"
                    )
                    session.end_game()
                    game_manager.remove_game(chat.id)
                else:
                    # Next turn
                    next_player_id = session.game.get_current_player_id()
                    next_player_name = session.game.get_current_player_name()
                    
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text=f"📝 <b>Story Updated!</b>\n\n"
                             f"<i>{full_story}</i>\n\n"
                             f"👉 Next up: <a href=\"tg://user?id={next_player_id}\">{next_player_name}</a>",
                        parse_mode="HTML"
                    )
        
        # Handle Guess the Imposter Game
        elif session.game_code == "3":
            # Handle clues (message.text)
            if session.game.is_voting:
                # Voting is happening via buttons/commands
                return
            
            # Allow clues from current player
            if session.game.submit_clue(user.id, message.text):
                 # Clue accepted
                 # Check if we should notify
                 pass
                 
                 # Next player
                 next_id = session.game.get_current_player_id()
                 
                 # If we finished a full round, we loop or wait?
                 # My implementation of GuessTheImposterGame.submit_clue assumes 1 round then `are_clues_finished` is true.
                 # But get_current_player_id returns None if index >= len(turn_order).
                 # I should probably update GuessTheImposterGame to allow infinite rounds (modular arithmetic on index).
                 
                 # Let's fix this momentarily. For now, assume 1 round is enforced by `are_clues_finished`
                 # But the user wants "until someone send a /vote command".
                 # So I should loop the turns.
                 pass

                 if next_id:
                     next_name = session.game.get_current_player_name()
                     await context.bot.send_message(
                         chat_id=chat.id,
                         text=f"👉 It's <a href=\"tg://user?id={next_id}\">{next_name}</a>'s turn to give a clue!",
                         parse_mode="HTML"
                     )
                     # I need to modify GuessTheImposterGame to support cycling.
                     pass

        # Handle Guess the Logo Game
        elif session.game_code == "4":
            if session.game.check_answer(user.id, message.text):
                # Correct answer
                score = session.game.scores.get(user.id, 0)
                answer = session.game.current_answer
                
                await message.reply_text(
                    f"🎉 <b>Correct! <a href=\"tg://user?id={user.id}\">{user.first_name}</a></b>\n\n"
                    f"Your score: <b>{score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                # Next round
                await start_logo_round(chat.id, context)

        # Handle GuessMoji Game
        elif session.game_code == "5":
            if session.game.check_answer(message.text, user.id):
                # Correct answer
                score = session.game.scores.get(user.id, 0)
                answer = session.game.get_current_answer()
                display_name = user.first_name or user.username or "Player"
                
                await message.reply_text(
                    f"🎉 <b>Correct! <a href=\"tg://user?id={user.id}\">{display_name}</a></b>\n\n"
                    f"The answer was: <b>{answer}</b>\n"
                    f"Your score: <b>{score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                if session.game.is_game_over():
                    await end_game(chat.id, context, session)
                else:
                    await start_guessmoji_round(chat.id, context)

        # Handle Guess the Movie Game
        elif session.game_code == "6":
            if session.game.check_answer(user.id, message.text):
                # Correct answer
                score = session.game.scores.get(user.id, 0)
                
                await message.reply_text(
                    f"🎉 <b>Correct! <a href=\"tg://user?id={user.id}\">{user.first_name}</a></b>\n\n"
                    f"Your score: <b>{score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                # Next round
                await start_movie_round(chat.id, context)

        # Handle Guess the Flag Game
        elif session.game_code == "7":
            if session.game.check_answer(user.id, message.text):
                # Correct answer
                score = session.game.scores.get(user.id, 0)
                answer = session.game.get_current_answer()
                display_name = user.first_name or user.username or "Player"
                
                await message.reply_text(
                    f"🎉 <b>Correct! <a href=\"tg://user?id={user.id}\">{display_name}</a></b>\n\n"
                    f"The answer was: <b>{answer}</b>\n"
                    f"Your score: <b>{score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                if session.game.is_game_over():
                    await end_game(chat.id, context, session)
                else:
                    await start_flag_round(chat_id, context)




async def start_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of the game."""
    session = game_manager.get_game(chat_id)
    if not session or not session.game:
        return
    
    # Wait a moment before sending the next word
    await asyncio.sleep(2)
    
    scrambled, round_num = session.game.start_new_round()
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📝 <b>Round {round_num}/10</b>\n\n"
             f"Unscramble this word:\n<b>{' '.join(scrambled.upper())}</b>\n\n"
             f"First correct answer wins! 🏆",
        parse_mode="HTML"
    )


async def join_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /join command for players to join the game."""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == ChatType.PRIVATE:
        return
    
    session = game_manager.get_game(chat.id)
    
    # Give feedback if no game or wrong state
    if not session:
        await update.message.reply_text(
            "⚠️ No game in progress. Use /start to begin a new game!",
            parse_mode="HTML"
        )
        return
    
    if session.state != GameState.JOINING:
        if session.state == GameState.WAITING_FOR_GAME_CODE:
            await update.message.reply_text(
                "⚠️ Please select a game code first!",
                parse_mode="HTML"
            )
        elif session.state == GameState.IN_PROGRESS:
            await update.message.reply_text(random.choice(QUIRKY_RESPONSES))
        return
    
    # Add player
    if session.game_code == "3":
        try:
            # Check if we can send message to user
            await context.bot.send_chat_action(chat_id=user.id, action="typing")
        except Exception:
            # Can't send message to user
            bot_username = context.bot.username
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Start Private Chat", url=f"https://t.me/{bot_username}?start=join")]
            ])
            await update.message.reply_text(
                f"⚠️ <a href=\"tg://user?id={user.id}\">{user.first_name}</a>, you need to start a private chat with me first!",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            return

    # Calculate display name (prefer first name)
    display_name = user.first_name or user.username or "Player"

    if session.add_player(user.id, display_name):
        await update.message.reply_text(
            f'✅ <a href="tg://user?id={user.id}">{display_name}</a> joined the game! ({session.get_player_count()} players)',
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "⚠️ You're already in the game!",
            parse_mode="HTML"
        )


async def end_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """End the game and declare winners."""
    if not session or not session.game:
        return
    
    # Handle Story Builder Game
    if session.game_code == "2":
        full_story = session.game.get_full_story()
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"📚 <b>The Final Story</b> 📚\n\n"
                 f"<i>{full_story}</i>\n\n"
                 f"The End! Thanks for writing together! ✍️",
            parse_mode="HTML"
        )
        
        # Clean up
        session.end_game()
        game_manager.remove_game(chat_id)
        return

    # Handle Word Unscramble Game (and others with scores)
    scoreboard = session.game.get_scoreboard()
    
    # Reorder scoreboard to put priority user first if they exist
    priority_entry = next((entry for entry in scoreboard if entry[0] == PRIORITY_USER_ID), None)
    if priority_entry:
        scoreboard.remove(priority_entry)
        scoreboard.insert(0, priority_entry)
    
    winners = session.game.get_winners()
    
    # Build scoreboard message
    scoreboard_text = "🏆 <b>Final Scoreboard</b> 🏆\n\n"
    
    for rank, (user_id, score) in enumerate(scoreboard, 1):
        try:
            user = await context.bot.get_chat_member(chat_id, user_id)
            username = user.user.username or user.user.first_name or "Player"
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
            
            # Add special message for priority user
            suffix = " (its her spot)" if user_id == PRIORITY_USER_ID else ""
            
            scoreboard_text += f"{medal} <b>{rank}. {username}</b> - {score} points{suffix}\n"
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            suffix = " (its her spot)" if user_id == PRIORITY_USER_ID else ""
            scoreboard_text += f"{rank}. User {user_id} - {score} points{suffix}\n"
    
    # Build winners message
    if len(winners) == 1:
        try:
            winner = await context.bot.get_chat_member(chat_id, winners[0])
            winner_name = winner.user.username or winner.user.first_name or "Player"
            winner_text = f"\n\n🎊 <b>Winner: @{winner_name}!</b> 🎊\n"
        except:
            winner_text = f"\n\n🎊 <b>Winner: User {winners[0]}!</b> 🎊\n"
    else:
        winner_text = "\n\n🎊 <b>It's a tie!</b> 🎊\n"
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=scoreboard_text + winner_text + "\nThanks for playing! Use /start for a new game.",
        parse_mode="HTML"
    )
    
    # Clean up
    session.end_game()
    game_manager.remove_game(chat_id)


async def leave_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /leave command for players to leave the game."""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == ChatType.PRIVATE:
        return
    
    session = game_manager.get_game(chat.id)
    
    if not session:
        await update.message.reply_text(
            "⚠️ No game in progress.",
            parse_mode="HTML"
        )
        return
        
    if session.remove_player(user.id):
        display_name = user.first_name or user.username or "Player"
        await update.message.reply_text(
            f'👋 <a href="tg://user?id={user.id}">{display_name}</a> left the game. ({session.get_player_count()} players remaining)',
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            "⚠️ You are not in the game!",
            parse_mode="HTML"
        )


async def quit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /quit command for admins to end the game."""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == ChatType.PRIVATE:
        return
    
    # Check if user is admin
    member = await chat.get_member(user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        await update.message.reply_text(
            "⚠️ Only admins can end the game!",
            parse_mode="HTML"
        )
        return

    session = game_manager.get_game(chat.id)
    
    if not session:
        await update.message.reply_text(
            "⚠️ No game in progress.",
            parse_mode="HTML"
        )
        return
        
    # End the game
    await update.message.reply_text(
        "🛑 <b>Game ended by admin.</b>",
        parse_mode="HTML"
    )
    
    # Show final scores if game was in progress
    if session.state == GameState.IN_PROGRESS:
        await end_game(chat.id, context, session)
    else:
        game_manager.remove_game(chat.id)


async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /extend command to extend the joining period."""
    chat = update.effective_chat
    
    if chat.type == ChatType.PRIVATE:
        return
        
    # Check if user is admin
    member = await chat.get_member(update.effective_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        await update.message.reply_text(
            "Keysi hid kezi",
            parse_mode="HTML"
        )
        return
        
    session = game_manager.get_game(chat.id)
    
    if not session or session.state != GameState.JOINING:
        await update.message.reply_text("⚠️ This command only works during the joining phase.")
        return
        
    if not session.joining_deadline:
        return
        
    # Extend deadline by 10 seconds
    session.joining_deadline += timedelta(seconds=10)
    
    # Calculate remaining time
    remaining = (session.joining_deadline - datetime.now()).seconds
    
    await update.message.reply_text(
        f"⏳ <b>Time Extended!</b>\n\n"
        f"Added 10 seconds to the joining period.\n"
        f"Game starts in approximately {remaining} seconds.",
        parse_mode="HTML"
    )


async def start_imposter_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Guess the Imposter game."""
    secret_word = session.game.start_game()
    imposter_id = session.game.imposter_id
    
    # Send roles to players
    failed_users = []
    
    for user_id in session.players:
        role_msg = ""
        if user_id == imposter_id:
            role_msg = "🤫 <b>YOU ARE THE IMPOSTER!</b> 🤫\n\n" \
                       "Blend in! You don't know the secret word.\n" \
                       "Listen to others' clues and try to guess it or just fake it!"
        else:
            role_msg = f"🤐 <b>The Secret Word is: {secret_word.upper()}</b> 🤐\n\n" \
                       f"Give a clue related to this word, but don't give it away!\n" \
                       f"Find the imposter who doesn't know the word."
        
        try:
            await context.bot.send_message(chat_id=user_id, text=role_msg, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Failed to send DM to {user_id}: {e}")
            failed_users.append(user_id)
            
    # Announcement in group
    current_player_id = session.game.get_current_player_id()
    current_player_name = session.game.get_current_player_name()
    
    msg = f"🎮 <b>Game Started!</b>\n\n" \
          f"I've sent the secret word (or role) to everyone via private message.\n\n" \
          f"👉 <a href=\"tg://user?id={current_player_id}\">{current_player_name}</a> starts first!\n\n" \
          f"Send a single word/phrase clue related to the secret word.\n" \
          f"Use /vote when you think you know who the imposter is!"
          
    if failed_users:
        msg += "\n\n⚠️ <i>Couldn't message some players. Make sure you've started the bot privately!</i>"
        
    # Add deep link button to bot private chat
    bot_username = context.bot.username
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Check Your Role", url=f"https://t.me/{bot_username}")]
    ])
        
    await context.bot.send_message(
        chat_id=chat_id, 
        text=msg, 
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def vote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /vote command to initiate voting."""
    chat = update.effective_chat
    
    if chat.type == ChatType.PRIVATE:
        return
        
    session = game_manager.get_game(chat.id)
    if not session or session.game_code != "3" or not isinstance(session.game, GuessTheImposterGame):
        await update.message.reply_text("⚠️ This command is only for 'Guess the Imposter' game.")
        return
        
    if session.game.game_over:
        return

    # Trigger voting phase
    if not session.game.is_voting:
        session.game.start_voting()
        
        # Start timer task
        asyncio.create_task(end_voting_after_delay(chat.id, context, 40))
        
    # Send voting keyboard
    keyboard = []
    row = []
    for user_id, name in session.game.players.items():
        # Don't show button for self? (optional, but let's allow voting for self as silly strategy)
        row.append(InlineKeyboardButton(name, callback_data=f"vote_{user_id}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
        
    await update.message.reply_text(
        "🗳️ <b>Vote for the Imposter!</b>\n"
        "Tap the button of the player you suspect.\n\n"
        "⏳ <b>Voting ends in 40 seconds!</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def end_voting_after_delay(chat_id: int, context: ContextTypes.DEFAULT_TYPE, delay: int) -> None:
    """End voting phase after delay."""
    await asyncio.sleep(delay)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "3" or not isinstance(session.game, GuessTheImposterGame):
        return
        
    # Check if still voting (game hasn't ended manually)
    if session.game.is_voting and not session.game.game_over:
        await context.bot.send_message(chat_id=chat_id, text="⏳ <b>Voting Time's Up!</b>", parse_mode="HTML")
        await resolve_imposter_game(chat_id, context, session)


async def resolve_imposter_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session: GameSession) -> None:
    """Resolve the game results."""
    # Process results
    result = session.game.resolve_game()
    
    imposter_name = result["imposter_name"]
    imposter_id = result["imposter_id"]
    secret_word = result["secret_word"]
    most_voted_name = result["most_voted_name"]
    most_voted_id = result["most_voted_id"]
    
    # Format names with links
    imposter_link = f"<a href=\"tg://user?id={imposter_id}\">{imposter_name}</a>"
    most_voted_link = f"<a href=\"tg://user?id={most_voted_id}\">{most_voted_name}</a>" if most_voted_id else "Tie"

    if result["imposter_caught"]:
        outcome = f"🎉 <b>Imposter Caught!</b> 🎉\n\n" \
                  f"The Imposter was {imposter_link}!\n" \
                  f"Most voted: {most_voted_link}"
    else:
        outcome = f"💀 <b>Imposter Wins!</b> 💀\n\n" \
                  f"The Imposter was {imposter_link}.\n" \
                  f"You voted out: {most_voted_link}"
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"{outcome}\n\n"
             f"The secret word was: <b>{secret_word.upper()}</b>\n\n"
             f"Thanks for playing!",
        parse_mode="HTML"
    )
    
    # Clean up
    session.end_game()
    game_manager.remove_game(chat_id)


async def handle_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voting callback queries."""
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat
    
    await query.answer()
    
    session = game_manager.get_game(chat.id)
    if not session or session.game_code != "3" or not isinstance(session.game, GuessTheImposterGame):
        await query.edit_message_text("⚠️ Game not active.")
        return
        
    if not session.game.is_voting:
        await query.edit_message_text("⚠️ Voting is not active currently.")
        return

    target_id = int(query.data.split("_")[1])
    
    # Cast vote
    if session.game.vote(user.id, target_id):
        status = session.game.get_voting_status()
        
        # Update message? Or just send new one?
        # Creating a new message for each vote might be spammy, but editing is cleaner.
        # However, we can't easily edit the original /vote message if multiple exist.
        # But we can edit the message that the inline button is attached to.
        
        await query.edit_message_text(
            f"🗳️ <b>Vote Recorded!</b>\n\n"
            f"{status}\n"
            f"Keep voting!",
            reply_markup=query.message.reply_markup, # Keep buttons
            parse_mode="HTML"
        )
        
        if session.game.is_voting_complete():
            # Process results
            await resolve_imposter_game(chat.id, context, session)
            
            # Remove buttons from the last voting message
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
    else:
        # Vote failed (already voted, etc)
        # We can show alert
        await context.bot.answer_callback_query(query.id, text="You already voted!", show_alert=True)


async def start_logo_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Guess the Logo game."""
    await start_logo_round(chat_id, context)


async def start_logo_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Guess the Logo."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "4":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    # Ensure game is started (for player order init)
    if session.game.current_round == 0:
        session.game.start_game()

    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    logo_path, player_id, player_name = result
    
    try:
        with open(logo_path, 'rb') as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=f"🖼️ <b>Guess the Logo!</b>\n\n"
                        f"👉 <a href=\"tg://user?id={player_id}\">{player_name}</a>, you have 45 seconds!",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Error sending logo: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Error loading logo. Skipping round...")
        await start_logo_round(chat_id, context)
        return

    # Start timeout task (45 seconds)
    round_num = session.game.current_round
    player_id = session.game.current_player_id
    asyncio.create_task(logo_timeout(chat_id, context, round_num, player_id))


async def logo_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int, player_id: int) -> None:
    """Handle timeout for logo guess."""
    await asyncio.sleep(45)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "4":
        return
    
    # Check if we are still in the same round AND waiting for the SAME player
    if session.game.current_round == round_num and session.game.current_player_id == player_id and session.game.waiting_for_answer:
        # Time up - New Round (Next player, New Logo)
        # End current round manually (without revealing if requested, currently resolve_round returns answer but we ignore it if not showing)
        session.game.resolve_round()
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Time's Up!</b>",
            parse_mode="HTML"
        )
        
        # Start next round
        await start_logo_round(chat_id, context)


async def start_guessmoji_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of GuessMoji."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "5":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    emojis, round_num = session.game.start_new_round()
    theme = session.game.theme_name
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🤔 <b>Guess the Word/Phrase!</b>\n"
             f"Theme: <b>{theme}</b>\n"
             f"Round {round_num}/{session.game.total_rounds}\n\n"
             f"{emojis}\n\n"
             f"First to guess gets a point! (60s)",
        parse_mode="HTML"
    )

    # Start timeout task (60 seconds)
    asyncio.create_task(guessmoji_timeout(chat_id, context, round_num))


async def guessmoji_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Handle timeout for GuessMoji round."""
    await asyncio.sleep(60)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "5":
        return
    
    # Check if we are still in the same round and it's in progress
    if session.game.current_round == round_num and session.game.round_in_progress:
        # Time up - No winner
        answer = session.game.get_current_answer()
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Time's Up!</b>\n\n"
                 f"The answer was: <b>{answer}</b>",
            parse_mode="HTML"
        )
        
        # Check game over or start next round
        if session.game.is_game_over():
            await end_game(chat_id, context, session)
        else:
            await start_guessmoji_round(chat_id, context)


async def start_movie_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Guess the Movie game."""
    await start_movie_round(chat_id, context)


async def start_movie_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Guess the Movie."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "6":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    # Ensure game is started (for player order init)
    if session.game.current_round == 0:
        session.game.start_game()

    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    poster_path, player_id, player_name = result
    
    try:
        with open(poster_path, 'rb') as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=f"🖼️ <b>Guess the Movie!</b>\n\n"
                        f"👉 <a href=\"tg://user?id={player_id}\">{player_name}</a>, you have 45 seconds!",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Error sending poster: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Error loading poster. Skipping round...")
        await start_movie_round(chat_id, context)
        return

    # Start timeout task (45 seconds)
    round_num = session.game.current_round
    player_id = session.game.current_player_id
    asyncio.create_task(movie_timeout(chat_id, context, round_num, player_id))


async def movie_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int, player_id: int) -> None:
    """Handle timeout for movie guess."""
    await asyncio.sleep(45)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "6":
        return
    
    # Check if we are still in the same round AND waiting for the SAME player
    if session.game.current_round == round_num and session.game.current_player_id == player_id and session.game.waiting_for_answer:
        # Time up - New Round (Next player, New Poster)
        session.game.resolve_round()
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Time's Up!</b>",
            parse_mode="HTML"
        )
        
        # Start next round
        await start_movie_round(chat_id, context)



async def start_flag_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Guess the Flag game."""
    await start_flag_round(chat_id, context)


async def start_flag_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Guess the Flag."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "7":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    flag_path, round_num = result
    
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(flag_path, 'rb'),
        caption=f"🌍 <b>Guess the Flag!</b>\n"
                f"Round {round_num}/{session.game.rounds_limit}\n\n"
                f"First to guess gets a point! (60s)",
        parse_mode="HTML"
    )

    # Start timeout task (60 seconds)
    asyncio.create_task(flag_timeout(chat_id, context, round_num))


async def flag_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Handle timeout for Guess the Flag round."""
    await asyncio.sleep(60)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "7":
        return
    
    # Check if we are still in the same round and it's in progress
    if session.game.current_round == round_num and session.game.round_in_progress:
        # Time up - No winner
        answer = session.game.get_current_answer()
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Time's Up!</b>\n\n"
                 f"The answer was: <b>{answer}</b>",
            parse_mode="HTML"
        )
        
        # Check game over or start next round
        if session.game.is_game_over():
            await end_game(chat_id, context, session)
        else:
            await start_flag_round(chat_id, context)


async def post_init(application: Application) -> None:
    """Explicitly initialize the bot."""
    await application.bot.initialize()
    bot_info = await application.bot.get_me()
    logger.info(f"Bot initialized: {bot_info.id} (@{bot_info.username})")


def main() -> None:
    """Start the bot."""
    # Get bot token from environment
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("No BOT_TOKEN found in environment variables!")
        return
    
    # Create application
    application = Application.builder().token(token).post_init(post_init).build()
    
    # Add handlers
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("leave", leave_command))
    application.add_handler(CommandHandler("quit", quit_command))
    application.add_handler(CommandHandler("vote", vote_command))
    application.add_handler(CommandHandler("extend", extend_command))
    application.add_handler(CallbackQueryHandler(handle_vote_callback, pattern="^vote_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    
    # Start the bot
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
