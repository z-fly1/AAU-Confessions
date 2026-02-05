import random
import json
import os
from typing import List, Dict, Optional, Tuple

class WordConnectGame:
    """Manages a single Word Connect game where players find words from given letters."""
    
    def __init__(self, rounds_limit: int = 10):
        """Initialize the game.
        
        Args:
            rounds_limit: Number of rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> total_score
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.levels: List[Dict] = []
        self.used_levels: List[int] = []
        
        # Current round state
        self.current_letters: List[str] = []
        self.target_words: List[str] = []
        self.found_words: Dict[str, Optional[int]] = {} # word -> user_id (who found it)
        self.revealed_hints: Dict[str, Set[int]] = {} # word -> set of indices of revealed letters
        self.round_in_progress = False
        
        self._load_levels()

    def _load_levels(self):
        """Load levels from the json file."""
        json_path = os.path.join(os.path.dirname(__file__), "word_connect.json")
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r') as f:
                    self.levels = json.load(f)
            else:
                # Fallback levels if file not found
                self.levels = [
                    {"letters": ["A", "R", "T"], "words": ["art", "rat", "at"]},
                    {"letters": ["D", "O", "G"], "words": ["dog", "god", "do", "go"]}
                ]
        except Exception as e:
            print(f"Error loading levels: {e}")
            self.levels = [{"letters": ["A", "R", "T"], "words": ["art", "rat", "at"]}]

    def start_new_round(self) -> Optional[Dict]:
        """Start a new round with a random level.
        
        Returns:
            Dict containing round info or None if game over.
        """
        if self.is_game_over():
            return None

        self.current_round += 1
        self.round_in_progress = True
        
        # Select a random level that hasn't been used
        available_indices = [i for i in range(len(self.levels)) if i not in self.used_levels]
        if not available_indices:
            self.used_levels = []
            available_indices = list(range(len(self.levels)))
            
        level_idx = random.choice(available_indices)
        self.used_levels.append(level_idx)
        level = self.levels[level_idx]
        
        self.current_letters = level["letters"]
        random.shuffle(self.current_letters) # Shuffle for display
        self.target_words = [w.lower() for w in level["words"]]
        self.found_words = {w: None for w in self.target_words}
        self.revealed_hints = {w: set() for w in self.target_words}
        
        return {
            "round": self.current_round,
            "letters": self.current_letters,
            "target_words_count": len(self.target_words),
            "word_lengths": [len(w) for w in sorted(self.target_words, key=len)]
        }

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

    def check_answer(self, user_id: int, answer: str) -> Tuple[bool, Optional[str]]:
        """Check if the answer is correct and award points.
        
        Args:
            user_id: ID of the user who sent the answer.
            answer: The word guessed.
            
        Returns:
            Tuple of (is_correct, feedback_message)
        """
        if not self.round_in_progress:
            return False, None
            
        answer = answer.lower().strip()
        
        if answer in self.found_words:
            if self.found_words[answer] is not None:
                return False, "Already found!"
            
            # Mark as found
            self.found_words[answer] = user_id
            # Award points based on word length
            points = len(answer)
            self.scores[user_id] = self.scores.get(user_id, 0) + points
            
            # Check if all words are found
            if all(id is not None for id in self.found_words.values()):
                self.round_in_progress = False
                
            return True, f"Found! +{points} points"
            
        return False, None

    def get_round_progress(self) -> str:
        """Get the visual progress of the round (e.g., _ _ _ for unfound words)."""
        # Group words by length
        words_by_len = {}
        for word in self.target_words:
            length = len(word)
            if length not in words_by_len:
                words_by_len[length] = []
            words_by_len[length].append(word)
            
        output = []
        for length in sorted(words_by_len.keys()):
            for word in sorted(words_by_len[length]):
                if self.found_words[word] is not None:
                    output.append(f"<code>{word.upper()}</code>")
                else:
                    # Show revealed letters and underscores for others
                    display_word = []
                    hints = self.revealed_hints.get(word, set())
                    for i, char in enumerate(word):
                        if i in hints:
                            display_word.append(char.upper())
                        else:
                            display_word.append("_")
                    output.append("<code>" + " ".join(display_word) + "</code>")
            output.append("") # Newline between different word types or groups
            
        return "\n".join(output).strip()

    def reveal_letter_hint(self) -> Optional[Tuple[str, str, int]]:
        """Reveal a random letter in one of the unanswered words.
        
        Returns:
            Tuple of (word_template, revealed_letter, position) or None if no words left to hint.
        """
        if not self.round_in_progress:
            return None
            
        # Filter for words that haven't been found yet
        unanswered = [w for w in self.target_words if self.found_words[w] is None]
        if not unanswered:
            return None
            
        # Filter for words that still have unrevealed letters
        can_hint = []
        for word in unanswered:
            if len(self.revealed_hints[word]) < len(word):
                can_hint.append(word)
                
        if not can_hint:
            return None
            
        # Pick a random word and a random unrevealed index
        word = random.choice(can_hint)
        unrevealed_indices = [i for i in range(len(word)) if i not in self.revealed_hints[word]]
        index = random.choice(unrevealed_indices)
        
        self.revealed_hints[word].add(index)
        
        return word, word[index].upper(), index

    def is_round_finished(self) -> bool:
        """Check if all words in the current round have been found."""
        return all(id is not None for id in self.found_words.values())

    def is_game_over(self) -> bool:
        """Check if the game has reached the rounds limit."""
        return self.current_round >= self.rounds_limit

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        """Get sorted scoreboard."""
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        """Get user IDs of the winners."""
        if not self.scores:
            return []
        scoreboard = self.get_scoreboard()
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]

    def get_player_count(self) -> int:
        """Get the number of players participating."""
        return len(self.players)
