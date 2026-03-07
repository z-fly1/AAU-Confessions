import os
import random
import logging
from typing import List, Dict, Optional, Tuple
from PIL import Image
import numpy as np

logger = logging.getLogger(__name__)

class HearMeOutGame:
    """Manages the Hear Me Out game where players submit pictures to be added to a cake."""

    # Slot coordinates provided by the user (10 slots)
    # Format: x1,y1, x2,y2, x3,y3, x4,y4 for each slot
    # Order provided by user. We will map them to target points.
    SLOT_COORDS = [
        [412,352,262,415,290,493,445,427],  # 1
        [340,276,510,262,515,346,345,356],  # 2
        [586,281,561,352,710,399,734,324],  # 3
        [521,362,484,438,640,511,675,432],  # 4
        [216,593,381,559,233,675,397,639],  # 5
        [314,481,307,570,480,577,484,489],  # 6
        [397,566,543,560,546,632,397,634],  # 7
        [515,509,496,588,639,622,656,538],  # 8
        [591,583,581,642,685,659,694,596],  # 9
        [670,561,652,620,755,650,772,590]   # 10
    ]

    def __init__(self, turns_limit: int = 10, chat_id: int = 0):
        self.turns_limit = min(turns_limit, 10)  # Max 10 slots available
        self.chat_id = chat_id
        
        # Player management
        self.players_list: List[int] = []  # To maintain turn order
        self.players: Dict[int, str] = {}  # user_id -> display_name
        self.scores: Dict[int, int] = {}   # Required by game manager interfaces, unused here
        
        # Game state
        self.current_turn: int = 0  # 1-indexed during gameplay
        self.is_active: bool = False
        
        # Image storage
        # Mapping turn number (1 to N) to the downloaded user image path
        self.user_images: Dict[int, str] = {}
        
        # Paths
        self.cake_dir = os.path.join(os.path.dirname(__file__), "hmo cake")

    def add_player(self, user_id: int, display_name: str) -> None:
        """Add a player to the game."""
        if user_id not in self.players:
            self.players[user_id] = display_name
            self.players_list.append(user_id)
            self.scores[user_id] = 0

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
            if user_id in self.players_list:
                self.players_list.remove(user_id)
            if user_id in self.scores:
                del self.scores[user_id]

    def start_game(self) -> str:
        """Start the game and set to turn 1."""
        self.is_active = True
        self.current_turn = 1
        
        # Limit the game length based on the number of players
        max_possible_turns = min(len(self.players_list), 10)
        self.turns_limit = max(1, max_possible_turns)
        
        self._shuffle_players()
        return "The Hear Me Out cake is ready! 🎂\nTake turns sending a picture of a character or celebrity."

    def _shuffle_players(self) -> None:
        """Randomize turn order."""
        random.shuffle(self.players_list)

    def get_current_player_id(self) -> Optional[int]:
        """Get the ID of the player whose turn it is."""
        if not self.is_active or self.current_turn > self.turns_limit or not self.players_list:
            return None
        # 1-indexed turn, list is 0-indexed
        idx = (self.current_turn - 1) % len(self.players_list)
        return self.players_list[idx]

    def get_current_player_name(self) -> str:
        """Get the name of the player whose turn it is."""
        pid = self.get_current_player_id()
        if pid and pid in self.players:
            return self.players[pid]
        return "Player"

    def is_game_over(self) -> bool:
        """Check if all turns are completed."""
        return not self.is_active or self.current_turn > self.turns_limit

    def submit_picture(self, user_id: int, image_path: str) -> Optional[str]:
        """
        Process the submitted picture by the current player.
        Returns the path to the newly generated composite image, or None if invalid submission.
        """
        if user_id != self.get_current_player_id():
            return None
            
        self.user_images[self.current_turn] = image_path
        
        # Generate composite
        composite_path = self._generate_composite()
        
        # Advance turn
        self.current_turn += 1
        if self.current_turn > self.turns_limit:
            self.is_active = False
            
        return composite_path

    def _find_coeffs(self, pa: List[Tuple[float, float]], pb: List[Tuple[float, float]]) -> List[float]:
        """
        Find coefficients for perspective transformation.
        pa: target points
        pb: source points
        """
        import numpy as np
        matrix = []
        for p1, p2 in zip(pa, pb):
            matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0]*p1[0], -p2[0]*p1[1]])
            matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1]*p1[0], -p2[1]*p1[1]])

        A = np.matrix(matrix, dtype=float)
        B = np.array(pb).reshape(8)
        
        try:
            res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
            return np.array(res).reshape(8).tolist()
        except np.linalg.LinAlgError:
            # Fallback identity if singular matrix
            return [1, 0, 0, 0, 1, 0, 0, 0]

    def _sort_points(self, pts: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """Sort points into TopLeft, BottomLeft, BottomRight, TopRight to match image corners."""
        pts = np.array(pts)
        rect = np.zeros((4, 2), dtype="float32")
        
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)] # Top-left
        rect[2] = pts[np.argmax(s)] # Bottom-right
        
        diff = np.diff(pts, axis=1)
        rect[3] = pts[np.argmin(diff)] # Top-right
        rect[1] = pts[np.argmax(diff)] # Bottom-left
        
        return [(int(x), int(y)) for x, y in rect]

    def _generate_composite(self) -> str:
        """
        Generates the composite image for the current turn.
        Takes N.png as the background and pastes images 1..N onto it.
        """
        # Load the base image for current turn
        base_img_path = os.path.join(self.cake_dir, f"{self.current_turn}.png")
        if not os.path.exists(base_img_path):
            # Fallback to 00.png if N.png not found
            base_img_path = os.path.join(self.cake_dir, "00.png")
            
        base_img = Image.open(base_img_path).convert("RGBA")
        width, height = base_img.size
        
        # Iterate over all turns up to now and paste their images
        for turn_idx in range(1, self.current_turn + 1):
            if turn_idx not in self.user_images:
                continue
                
            user_img_path = self.user_images[turn_idx]
            try:
                user_img = Image.open(user_img_path).convert("RGBA")
            except Exception as e:
                logger.error(f"Error opening user image {user_img_path}: {e}")
                continue
                
            # Get slot coords for this turn (0-indexed)
            coords_flat = self.SLOT_COORDS[turn_idx - 1]
            target_pts = [(coords_flat[i], coords_flat[i+1]) for i in range(0, 8, 2)]
            
            # Sort points consistently: TL, BL, BR, TR
            target_pts = self._sort_points(target_pts)
            
            u_width, u_height = user_img.size
            source_pts = [(0, 0), (0, u_height), (u_width, u_height), (u_width, 0)]
            
            coeffs = self._find_coeffs(target_pts, source_pts)
            
            # Warp user image to the perspective
            warped = user_img.transform((width, height), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
            
            # Create a mask from the warped image's alpha channel
            mask = warped.split()[3]
            
            # Paste the warped image onto the base image
            base_img.paste(warped, (0, 0), mask)
            
        # Save output
        output_path = f"/tmp/hmo_cake_{self.chat_id}_{self.current_turn}.png"
        base_img.save(output_path, format="PNG")
        return output_path

    def get_scoreboard(self) -> List[Tuple[int, int]]:
        return sorted(self.scores.items(), key=lambda x: x[1], reverse=True)

    def get_winners(self) -> List[int]:
        return []

    def get_player_count(self) -> int:
        return len(self.players)
