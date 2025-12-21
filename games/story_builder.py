import random
from typing import List, Optional, Dict


class StoryBuilderGame:
    """Manages a collaborative story builder game."""
    
    STARTING_PROMPTS = [
        "Once upon a time in a world were women didn't exist...",
        "It was a dark and stormy night...",
        "The spaceship landed softly on the purple grass...",
        "Deep in the ocean, something began to glow...",
        "The detective picked up the mysterious letter...",
        "Everyone thought the cat was normal, until...",
        "The door opened, but no one was there...",
        "The last taxi left the station at midnight..."
    ]
    
    def __init__(self, rounds_per_player: int = 2):
        """Initialize a new story game.
        
        Args:
            rounds_per_player: Number of times each player contributes (default: 2)
        """
        self.rounds_per_player = rounds_per_player
        self.story_segments: List[str] = []
        self.players: List[int] = []  # List of user IDs in turn order
        self.player_names: Dict[int, str] = {}  # user_id -> display name
        self.current_turn_index = 0
        self.total_turns_completed = 0
        self.is_started = False
        
    def add_player(self, user_id: int, name: str) -> None:
        """Add a player to the game.
        
        Args:
            user_id: Telegram user ID
            name: Display name for the player
        """
        if user_id not in self.players:
            self.players.append(user_id)
            self.player_names[user_id] = name
            
    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game.
        
        Args:
            user_id: Telegram user ID
        """
        if user_id in self.players:
            # If removing current player, adjust index if needed
            if self.players.index(user_id) < self.current_turn_index:
                self.current_turn_index -= 1
            
            self.players.remove(user_id)
            if user_id in self.player_names:
                del self.player_names[user_id]
                
            # Handle case where current player was removed and index is now out of bounds
            if self.current_turn_index >= len(self.players):
                self.current_turn_index = 0
                
    def start_game(self) -> str:
        """Start the game and return the starting prompt.
        
        Returns:
            The starting story segment
        """
        if not self.players:
            return "Error: No players joined."
            
        self.is_started = True
        # Shuffle players for random turn order
        random.shuffle(self.players)
        
        # Pick random starting prompt
        start_text = random.choice(self.STARTING_PROMPTS)
        self.story_segments.append(start_text)
        
        return start_text
    
    def get_current_player_id(self) -> Optional[int]:
        """Get the user ID of the player whose turn it is."""
        if not self.players:
            return None
        return self.players[self.current_turn_index]
    
    def get_current_player_name(self) -> str:
        """Get the name of the player whose turn it is."""
        player_id = self.get_current_player_id()
        if player_id:
            return self.player_names.get(player_id, "Unknown Player")
        return "Unknown"
        
    def add_story_segment(self, text: str, user_id: int) -> bool:
        """Add a segment to the story if it's the user's turn.
        
        Args:
            text: The story segment to add
            user_id: The user attempting to add the segment
            
        Returns:
            True if segment was added, False if not user's turn
        """
        if not self.is_started or not self.players:
            return False
            
        if user_id != self.players[self.current_turn_index]:
            return False
            
        # Add the segment
        self.story_segments.append(text.strip())
        self.total_turns_completed += 1
        
        # Advance turn
        self.current_turn_index = (self.current_turn_index + 1) % len(self.players)
        
        return True
        
    def get_full_story(self) -> str:
        """Get the full story text."""
        return " ".join(self.story_segments)
    
    def is_game_over(self) -> bool:
        """Check if the game has ended (all rounds completed)."""
        if not self.players:
            return True
        return self.total_turns_completed >= (len(self.players) * self.rounds_per_player)
    
    def get_player_count(self) -> int:
        """Get number of players."""
        return len(self.players)
