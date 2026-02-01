import os
import random
import re
from typing import List, Dict, Optional, Tuple, Set

class SoccerTriviaGame:
    """Manages a soccer trivia game with mixed question types (Listing and Logos)."""

    QUESTIONS = [
        {
            "id": "pl_25_26",
            "type": "listing",
            "question": "Name a team playing in the Premier League for the 2025/2026 season!",
            "file": "pl teams 2026.txt"
        },
        {
            "id": "pl_all_time",
            "type": "listing",
            "question": "Name a team that has played in the Premier League at any point since its inception in 1992!",
            "file": "pl teams.txt"
        }
    ]

    def __init__(self, rounds_limit: int = 10):
        """Initialize the game.
        
        Args:
            rounds_limit: Number of questions/rounds to play.
        """
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> total_score
        self.players: Dict[int, str] = {} # user_id -> display_name
        
        # Data storage
        self.question_data: Dict[str, Set[str]] = {}
        self.logos: List[Tuple[str, str]] = [] # list of (path, answer)
        
        # De-duplication Pools
        self.available_types: List[str] = []
        self.available_logos: List[Tuple[str, str]] = []
        
        # Player Turn Logic (for Logos)
        self.player_ids: List[int] = [] 
        self.turn_index: int = 0
        
        # Current round state
        self.current_question: Optional[Dict] = None
        self.round_type: Optional[str] = None # 'listing' or 'logo'
        self.round_in_progress: bool = False
        self.current_player_id: Optional[int] = None # For turn-based rounds
        
        # Listing specific state
        self.claimed_answers: Set[str] = set() 
        self.round_scores: Dict[int, int] = {} # user_id -> count in current listing round
        
        # Logo specific state
        self.current_logo_path: Optional[str] = None
        self.current_answer: Optional[str] = None

        self._load_data()

    def _load_data(self):
        """Load answer lists and logos from the soccer directory."""
        soccer_dir = os.path.join(os.path.dirname(__file__), "soccer")
        if not os.path.exists(soccer_dir):
            return

        # Load Listing Data
        for q in self.QUESTIONS:
            file_path = os.path.join(soccer_dir, q["file"])
            if os.path.exists(file_path):
                with open(file_path, "r") as f:
                    answers = {line.strip() for line in f if line.strip()}
                    self.question_data[q["id"]] = answers
                    
        # Load Logo Data
        logo_dir = os.path.join(soccer_dir, "logos")
        if os.path.exists(logo_dir):
            for filename in os.listdir(logo_dir):
                if filename.lower().endswith('.webp'):
                    answer = os.path.splitext(filename)[0]
                    self.logos.append((os.path.join(logo_dir, filename), answer))

    def _refresh_type_pool(self):
        """Refresh the pool of round types."""
        self.available_types = ["pl_25_26", "pl_all_time", "logo"]
        random.shuffle(self.available_types)

    def _refresh_logo_pool(self):
        """Refresh the pool of available logos."""
        self.available_logos = list(self.logos)
        random.shuffle(self.available_logos)

    def start_new_round(self) -> Optional[Dict[str, any]]:
        """Start a new round with logic to avoid repeats.
        
        Returns:
            Dict containing round info or None if game over.
        """
        if self.is_game_over() or not self.players:
            return None

        self.current_round += 1
        self.round_in_progress = True
        
        # Get next type from pool
        if not self.available_types:
            self._refresh_type_pool()
        
        choice = self.available_types.pop()
        
        if choice == "logo" and self.logos:
            self.round_type = "logo"
            # Turn rotation
            if not self.player_ids:
                self.player_ids = list(self.players.keys())
                random.shuffle(self.player_ids)
            
            self.turn_index = (self.turn_index + 1) % len(self.player_ids)
            self.current_player_id = self.player_ids[self.turn_index]
            
            # Get next logo from pool
            if not self.available_logos:
                self._refresh_logo_pool()
            
            logo_path, answer = self.available_logos.pop()
            self.current_logo_path = logo_path
            self.current_answer = answer
            
            return {
                "type": "logo",
                "round": self.current_round,
                "logo_path": logo_path,
                "player_id": self.current_player_id,
                "player_name": self.players.get(self.current_player_id, "Player")
            }
        else:
            # Listing Round
            self.round_type = "listing"
            self.claimed_answers = set()
            self.round_scores = {}
            self.current_player_id = None
            
            # Use the choice if it was a listing ID, else pick a listing ID from the pool
            # (Basically, if popping 'logo' but no logos exist, we need to pick a listing type)
            if choice not in ["pl_25_26", "pl_all_time"]:
                # Filter pool for listing types or just pick one if pool is empty/only has logo
                listing_types = [t for t in self.available_types if t != "logo"]
                if not listing_types:
                    self._refresh_type_pool()
                    listing_types = [t for t in self.available_types if t != "logo"]
                
                choice = random.choice(listing_types)
                if choice in self.available_types:
                    self.available_types.remove(choice)
            
            self.current_question = next(q for q in self.QUESTIONS if q["id"] == choice)
            
            return {
                "type": "listing",
                "round": self.current_round,
                "question": self.current_question["question"]
            }

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game."""
        self.players[user_id] = display_name
        if user_id not in self.scores:
            self.scores[user_id] = 0
        if user_id not in self.player_ids:
            self.player_ids.append(user_id)

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.scores:
            del self.scores[user_id]
        if user_id in self.player_ids:
            idx = self.player_ids.index(user_id)
            self.player_ids.remove(user_id)
            if idx < self.turn_index:
                self.turn_index -= 1
            if self.turn_index >= len(self.player_ids) and self.player_ids:
                self.turn_index = 0

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison."""
        return re.sub(r'[^a-zA-Z0-9]', '', text).lower()

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check the provided answer based on round type."""
        if not self.round_in_progress:
            return False

        norm_input = self._normalize(answer)

        if self.round_type == "listing":
            q_id = self.current_question["id"]
            valid_answers = self.question_data.get(q_id, set())
            
            if norm_input in self.claimed_answers:
                return False
                
            for valid in valid_answers:
                if norm_input == self._normalize(valid):
                    self.claimed_answers.add(norm_input)
                    self.round_scores[user_id] = self.round_scores.get(user_id, 0) + 1
                    return True
        
        elif self.round_type == "logo":
            if user_id != self.current_player_id or not self.current_answer:
                return False
                
            if norm_input == self._normalize(self.current_answer):
                self.scores[user_id] = self.scores.get(user_id, 0) + 1
                self.round_in_progress = False
                return True
                
        return False
    
    def resolve_round(self) -> Dict[str, any]:
        """End current round manually. Returns result info."""
        prev_type = self.round_type
        self.round_in_progress = False
        
        if prev_type == "listing":
            # Award points for listings
            for uid, count in self.round_scores.items():
                self.scores[uid] = self.scores.get(uid, 0) + count
                
            q_id = self.current_question["id"] if self.current_question else ""
            all_answers = self.question_data.get(q_id, set())
            missed = all_answers - {ans for ans in all_answers if self._normalize(ans) in self.claimed_answers}
            sample_missed = random.sample(list(missed), min(5, len(missed))) if missed else []
            
            return {
                "type": "listing",
                "round_scores": self.round_scores,
                "sample_missed": sample_missed,
                "total_claimed": len(self.claimed_answers)
            }
        else:
            # Logo round reveal
            answer = self.current_answer
            return {
                "type": "logo",
                "answer": answer
            }

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
