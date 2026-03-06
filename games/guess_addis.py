import os
import random
import re
import json
from typing import List, Dict, Optional, Tuple

class GuessAddisGame:
    """Manages a 'Guess Addis' game where players identify local areas in Addis Ababa."""

    def __init__(self, rounds_limit: int = 20, used_images: Optional[List[str]] = None):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
            used_images: Optional list of already used image paths to avoid repetition.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.data: List[Dict] = [] # list of {image: str, answers: List[str]}
        self.used_images = used_images if used_images is not None else []
        
        # Current round state
        self.current_image_path: Optional[str] = None
        self.current_answers: List[str] = []
        self.waiting_for_answer: bool = False

        self._load_data()

    def _load_data(self):
        """Load data from the guess_addis.json file."""
        data_path = os.path.join(os.path.dirname(__file__), "guess_addis.json")
        image_dir = os.path.join(os.path.dirname(__file__), "guess addis")
        
        try:
            if os.path.exists(data_path):
                with open(data_path, 'r', encoding='utf-8') as f:
                    raw_data = json.load(f)
                    for item in raw_data:
                        image_path = os.path.join(image_dir, item['image'])
                        if os.path.exists(image_path):
                            self.data.append({
                                'path': image_path,
                                'answers': item['answers']
                            })
            else:
                # Fallback to loading directly from directory if JSON missing
                if os.path.exists(image_dir):
                    for filename in os.listdir(image_dir):
                        if filename.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
                            clean_name = os.path.splitext(filename)[0]
                            self.data.append({
                                'path': os.path.join(image_dir, filename),
                                'answers': [clean_name]
                            })
        except Exception as e:
            print(f"Error loading Guess Addis data: {e}")

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
        # Remove common English special chars but keep Amharic
        # This regex removes non-alphanumeric chars but keeps most unicode (including Amharic)
        # However, for Amharic, we usually just want to strip whitespace.
        normalized = text.lower().strip()
        # For English/Alphanumeric parts, we can be more aggressive
        normalized = re.sub(r'[^a-zA-Z0-9\u1200-\u137F]', '', normalized)
        return normalized

    def start_game(self) -> None:
        """Start the game."""
        self.current_round = 0

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a NEW image.
        
        Returns:
            Tuple of (image_path, round_number) or None if game over/error.
        """
        if self.is_game_over() or not self.players:
            return None

        self.current_round += 1
        
        # Pick a random image not used yet
        available_images = [d for d in self.data if d['path'] not in self.used_images]
        if not available_images:
            self.used_images.clear()
            available_images = self.data
        
        if not available_images:
            return None 

        selected = random.choice(available_images)
        self.used_images.append(selected['path'])
        self.current_image_path = selected['path']
        self.current_answers = selected['answers']
        self.waiting_for_answer = True
        
        return self.current_image_path, self.current_round

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if the answer is correct."""
        if not self.waiting_for_answer:
            return False
            
        if not self.current_answers:
            return False

        normalized_input = self._normalize_answer(answer)
        
        for correct_answer in self.current_answers:
            if normalized_input == self._normalize_answer(correct_answer):
                self.scores[user_id] = self.scores.get(user_id, 0) + 1
                self.waiting_for_answer = False
                return True
        
        return False
    
    def resolve_round(self, correct: bool = False) -> str:
        """End current round manually. Returns the primary answer."""
        self.waiting_for_answer = False
        return self.current_answers[0] if self.current_answers else "Unknown"

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
