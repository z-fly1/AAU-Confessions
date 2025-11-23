# AAU Confessions Bot - Chat Web App Integration Guide

## Quick Start - Option B: Use Main Bot's Database

You've chosen to integrate the chat API with your existing bot! This means:
- ✅ No need to install PostgreSQL libraries (`psycopg2`)
- ✅ Uses the same database connection as your bot
- ✅ Runs alongside your bot in a single process
- ✅ Simpler deployment

## Step 1: Install Flask Dependencies

```bash
cd chat
pip3 install -r requirements.txt
```

This will only install Flask and Flask-CORS (no database drivers needed).

## Step 2: Integrate with Your Bot

Add these lines to your `main.py`:

### At the top with other imports:
```python
from chat.start_chat_api import start_chat_api
```

### In your `setup()` function, after `db` is created:
```python 
async def setup():
    global db, bot_info
    db = await create_db_pool()  # Your existing database pool creation
    bot_info = await bot.get_me()
    
    # 🆕 START CHAT API SERVER
    start_chat_api(db)
    
    # ... rest of your existing setup code
```

That's it! The chat API will automatically start when your bot starts.

## Step 3: Test It

1. **Start your bot normally:**
   ```bash
   python main.py
   ```

2. **You should see this in the logs:**
   ```
   INFO - Chat API server started successfully
   INFO - Starting Chat API server on port 5001
   ```

3. **Test the API:**
   ```bash
   curl http://localhost:5001/health
   # Should return: {"status":"ok"}
   ```

## Step 4: Update Frontend

Update the API URL in `chat/app.js` (line 8):

```javascript
const API_BASE_URL = 'http://localhost:5001/api';  // For local testing

// For production, use your deployed URL:
// const API_BASE_URL = 'https://your-domain.com/api';
```

## Step 5: Test the Web App

1. **Open the web app:**
   ```bash
   cd chat
   python3 -m http.server 8000
   ```

2. **Visit in browser:**
   ```
   http://localhost:8000/index.html
   ```

3. **Or use the test page:**
   ```
   http://localhost:8000/test.html
   ```

## How It Works

```
┌─────────────────┐
│   Your Bot      │
│   (main.py)     │
│                 │
│  ┌───────────┐  │
│  │ Main Bot  │  │
│  │ Logic     │  │
│  └───────────┘  │
│        │        │
│        ▼        │
│  ┌───────────┐  │
│  │ Database  │◄─┼─┐
│  │ Pool      │  │ │
│  └───────────┘  │ │ Shared!
│        ▲        │ │
│        │        │ │
│  ┌───────────┐  │ │
│  │ Chat API  │──┼─┘
│  │ (Flask)   │  │
│  └───────────┘  │
│                 │
└─────────────────┘
  Running as one
  process with
  separate thread
```

## Port Configuration

The chat API runs on port **5001** by default (different from your bot's health check port).

To change it, add to your `.env`:
```
CHAT_API_PORT=5001
```

## Deployment

When deploying:

1. **Your bot server** will run both the bot AND the chat API
2. **Deploy frontend** (HTML/CSS/JS) separately to:
   - GitHub Pages
   - Vercel
   - Netlify
   - Any static hosting

3. **Update `app.js`** with your deployed API URL

## Troubleshooting

### "Failed to import chat API"
```bash
pip3 install Flask Flask-CORS
```

### "Chat API server started but no response"
- Check if port 5001 is available
- Check firewall settings
- View logs for errors

### "Database connection error"
- The chat API uses your bot's database pool
- If your bot works, the API should work
- Check that `setup()` runs before starting the API

## File Structure

```
aau-confessions/
├── main.py                          # Your bot (⚠️ EDIT THIS)
├── chat/
│   ├── chat_api_integrated.py       # ✅ API using bot's DB
│   ├── start_chat_api.py            # ✅ Integration helper
│   ├── index.html                   # ✅ Web app
│   ├── styles.css                   # ✅ Styling
│   ├── app.js                       # ✅ Logic
│   ├── requirements.txt             # ✅ Flask only
│   ├── README.md                    # Full documentation
│   └── INTEGRATION.md               # This file
```

## Next Steps

1. ✅ Install Flask dependencies
2. ✅ Add 2 lines to `main.py`  
3. ✅ Run your bot
4. ✅ Test the web app locally
5. 🚀 Deploy and configure Telegram Mini App

See `README.md` for deployment instructions!

## Benefits of This approach

✅ **Simpler**: No separate database connection  
✅ **Efficient**: Shared connection pool  
✅ **Reliable**: Same database as your bot  
✅ **Easy to deploy**: Everything in one process  
✅ **No PostgreSQL libraries needed**: Just Flask!

---

**Need help?** Check `chat/README.md` for full documentation.
