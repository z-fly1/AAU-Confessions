import random
from typing import List, Dict, Optional, Tuple

class GuessMojiGame:
    """Manages a single GuessMoji game with multiple rounds."""
    
    # Dictionary of emoji puzzles categorized by theme
    THEMED_PUZZLES = {
        "Movies 🎬": [
            {"emojis": "🦁👑", "answer": "The Lion King"},
            {"emojis": "🍦😱", "answer": "I Scream"},
            {"emojis": "🌎👽👊", "answer": "Independence Day"},
            {"emojis": "🦇👨", "answer": "Batman"},
            {"emojis": "🕷️👨", "answer": "Spiderman"},
            {"emojis": "👻🚫", "answer": "Ghostbusters"},
            {"emojis": "🚢🧊🆘", "answer": "Titanic"},
            {"emojis": "🦖🦕🏞️", "answer": "Jurassic Park"},
            {"emojis": "🏠😱", "answer": "Home Alone"},
            {"emojis": "👧👠🌪️🦁", "answer": "The Wizard of Oz"},
            {"emojis": "🧙‍♂️⚡👓", "answer": "Harry Potter"},
            {"emojis": "🍫🏭🎫", "answer": "Charlie and the Chocolate Factory"},
            {"emojis": "🕒🐇🎩🍵", "answer": "Alice in Wonderland"},
            {"emojis": "🐠🔍", "answer": "Finding Nemo"},
            {"emojis": "🐼🥋", "answer": "Kung Fu Panda"},
            {"emojis": "🐍✈️", "answer": "Snakes on a Plane"},
            {"emojis": "🔥🏹👧", "answer": "The Hunger Games"},
            {"emojis": "👽📞👆", "answer": "ET"},
            {"emojis": "👨‍💼🔪🚿", "answer": "Psycho"},
            {"emojis": "🦈🏖️😱", "answer": "Jaws"},
            {"emojis": "👀👂👃👅✋", "answer": "The Sixth Sense"},
            {"emojis": "👨‍🚀🚀🌌", "answer": "Interstellar"},
            {"emojis": "💍🌋👀", "answer": "Lord of the Rings"},
            {"emojis": "🏴‍☠️🦜💰", "answer": "Pirates of the Caribbean"},
            {"emojis": "💊🕶️🖥️", "answer": "The Matrix"},
            {"emojis": "🐔🏃💨", "answer": "Chicken Run"},
            {"emojis": "🐜👨", "answer": "Antman"},
            {"emojis": "😈👗👠", "answer": "The Devil Wears Prada"},
            {"emojis": "👨‍🎤🎸⚡", "answer": "Bohemian Rhapsody"},
            {"emojis": "🐀👨‍🍳🍲", "answer": "Ratatouille"},
            {"emojis": "🐷🕸️", "answer": "Charlottes Web"},
            {"emojis": "🐝🎥", "answer": "Bee Movie"},
            {"emojis": "🎈🏠👨‍🦳", "answer": "Up"},
            {"emojis": "🚗💨😠", "answer": "Fast and Furious"},
            {"emojis": "🧛‍♂️🐺💔", "answer": "Twilight"},
            {"emojis": "❄️👸☃️", "answer": "Frozen"},
            {"emojis": "🐢🍕⚔️", "answer": "Teenage Mutant Ninja Turtles"},
            {"emojis": "🕰️🔙🚗", "answer": "Back to the Future"},
            {"emojis": "🥊🐅", "answer": "Rocky"},
            {"emojis": "🚔🐊🏖️", "answer": "Miami Vice"}
        ],
        "Idioms & Dictionary 📖": [
            {"emojis": "🌧️🐱🐶", "answer": "Raining cats and dogs"},
            {"emojis": "👂🎶", "answer": "Music to my ears"},
            {"emojis": "❄️🧊", "answer": "Break the ice"},
            {"emojis": "🐷✈️", "answer": "When pigs fly"},
            {"emojis": "🍰🚶", "answer": "Piece of cake"},
            {"emojis": "🍎👀", "answer": "Apple of my eye"},
            {"emojis": "🤐👄", "answer": "Zip your lip"},
            {"emojis": "👀🐈", "answer": "Curiosity killed the cat"},
            {"emojis": "🔥🍳", "answer": "Out of the frying pan"},
            {"emojis": "🐺🐑👗", "answer": "Wolf in sheeps clothing"},
            {"emojis": "🎣🐟", "answer": "Big fish in a small pond"},
            {"emojis": "🥔🛋️", "answer": "Couch potato"},
            {"emojis": "📖🐛", "answer": "Bookworm"},
            {"emojis": "🥶🦃", "answer": "Cold turkey"},
            {"emojis": "🐸🗣️", "answer": "Frog in my throat"},
            {"emojis": "🌙🌕", "answer": "Once in a blue moon"},
            {"emojis": "🧂🌍", "answer": "Salt of the earth"},
            {"emojis": "🚫😭🍼", "answer": "Don't cry over spilt milk"},
            {"emojis": "👄🚢", "answer": "Loose lips sink ships"},
            {"emojis": "🦶👄", "answer": "Foot in mouth"}
        ],
        "Songs 🎵": [
            {"emojis": "👁️🐯", "answer": "Eye of the Tiger"},
            {"emojis": "🌂🌧️", "answer": "Umbrella"},
            {"emojis": "👋🤔", "answer": "Hello"},
            {"emojis": "🧨🎆", "answer": "Firework"},
            {"emojis": "💃👸", "answer": "Dancing Queen"},
            {"emojis": "🍋☕", "answer": "Lemonade"},
            {"emojis": "🧞‍♂️🏺", "answer": "Genie in a Bottle"},
            {"emojis": "♦️💍", "answer": "Diamonds"},
            {"emojis": "🚣🚣🚣", "answer": "Row Row Row Your Boat"},
            {"emojis": "🕷️🕸️", "answer": "Itsy Bitsy Spider"},
            {"emojis": "👶🦈", "answer": "Baby Shark"},
            {"emojis": "🍬🍭", "answer": "Candy Shop"},
            {"emojis": "🚗🛣️", "answer": "Highway to Hell"},
            {"emojis": "🚀👨", "answer": "Rocket Man"},
            {"emojis": "🏚️🧱", "answer": "Brick House"},
            {"emojis": "🔥🌧️", "answer": "Set Fire to the Rain"},
            {"emojis": "🌌✨", "answer": "A Sky Full of Stars"},
            {"emojis": "🚶‍♂️🔥", "answer": "Walk on Fire"},
            {"emojis": "🌪️🌬️", "answer": "Rock You Like a Hurricane"},
            {"emojis": "🍦🚐", "answer": "Ice Cream Man"}
        ],
        "Places 🌍": [
            {"emojis": "🗽🍎", "answer": "New York"},
            {"emojis": "🗼🥐", "answer": "Paris"},
            {"emojis": "💂‍♂️☕🌧️", "answer": "London"},
            {"emojis": "🏜️🐫📐", "answer": "Egypt"},
            {"emojis": "🏯🌸🍣", "answer": "Japan"},
            {"emojis": "🍝🍕🏟️", "answer": "Italy"},
            {"emojis": "🐨🦘🥥", "answer": "Australia"},
            {"emojis": "🍁🏒🥞", "answer": "Canada"},
            {"emojis": "💃🐂🥘", "answer": "Spain"},
            {"emojis": "🌵🌮☀️", "answer": "Mexico"},
            {"emojis": "🏖️🌴👙", "answer": "Hawaii"},
            {"emojis": "🎰🎲💒", "answer": "Las Vegas"},
            {"emojis": "🏔️🍫🧀", "answer": "Switzerland"},
            {"emojis": "🦁🦓🦒", "answer": "Africa"},
            {"emojis": "🥶🐧❄️", "answer": "Antarctica"}
        ]
    }
    
    def __init__(self, total_rounds: int = 20):
        """Initialize a new game with a random theme.
        
        Args:
            total_rounds: Number of rounds to play (default: 20)
        """
        self.total_rounds = total_rounds
        self.current_round = 0
        self.scores: Dict[int, int] = {}  # user_id -> score
        self.current_puzzle: Optional[Dict[str, str]] = None
        self.used_puzzles: List[Dict[str, str]] = []
        self.round_in_progress = False
        
        # Randomly select a theme
        self.theme_name = random.choice(list(self.THEMED_PUZZLES.keys()))
        self.current_theme_puzzles = self.THEMED_PUZZLES[self.theme_name]
        
    def add_player(self, user_id: int) -> None:
        """Add a player to the game."""
        if user_id not in self.scores:
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.scores:
            del self.scores[user_id]
            
    def start_new_round(self) -> Tuple[str, int]:
        """Start a new round with a random puzzle from the current theme.
        
        Returns:
            Tuple of (emojis, round_number)
        """
        self.current_round += 1
        self.round_in_progress = True
        
        # Select a random puzzle that hasn't been used
        available_puzzles = [p for p in self.current_theme_puzzles if p not in self.used_puzzles]
        if not available_puzzles:
            # If we run out of puzzles in the theme, reset used puzzles or switch themes?
            # Let's reset used puzzles for now to keep it simple.
            self.used_puzzles = []
            available_puzzles = self.current_theme_puzzles
            
        self.current_puzzle = random.choice(available_puzzles)
        self.used_puzzles.append(self.current_puzzle)
        
        return self.current_puzzle["emojis"], self.current_round
    
    def check_answer(self, answer: str, user_id: int) -> bool:
        """Check if an answer is correct and award points.
        
        Args:
            answer: The user's answer
            user_id: Telegram user ID
            
        Returns:
            True if answer is correct, False otherwise
        """
        if not self.current_puzzle or not self.round_in_progress:
            return False
            
        correct_answer = self.current_puzzle["answer"].lower()
        # Remove special characters and extra spaces for fuzzy matching
        cleaned_user_answer = "".join(e for e in answer.lower() if e.isalnum())
        cleaned_correct_answer = "".join(e for e in correct_answer if e.isalnum())
        
        if cleaned_user_answer == cleaned_correct_answer:
            # Award point to the user
            self.scores[user_id] = self.scores.get(user_id, 0) + 1
            self.round_in_progress = False
            return True
        return False
    
    def get_current_answer(self) -> Optional[str]:
        """Get the current answer (for revealing)."""
        return self.current_puzzle["answer"] if self.current_puzzle else None
    
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
        return len(self.scores)
