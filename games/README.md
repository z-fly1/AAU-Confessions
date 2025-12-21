# Telegram Group Games Bot

A Telegram bot that hosts multiplayer games in groups. Currently supports Word Unscramble game with more games coming soon!

## Features

- 🎮 Multiple game support (currently Word Unscramble)
- 👥 Multiplayer gameplay with score tracking
- 🛡️ Requires admin privileges for proper game management
- 🏆 Automatic winner declaration after 10 rounds
- 📊 Real-time scoreboard updates

## Prerequisites

- Python 3.8 or higher
- A Telegram Bot Token from [@BotFather](https://t.me/botfather)

## Setup

1. **Clone or download this repository**

2. **Create a virtual environment (recommended)**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Get your Bot Token**
   - Open Telegram and search for [@BotFather](https://t.me/botfather)
   - Send `/newbot` and follow the instructions
   - Copy the bot token you receive

5. **Configure the bot**
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and replace `your_bot_token_here` with your actual bot token.

6. **Run the bot**
   ```bash
   python main.py
   ```

## How to Play

### Setting Up in a Group

1. **Add the bot to your Telegram group**
2. **Make the bot an admin** (required for the bot to function)
3. The bot will confirm it's ready with a message

### Playing Word Unscramble

1. **Start a game**: Any member sends `/start` in the group
2. **Select game code**: Send `1` for Word Unscramble
3. **Join the game**: Players have 20 seconds to send `/join`
4. **Play**: The bot sends scrambled words, first correct answer wins the round
5. **Winner**: After 10 rounds, the player with the most points wins!

## Game Codes

- **1** - Word Unscramble Game (10 rounds, first to answer correctly wins points)

## Project Structure

```
.
├── main.py              # Main bot application
├── game_manager.py      # Game session management
├── word_unscramble.py   # Word unscramble game logic
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── README.md           # This file
```

## Admin Privileges

The bot requires admin privileges to:
- Properly manage game state
- Ensure fair gameplay
- Handle player interactions effectively

Without admin rights, the bot will not start games.

## Troubleshooting

**Bot doesn't respond**
- Make sure the bot is an admin in the group
- Check that the bot is running (`python main.py`)
- Verify your bot token is correct in `.env`

**Game won't start**
- Ensure at least 2 players have joined with `/join`
- Wait for the full 20-second joining period

**Players can't join**
- Make sure players send `/join` within 20 seconds of game announcement
- Each player can only join once per game

## Contributing

Feel free to add more games or improve existing ones! The modular structure makes it easy to add new game types.

## License

MIT License - feel free to use and modify!
