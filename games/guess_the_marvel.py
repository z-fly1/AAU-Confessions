import os
import random
import re
from typing import List, Dict, Optional, Tuple

class GuessMarvelGame:
    """Manages a guess the marvel character game where players identify characters from images."""

    def __init__(self, rounds_limit: int = 15):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.characters: List[Tuple[str, str]] = [] # list of (image_path, answer_key)
        self.used_characters: List[str] = []
        
        # Current round state
        self.current_image_path: Optional[str] = None
        self.current_answer: Optional[str] = None
        self.waiting_for_answer: bool = False

        self._load_characters()

    def _load_characters(self):
        """Load character images from the marvel directory."""
        marvel_dir = os.path.join(os.path.dirname(__file__), "marvel")
        if not os.path.exists(marvel_dir):
            return

        for filename in os.listdir(marvel_dir):
            if filename.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
                clean_name = os.path.splitext(filename)[0]
                self.characters.append((os.path.join(marvel_dir, filename), clean_name))

    def add_player(self, user_id: int, display_name: str) -> None:
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

    def _normalize_answer(self, text: str) -> str:
        """Normalize answer for comparison (remove special chars, lowercase)."""
        return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

    def start_game(self) -> None:
        """Start the game."""
        self.current_round = 0
        self.used_characters = []

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a random character.
        
        Returns:
            Tuple of (image_path, round_number) or None if game over/error.
        """
        if self.is_game_over() or not self.players:
            return None

        self.current_round += 1
        
        # Pick a random character not used yet
        available = [c for c in self.characters if c[0] not in self.used_characters]
        if not available:
            self.used_characters = []
            available = self.characters
        
        if not available:
            return None 

        image_path, answer = random.choice(available)
        self.used_characters.append(image_path)
        self.current_image_path = image_path
        self.current_answer = answer
        self.waiting_for_answer = True
        
        return image_path, self.current_round

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if the answer is correct."""
        if not self.waiting_for_answer:
            return False
            
        if not self.current_answer:
            return False

        normalized_input = self._normalize_answer(answer)
        normalized_correct = self._normalize_answer(self.current_answer)
        
        if normalized_input == normalized_correct:
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            self.waiting_for_answer = False
            return True
        
        return False
    
    def resolve_round(self) -> str:
        """End current round manually. Returns answer."""
        self.waiting_for_answer = False
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
        return len(self.players)
