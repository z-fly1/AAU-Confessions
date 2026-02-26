import os
import random
import re
from typing import List, Dict, Optional, Tuple

class GuessTheLogoGame:
    """Manages a guess the logo game where players take turns identifying logos."""

    def __init__(self, rounds_limit: int = 20):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.logos: List[Tuple[str, str]] = [] # list of (filename, answer_key)
        self.used_logos: List[str] = []
        
        # Round Robin Logic
        self.player_ids: List[int] = [] 
        self.turn_index: int = 0
        
        # Current round state
        self.current_player_id: Optional[int] = None
        self.current_logo_path: Optional[str] = None
        self.current_answer: Optional[str] = None
        self.waiting_for_answer: bool = False

        self._load_logos()

    def _load_logos(self):
        """Load logo files from the logos directory."""
        logo_dir = os.path.join(os.path.dirname(__file__), "logos")
        if not os.path.exists(logo_dir):
            return

        for filename in os.listdir(logo_dir):
            if filename.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
                clean_name = os.path.splitext(filename)[0]
                self.logos.append((os.path.join(logo_dir, filename), clean_name))

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
        # Re-sync player ids if needed? 
        # For simplicity, we regenerate player_ids only on start or when needed would be better but we need consistent order.
        # If active player leaves, handle it? 
        if user_id in self.player_ids:
            # If the current player left, we need to adjust index
            idx = self.player_ids.index(user_id)
            self.player_ids.remove(user_id)
            if idx < self.turn_index:
                self.turn_index -= 1
            if self.turn_index >= len(self.player_ids):
                self.turn_index = 0

    def _normalize_answer(self, text: str) -> str:
        """Normalize answer for comparison (remove special chars, lowercase)."""
        return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

    def start_game(self) -> None:
        """Start the game."""
        self.current_round = 0
        self.used_logos = []
        
        # Initialize turn order
        self.player_ids = list(self.players.keys())
        random.shuffle(self.player_ids)
        self.turn_index = 0

    def start_new_round(self) -> Optional[Tuple[str, int, str]]:
        """Start a new round with a NEW logo.
        
        Returns:
            Tuple of (logo_path, player_id, player_name) or None if game over/error.
        """
        if self.is_game_over() or not self.players:
            return None

        self.current_round += 1
        
        # Just pick next in turn
        if not self.player_ids:
             self.player_ids = list(self.players.keys())
             random.shuffle(self.player_ids)
             
        # Advance turn for new start logic? 
        # User said "next person in turn" gets the question.
        # So we just take current turn index.
        # If we just finished a round successfully, let's bump the turn index for variety
        # But wait, if we got it right, maybe we go again? No, "randomly ask the next person in turn".
        
        self.turn_index = (self.turn_index + 1) % len(self.player_ids)
        self.current_player_id = self.player_ids[self.turn_index]
        
        # Pick a random logo not used yet
        available_logos = [l for l in self.logos if l[0] not in self.used_logos]
        if not available_logos:
            self.used_logos = []
            available_logos = self.logos
        
        if not available_logos:
            return None 

        logo_path, answer = random.choice(available_logos)
        self.used_logos.append(logo_path)
        self.current_logo_path = logo_path
        self.current_answer = answer
        self.waiting_for_answer = True
        
        return logo_path, self.current_player_id, self.players[self.current_player_id]

    def pass_turn(self) -> Optional[Tuple[int, str]]:
        """Pass turn to next player (same logo).
        
        Returns:
            Tuple of (player_id, player_name) or None.
        """
        if not self.player_ids:
            return None
            
        self.turn_index = (self.turn_index + 1) % len(self.player_ids)
        self.current_player_id = self.player_ids[self.turn_index]
        self.waiting_for_answer = True # Still waiting
        
        return self.current_player_id, self.players[self.current_player_id]

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if the answer is correct."""
        if not self.waiting_for_answer:
            return False
            
        if user_id != self.current_player_id:
            return False
            
        if not self.current_answer:
            return False

        normalized_input = self._normalize_answer(answer)
        normalized_correct = self._normalize_answer(self.current_answer)
        
        if normalized_input == normalized_correct:
            self.scores[user_id] += 1
            self.waiting_for_answer = False
            return True
        
        return False
    
    def resolve_round(self, correct: bool = False) -> str:
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
