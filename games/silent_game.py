from typing import List, Dict, Optional, Tuple, Set

class SilentGame:
    """Manages 'The Silent Game' where players must stay silent to win."""

    def __init__(self):
        """Initialize the game."""
        self.players: Dict[int, str] = {}  # user_id -> display_name
        self.losers: Set[int] = set()
        self.game_started: bool = False

    def add_player(self, user_id: int, display_name: str = "Player") -> None:
        """Add a player to the game."""
        if user_id not in self.players:
            self.players[user_id] = display_name

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.losers:
            self.losers.remove(user_id)

    def start_game(self) -> None:
        """Start the game."""
        self.game_started = True

    def eliminate_player(self, user_id: int) -> bool:
        """Eliminate a player if they were in the game.
        
        Returns:
            True if player was eliminated, False otherwise.
        """
        if user_id in self.players and user_id not in self.losers:
            self.losers.add(user_id)
            return True
        return False

    def is_game_over(self) -> bool:
        """Check if the game is over (1 or 0 players left silent)."""
        active_players = [uid for uid in self.players if uid not in self.losers]
        # Game ends when only one person is left standing
        return len(active_players) <= 1

    def get_winner(self) -> Optional[int]:
        """Get the ID of the winner."""
        active_players = [uid for uid in self.players if uid not in self.losers]
        return active_players[0] if active_players else None

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        """Return a scoreboard matching the expected format (user_id, score).
        
        Since silence isn't point-based, we'll return 1 for active and 0 for losers.
        """
        scoreboard = []
        for uid in self.players:
            score = 1 if uid not in self.losers else 0
            scoreboard.append((uid, score))
        
        # Sort so active players (score 1) are at the top
        return sorted(scoreboard, key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        """Get winners list."""
        winner = self.get_winner()
        return [winner] if winner else []
