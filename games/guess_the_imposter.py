import random
from typing import List, Dict, Optional, Set

class GuessTheImposterGame:
    """Manages a 'Guess the Imposter' game session."""

    # Built-in word list for the game - pairs of related words or just simple nouns
    # For now, just a list of words. The imposter has to blend in without knowing the word.
    WORD_LIST = [
        "Airport", "Classroom", "Library", "Market", "Restaurant", "Cinema",
"Forest", "Desert", "River", "Island", "Garden", "Park",
"Drums", "Violin", "Microphone", "Camera", "Headphones", "Speaker",
"Cricket", "Tennis", "Swimming", "Running", "Cycling", "Boxing",
"Sandwich", "Pasta", "IceCream", "Donut", "Pancake", "Noodles",
"Elephant", "Giraffe", "Zebra", "Monkey", "Rabbit", "Horse",
"Tablet", "Laptop", "Printer", "Router", "Keyboard", "Mouse",
"Bus", "Train", "Airplane", "Scooter", "Truck", "Boat",
"Star", "Cloud", "Rain", "Wind", "Thunder", "Rainbow",
"Newspaper", "Magazine", "Notebook", "Pen", "Pencil", "Eraser",
"Juice", "Milk", "Water", "Soda", "Smoothie", "Milkshake"
    ]

    def __init__(self):
        """Initialize a new game."""
        self.players: Dict[int, str] = {}  # user_id -> display_name
        self.secret_word: Optional[str] = None
        self.imposter_id: Optional[int] = None
        
        # Game state
        self.turn_order: List[int] = []
        self.current_turn_index: int = 0
        self.clues: Dict[int, str] = {}  # user_id -> clue
        self.votes: Dict[int, int] = {}  # voter_id -> voted_user_id
        self.is_voting: bool = False
        self.game_over: bool = False
        
    def add_player(self, user_id: int, display_name: str) -> None:
        """Add a player to the game."""
        self.players[user_id] = display_name

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
            # Handle removal during game if necessary (simplified for now)

    def start_game(self) -> str:
        """Start the game, pick word and imposter.
        
        Returns:
            The secret word (for the bot to know internally)
        """
        self.secret_word = random.choice(self.WORD_LIST)
        player_ids = list(self.players.keys())
        self.imposter_id = random.choice(player_ids)
        
        # Randomize turn order
        self.turn_order = player_ids.copy()
        random.shuffle(self.turn_order)
        self.current_turn_index = 0
        self.clues = {}
        self.votes = {}
        self.is_voting = False
        self.game_over = False
        
        return self.secret_word

    def get_player_role(self, user_id: int) -> str:
        """Get the role/word for a player to be sent privately."""
        if user_id == self.imposter_id:
            return "IMPOSTER"
        return self.secret_word

    def get_current_player_id(self) -> int:
        """Get the user_id of the player whose turn it is."""
        if not self.turn_order:
            return None
        return self.turn_order[self.current_turn_index % len(self.turn_order)]

    def get_current_player_name(self) -> str:
        """Get the name of the player whose turn it is."""
        player_id = self.get_current_player_id()
        if player_id:
            return self.players.get(player_id, "Unknown Player")
        return "Unknown"

    def submit_clue(self, user_id: int, clue: str) -> bool:
        """Submit a clue for the current player.
        
        Returns:
            True if clue was accepted and turn advanced.
        """
        if self.is_voting or self.game_over:
            return False
            
        if user_id != self.get_current_player_id():
            return False
            
        self.clues[user_id] = clue
        self.current_turn_index += 1
        return True

    def are_clues_finished(self) -> bool:
        """Check if all players have submitted clues."""
        return self.current_turn_index >= len(self.turn_order)

    def start_voting(self) -> None:
        """Enable voting phase."""
        self.is_voting = True

    def vote(self, voter_id: int, suspect_id: int) -> bool:
        """Cast a vote.
        
        Returns:
            True if vote recorded.
        """
        if not self.is_voting or self.game_over:
            return False
        
        if voter_id not in self.players or suspect_id not in self.players:
            return False
            
        self.votes[voter_id] = suspect_id
        return True

    def get_vote_counts(self) -> Dict[int, int]:
        """Get current vote counts (suspect_id -> count)."""
        counts = {}
        for suspect in self.votes.values():
            counts[suspect] = counts.get(suspect, 0) + 1
        return counts

    def get_voting_status(self) -> str:
        """Get a string representation of who has voted."""
        voted_count = len(self.votes)
        total_players = len(self.players)
        return f"{voted_count}/{total_players} players have voted."

    def is_voting_complete(self) -> bool:
        """Check if all players have voted."""
        return len(self.votes) == len(self.players)

    def resolve_game(self) -> dict:
        """Calculate results.
        
        Returns:
            Dict containing:
            - imposter_caught: bool
            - imposter_id: int
            - imposter_name: str
            - secret_word: str
            - votes: dict (counts)
            - most_voted_player: int (or None if tie)
        """
        self.game_over = True
        vote_counts = self.get_vote_counts()
        
        # Find player with max votes
        max_votes = 0
        most_voted = None
        tie = False
        
        for pid, count in vote_counts.items():
            if count > max_votes:
                max_votes = count
                most_voted = pid
                tie = False
            elif count == max_votes:
                tie = True
        
        imposter_caught = (most_voted == self.imposter_id) and not tie
        
        return {
            "imposter_caught": imposter_caught,
            "imposter_id": self.imposter_id,
            "imposter_name": self.players.get(self.imposter_id, "Unknown"),
            "secret_word": self.secret_word,
            "vote_counts": vote_counts,
            "most_voted_id": most_voted,
            "most_voted_name": self.players.get(most_voted, "Unknown") if most_voted else "Tie"
        }
