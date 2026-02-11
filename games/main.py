import os
import json
import logging
import asyncio
import random
import threading
import html
from typing import Optional, Dict, List, Tuple, Union
from dotenv import load_dotenv
from flask import Flask

from telegram import Update, ChatMember, ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChosenInlineResultHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatType, ChatMemberStatus, ParseMode
from telegram import InlineQueryResultArticle, InputTextMessageContent, InlineQueryResultCachedPhoto
from telegram.error import NetworkError, Forbidden, TimedOut, TelegramError

from datetime import datetime, timedelta

from game_manager import GameManager, GameState, GameSession
from story_builder import StoryBuilderGame
from guess_the_imposter import GuessTheImposterGame
from guess_the_logo import GuessTheLogoGame
from guess_the_movie import GuessTheMovieGame
from guess_the_flag import GuessTheFlagGame
from soccer_trivia import SoccerTriviaGame
from guessmoji import GuessMojiGame
from general_knowledge import GeneralKnowledgeGame
from guess_character import GuessCharacterGame
from word_connect import WordConnectGame
from wdym_game import MemeGame
from taylor_shakespeare import TaylorShakespeareGame
from silent_game import SilentGame
from twenty_questions import TwentyQuestionsGame


# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)



# Initialize game manager
game_manager = GameManager()

# Global task tracker for game-related background tasks (timers, hints, reminders)
active_game_tasks: Dict[int, List[asyncio.Task]] = {}

def track_game_task(chat_id: int, task: asyncio.Task) -> None:
    """Register a background task for a specific game chat."""
    if chat_id not in active_game_tasks:
        active_game_tasks[chat_id] = []
    active_game_tasks[chat_id].append(task)
    # Clean up finished tasks from the list occasionally
    task.add_done_callback(lambda t: active_game_tasks[chat_id].remove(t) if chat_id in active_game_tasks and t in active_game_tasks[chat_id] else None)

def cancel_game_tasks(chat_id: int) -> None:
    """Cancel all background tasks for a specific game chat."""
    if chat_id in active_game_tasks:
        for task in active_game_tasks[chat_id]:
            if not task.done():
                task.cancel()
        del active_game_tasks[chat_id]


# Allowed Group IDs
ALLOWED_CHAT_IDS = [
    -1003170577690,  # @aau_confessions
    -1003696845309,  # Testing Group
]

# Global lock for meme caching
meme_cache_lock = asyncio.Lock()


# Quirky response messages
QUIRKY_RESPONSES = [
    "Yeah… that’s not happening",
    "I considered it. Briefly. No.",
    "I refuse, and I stand by that decision",
    "Absolutely not. Hope this helps.",
    "I said no in several timelines.",
    "The request was processed and found unnecessary",
    "I refuse to participate in this chaos.",
    "I could… but I won’t",
    "This request embarrasses you.",
    "I’d rather do nothing.",
    "Please don’t ever ask that again",
    "I’m pretending I didn’t see that.",
    "I’d rather reboot",
    "Ere",
    "Ask me again and I’ll still say no.",
    "I refuse to acknowledge this."
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
    user = update.effective_user
    
    # Only work in groups
    if chat.type == ChatType.PRIVATE:
        await update.message.reply_text(
            "Sup\n\n"
            "Add me to a group and make me an admin to start playing games."
        )
        return
    
    # Check Allowed Group
    if chat.id not in ALLOWED_CHAT_IDS:
        await update.message.reply_text(
            "<b>Womp Womp</b>\n\n"
            "I only work in the @aau_confessions group",
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
    
    # Create a new game session
    session = game_manager.create_game(chat.id)
    session.initiator_id = user.id
    
    # Quirky intro
    await update.message.reply_text(
        "🎮 <b>Welcome to Game Bot!</b>\n\n"
        "Please select a game by sending its code:\n\n"
        "<b>1</b> - Word Unscramble Game\n"
        "<b>2</b> - Story Builder Game\n"
        "<b>3</b> - Guess the Imposter\n"
        "<b>4</b> - Guess the Logo\n"
        "<b>5</b> - GuessMoji Game\n"
        "<b>6</b> - Guess the Movie\n"
        "<b>7</b> - Guess the Flag\n"
        "<b>8</b> - Soccer Trivia\n"
        "<b>9</b> - General Knowledge\n"
        "<b>10</b> - Guess the Character\n"
        "<b>11</b> - Word Connect Game\n"
        "<b>12</b> - What You Meme\n"
        "<b>13</b> - Taylor Swift Or Shakespeare\n"
        "<b>14</b> - The Silent Game\n"
        "<b>15</b> - 20 Questions\n\n"
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
        elif session.game_code == "8":
            # Soccer Trivia
            await start_soccer_trivia_game(chat_id, context, session)
        elif session.game_code == "9":
            # General Knowledge
            await start_general_knowledge_game(chat_id, context, session)
        elif session.game_code == "10":
            # Guess the Character
            await start_character_game(chat_id, context, session)
        elif session.game_code == "11":
            # Word Connect
            await start_word_connect_game(chat_id, context, session)
        elif session.game_code == "12":
            # What You Meme
            await start_wdym_game(chat_id, context, session)
        elif session.game_code == "13":
            # Taylor Swift Or Shakespeare
            await start_ts_game(chat_id, context, session)
        elif session.game_code == "14":
            # The Silent Game
            await start_silent_game(chat_id, context, session)
        elif session.game_code == "15":
            # 20 Questions
            await start_20q_game(chat_id, context, session)



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


async def start_silent_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the silent game and announce the rules."""
    session.game.start_game()
    await context.bot.send_message(
        chat_id=chat_id,
        text="🤫 <b>The Silent Game has Started!</b>\n\n"
             "The rules are simple:\n"
             "1. If you joined, <b>STAY SILENT</b>. The last person to stay silent wins!\n"
             "2. If you didn't join, <b>DON'T SEND ANYTHING</b>. Your messages will be deleted.\n"
             "3. If a player sends any message, they are eliminated.\n\n"
             "Good luck... and SHHH! 🤐",
        parse_mode="HTML"
    )


async def process_silent_game_content(update: Update, context: ContextTypes.DEFAULT_TYPE, session) -> bool:
    """
    Handle a message during the Silent Game.
    Returns True if the message was handled as a violation, False otherwise.
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if not message or not session or not session.game or session.game_code != "14" or session.state != GameState.IN_PROGRESS:
        return False

    user_id = user.id
    if user_id in session.game.players and user_id not in session.game.losers:
        # Player sending content!
        session.game.eliminate_player(user_id)
        
        # React with 👎
        try:
            await context.bot.set_message_reaction(
                chat_id=chat.id,
                message_id=message.message_id,
                reaction=[ReactionTypeEmoji(emoji="👎")]
            )
        except Exception as e:
            logger.error(f"Error setting reaction: {e}")
        
        await message.reply_text(
            f"👎 <b><a href=\"tg://user?id={user_id}\">{user.first_name}</a>, you lost!</b>",
            parse_mode="HTML"
        )
        
        # Check if game is over
        if session.game.is_game_over():
            await end_game(chat.id, context, session)
        return True
    else:
        # Non-player or already eliminated sending content - delete
        try:
            await message.delete()
        except Exception as e:
            logger.error(f"Error deleting message: {e}")
        return True


async def handle_misc_content(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle non-text/non-photo messages (stickers, voice, etc.) during games like Silent Game."""
    chat = update.effective_chat
    session = game_manager.get_game(chat.id)
    if session and session.game_code == "14":
        await process_silent_game_content(update, context, session)


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
    
    # If waiting for game code, process numbers
    if session.state == GameState.WAITING_FOR_GAME_CODE:
        # Check if it's the initiator
        if session.initiator_id and user.id != session.initiator_id:
            return # Ignore others picking game
            
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
            elif game_code == "7":
                game_name = "Guess the Flag"
                min_players = "2"
            elif game_code == "8":
                game_name = "Soccer Trivia"
                min_players = "2"
            elif game_code == "9":
                game_name = "General Knowledge"
                min_players = "2"
            elif game_code == "10":
                game_name = "Guess the Character"
                min_players = "2"
            elif game_code == "11":
                game_name = "Word Connect"
                min_players = "2"
            elif game_code == "12":
                game_name = "What You Meme"
                min_players = "2"
            elif game_code == "13":
                game_name = "Taylor Swift Or Shakespeare"
                min_players = "2"
            elif game_code == "14":
                game_name = "The Silent Game"
                min_players = "2"
            elif game_code == "15":
                game_name = "20 Questions"
                min_players = "2"

            else:
                game_name = "General Knowledge" # Default fallback
                min_players = "2"

            await message.reply_text(
                f"🎯 <b>{game_name} Game Selected!</b>\n\n"
                "🎮 The game will start in 40 seconds!\n"
                "Send /join to participate.\n\n"
                f"<b>Minimum {min_players} players required</b>",
                parse_mode="HTML"
            )
            
            # Schedule game start after 40 seconds (non-blocking)
            track_game_task(chat.id, asyncio.create_task(start_game_after_delay(chat.id, context, 40)))
        else:
            await message.reply_text(
                "❌ Invalid game code. Please send <b>1</b> to <b>15</b>.",
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
                    await start_flag_round(chat.id, context)

        # Handle Soccer Trivia Game
        elif session.game_code == "8":
            if session.game.check_answer(user.id, message.text):
                if session.game.round_type == "listing":
                    await message.reply_text(
                        f"✅ <b>Correct!</b> That's one of them. You got a point!",
                        parse_mode="HTML"
                    )
                else:
                    # Logo round - score is updated, round is over
                    score = session.game.scores.get(user.id, 0)
                    await message.reply_text(
                        f"⚽️ <b>Correct! <a href=\"tg://user?id={user.id}\">{user.first_name}</a></b>\n\n"
                        f"The answer was: <b>{session.game.current_answer}</b>\n"
                        f"Your score: <b>{score}</b> point(s)",
                        parse_mode="HTML"
                    )
                    
                    if session.game.is_game_over():
                        await end_game(chat.id, context, session)
                    else:
                        await start_soccer_trivia_round(chat.id, context)
            else:
                pass

        # Handle General Knowledge Game
        elif session.game_code == "9":
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
                    await start_general_knowledge_round(chat.id, context)

        # Handle Guess the Character Game
        elif session.game_code == "10":
            if session.game.check_answer(user.id, message.text):
                # Correct answer
                score = session.game.scores.get(user.id, 0)
                answer = session.game.get_current_answer()
                full_image = session.game.get_full_image()
                display_name = user.first_name or user.username or "Player"
                
                # Send confirmation first
                await message.reply_text(
                    f"🎉 <b>Correct! <a href=\"tg://user?id={user.id}\">{display_name}</a></b>\n\n"
                    f"The answer was: <b>{answer}</b>\n"
                    f"Your score: <b>{score}</b> point(s)",
                    parse_mode="HTML"
                )
                
                # Send the full image
                try:
                    with open(full_image, 'rb') as f:
                        await context.bot.send_photo(
                            chat_id=chat.id,
                            photo=f,
                            caption=f"✅ <b>Full Picture: {answer}</b>",
                            parse_mode="HTML"
                        )
                except Exception as e:
                    logger.error(f"Error sending full image: {e}")

                if session.game.is_game_over():
                    await end_game(chat.id, context, session)
                else:
                    await start_character_round(chat.id, context)

        # Handle Word Connect Game
        elif session.game_code == "11":
            is_correct, feedback = session.game.check_answer(user.id, message.text)
            if is_correct:
                progress = session.game.get_round_progress()
                display_name = user.first_name or user.username or "Player"
                letters = session.game.current_letters
                
                await message.reply_text(
                    f"🎉 <b>{feedback}! <a href=\"tg://user?id={user.id}\">{display_name}</a></b>\n\n"
                    f"Letters: <b>{' '.join(letters).upper()}</b>\n\n"
                    f"{progress}",
                    parse_mode="HTML"
                )
                
                # Reset hint timer
                cancel_game_tasks(chat.id)
                track_game_task(chat.id, asyncio.create_task(word_connect_hint_timeout(chat.id, context, session.game.current_round)))
                
                if session.game.is_round_finished():
                    # Cancel hint timer
                    cancel_game_tasks(chat.id)
                    
                    await context.bot.send_message(
                        chat_id=chat.id,
                        text="🎊 <b>Round Completed!</b> 🎊\nAll words found!",
                        parse_mode="HTML"
                    )
                    
                    if session.game.is_game_over():
                        await end_game(chat.id, context, session)
                    else:
                        await start_word_connect_round(chat.id, context)
            elif feedback:
                # feedback contains "Already found!"
                await message.reply_text(f"⚠️ {feedback}", parse_mode="HTML")

        # Handle The Silent Game
        elif session.game_code == "14":
            await process_silent_game_content(update, context, session)

        # Handle 20 Questions Game
        elif session.game_code == "15":
            if not session.game.round_in_progress:
                return

            user_id = user.id
            text = message.text.strip()
            
            # Host can only answer text ending in ?
            # Check if it is the host speaking
            if user_id == session.game.host_id:
                pass
            else:
                # Guesser logic
                is_action, result = session.game.check_guess_or_question(user_id, text)
                
                if result == 'QUESTION_COUNTED':
                    # React to the question
                    try:
                        # Try user's preferred 'Alien Monster'
                        await context.bot.set_message_reaction(
                            chat_id=chat.id,
                            message_id=message.message_id,
                            reaction=[ReactionTypeEmoji(emoji="👾")]
                        )
                    except Exception:
                        try:
                            # Fallback to standard 'Thinking Face'
                            await context.bot.set_message_reaction(
                                chat_id=chat.id,
                                message_id=message.message_id,
                                reaction=[ReactionTypeEmoji(emoji="🤔")]
                            )
                        except Exception:
                            pass # Group has restricted reactions

                    remaining = session.game.max_questions - session.game.questions_asked
                    if remaining <= 5:
                         await message.reply_text(f"⚠️ <b>{remaining} questions left!</b>", parse_mode="HTML")

                elif result == 'LIMIT_REACHED':
                    await message.reply_text("🚫 <b>20 Questions Reached!</b> Host wins this round.")
                    session.game.host_wins_round()
                    
                    if session.game.is_game_over():
                        await end_game(chat.id, context, session)
                    else:
                        await start_20q_round(chat.id, context)
                        
                elif result == 'CORRECT':
                    display_name = user.first_name
                    await message.reply_text(
                        f"🎉 <b>Correct! <a href=\"tg://user?id={user_id}\">{display_name}</a> got it!</b>\n"
                        f"The word was: <b>{session.game.current_word}</b>\n\n"
                        f"<i>{display_name} is now the Host!</i>",
                        parse_mode="HTML"
                    )
                    
                    if session.game.is_game_over():
                        await end_game(chat.id, context, session)
                    else:
                        # Winner becomes host
                        await start_20q_round(chat.id, context, forced_host_id=user_id)





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
    

    
    winners = session.game.get_winners()
    
    # Build scoreboard message
    scoreboard_text = "🏆 <b>Final Scoreboard</b> 🏆\n\n"
    
    for rank, (user_id, score) in enumerate(scoreboard, 1):
        try:
            user = await context.bot.get_chat_member(chat_id, user_id)
            username = user.user.username or user.user.first_name or "Player"
            medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
            
            scoreboard_text += f"{medal} <b>{username}</b> - {score} points\n"
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            scoreboard_text += f"{rank}. User {user_id} - {score} points\n"

    
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
    cancel_game_tasks(chat_id)
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
    """Handle /quit command with voting mechanism."""
    chat = update.effective_chat
    user = update.effective_user
    
    if chat.type == ChatType.PRIVATE:
        return
        
    session = game_manager.get_game(chat.id)
    if not session:
        return

    # Check if a vote is already in progress
    if session.quit_vote_message_id:
        await update.message.reply_text("⚠️ A quit vote is already in progress!")
        return

    # Don't require vote if no one has joined yet or only 1 player
    if len(session.players) <= 1:
        await update.message.reply_text("👋 Game ended.")
        session.end_game()
        game_manager.remove_game(chat.id)
        cancel_game_tasks(chat.id)
        return

    # Only admins can trigger the quit vote
    try:
        member = await chat.get_member(user.id)
        if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("❌ Only group admins can initiate a quit vote.")
            return
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return

    # Start voting
    session.quit_votes = {user.id} if user.id in session.players else set()
    total_players = len(session.players)
    required_votes = (total_players + 1) // 2
    current_votes = len(session.quit_votes)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Vote to Quit ({current_votes}/{required_votes})", callback_data="vote_quit_game")]
    ])

    msg = await update.message.reply_text(
        f"🚨 <b>Quit Vote Started!</b>\n\n"
        f"The game will end if <b>{required_votes}</b> players agree.\n"
        f"Valid for 60 seconds.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    session.quit_vote_message_id = msg.message_id
    
    # Auto-cancel vote after 60s
    track_game_task(chat.id, asyncio.create_task(quit_vote_timeout(chat.id, context, msg.message_id)))

async def quit_vote_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, message_id: int):
    """Clean up voting after timeout."""
    await asyncio.sleep(60)
    session = game_manager.get_game(chat_id)
    if session and session.quit_vote_message_id == message_id:
        session.quit_vote_message_id = None
        session.quit_votes = set()
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text="❌ <b>Quit vote expired.</b> Game continues!",
                parse_mode="HTML"
            )
        except Exception:
            pass

async def handle_quit_vote_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quit vote button clicks."""
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat
    
    session = game_manager.get_game(chat.id)
    if not session or not session.quit_vote_message_id or session.quit_vote_message_id != query.message.message_id:
        await query.answer("Vote no longer active.")
        return

    if user.id not in session.players:
        await query.answer("❌ Only joined players can vote!", show_alert=True)
        return

    if user.id in session.quit_votes:
        await query.answer("You already voted!")
        return

    session.quit_votes.add(user.id)
    total_players = len(session.players)
    required_votes = (total_players + 1) // 2
    current_votes = len(session.quit_votes)

    if current_votes >= required_votes:
        await query.answer("Game ended by vote!")
        await context.bot.edit_message_text(
            chat_id=chat.id,
            message_id=session.quit_vote_message_id,
            text=f"🛑 <b>Game Terminated!</b>\nMajority voted to quit ({current_votes}/{total_players}).",
            parse_mode="HTML"
        )
        session.end_game()
        # If in progress, show final scores? Or just remove. 
        # Usually it's better to just end it if they voted to quit.
        game_manager.remove_game(chat.id)
        cancel_game_tasks(chat.id)
    else:
        await query.answer("Vote counted!")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Vote to Quit ({current_votes}/{required_votes})", callback_data="vote_quit_game")]
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            pass


async def extend_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /extend command to extend the joining period."""
    chat = update.effective_chat
    
    if chat.type == ChatType.PRIVATE:
        return
        
    # Check if user is admin
    member = await chat.get_member(update.effective_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        await update.message.reply_text(
            "",
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
    
    try:
        await query.answer()
    except (NetworkError, TimedOut, TelegramError) as e:
        logger.warning(f"Failed to answer callback query: {e}")
    
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
    track_game_task(chat_id, asyncio.create_task(logo_timeout(chat_id, context, round_num, player_id)))


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
    track_game_task(chat_id, asyncio.create_task(guessmoji_timeout(chat_id, context, round_num)))


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
    track_game_task(chat_id, asyncio.create_task(movie_timeout(chat_id, context, round_num, player_id)))


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
    track_game_task(chat_id, asyncio.create_task(flag_timeout(chat_id, context, round_num)))


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


async def start_soccer_trivia_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Soccer Trivia game."""
    await start_soccer_trivia_round(chat_id, context)


async def start_soccer_trivia_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Soccer Trivia."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "8":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    round_type = result["type"]
    round_num = result["round"]
    
    if round_type == "listing":
        question_text = result["question"]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚽️ <b>Soccer Trivia!</b>\n"
                 f"Round {round_num}/{session.game.rounds_limit}\n\n"
                 f"👉 <b>{question_text}</b>\n\n"
                 f"🔥 <b>You have 80 seconds</b> to list as many as possible!\n"
                 f"Each correct team can only be claimed once!",
            parse_mode="HTML"
        )
        # Start timeout task (80 seconds)
        track_game_task(chat_id, asyncio.create_task(soccer_trivia_timeout(chat_id, context, round_num, "listing")))
        
    elif round_type == "logo":
        logo_path = result["logo_path"]
        player_id = result["player_id"]
        player_name = result["player_name"]
        
        try:
            with open(logo_path, 'rb') as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=f"⚽️ <b>Soccer Trivia: Guess the Logo!</b>\n"
                            f"Round {round_num}/{session.game.rounds_limit}\n\n"
                            f"👉 <a href=\"tg://user?id={player_id}\">{player_name}</a>, you have 45 seconds!",
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.error(f"Error sending soccer logo: {e}")
            await context.bot.send_message(chat_id=chat_id, text="⚠️ Error loading logo. Skipping round...")
            await start_soccer_trivia_round(chat_id, context)
            return
            
        # Start timeout task (45 seconds)
        track_game_task(chat_id, asyncio.create_task(soccer_trivia_timeout(chat_id, context, round_num, "logo", player_id)))


async def soccer_trivia_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int, type: str, player_id: int = None) -> None:
    """Handle timeout for Soccer Trivia round."""
    timeout_duration = 80 if type == "listing" else 45
    await asyncio.sleep(timeout_duration)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "8":
        return
    
    # Check if we are still in the same round and it's in progress
    if session.game.current_round == round_num and session.game.round_in_progress:
        # Check player if it's a logo round
        if type == "logo" and session.game.current_player_id != player_id:
            return

        # Time up - Resolve and show results
        results = session.game.resolve_round()
        
        if type == "listing":
            round_scores = results["round_scores"]
            sample_missed = results["sample_missed"]
            total_claimed = results["total_claimed"]
            
            msg = f"⏰ <b>Time's Up!</b>\n\n"
            msg += f"Total teams found: <b>{total_claimed}</b>\n"
            
            if round_scores:
                msg += "\n<b>Round Performance:</b>\n"
                sorted_performers = sorted(round_scores.items(), key=lambda x: x[1], reverse=True)
                for uid, count in sorted_performers:
                    try:
                        member = await context.bot.get_chat_member(chat_id, uid)
                        name = member.user.first_name or member.user.username or "Player"
                        msg += f"• <b>{name}</b>: {count} team(s)\n"
                    except Exception:
                        msg += f"• User {uid}: {count} team(s)\n"
            else:
                msg += "\n<i>No one found any teams!</i>\n"
                
            if sample_missed:
                msg += f"\nSome missed teams: <i>{', '.join(sample_missed)}</i>"
            
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            
        elif type == "logo":
            answer = results["answer"]
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
            await start_soccer_trivia_round(chat_id, context)


async def start_general_knowledge_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the General Knowledge game."""
    await start_general_knowledge_round(chat_id, context)


async def start_general_knowledge_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of General Knowledge."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "9":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    question_text, round_num = session.game.start_new_round()
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🧠 <b>General Knowledge!</b>\n"
             f"Round {round_num}/{session.game.total_rounds}\n\n"
             f"👉 <b>{question_text}</b>\n\n"
             f"First to guess gets a point! (60s)",
        parse_mode="HTML"
    )

    # Start timeout task (60 seconds)
    track_game_task(chat_id, asyncio.create_task(general_knowledge_timeout(chat_id, context, round_num)))


async def general_knowledge_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Handle timeout for General Knowledge round."""
    await asyncio.sleep(60)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "9":
        return
    
    # Check if we are still in the same round and it's in progress
    if session.game.current_round == round_num and session.game.round_in_progress:
        # Time up - No winner
        session.game.round_in_progress = False
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
            await start_general_knowledge_round(chat_id, context)



async def start_character_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Guess the Character game."""
    await start_character_round(chat_id, context)


async def start_character_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Guess the Character."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "10":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    cropped_path, round_num = result
    
    try:
        with open(cropped_path, 'rb') as f:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=f,
                caption=f"🔍 <b>Guess the Person/Character!</b>\n"
                        f"Round {round_num}/{session.game.rounds_limit}\n\n"
                        f"First to guess gets a point! (60s)",
                parse_mode="HTML"
            )
    except Exception as e:
        logger.error(f"Error sending cropped image: {e}")
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Error loading image. Skipping round...")
        await start_character_round(chat_id, context)
        return

    # Start timeout task (60 seconds)
    track_game_task(chat_id, asyncio.create_task(character_timeout(chat_id, context, round_num)))


async def character_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Handle timeout for Guess the Character round."""
    await asyncio.sleep(60)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "10":
        return
    
    # Check if we are still in the same round and it's in progress
    if session.game.current_round == round_num and session.game.round_in_progress:
        # Time up - No winner
        answer = session.game.resolve_round()
        full_image = session.game.get_full_image()
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Time's Up!</b>\n\n"
                 f"The answer was: <b>{answer}</b>",
            parse_mode="HTML"
        )
        
        # Send full image on timeout too? Usually good to show it.
        try:
            with open(full_image, 'rb') as f:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=f"✅ <b>Full Picture: {answer}</b>",
                    parse_mode="HTML"
                )
        except Exception:
            pass
        
        # Check game over or start next round
async def word_connect_hint_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Reveal a hint after 30 seconds if the round is still in progress."""
    try:
        await asyncio.sleep(30)
        
        session = game_manager.get_game(chat_id)
        if not session or session.game_code != "11" or session.state != GameState.IN_PROGRESS:
            return
        
        if session.game.current_round != round_num or not session.game.round_in_progress:
            return
            
        hint_result = session.game.reveal_letter_hint()
        if hint_result:
            progress = session.game.get_round_progress()
            letters = session.game.current_letters
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"💡 <b>Hint!</b> A letter has been revealed:\n\n"
                     f"Letters: <b>{' '.join(letters).upper()}</b>\n\n"
                     f"{progress}",
                parse_mode="HTML"
            )
            
            if session.game.is_round_finished():
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎊 <b>Round Completed!</b> 🎊\nAll words found!",
                    parse_mode="HTML"
                )
                
                if session.game.is_game_over():
                    await end_game(chat_id, context, session)
                else:
                    await start_word_connect_round(chat_id, context)
            else:
                # Schedule another hint
                track_game_task(chat_id, asyncio.create_task(word_connect_hint_timeout(chat_id, context, round_num)))
    except asyncio.CancelledError:
        pass


async def start_word_connect_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Word Connect game."""
    await start_word_connect_round(chat_id, context)


async def start_word_connect_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Word Connect."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "11":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    result = session.game.start_new_round()
    if not result:
        # Game Over
        await end_game(chat_id, context, session)
        return

    letters = result["letters"]
    round_num = result["round"]
    progress = session.game.get_round_progress()
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"🔠 <b>Word Connect!</b>\n"
             f"Round {round_num}/{session.game.rounds_limit}\n\n"
             f"Letters: <b>{' '.join(letters).upper()}</b>\n\n"
             f"{progress}\n\n"
             f"Swipe (type) the words to form them!",
        parse_mode="HTML"
    )

    # Start hint timer
    track_game_task(chat_id, asyncio.create_task(word_connect_hint_timeout(chat_id, context, round_num)))


async def start_wdym_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the What You Meme game."""
    await start_wdym_round(chat_id, context)


def get_meme_cache() -> Dict[str, str]:
    """Synchronously read the meme cache from disk."""
    cache_path = os.path.join(os.path.dirname(__file__), "meme_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading meme cache: {e}")
    return {}


async def ensure_memes_cached(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, str]:
    """Ensure all memes are uploaded to Telegram and their file_ids are cached in background."""
    async with meme_cache_lock:
        cache_path = os.path.join(os.path.dirname(__file__), "meme_cache.json")
        meme_dir = os.path.join(os.path.dirname(__file__), "wdym", "memes")
        
        cache = get_meme_cache()

        if not os.path.exists(meme_dir):
            return cache

        memes = sorted([f for f in os.listdir(meme_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
        updated = False
        
        # Use the testing group as a storage chat
        storage_chat_id = ALLOWED_CHAT_IDS[1]
        
        for meme in memes:
            if meme not in cache:
                try:
                    meme_path = os.path.join(meme_dir, meme)
                    with open(meme_path, 'rb') as f:
                        msg = await context.bot.send_photo(
                            chat_id=storage_chat_id,
                            photo=f,
                            caption=f"Caching meme: {meme}",
                            disable_notification=True
                        )
                        file_id = msg.photo[-1].file_id
                        cache[meme] = file_id
                        updated = True
                        with open(cache_path, 'w') as sf:
                            json.dump(cache, sf, indent=2)
                        await asyncio.sleep(2)
                except Exception as e:
                    if "Flood control exceeded" in str(e):
                        retry_after = 30
                        try:
                            import re
                            match = re.search(r"Retry in (\d+) seconds", str(e))
                            if match: retry_after = int(match.group(1)) + 1
                        except: pass
                        logger.warning(f"Rate limited during caching. Sleeping for {retry_after}s...")
                        await asyncio.sleep(retry_after)
                    else:
                        logger.error(f"Error caching meme {meme}: {e}")
                        await asyncio.sleep(1)

        if updated:
            try:
                with open(cache_path, 'w') as f:
                    json.dump(cache, f, indent=2)
                logger.info("Meme cache fully updated.")
            except Exception as e:
                logger.error(f"Error saving meme cache: {e}")
                
        return cache

async def start_wdym_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of What You Meme."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "12":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    result = session.game.start_new_round()
    if not result:
        # Game Over or not enough players
        if len(session.game.players) < 2:
             await context.bot.send_message(
                chat_id=chat_id,
                text="⚠️ Not enough players to continue! Need at least 2 players.",
                parse_mode="HTML"
            )
             await end_game(chat_id, context, session)
        else:
            await end_game(chat_id, context, session)
        return

    question = result["question"]
    round_num = result["round"]
    
    keyboard = [
        [InlineKeyboardButton("What you meme", switch_inline_query_current_chat="meme ")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Round {round_num}\n"
             f"<b>{question}</b>",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    # Start timeout task (45 seconds total)
    track_game_task(chat_id, asyncio.create_task(wdym_timeout_manager(chat_id, context, round_num)))


async def wdym_timeout_manager(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Manage 30s reminder and 45s force-skip for WDYM."""
    # 30 second reminder
    await asyncio.sleep(30)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "12" or session.game.current_round != round_num or not session.game.round_in_progress:
        return
        
    pending = session.game.get_pending_players()
    if pending:
        mention_list = []
        for uid in pending:
            name = session.game.players[uid]
            mention_list.append(f"<a href=\"tg://user?id={uid}\">{name}</a>")
            
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>Hurry up!</b> 15 seconds left!\n\n"
                 f"Still waiting for: {', '.join(mention_list)}",
            parse_mode="HTML"
        )
        
        # 15 more seconds total (45s)
        await asyncio.sleep(15)
        
        # Check again
        session = game_manager.get_game(chat_id)
        if not session or session.game_code != "12" or session.game.current_round != round_num or not session.game.round_in_progress:
            return
            
        pending = session.game.get_pending_players()
        if pending:
            await context.bot.send_message(
                chat_id=chat_id,
                text="⌛️ <b>Time's up!</b> Moving to the next question...",
                parse_mode="HTML"
            )
            session.game.round_in_progress = False
            if session.game.is_game_over():
                await end_game(chat_id, context, session)
            else:
                await start_wdym_round(chat_id, context)


async def handle_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect meme submissions by watching for photos in WDYM games."""
    message = update.effective_message
    if not message or not message.photo:
        return
        
    user = update.effective_user
    chat = update.effective_chat
    
    session = game_manager.get_game(chat.id)
    if not session:
        return

    # Check for Silent Game violation first
    if session.game_code == "14":
        if await process_silent_game_content(update, context, session):
            return

    if session.game_code != "12" or not session.game.round_in_progress:
        return
        
    # If the user is a player, any photo they send is a submission
    if user.id in session.game.players:
        file_id = message.photo[-1].file_id
        success = session.game.submit_meme(user.id, file_id)
        if success:
            # Check if all players have submitted
            pending = session.game.get_pending_players()
            if not pending:
                session.game.round_in_progress = False
                if session.game.is_game_over():
                    await end_game(chat.id, context, session)
                else:
                    await start_wdym_round(chat.id, context)


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries for memes."""
    query = update.inline_query.query
    if not query.startswith("meme"):
        return

    offset = int(update.inline_query.offset) if update.inline_query.offset else 0

    # Get cache immediately (non-blocking)
    cache = get_meme_cache()
    # Trigger background update check
    asyncio.create_task(ensure_memes_cached(context))
    
    # Find session to get current prompt
    user = update.inline_query.from_user
    session = None
    for s in game_manager.active_games.values():
        if user.id in s.players and s.game_code == "12":
            session = s
            break
    
    prompt = session.game.current_question if (session and session.game.round_in_progress) else "Meme time!"
    
    cache_items = sorted(cache.items())
    results = []
    
    # Slice the items based on offset
    end_idx = min(offset + 50, len(cache_items))
    for i in range(offset, end_idx):
        meme, file_id = cache_items[i]
        results.append(
            InlineQueryResultCachedPhoto(
                id=f"wdym_{meme}", 
                photo_file_id=file_id,
                title=f"Meme {i+1}",
                caption=f"🃏 <b>{prompt}</b>",
                parse_mode="HTML"
            )
        )
    
    if not results and offset == 0:
        results.append(
            InlineQueryResultArticle(
                id="caching",
                title="Memes are being cached...",
                description="Please wait a moment and try again.",
                input_message_content=InputTextMessageContent("Bot is still processing memes. Please wait.")
            )
        )
    
    try:
        next_offset = str(offset + 50) if offset + 50 < len(cache_items) else ""
        await update.inline_query.answer(results, cache_time=5, is_personal=True, next_offset=next_offset)
    except (NetworkError, TimedOut, TelegramError) as e:
        logger.warning(f"Failed to answer inline query: {e}")
    except Exception as e:
        logger.error(f"Error answering inline query: {e}")


async def chosen_inline_result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track when a user picks a meme from inline query."""
    result = update.chosen_inline_result
    result_id = result.result_id
    if not result_id.startswith("wdym_"):
        return
        
    user = result.from_user
    meme_filename = result_id.replace("wdym_", "")
    
    # Get file_id from cache to keep tracking consistent if needed, 
    # but the game logic just needs to know who submitted.
    # We can use filename as the unique identifier for the submission.
    
    # We don't have chat_id here, but we can find the session by user
    session = None
    for chat_id, s in game_manager.active_games.items():
        if user.id in s.players and s.game_code == "12":
            session = s
            break
            
    if not session or not session.game.round_in_progress:
        return
        
    success = session.game.submit_meme(user.id, meme_filename)
    if success:
        # Check if everyone submitted
        pending = session.game.get_pending_players()
        if not pending:
            # All done!
            session.game.round_in_progress = False
            if session.game.is_game_over():
                await end_game(session.chat_id, context, session)
            else:
                await start_wdym_round(session.chat_id, context)


async def start_ts_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session) -> None:
    """Start the Taylor Swift vs Shakespeare game."""
    await start_ts_round(chat_id, context)

async def start_20q_game(chat_id: int, context: ContextTypes.DEFAULT_TYPE, session: GameSession) -> None:
    """Start the 20 Questions game."""
    session.game.round_in_progress = False
    await context.bot.send_message(
        chat_id=chat_id,
        text="🕵️‍♂️ <b>20 Questions Started!</b>\n\n"
             "Rules:\n"
             "1. One player is the <b>Host</b> and gets a secret word.\n"
             "2. Everyone else asks Yes/No questions.\n"
             "3. Questions <b>must end with a ?</b> to be counted.\n"
             "4. You have <b>20 Questions</b> or <b>5 Minutes</b> to guess the word.\n"
             "5. If you guess it, YOU become the Host!\n\n"
             "Starting first round...",
        parse_mode="HTML"
    )
    # Start first round
    await start_20q_round(chat_id, context)


async def start_20q_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE, forced_host_id: Optional[int] = None) -> None:
    """Start a new round of 20 Questions."""
    session = game_manager.get_game(chat_id)
    if not session or not isinstance(session.game, TwentyQuestionsGame):
        return

    try:
        # Start logical round
        if not session.game.start_new_round(forced_host_id):
            await context.bot.send_message(chat_id=chat_id, text="Not enough players to continue!")
            session.end_game()
            game_manager.remove_game(chat_id)
            return

        host_id = session.game.host_id
        host_name = session.game.get_host_name()
        
        # Create keyboard
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤐 View Secret Word (Host Only)", callback_data="view_secret_word")]
        ])

        # Using standard HTML
        message_text = (
            f"🔴 <b>Round {session.game.current_round}</b>\n\n"
            f"👤 <b>Host:</b> <a href=\"tg://user?id={host_id}\">{html.escape(host_name)}</a>\n"
            f"❓ <b>Questions Remaining:</b> 20\n"
            f"⏱ <b>Time Limit:</b> 5 Minutes\n\n"
            f"Host, click below to see your word!"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
        # 5 Minute Timeout
        task = asyncio.create_task(twenty_questions_timeout(chat_id, context, session.game.current_round))
        track_game_task(chat_id, task)
        
    except Exception as e:
        logger.error(f"Error in start_20q_round: {e}", exc_info=True)
        await context.bot.send_message(chat_id=chat_id, text="⚠️ Error starting round. check logs.")


async def twenty_questions_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int):
    """Handle 5 minute timeout for 20 questions round."""
    try:
        await asyncio.sleep(300) # 5 minutes
        
        session = game_manager.get_game(chat_id)
        if session and session.game_code == "15" and session.game.current_round == round_num and session.game.round_in_progress:
            # Time up! Host wins.
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⏰ <b>Time's Up!</b>\n\n"
                     f"The word was: <b>{session.game.current_word}</b>\n"
                     f"Host gets a point!",
                parse_mode="HTML"
            )
            session.game.host_wins_round()
            
            if session.game.is_game_over():
                await end_game(chat_id, context, session)
            else:
                await start_20q_round(chat_id, context)
                
    except asyncio.CancelledError:
        pass


async def handle_20q_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle View Secret Word callback."""
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat
    
    session = game_manager.get_game(chat.id)
    if not session or session.game_code != "15":
        await query.answer("Game not active.")
        return

    if user.id != session.game.host_id:
        await query.answer("❌ You are not the Host!", show_alert=True)
    else:
        word = session.game.current_word
        await query.answer(f"🤫 Secret Word: {word}", show_alert=True)


async def start_ts_round(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a new round of Taylor Swift vs Shakespeare."""
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "13":
        return

    # Delay slightly
    await asyncio.sleep(2)
    
    quote, round_num = session.game.start_new_round()
    if not quote:
        await end_game(chat_id, context, session)
        return

    keyboard = [
        [
            InlineKeyboardButton("Taylor Swift", callback_data=f"ts_vote_Taylor Swift"),
            InlineKeyboardButton("Shakespeare", callback_data=f"ts_vote_Shakespeare")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📜 <b>Taylor Swift Or Shakespeare?</b>\n"
             f"Round {round_num}/{session.game.rounds_limit}\n\n"
             f"<i>\"{quote}\"</i>\n\n"
             f"Choose your answer! (30s)",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

    # Start timeout task (30 seconds)
    track_game_task(chat_id, asyncio.create_task(ts_round_timeout(chat_id, context, round_num)))

async def ts_round_timeout(chat_id: int, context: ContextTypes.DEFAULT_TYPE, round_num: int) -> None:
    """Handle timeout for Taylor Swift vs Shakespeare round."""
    await asyncio.sleep(30)
    
    session = game_manager.get_game(chat_id)
    if not session or session.game_code != "13" or not session.game.round_in_progress:
        return

    if session.game.current_round != round_num:
        return

    result = session.game.resolve_round()
    if not result:
        return

    correct_author = result["correct_author"]
    winners_ids = result["winners"]
    quote = result["quote"]

    winner_mentions = []
    for uid in winners_ids:
        name = session.game.players.get(uid, "Player")
        winner_mentions.append(f"<a href=\"tg://user?id={uid}\">{name}</a>")

    if winner_mentions:
        winners_text = f"✅ <b>Correct!</b> It was <b>{correct_author}</b>!\n\n" \
                       f"🏆 Winners this round: {', '.join(winner_mentions)}"
    else:
        winners_text = f"❌ <b>Too slow!</b> Nobody guessed right.\n" \
                       f"The correct author was: <b>{correct_author}</b>"

    await context.bot.send_message(
        chat_id=chat_id,
        text=winners_text,
        parse_mode="HTML"
    )

    if session.game.is_game_over():
        await end_game(chat_id, context, session)
    else:
        await start_ts_round(chat_id, context)

async def handle_ts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle voting callback queries for Taylor vs Shakespeare."""
    query = update.callback_query
    user = query.from_user
    chat = update.effective_chat
    
    author = query.data.split("_")[2]
    
    session = game_manager.get_game(chat.id)
    if not session or session.game_code != "13" or not session.game.round_in_progress:
        await query.answer("Game not active.")
        return

    # Just record the vote
    try:
        if session.game.record_vote(user.id, author):
            await query.answer(f"Voted for {author}!")
        else:
            await query.answer("Couldn't record vote.")
    except (NetworkError, TimedOut, TelegramError) as e:
        logger.warning(f"Failed to answer TS callback query: {e}")


async def error_handler(update: Optional[Update], context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    if isinstance(context.error, (NetworkError, TimedOut)):
        logger.warning(f'Network error: {context.error}')
        return
    if isinstance(context.error, Forbidden):
        logger.warning(f'Bot was blocked or kicked: {context.error}')
        return
        
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)


async def post_init(application: Application) -> None:
    """Explicitly initialize the bot."""
    await application.bot.initialize()
    bot_info = await application.bot.get_me()
    logger.info(f"Bot initialized: {bot_info.id} (@{bot_info.username})")
    
    # Trigger background caching on startup with proper context
    # Use a dummy context since ensure_memes_cached only needs context.bot
    from telegram.ext import CallbackContext
    dummy_context = CallbackContext(application)
    asyncio.create_task(ensure_memes_cached(dummy_context))


def main() -> None:
    """Start the bot."""
    # Get bot token from environment
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("No BOT_TOKEN found in environment variables!")
        return
    
    # Create application
    application = Application.builder().token(token).post_init(post_init).build()
    
    # Add global error handler
    application.add_error_handler(error_handler)
    
    # Add handlers
    application.add_handler(ChatMemberHandler(my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("join", join_command))
    application.add_handler(CommandHandler("leave", leave_command))
    application.add_handler(CommandHandler("quit", quit_command))
    application.add_handler(CommandHandler("vote", vote_command))
    application.add_handler(CommandHandler("extend", extend_command))
    application.add_handler(CallbackQueryHandler(handle_vote_callback, pattern="^vote_"))
    application.add_handler(CallbackQueryHandler(handle_ts_callback, pattern="^ts_vote_"))
    application.add_handler(CallbackQueryHandler(handle_quit_vote_callback, pattern="^vote_quit_game$"))
    application.add_handler(CallbackQueryHandler(handle_20q_callback, pattern="^view_secret_word$"))
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.add_handler(ChosenInlineResultHandler(chosen_inline_result_handler))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Add handler for all other content types (stickers, voice, etc.) for games like Silent Game
    # We use negation to catch everything that isn't already handled above (TEXT and PHOTO)
    application.add_handler(MessageHandler(~filters.TEXT & ~filters.PHOTO & ~filters.COMMAND, handle_misc_content))

    
    # Start the bot
    logger.info("Bot starting...")

    # Set up Flask server for health checks
    app = Flask(__name__)

    @app.route('/')
    def health_check():
        return "Bot is running!", 200

    def run_flask():
        # Use PORT environment variable from Render, default to 8080
        port = int(os.environ.get("PORT", 8080))
        app.run(host="0.0.0.0", port=port)

    # Run Flask in a separate daemon thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"Health check server started on port {os.environ.get('PORT', 8080)}")

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
