import os
import random
import re
from typing import List, Dict, Optional, Tuple

class GuessTheBookGame:
    """Manages a guess the book game where players identify books from their cover images."""

    def __init__(self, rounds_limit: int = 20):
        """Initialize the game.
        
        Args:
            rounds_limit: Maximum number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.books: List[Tuple[str, str]] = [] # list of (base_image_path, answer_key)
        self.used_books: List[str] = []
        
        # Current round state
        self.current_book_path: Optional[str] = None
        self.current_answer: Optional[str] = None
        self.waiting_for_answer: bool = False

        self._load_books()

    def _load_books(self):
        """Load book files from the book directory."""
        book_dir = os.path.join(os.path.dirname(__file__), "book")
        if not os.path.exists(book_dir):
            return

        # Map base names to their images
        # We only want the ones WITHOUT -2 as the primary guess target
        files = os.listdir(book_dir)
        for filename in files:
            if filename.lower().endswith(('.webp', '.png', '.jpg', '.jpeg')):
                if not filename.lower().endswith('-2.jpg') and not filename.lower().endswith('-2.png') and not filename.lower().endswith('-2.jpeg') and not filename.lower().endswith('-2.webp'):
                    clean_name = os.path.splitext(filename)[0]
                    self.books.append((os.path.join(book_dir, filename), clean_name))

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
        self.used_books = []

    def start_new_round(self) -> Optional[Tuple[str, int]]:
        """Start a new round with a NEW book.
        
        Returns:
            Tuple of (book_path, round_number) or None if game over/error.
        """
        if self.is_game_over() or not self.players:
            return None

        self.current_round += 1
        
        # Pick a random book not used yet
        available_books = [b for b in self.books if b[0] not in self.used_books]
        if not available_books:
            self.used_books = []
            available_books = self.books
        
        if not available_books:
            return None 

        book_path, answer = random.choice(available_books)
        self.used_books.append(book_path)
        self.current_book_path = book_path
        self.current_answer = answer
        self.waiting_for_answer = True
        
        return book_path, self.current_round

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
    
    def get_reveal_image(self) -> Optional[str]:
        """Get the path to the reveal image (with -2 suffix)."""
        if not self.current_book_path:
            return None
        
        base, ext = os.path.splitext(self.current_book_path)
        reveal_path = f"{base}-2{ext}"
        
        # Check if it exists with exact case
        if os.path.exists(reveal_path):
            return reveal_path
            
        # Try lowercase -2 if original was different case? 
        # Actually let's just search the directory for a matching start
        book_dir = os.path.dirname(self.current_book_path)
        base_name = os.path.basename(base).lower()
        
        for filename in os.listdir(book_dir):
            if filename.lower().startswith(base_name) and "-2" in filename.lower():
                return os.path.join(book_dir, filename)
                
        return None

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
