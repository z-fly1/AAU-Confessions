import os
import random
import re
from typing import List, Dict, Optional, Tuple

class GuessCharacterGame:
    """Manages a guess the person/character game from cropped images."""

    def __init__(self, rounds_limit: int = 15):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.images: List[Dict[str, str]] = [] # list of {"cropped": path, "full": path, "answer": name}
        self.used_images: List[int] = []
        
        # Current round state
        self.current_cropped_path: Optional[str] = None
        self.current_full_path: Optional[str] = None
        self.current_answer: Optional[str] = None
        self.round_in_progress: bool = False

        self._load_images()

    def _load_images(self):
        """Load image pairs from the guess image directory."""
        img_dir = os.path.join(os.path.dirname(__file__), "guess image")
        if not os.path.exists(img_dir):
            return

        # Pattern: name-1.ext (cropped) and name-2.ext (full)
        all_files = os.listdir(img_dir)
        pairs = {}

        for filename in all_files:
            if filename.startswith('.'): continue
            
            # Split by -1 or -2 before extension
            match = re.match(r"(.*)-([12])\.(jpg|png|jpeg|webp|JPG|PNG)", filename)
            if match:
                base_name = match.group(1).strip()
                suffix = match.group(2)
                
                if base_name not in pairs:
                    pairs[base_name] = {"cropped": None, "full": None}
                
                if suffix == "1":
                    pairs[base_name]["cropped"] = os.path.join(img_dir, filename)
                elif suffix == "2":
                    pairs[base_name]["full"] = os.path.join(img_dir, filename)

        for name, paths in pairs.items():
            if paths["cropped"] and paths["full"]:
                self.images.append({
                    "cropped": paths["cropped"],
                    "full": paths["full"],
                    "answer": name
                })

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
        return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a random character image.
        
        Returns:
            Tuple of (cropped_path, round_number) or None if game over.
        """
        if self.is_game_over():
            return None

        self.current_round += 1
        self.round_in_progress = True
        
        available_indices = [i for i in range(len(self.images)) if i not in self.used_images]
        if not available_indices:
            self.used_images = []
            available_indices = list(range(len(self.images)))
        
        if not available_indices:
            return None 

        idx = random.choice(available_indices)
        self.used_images.append(idx)
        
        round_data = self.images[idx]
        self.current_cropped_path = round_data["cropped"]
        self.current_full_path = round_data["full"]
        self.current_answer = round_data["answer"]
        
        return self.current_cropped_path, self.current_round

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if the answer is correct."""
        if not self.round_in_progress or not self.current_answer:
            return False

        normalized_input = self._normalize_answer(answer)
        normalized_correct = self._normalize_answer(self.current_answer)
        
        if normalized_input == normalized_correct:
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            self.round_in_progress = False
            return True
        
        return False
    
    def get_current_answer(self) -> Optional[str]:
        return self.current_answer

    def get_full_image(self) -> Optional[str]:
        return self.current_full_path

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
