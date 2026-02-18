import json
import os
import random
import re
from typing import List, Dict, Optional, Tuple


class GuessTheSongGame:
    """Manages a 'Guess the Song from the Intro' game with dual scoring (title + artist)."""

    def __init__(self, total_rounds: int = 15):
        """Initialize a new game.
        
        Args:
            total_rounds: Number of rounds to play (default: 15)
        """
        self.total_rounds = total_rounds
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.songs: List[Dict] = []
        self.used_song_indices: List[int] = []
        self.current_song: Optional[Dict] = None
        self.players: Dict[int, str] = {}  # user_id -> display_name
        self.round_in_progress: bool = False

        # Per-round state for dual guessing
        self.title_guessed: bool = False
        self.artist_guessed: bool = False
        self.title_guesser_id: Optional[int] = None
        self.artist_guesser_id: Optional[int] = None

        self._load_songs()

    def _load_songs(self):
        """Load songs from the songs.json file."""
        json_path = os.path.join(os.path.dirname(__file__), "intro", "songs.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                self.songs = json.load(f)
        except Exception as e:
            print(f"Error loading songs: {e}")
            self.songs = []

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game."""
        self.players[user_id] = display_name
        if user_id not in self.scores:
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.scores:
            del self.scores[user_id]

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison (remove special chars, lowercase)."""
        if not text:
            return ""
        return re.sub(r'[^a-z0-9]', '', text.lower())

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a random song.
        
        Returns:
            Tuple of (audio_path, round_number) or None if game over
        """
        if self.current_round >= self.total_rounds:
            return None

        self.current_round += 1

        if not self.songs:
            return None

        # Reset used songs if we've gone through all of them
        if len(self.used_song_indices) >= len(self.songs):
            self.used_song_indices = []

        # Find an unused song
        available_indices = [i for i in range(len(self.songs)) if i not in self.used_song_indices]
        idx = random.choice(available_indices)
        self.used_song_indices.append(idx)

        self.current_song = self.songs[idx]
        self.round_in_progress = True

        # Reset per-round state
        self.title_guessed = False
        self.artist_guessed = False
        self.title_guesser_id = None
        self.artist_guesser_id = None

        audio_path = os.path.join(os.path.dirname(__file__), "intro", self.current_song["audio_file"])
        return audio_path, self.current_round

    def check_title(self, user_id: int, text: str) -> bool:
        """Check if the text matches the song title.
        
        Args:
            user_id: Telegram user ID
            text: The user's guess
            
        Returns:
            True if title is correct and wasn't already guessed
        """
        if not self.current_song or user_id not in self.players:
            return False
        if self.title_guessed:
            return False

        norm_guess = self._normalize(text)
        norm_title = self._normalize(self.current_song["title"])

        if norm_guess == norm_title:
            self.title_guessed = True
            self.title_guesser_id = user_id
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            return True
        return False

    def check_artist(self, user_id: int, text: str) -> bool:
        """Check if the text matches the song artist.
        
        Args:
            user_id: Telegram user ID
            text: The user's guess
            
        Returns:
            True if artist is correct and wasn't already guessed
        """
        if not self.current_song or user_id not in self.players:
            return False
        if self.artist_guessed:
            return False
        
        artist = self.current_song.get("artist", "")
        if not artist:
            return False  # Artist not set yet

        norm_guess = self._normalize(text)
        norm_artist = self._normalize(artist)

        if norm_guess == norm_artist:
            self.artist_guessed = True
            self.artist_guesser_id = user_id
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            return True
        return False

    def is_round_complete(self) -> bool:
        """Check if both title and artist have been guessed (or artist is empty)."""
        artist = self.current_song.get("artist", "") if self.current_song else ""
        if not artist:
            # If artist not set, round is complete when title is guessed
            return self.title_guessed
        return self.title_guessed and self.artist_guessed

    def get_song_info(self) -> Optional[Dict]:
        """Get current song info including cover path.
        
        Returns:
            Dict with title, artist, cover_path or None
        """
        if not self.current_song:
            return None

        cover_path = os.path.join(
            os.path.dirname(__file__), "intro", "covers", self.current_song.get("cover_file", "")
        )
        return {
            "title": self.current_song["title"],
            "artist": self.current_song.get("artist", "Unknown"),
            "cover_path": cover_path,
        }

    def get_current_title(self) -> str:
        """Get the current song title."""
        if not self.current_song:
            return ""
        return self.current_song["title"]

    def get_current_artist(self) -> str:
        """Get the current song artist."""
        if not self.current_song:
            return ""
        return self.current_song.get("artist", "")

    def is_game_over(self) -> bool:
        """Check if the game has ended."""
        return self.current_round >= self.total_rounds

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        """Get sorted scoreboard."""
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        """Get list of user IDs with the highest score."""
        if not self.scores:
            return []
        scoreboard = self.get_scoreboard()
        if not scoreboard:
            return []
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]

    def get_player_count(self) -> int:
        """Get number of players in the game."""
        return len(self.players)
