import os
import random
import re
from typing import List, Dict, Optional, Tuple

class GuessTheFlagGame:
    """Manages a guess the flag game where players identify country flags."""

    def __init__(self, rounds_limit: int = 15):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.flags: List[Tuple[str, str]] = [] # list of (absolute_path, country_name)
        self.used_flags: List[str] = []
        
        # Current round state
        self.current_flag_path: Optional[str] = None
        self.current_answer: Optional[str] = None
        self.round_in_progress: bool = False

        self._load_flags()

    def _load_flags(self):
        """Load flag files from the flags directory."""
        flag_dir = os.path.join(os.path.dirname(__file__), "flags")
        if not os.path.exists(flag_dir):
            return

        for filename in os.listdir(flag_dir):
            if filename.lower().endswith('.webp'):
                # Handle cases like "bosnia & herzegovina.webp"
                clean_name = os.path.splitext(filename)[0]
                self.flags.append((os.path.join(flag_dir, filename), clean_name))

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game."""
        if user_id not in self.scores:
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.scores:
            del self.scores[user_id]

    def _normalize_answer(self, text: str) -> str:
        """Normalize answer for comparison (remove special chars, lowercase)."""
        # Keep only alphanumeric characters
        return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a random flag.
        
        Returns:
            Tuple of (flag_path, round_number) or None if game over.
        """
        if self.is_game_over():
            return None

        self.current_round += 1
        self.round_in_progress = True
        
        # Pick a random flag not used yet
        available_flags = [f for f in self.flags if f[0] not in self.used_flags]
        if not available_flags:
            # Reset used flags if we've gone through all
            self.used_flags = []
            available_flags = self.flags
        
        if not available_flags:
            return None 

        flag_path, answer = random.choice(available_flags)
        self.used_flags.append(flag_path)
        self.current_flag_path = flag_path
        self.current_answer = answer
        
        return flag_path, self.current_round

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if the answer is correct."""
        if not self.round_in_progress or not self.current_answer:
            return False

        normalized_input = self._normalize_answer(answer)
        normalized_correct = self._normalize_answer(self.current_answer)
        
        # Additional check for common variations if needed (e.g., "USA" vs "United States")
        # For now, stick to the filename
        if normalized_input == normalized_correct:
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            self.round_in_progress = False
            return True
        
        return False
    
    def get_current_answer(self) -> Optional[str]:
        """Get current answer."""
        return self.current_answer

    def resolve_round(self) -> str:
        """End current round manually (e.g., timeout). Returns answer."""
        self.round_in_progress = False
        return self.current_answer

    def is_game_over(self) -> bool:
        return self.current_round >= self.rounds_limit

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        if not self.scores:
            return []
        scoreboard = self.get_scoreboard()
        if not scoreboard:
            return []
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]

    def get_player_count(self) -> int:
        return len(self.scores)
