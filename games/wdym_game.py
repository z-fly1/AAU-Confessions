import random
import json
import os
from typing import List, Dict, Optional, Tuple, Set

class MemeGame:
    """Manages 'What Do You Meme' game logic."""
    
    def __init__(self, rounds_limit: int = 10):
        self.rounds_limit = rounds_limit
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> submissions
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.player_order: List[int] = []
        
        self.questions: List[str] = []
        self.used_questions: List[int] = []
        
        # Current round state
        self.current_question: Optional[str] = None
        self.submissions: Dict[int, str] = {} # user_id -> file_id (or something to mark as done)
        self.round_in_progress = False
        
        self._load_questions()

    def _load_questions(self):
        json_path = os.path.join(os.path.dirname(__file__), "wdym", "questions.json")
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r') as f:
                    self.questions = json.load(f)
            else:
                self.questions = ["When you realize it's Monday tomorrow."]
        except Exception as e:
            print(f"Error loading questions: {e}")
            self.questions = ["When you realize it's Monday tomorrow."]

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        if user_id not in self.players:
            self.players[user_id] = display_name
            self.player_order.append(user_id)
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        if user_id in self.players:
            del self.players[user_id]
            if user_id in self.player_order:
                self.player_order.remove(user_id)
            if user_id in self.scores:
                del self.scores[user_id]

    def start_new_round(self) -> Optional[Dict]:
        if self.is_game_over() or len(self.players) < 2:
            return None

        self.current_round += 1
        self.submissions = {}
        self.round_in_progress = True
        
        # Select Question
        available_indices = [i for i in range(len(self.questions)) if i not in self.used_questions]
        if not available_indices:
            self.used_questions = []
            available_indices = list(range(len(self.questions)))
            
        q_idx = random.choice(available_indices)
        self.used_questions.append(q_idx)
        self.current_question = self.questions[q_idx]
        
        return {
            "round": self.current_round,
            "question": self.current_question
        }

    def submit_meme(self, user_id: int, file_id: str) -> bool:
        """Record a submission. No judge means everyone gets a point for participating."""
        if not self.round_in_progress:
            return False
        
        if user_id not in self.players:
            return False
            
        if user_id not in self.submissions:
            self.scores[user_id] += 1 # 1 point for participating
            self.submissions[user_id] = file_id
            return True
        return False

    def get_pending_players(self) -> List[int]:
        """Get players who haven't submitted a meme yet."""
        return [uid for uid in self.player_order if uid not in self.submissions]

    def is_game_over(self) -> bool:
        return self.current_round >= self.rounds_limit

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        if not self.scores:
            return []
        scoreboard = self.get_scoreboard()
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]
