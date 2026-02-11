import random
from typing import List, Dict, Optional, Set, Tuple

class TwentyQuestionsGame:
    """Manages a '20 Questions' game session."""
    
    # Simple word list for 20 questions
    WORD_LIST = [
        "Phone","Dog","Teacher","Hospital","Love","Laptop","Lion","Airport","Time",
  "Chair","Programmer","Snake","Book","Money","Car","Doctor","Forest","Freedom",
  "Camera","Cat","University","Fear","Bottle","Elephant","River","Justice",
  "Watch","Pilot","Mountain","Hope","Headphones","Guitar","Singer","Dream",
  "Backpack","Monkey","Zoo","Happiness","Keyboard","Hacker","Internet","Shadow",
  "Shoes","Police officer","City","Truth","Television","Bird","Village","Lie",
  "Wallet","Chef","Restaurant","Luck","Key","Fish","Ocean","Risk",
  "Computer","Robot","AI","Power","Drone","Soldier","War","Control",
  "Mirror","Actor","Movie","Fame","Microphone","Podcast","Voice","Silence",
  "Bus","Driver","Road","Speed","Train","Station","Journey","Chance",
  "House","Family","Home","Trust","Bed","Sleep","Dream","Nightmare",
  "Light bulb","Electricity","Energy","Darkness","Clock","Time","Memory","Past",
  "Future","Calendar","Plan","Goal","Success","Failure","Work","Office",
  "Company","Boss","Salary","Greed","Bank","ATM","Credit","Debt",
  "Bitcoin","Blockchain","Wealth","Poverty","Market","Shop","Mall","Price",
  "School","Student","Exam","Stress","Paper","Pen","Knowledge","Wisdom",
  "Book","Library","Secret","Mystery","Detective","Spy","Information","Data",
  "Server","Database","Cloud","Network","Wi-Fi","Router","Signal","Noise",
  "Music","Song","Album","Playlist","Emotion","Anger","Sadness","Pride",
  "Jealousy","Envy","Friendship","Relationship","Breakup","Heart",
  "Marriage","Wedding","Ring","Promise","Religion","Faith","Belief","Prayer",
  "Church","Mosque","Temple","Peace","War","Conflict","Revenge","Justice",
  "Gun","Knife","Weapon","Danger","Police","Law","Order","Chaos",
  "Fire","Water","Air","Earth","Storm","Rain","Thunder","Lightning",
  "Sun","Moon","Star","Planet","Space","Universe","Galaxy","Alien",
  "Monster","Ghost","Zombie","Vampire","Legend","Myth","Story","Hero",
  "Villain","Power","Magic","Spell","Curse","Blessing","Fate","Destiny",
  "Choice","Decision","Mind","Brain","Thought","Idea","Imagination",
  "Art","Painting","Design","Creativity","Talent","Skill","Practice",
  "Sport","Football","Basketball","Referee","Champion","Victory","Loss",
  "Gym","Strength","Health","Medicine","Doctor","Disease","Cure","Vaccine"
    ]

    def __init__(self, rounds_limit: int = 10):
        """Initialize the game."""
        self.players: Dict[int, str] = {}  # user_id -> display_name
        self.scores: Dict[int, int] = {}   # user_id -> score
        self.rounds_limit = rounds_limit
        self.current_round = 0
        
        # Round state
        self.host_id: Optional[int] = None
        self.current_word: Optional[str] = None
        self.questions_asked: int = 0
        self.max_questions: int = 20
        self.round_in_progress: bool = False
        
        # Track previous hosts to ensure rotation
        self.previous_hosts: List[int] = []

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game."""
        if user_id not in self.players:
            self.players[user_id] = display_name
            if user_id not in self.scores:
                self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
            # Keep score in case they rejoin? Or maybe not. Let's keep it simple.
            
        # If the host leaves, we need to handle that externally (end round)
        
    def start_new_round(self, forced_host_id: Optional[int] = None) -> bool:
        """Start a new round.
        
        Args:
            forced_host_id: If provided, this player becomes the host (e.g., winner).
            
        Returns:
            True if round started, False if not enough players.
        """
        if len(self.players) < 2:
            self.round_in_progress = False
            return False
            
        self.current_round += 1
        self.questions_asked = 0
        self.round_in_progress = True
        
        # Select Host
        if forced_host_id and forced_host_id in self.players:
            self.host_id = forced_host_id
        else:
            # Pick a random host who hasn't been host recently if possible
            potential_hosts = [id for id in self.players if id not in self.previous_hosts]
            if not potential_hosts:
                # Reset history if everyone has been host
                self.previous_hosts = []
                potential_hosts = list(self.players.keys())
            
            self.host_id = random.choice(potential_hosts)
            
        self.previous_hosts.append(self.host_id)
        
        # Select Word
        self.current_word = random.choice(self.WORD_LIST)
        
        return True

    def check_guess_or_question(self, user_id: int, text: str) -> Tuple[bool, Optional[str]]:
        """Process a message from a player.
        
        Returns:
            (is_game_action, result_message)
            is_game_action: True if this message counted as a question or logic check.
            result_message: 'CORRECT', 'QUESTION_COUNTED', 'LIMIT_REACHED', or None
        """
        if not self.round_in_progress or user_id == self.host_id:
            return False, None
            
        text = text.strip()
        
        # Check if it's a question
        if text.endswith('?'):
            self.questions_asked += 1
            if self.questions_asked >= self.max_questions:
                return True, 'LIMIT_REACHED'
            return True, 'QUESTION_COUNTED'
            
        # Check if it's a guess
        if self.current_word and text.lower() == self.current_word.lower():
            # Correct guess!
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            self.round_in_progress = False
            return True, 'CORRECT'
            
        return False, None

    def host_wins_round(self) -> None:
        """Award point to host when questions run out or time runs out."""
        if self.host_id:
            self.scores[self.host_id] = self.scores.get(self.host_id, 0) + 1
        self.round_in_progress = False

    def get_host_name(self) -> str:
        return self.players.get(self.host_id, "Unknown") if self.host_id else "None"

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def is_game_over(self) -> bool:
        return self.current_round >= self.rounds_limit

    def get_winners(self) -> List[int]:
        """Return the user IDs of the player(s) with the highest score."""
        if not self.scores:
            return []
        max_score = max(self.scores.values())
        if max_score == 0:
            return []
        return [uid for uid, score in self.scores.items() if score == max_score]
