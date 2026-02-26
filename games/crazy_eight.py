import os
import random
import re
from typing import List, Dict, Optional, Tuple

class Card:
    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit
        self.filename = f"{rank}_of_{suit}.png"

    def __str__(self):
        return f"{self.rank.capitalize()} of {self.suit.capitalize()}"

    def __eq__(self, other):
        if not isinstance(other, Card):
            return False
        return self.rank == other.rank and self.suit == other.suit

class Crazy8Game:
    """Manages a game of Crazy 8."""

    RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'jack', 'queen', 'king', 'ace']
    SUITS = ['clubs', 'diamonds', 'hearts', 'spades']

    def __init__(self):
        self.players: Dict[int, str] = {} # user_id -> display_name
        self.hands: Dict[int, List[Card]] = {} # user_id -> list of cards
        
        self.deck: List[Card] = []
        self.discard_pile: List[Card] = []
        
        self.player_ids: List[int] = [] 
        self.turn_index: int = 0
        
        self.current_player_id: Optional[int] = None
        self.active_suit: Optional[str] = None
        self.game_over: bool = False
        self.winner: Optional[int] = None

    def add_player(self, user_id: int, display_name: str) -> None:
        """Add a player to the game."""
        self.players[user_id] = display_name
        self.hands[user_id] = []

    def remove_player(self, user_id: int) -> None:
        """Remove a player from the game."""
        if user_id in self.players:
            del self.players[user_id]
        if user_id in self.hands:
            # Optionally return cards to deck? Just discard them for simplicity.
            self.discard_pile.extend(self.hands[user_id])
            del self.hands[user_id]
            
        if user_id in self.player_ids:
            idx = self.player_ids.index(user_id)
            self.player_ids.remove(user_id)
            if not self.player_ids:
                self.game_over = True
                return
            if idx < self.turn_index:
                self.turn_index -= 1
            if self.turn_index >= len(self.player_ids):
                self.turn_index = 0
            if self.current_player_id == user_id:
                self.current_player_id = self.player_ids[self.turn_index]

    def _initialize_deck(self):
        self.deck = []
        for suit in self.SUITS:
            for rank in self.RANKS:
                self.deck.append(Card(rank, suit))
        random.shuffle(self.deck)

    def _draw_card(self) -> Optional[Card]:
        if not self.deck:
            if len(self.discard_pile) > 1:
                # Keep top card, shuffle rest into deck
                top_card = self.discard_pile.pop()
                self.deck = self.discard_pile
                random.shuffle(self.deck)
                self.discard_pile = [top_card]
            else:
                return None # No cards left anywhere!
        if self.deck:
            return self.deck.pop()
        return None

    def start_game(self) -> Optional[str]:
        """Start the game, deal cards, set initial state.
        Returns the initial message detailing the top card and whose turn it is.
        """
        self._initialize_deck()
        self.discard_pile = []
        
        # Initialize turn order
        self.player_ids = list(self.players.keys())
        random.shuffle(self.player_ids)
        self.turn_index = 0
        self.current_player_id = self.player_ids[self.turn_index]
        self.game_over = False
        self.winner = None

        # Deal 7 cards to each player
        for pid in self.player_ids:
            self.hands[pid] = []
            for _ in range(7):
                card = self._draw_card()
                if card:
                    self.hands[pid].append(card)

        # Flip top card
        top_card = self._draw_card()
        while top_card and top_card.rank == '8':
            # Put 8s back and draw again to avoid starting with an 8
            self.deck.insert(0, top_card)
            top_card = self._draw_card()
            
        if top_card:
            self.discard_pile.append(top_card)
            self.active_suit = top_card.suit

        return self.get_top_card().filename if self.get_top_card() else None

    def get_top_card(self) -> Optional[Card]:
        if self.discard_pile:
            return self.discard_pile[-1]
        return None

    def get_player_hand_text(self, user_id: int) -> str:
        """Get formatted text of player's hand for the alert."""
        if user_id not in self.hands:
            return "You are not in the game."
        cards = self.hands[user_id]
        if not cards:
            return "You have no cards."
        
        # Sort cards by suit then rank for easier reading
        suit_order = {s: i for i, s in enumerate(self.SUITS)}
        rank_order = {r: i for i, r in enumerate(self.RANKS)}
        
        sorted_cards = sorted(cards, key=lambda c: (suit_order[c.suit], rank_order[c.rank]))
        return "\n".join([f"- {str(c)}" for c in sorted_cards])

    def parse_card_from_text(self, text: str) -> Optional[Card]:
        """Attempt to parse a played card from user text."""
        text = text.lower()
        # Handle "Played: Rank of Suit" prefix from inline queries
        if text.startswith("played:"):
            text = text[len("played:"):].strip()
        # Clean up text a bit
        text = text.replace('the ', '').replace(' of ', ' ')
        tokens = text.split()
        if len(tokens) < 2:
            return None
        
        # E.g. "5 hearts" or "ace spades"
        rank = None
        suit = None
        for r in self.RANKS:
            if r in tokens or (r == 'ace' and 'a' in tokens) or (r == 'jack' and 'j' in tokens) or (r == 'queen' and 'q' in tokens) or (r == 'king' and 'k' in tokens):
                rank = r
                break
        
        for s in self.SUITS:
            if s in text or (s[:-1] in text): # allow "heart" or "hearts"
                suit = s
                break
                
        if rank and suit:
            return Card(rank, suit)
        return None

    def is_valid_play(self, played_card: Card) -> bool:
        """Check if a card can be played on the current top card/active suit."""
        top_card = self.get_top_card()
        if not top_card:
            return True
        
        # 8 can be played on anything
        if played_card.rank == '8':
            return True
            
        # Match rank or active suit
        return played_card.rank == top_card.rank or played_card.suit == self.active_suit

    def play_card(self, user_id: int, card_text: str) -> Tuple[bool, str, Optional[str]]:
        """Attempt to play a card.
        Returns (success, message, filename_of_played_card)
        """
        if self.game_over:
            return False, "The game is over.", None
            
        if user_id != self.current_player_id:
            return False, "It's not your turn!", None
            
        played_card = self.parse_card_from_text(card_text)
        if not played_card:
            return False, "I couldn't understand that card. Try e.g. '5 of hearts'.", None
            
        # Check if player has the card
        player_hand = self.hands[user_id]
        card_in_hand = None
        for c in player_hand:
            if c == played_card:
                card_in_hand = c
                break
                
        if not card_in_hand:
            return False, f"You don't have the {str(played_card)} in your hand.", None
            
        if not self.is_valid_play(played_card):
            return False, f"Invalid play! You must match the rank ({self.get_top_card().rank}) or suit ({self.active_suit}), or play an 8.", None
            
        # Play the card
        player_hand.remove(card_in_hand)
        self.discard_pile.append(card_in_hand)
        self.active_suit = card_in_hand.suit
        
        # Check for win
        if len(player_hand) == 0:
            self.game_over = True
            self.winner = user_id
            return True, f"{self.players[user_id]} played {str(card_in_hand)} and won the game!", card_in_hand.filename
            
        # Advance turn
        self._advance_turn()
        
        msg = f"{self.players[user_id]} played {str(card_in_hand)}."
        if card_in_hand.rank == '8':
            msg += f"\nThe active suit is now {self.active_suit.capitalize()}!"
            
        msg += f"\n\nIt's now {self.players[self.current_player_id]}'s turn."
        # Add warning if running low
        if len(player_hand) == 1:
            msg = f"⚠️ {self.players[user_id]} has 1 card left!\n" + msg
            
        return True, msg, card_in_hand.filename

    def draw_card_for_player(self, user_id: int) -> Tuple[bool, str]:
        """Current player draws a card."""
        if self.game_over:
            return False, "The game is over."
            
        if user_id != self.current_player_id:
            return False, "It's not your turn!"
            
        card = self._draw_card()
        if not card:
            return False, "No cards left to draw!"
            
        self.hands[user_id].append(card)
        self._advance_turn()
        
        return True, f"{self.players[user_id]} drew a card and passed their turn.\nIt is now {self.players[self.current_player_id]}'s turn."

    def _advance_turn(self):
        if not self.player_ids:
            return
        self.turn_index = (self.turn_index + 1) % len(self.player_ids)
        self.current_player_id = self.player_ids[self.turn_index]

    def get_player_count(self) -> int:
        return len(self.players)
