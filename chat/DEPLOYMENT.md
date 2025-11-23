# Production Deployment Guide - Telegram Mini App

## 🚀 Overview

This guide will help you deploy your chat management web app as a Telegram Mini App in production.

## Architecture

```
┌─────────────────────────────────┐
│   Your Server (e.g., Render)    │
│                                 │
│  ┌────────────────────────┐    │
│  │   Your Bot + Chat API  │    │
│  │   (main.py running)    │    │
│  │   Port: 5001           │    │
│  └────────────────────────┘    │
└─────────────────────────────────┘
            ▲
            │ API calls
            │
┌───────────┴─────────────────────┐
│  Static Hosting (GitHub Pages,  │
│  Vercel, Netlify)                │
│                                  │
│  Web App Files:                  │
│  - index.html                    │
│  - styles.css                    │
│  - app.js                        │
└──────────────────────────────────┘
            ▲
            │ Opens in
            │
    ┌───────┴────────┐
    │   Telegram     │
    │   Mini App     │
    └────────────────┘
```

---

## Part 1: Deploy Backend (Bot + Chat API)

Your bot and chat API run together. Deploy where your bot is currently hosted.

### Option A: Already on Render

If your bot is already on Render:

1. **Update Environment Variables**

Add to your Render environment variables:
```
CHAT_API_PORT=5001
CHAT_API_DEV_MODE=false
```

2. **Verify Bot Starts Chat API**

Check your deployment logs for:
```
Chat API server started successfully
Starting Chat API server on port 5001
```

3. **Get Your API URL**

Your API will be at:
```
https://your-render-app-name.onrender.com:5001/api
```

Or if Render only exposes port 443/80, you might need to configure a reverse proxy or use the same port as your health check server.

### Option B: New Deployment

If starting fresh:

1. **Deploy to Render**
   - Create a new Web Service
   - Connect your GitHub repo
   - Build Command: `pip install -r requirements.txt && pip install -r chat/requirements.txt`
   - Start Command: `python main.py`

2. **Set Environment Variables**
   ```
   BOT_TOKEN=your_token
   DATABASE_URL=your_postgres_url
   ADMIN_ID=your_id
   CONTACT_ADMIN_ID=your_id
   CHANNEL_ID=your_channel
   GEMINI_API_KEYS=your_keys
   CHAT_API_PORT=5001
   CHAT_API_DEV_MODE=false
   ```

3. **Note Your URL**
   ```
   https://your-app-name.onrender.com
   ```

### Important: Port Configuration

⚠️ **If your hosting only exposes one port:**

You may need to run the chat API on the same port as your health check. Update in `.env`:
```
CHAT_API_PORT=8080  # Same as your PORT variable
```

The chat API will be at: `https://your-domain.com/api/chats`

---

## Part 2: Deploy Frontend (Web App)

The frontend files (HTML, CSS, JS) need to be hosted on a static file server.

### Option A: GitHub Pages (Recommended - Free & Easy)

1. **Create a New Repository**
   ```bash
   # On GitHub, create a new repo called "aau-chat-app"
   ```

2. **Upload Files**
   
   Upload these files to the repo:
   - `chat/index.html`
   - `chat/styles.css`
   - `chat/app.js`

3. **Enable GitHub Pages**
   - Go to repo Settings → Pages
   - Source: Deploy from main branch
   - Directory: / (root)
   - Save

4. **Your Web App URL**
   ```
   https://your-username.github.io/aau-chat-app/
   ```

### Option B: Vercel (Free, Fast)

```bash
# Install Vercel CLI
npm install -g vercel

# Deploy
cd chat
vercel

# Follow prompts, select:
# - Project: aau-chat-app
# - Settings: Use defaults
```

Your URL: `https://aau-chat-app.vercel.app`

### Option C: Netlify (Free, Drag & Drop)

1. Go to [app.netlify.com](https://app.netlify.com)
2. Drag the `chat` folder into Netlify
3. Get your URL: `https://aau-chat-app.netlify.app`

---

## Part 3: Update Frontend Configuration

After deploying both backend and frontend:

1. **Update API URL in `app.js`**

   Edit line 7 in `app.js`:
   ```javascript
   const API_BASE_URL = 'https://your-render-app.onrender.com/api';
   ```

2. **Redeploy Frontend**
   
   Commit and push the change, or re-upload to your static host.

---

## Part 4: Configure Telegram Mini App

Now connect everything to Telegram!

### Step 1: Open BotFather

1. Go to [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/mybots`
3. Select your bot

### Step 2: Configure Menu Button

1. Click **"Bot Settings"**
2. Click **"Menu Button"**
3. Click **"Configure Menu Button"**
4. Send the button text:
   ```
   💬 Chat App
   ```
5. Send your web app URL:
   ```
   https://your-username.github.io/aau-chat-app/
   ```

### Step 3: Test It!

1. Open your bot in Telegram
2. Click the **menu button** (≡) next to the message input
3. The web app should open!

### Alternative: Add a Command

You can also add a `/chatapp` command. This is already in your bot if you add this to `main.py`:

```python
from aiogram.types import WebAppInfo

@dp.message(Command("chatapp"))
async def chatapp_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Open Chat App", 
            web_app=WebAppInfo(url="https://your-username.github.io/aau-chat-app/")
        )]
    ])
    await message.answer(
        "🚀 Open the chat app to manage your conversations with a beautiful interface!", 
        reply_markup=keyboard
    )
```

---

## Part 5: Security & Configuration

### Disable Development Mode

Make sure in your **production environment variables**:
```
CHAT_API_DEV_MODE=false
```

This re-enables Telegram authentication.

### CORS Configuration

For production, you may want to restrict CORS. In `chat_api_integrated.py`, update:

```python
# Instead of "*", specify your frontend domain:
CORS(app, resources={
    r"/api/*": {
        "origins": "https://your-username.github.io",  # Your frontend domain
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"],
    }
})
```

---

## Part 6: Testing

### Test Checklist

- [ ] Bot responds to commands
- [ ] Chat API health check works: `https://your-api.com/health`
- [ ] Web app loads in browser
- [ ] Menu button opens web app in Telegram
- [ ] Can see chat list (in Telegram)
- [ ] Can send messages (in Telegram)
- [ ] Real-time polling works
- [ ] Authentication works (only in Telegram, not in browser)

### Common Issues

**"Unauthorized" in Telegram**
- Check `CHAT_API_DEV_MODE=false` in production
- Verify `BOT_TOKEN` is correct

**"CORS error"**
- Update CORS origins to include your frontend domain
- Redeploy backend

**"Can't connect to API"**
- Verify API URL in `app.js` is correct
- Check backend logs for errors
- Test health endpoint directly

**"Empty chat list"**
- Make sure your user has accepted chats in the bot
- Check development mode is off
- Verify database connection

---

## Quick Deployment Summary

```bash
# 1. Backend (already deployed with your bot)
# Just add these to .env:
CHAT_API_PORT=5001
CHAT_API_DEV_MODE=false

# 2. Frontend
cd chat
# Upload index.html, styles.css, app.js to GitHub Pages/Vercel/Netlify

# 3. Update app.js
# Line 7: API_BASE_URL = 'https://your-backend.com/api'

# 4. Configure in Telegram
# BotFather → Bot Settings → Menu Button → Add your web app URL

# 5. Test!
# Open your bot → Click menu button → Chat app opens!
```

---

## File Checklist

### Backend (Your Server)
- [x] `main.py` (with `start_chat_api(db)` line added)
- [x] `chat/chat_api_integrated.py`
- [x] `chat/start_chat_api.py`
- [x] Environment variable: `CHAT_API_DEV_MODE=false`

### Frontend (Static Hosting)
- [x] `index.html`
- [x] `styles.css`
- [x] `app.js` (with updated API_BASE_URL)

### Telegram
- [x] Menu button configured in BotFather
- [x] Optional: `/chatapp` command added

---

## Support

If you encounter issues:

1. **Check backend logs** for API errors
2. **Check browser console** (F12) for frontend errors
3. **Test API directly**: `curl https://your-api.com/health`
4. **Verify environment variables** are set correctly

---

## Example `.env` for Production

```env
# Bot Configuration
BOT_TOKEN=123456:ABC-DEF...
ADMIN_ID=123456789
CONTACT_ADMIN_ID=123456789
CHANNEL_ID=-100123456789
DATABASE_URL=postgresql://user:pass@host:5432/db
PORT=8080

# Gemini (if using)
GEMINI_API_KEYS=key1,key2,key3

# Chat API
CHAT_API_PORT=8080  # Same as PORT if single port
CHAT_API_DEV_MODE=false  # IMPORTANT: false in production

# Optional
PAGE_SIZE=15
RESERVED_NICKNAMES=Admin,Moderator
```

---

🎉 **You're all set!** Your users can now manage their chats through a beautiful web interface directly in Telegram!
