import random
import json
import os
from typing import Dict, List, Tuple, Any

class TaylorShakespeareGame:
    def __init__(self, rounds: int = 10):
        self.rounds_limit = rounds
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # {user_id: total_score}
        self.data: List[Dict[str, str]] = []
        self.current_item: Dict[str, str] = None
        self.current_votes: Dict[int, str] = {}  # {user_id: chosen_author}
        self.round_in_progress = False
        self.players: Dict[int, str] = {} # {id: name}
        self.load_data()

    def add_player(self, user_id: int, name: str):
        """Add a player to the game."""
        self.players[user_id] = name

    def remove_player(self, user_id: int):
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.scores:
            del self.scores[user_id]

    def load_data(self):
        """Load quotes from JSON file."""
        data_path = os.path.join(os.path.dirname(__file__), "taylor_shakespeare.json")
        try:
            with open(data_path, 'r') as f:
                self.data = json.load(f)
        except Exception as e:
            print(f"Error loading TS data: {e}")
            self.data = []

    def start_new_round(self) -> Tuple[str, int]:
        """Prepare a new round by picking a random quote."""
        if not self.data:
            return None, 0
        
        self.current_round += 1
        self.current_item = random.choice(self.data)
        self.current_votes = {}
        self.round_in_progress = True
        return self.current_item["quote"], self.current_round

    def record_vote(self, user_id: int, author: str) -> bool:
        """Record a player's answer. Return True if recorded successfully."""
        if not self.round_in_progress:
            return False
        # Allow changing vote until timeout
        self.current_votes[user_id] = author
        return True

    def resolve_round(self) -> Dict[str, Any]:
        """Find winners of the current round and update scores."""
        if not self.round_in_progress or not self.current_item:
            return None
        
        self.round_in_progress = False
        correct_author = self.current_item["author"]
        winners = []
        
        for user_id, chosen in self.current_votes.items():
            if chosen == correct_author:
                winners.append(user_id)
                self.scores[user_id] = self.scores.get(user_id, 0) + 1
        
        return {
            "correct_author": correct_author,
            "winners": winners,
            "quote": self.current_item["quote"]
        }

    def is_game_over(self) -> bool:
        return self.current_round >= self.rounds_limit

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        if not self.scores:
            return []
        max_score = max(self.scores.values())
        return [uid for uid, score in self.scores.items() if score == max_score]
