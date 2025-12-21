import random
from typing import List, Dict, Optional


class WordUnscrambleGame:
    """Manages a single word unscramble game with multiple rounds."""
    
    # Built-in word list for the game
    WORD_LIST = [
  "shore",
  "brisk",
  "flint",
  "cargo",
  "sweep",
  "orbit",
  "pulse",
  "vivid",
  "crest",
  "hollow",
  "ripple",
  "anchor",
  "sector",
  "stride",
  "hazard",
  "marrow",
  "cliff",
  "forge",
  "glide",
  "spark",
  "prism",
  "grain",
  "suite",
  "morph",
  "trace",
  "quilt",
  "crane",
  "woven",
  "storm",
  "ember",
  "auras",
  "fable",
  "tamed",
  "linen",
  "delta",
  "spice",
  "crown",
  "stark",
  "flora",
  "oxide",
  "plush",
  "prime",
  "widen",
  "slope",
  "realm",
  "merge",
  "alive",
  "chant",
  "drape",
  "forge",
  "slant",
  "temple",
  "vacant",
  "mantle",
  "shiver",
  "wander",
  "strike",
  "cipher"
]
    
    def __init__(self, total_rounds: int = 10):
        """Initialize a new game.
        
        Args:
            total_rounds: Number of rounds to play (default: 10)
        """
        self.total_rounds = total_rounds
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.current_word: Optional[str] = None
        self.current_scrambled: Optional[str] = None
        self.used_words: List[str] = []
        
    def add_player(self, user_id: int) -> None:
        """Add a player to the game.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id not in self.scores:
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id in self.scores:
            del self.scores[user_id]
            
    def scramble_word(self, word: str) -> str:
        """Scramble a word ensuring it's different from the original.
        
        Args:
            word: The word to scramble
            
        Returns:
            Scrambled version of the word
        """
        word_list = list(word)
        scrambled = word_list.copy()
        
        # Keep shuffling until we get a different arrangement
        # (or max 50 attempts for very short words)
        attempts = 0
        while ''.join(scrambled) == word and attempts < 50:
            random.shuffle(scrambled)
            attempts += 1
            
        return ''.join(scrambled)
    
    def start_new_round(self) -> tuple[str, int]:
        """Start a new round with a random word.
        
        Returns:
            Tuple of (scrambled_word, round_number)
        """
        self.current_round += 1
        
        # Select a random word that hasn't been used
        available_words = [w for w in self.WORD_LIST if w not in self.used_words]
        if not available_words:
            # Reset used words if we've gone through all
            self.used_words = []
            available_words = self.WORD_LIST
            
        self.current_word = random.choice(available_words)
        self.used_words.append(self.current_word)
        self.current_scrambled = self.scramble_word(self.current_word)
        
        return self.current_scrambled, self.current_round
    
    def check_answer(self, answer: str, user_id: int) -> bool:
        """Check if an answer is correct and award points.
        
        Args:
            answer: The user's answer
            user_id: Telegram user ID
            
        Returns:
            True if answer is correct, False otherwise
        """
        if not self.current_word:
            return False
            
        if answer.lower().strip() == self.current_word.lower():
            # Award point to the user
            if user_id in self.scores:
                self.scores[user_id] += 1
            else:
                self.scores[user_id] = 1
            return True
        return False
    
    def get_current_word(self) -> Optional[str]:
        """Get the current unscrambled word (for revealing answer)."""
        return self.current_word
    
    def is_game_over(self) -> bool:
        """Check if the game has ended."""
        return self.current_round >= self.total_rounds
    
    def get_scoreboard(self) -> List[tuple[int, int]]:
        """Get sorted scoreboard.
        
        Returns:
            List of (user_id, score) tuples sorted by score (descending)
        """
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)
    
    def get_winners(self) -> List[int]:
        """Get list of user IDs with the highest score.
        
        Returns:
            List of user_ids who have the winning score
        """
        if not self.scores:
            return []
            
        scoreboard = self.get_scoreboard()
        if not scoreboard:
            return []
            
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]
    
    def get_player_count(self) -> int:
        """Get number of players in the game."""
        return len(self.scores)
