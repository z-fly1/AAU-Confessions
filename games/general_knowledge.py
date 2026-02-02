import json
import os
import random
import re
from typing import List, Dict, Optional, Union, Tuple


class GeneralKnowledgeGame:
    """Manages a general knowledge trivia game with multiple rounds."""

    def __init__(self, total_rounds: int = 15):
        """Initialize a new game.
        
        Args:
            total_rounds: Number of rounds to play (default: 15)
        """
        self.total_rounds = total_rounds
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.questions: List[Dict] = []
        self.used_question_indices: List[int] = []
        self.current_question: Optional[Dict] = None
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.round_in_progress: bool = False

        self._load_questions()

    def _load_questions(self):
        """Load questions from the general_knowledge.json file."""
        json_path = os.path.join(os.path.dirname(__file__), "general knowledge", "general_knowledge.json")
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                self.questions = json.load(f)
        except Exception as e:
            print(f"Error loading general knowledge questions: {e}")
            self.questions = []

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game.
        
        Args:
            user_id: Telegram user ID
            display_name: Player's display name
        """
        self.players[user_id] = display_name
        if user_id not in self.scores:
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.scores:
            del self.scores[user_id]

    def _normalize(self, text: str) -> str:
        """Normalize text for comparison (remove special chars, lowercase)."""
        if not text:
            return ""
        return re.sub(r'[^a-z0-9]', '', text.lower())

    def start_new_round(self) -> Tuple[str, int]:
        """Start a new round with a random question.
        
        Returns:
            Tuple of (question_text, round_number)
        """
        self.current_round += 1

        if not self.questions:
            return "No questions available.", self.current_round

        # Reset used questions if we've gone through all of them
        if len(self.used_question_indices) >= len(self.questions):
            self.used_question_indices = []

        # Find an unused question
        available_indices = [i for i in range(len(self.questions)) if i not in self.used_question_indices]
        idx = random.choice(available_indices)
        self.used_question_indices.append(idx)
        
        self.current_question = self.questions[idx]
        self.round_in_progress = True
        return self.current_question["question"], self.current_round

    def check_answer(self, user_id: int, answer: str) -> bool:
        """Check if an answer is correct and award points.
        
        Args:
            user_id: Telegram user ID
            answer: The user's answer
            
        Returns:
            True if answer is correct, False otherwise
        """
        if not self.current_question or user_id not in self.players:
            return False

        correct_answer = self.current_question["answer"]
        norm_user_answer = self._normalize(answer)

        if isinstance(correct_answer, list):
            # Check if answer matches any item in the list
            for valid in correct_answer:
                if norm_user_answer == self._normalize(valid):
                    self.scores[user_id] += 1
                    self.round_in_progress = False
                    return True
        else:
            # Check single string answer
            if norm_user_answer == self._normalize(correct_answer):
                self.scores[user_id] += 1
                self.round_in_progress = False
                return True

        return False

    def get_current_answer(self) -> str:
        """Get the current correct answer(s) as a string for display."""
        if not self.current_question:
            return ""
        
        ans = self.current_question["answer"]
        if isinstance(ans, list):
            return " or ".join(ans)
        return ans

    def is_game_over(self) -> bool:
        """Check if the game has ended."""
        return self.current_round >= self.total_rounds

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        """Get sorted scoreboard."""
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        """Get list of user IDs with the highest score."""
        if not self.scores:
            return []
            
        scoreboard = self.get_scoreboard()
        if not scoreboard:
            return []
            
        highest_score = scoreboard[0][1]
        return [user_id for user_id, score in scoreboard if score == highest_score]

    def get_player_count(self) -> int:
        """Get number of players in the game."""
        return len(self.players)
