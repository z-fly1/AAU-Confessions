# AAU Confessions Chat Web App

A modern Telegram Mini App for managing chats in the AAU Confessions bot.

## Features

✨ **Beautiful UI** - Dark mode with glassmorphism effects and smooth animations  
💬 **Real-time messaging** - Automatic polling for new messages  
🔒 **Secure** - Telegram WebApp authentication  
📱 **Responsive** - Works perfectly on mobile and desktop  
🎨 **Modern design** - Premium aesthetic with gradient accents

## Architecture

### Backend (chat_api.py)
Flask-based REST API that:
- Authenticates users via Telegram WebApp initData
- Provides endpoints for chats, messages, and sending
- Uses the same PostgreSQL database as the main bot
- Supports CORS for local development

### Frontend (index.html, styles.css, app.js)
Single-page application with:
- Telegram WebApp SDK integration
- Two-panel layout (chat list + conversation)
- Responsive design for mobile/desktop
- Real-time message polling
- Beautiful animations and transitions

## Local Development

### 1. Install Dependencies

```bash
cd chat
pip install -r requirements.txt
```

### 2. Configure Environment

Make sure your `.env` file has:
```
BOT_TOKEN=your_bot_token
DATABASE_URL=your_postgres_url
CHAT_API_PORT=5000  # Optional, defaults to 5000
```

### 3. Run the API Server

```bash
python chat_api.py
```

The API will be available at `http://localhost:5000`

### 4. Test the Web App

Open `index.html` in your browser. For local testing without Telegram, the app will use mock authentication.

To test in Telegram:
1. Deploy the web app to a public URL (see Deployment section)
2. Update `API_BASE_URL` in `app.js` to your API URL
3. Configure the Mini App in your bot (see Bot Integration section)

## Deployment

### Deploy Web App

The frontend files (index.html, styles.css, app.js) are static and can be hosted anywhere:

**Option 1: GitHub Pages**
1. Create a new repository
2. Upload the three files
3. Enable GitHub Pages in repository settings
4. Your app will be available at `https://username.github.io/repo-name/`

**Option 2: Vercel**
1. Install Vercel CLI: `npm install -g vercel`
2. Run `vercel` in the chat directory
3. Follow the prompts

**Option 3: Netlify**
1. Drag and drop the chat folder to Netlify

### Deploy API Server

The API server (chat_api.py) needs to run on a server with Python support:

**Option 1: Render**
1. Create a new Web Service
2. Connect your repository
3. Set build command: `pip install -r chat/requirements.txt`
4. Set start command: `python chat/chat_api.py`
5. Add environment variables

**Option 2: Railway**
1. Create a new project
2. Connect your repository
3. Add a Procfile: `web: python chat/chat_api.py`
4. Add environment variables

**Option 3: Run on same server as bot**
```bash
cd chat
nohup python chat_api.py &
```

### Update API URL

After deploying the API, update `API_BASE_URL` in `app.js`:
```javascript
const API_BASE_URL = 'https://your-api-domain.com/api';
```

## Bot Integration

### 1. Configure Mini App in BotFather

1. Open @BotFather in Telegram
2. Send `/mybots`
3. Select your bot
4. Click "Bot Settings" → "Menu Button"
5. Click "Configure Menu Button"
6. Enter your web app URL (e.g., `https://username.github.io/chat-app/`)

### 2. Add Command to Bot (Optional)

You can also add a `/chatapp` command to open the Mini App. Add this to your main.py:

```python
@dp.message(Command("chatapp"))
async def chatapp_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Open Chat App", 
            web_app=types.WebAppInfo(url="https://your-web-app-url.com/")
        )]
    ])
    await message.answer("Open the chat app to manage your conversations:", reply_markup=keyboard)
```

## API Reference

### Authentication
All endpoints (except `/health`) require authentication via Telegram WebApp initData:
```
Authorization: twa <initData>
```

### Endpoints

#### `GET /api/chats`
Get all active chats for the authenticated user.

**Response:**
```json
{
  "chats": [
    {
      "partnerId": 123456,
      "partnerName": "User Name",
      "partnerEmoji": "👤",
      "lastMessage": "Hello!",
      "lastMessageTime": "2024-01-15T10:30:00",
      "isOwn": false
    }
  ]
}
```

#### `GET /api/chats/:partnerId/messages`
Get message history with a partner.

**Query Parameters:**
- `limit` (optional): Number of messages to fetch (default: 50)
- `before` (optional): Message ID to fetch messages before (for pagination)

**Response:**
```json
{
  "messages": [
    {
      "id": 1,
      "senderId": 123456,
      "isOwn": false,
      "text": "Hello!",
      "hasSticker": false,
      "hasAnimation": false,
      "timestamp": "2024-01-15T10:30:00"
    }
  ],
  "partner": {
    "id": 123456,
    "name": "User Name",
    "emoji": "👤"
  }
}
```

#### `POST /api/chats/:partnerId/messages`
Send a message to a partner.

**Request Body:**
```json
{
  "text": "Hello!"
}
```

**Response:**
```json
{
  "message": {
    "id": 2,
    "senderId": 789012,
    "isOwn": true,
    "text": "Hello!",
    "hasSticker": false,
    "hasAnimation": false,
    "timestamp": "2024-01-15T10:31:00"
  }
}
```

#### `GET /api/chats/:partnerId/info`
Get information about a chat partner.

**Response:**
```json
{
  "id": 123456,
  "name": "User Name",
  "emoji": "👤",
  "bio": "Student at AAU"
}
```

## Troubleshooting

### CORS Errors
If you see CORS errors in the browser console:
1. Make sure Flask-CORS is installed
2. Check that the API is running
3. Verify the API_BASE_URL in app.js

### Authentication Failures
If authentication fails:
1. Check that BOT_TOKEN is correct in .env
2. Verify the initData is being sent correctly
3. For local testing, the app will use mock auth

### Messages Not Loading
If messages don't load:
1. Check browser console for errors
2. Verify the API server is running
3. Check database connection
4. Ensure chat_requests and chat_messages tables exist

### Polling Not Working
If real-time updates don't work:
1. Check that polling is enabled (POLL_INTERVAL in app.js)
2. Verify the API endpoint is accessible
3. Check browser console for errors

## Development Tips

### Testing Locally
1. Run the API server: `python chat_api.py`
2. Open `index.html` in browser (or use a local server)
3. The app will use mock authentication for local testing

### Debugging
- Open browser DevTools (F12)
- Check Console tab for errors
- Check Network tab for API requests
- Use `console.log()` in app.js for debugging

### Making Changes
- **UI changes**: Edit `styles.css` and `index.html`
- **Logic changes**: Edit `app.js`
- **API changes**: Edit `chat_api.py`
- Refresh browser to see changes (no build step needed!)

## License

Same as the main AAU Confessions Bot project.

## Support

For issues or questions, contact the bot administrator.
