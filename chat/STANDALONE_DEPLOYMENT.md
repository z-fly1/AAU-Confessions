# Chat API - Standalone Deployment Guide

This guide explains how to run the Chat API as a **separate service** on your VPS, independent from the main bot.

## Why Run as Separate Service?

Running the chat API as a standalone service has several benefits:
- **Independence**: API runs independently from the main bot
- **Easy Restart**: Can restart API without restarting the entire bot
- **Better Monitoring**: Separate logs and status checks via systemd
- **Resource Management**: Can allocate separate resources if needed

## Prerequisites

- VPS with systemd (Ubuntu, Debian, etc.)
- PostgreSQL database running
- Python 3.7+ installed
- Root or sudo access

## Quick Deployment

### Automated Deployment (Recommended)

1. **Copy files to your VPS** (if deploying locally first):
   ```bash
   scp chat/chat_api.py botuser@46.224.108.57:/home/botuser/AAU-Confessions/chat/
   scp chat/chat-api.service botuser@46.224.108.57:/home/botuser/AAU-Confessions/chat/
   scp chat/deploy-chat-api.sh botuser@46.224.108.57:/home/botuser/AAU-Confessions/chat/
   ```

2. **SSH into your VPS**:
   ```bash
   ssh botuser@46.224.108.57
   ```

3. **Run the deployment script**:
   ```bash
   cd ~/AAU-Confessions
   chmod +x chat/deploy-chat-api.sh
   bash chat/deploy-chat-api.sh
   ```

That's it! The script will:
- Install required Python packages
- Set up the systemd service
- Start the API server
- Test the health endpoint

### Manual Deployment

If you prefer to deploy manually:

1. **SSH into your VPS**:
   ```bash
   ssh botuser@46.224.108.57
   ```

2. **Navigate to project directory**:
   ```bash
   cd ~/AAU-Confessions
   ```

3. **Install dependencies**:
   ```bash
   pip3 install Flask Flask-CORS psycopg2-binary python-dotenv
   ```

4. **Copy service file**:
   ```bash
   sudo cp chat/chat-api.service /etc/systemd/system/
   ```

5. **Reload systemd**:
   ```bash
   sudo systemctl daemon-reload
   ```

6. **Enable and start service**:
   ```bash
   sudo systemctl enable chat-api
   sudo systemctl start chat-api
   ```

7. **Check status**:
   ```bash
   sudo systemctl status chat-api
   ```

8. **Test health endpoint**:
   ```bash
   curl http://localhost:10000/health
   # Should return: {"status":"ok"}
   ```

## Verification

### Check if API is Running

```bash
# Check service status
sudo systemctl status chat-api

# View recent logs
sudo journalctl -u chat-api -n 50

# Follow logs in real-time
sudo journalctl -u chat-api -f

# Check if port 10000 is listening
sudo lsof -i :10000
# OR
sudo netstat -tlnp | grep 10000
```

### Test API Endpoints

```bash
# Health check
curl http://localhost:10000/health

# From outside the VPS (requires firewall rules - see below)
curl http://46.224.108.57:10000/health
```

## Firewall Configuration

If you're using UFW (Ubuntu Firewall), allow port 10000:

```bash
# Allow port 10000
sudo ufw allow 10000/tcp

# Check firewall status
sudo ufw status
```

## Service Management

### Common Commands

```bash
# Start service
sudo systemctl start chat-api

# Stop service
sudo systemctl stop chat-api

# Restart service
sudo systemctl restart chat-api

# Check status
sudo systemctl status chat-api

# Enable auto-start on boot
sudo systemctl enable chat-api

# Disable auto-start
sudo systemctl disable chat-api

# View logs
sudo journalctl -u chat-api -f
```

### After .env Changes

If you update your `.env` file, restart the service:

```bash
sudo systemctl restart chat-api
```

## Troubleshooting

### API Won't Start

1. **Check logs**:
   ```bash
   sudo journalctl -u chat-api -n 100 --no-pager
   ```

2. **Common issues**:
   - Missing Python packages: `pip3 install Flask Flask-CORS psycopg2-binary python-dotenv`
   - Database connection issues: Verify `DATABASE_URL` in `.env`
   - Port already in use: Check if another service is using port 10000

### Can't Connect from Web App

1. **Check if service is running**:
   ```bash
   sudo systemctl status chat-api
   ```

2. **Test locally first**:
   ```bash
   curl http://localhost:10000/health
   ```

3. **Check firewall**:
   ```bash
   sudo ufw status | grep 10000
   # If not listed, add rule:
   sudo ufw allow 10000/tcp
   ```

4. **Test from outside**:
   ```bash
   curl http://46.224.108.57:10000/health
   ```

### Database Connection Errors

Check your `.env` file has the correct `DATABASE_URL`:
```
DATABASE_URL=postgresql://confessuser:password@localhost:5432/confessiondb
```

Test PostgreSQL is running:
```bash
sudo systemctl status postgresql
```

## Monitoring

### View Real-time Logs

```bash
sudo journalctl -u chat-api -f
```

### Check Resource Usage

```bash
# CPU and memory usage
top -p $(pgrep -f "chat/chat_api.py")
```

## Architecture

```
┌─────────────────────────┐
│   Netlify Frontend       │
│  (Web App - app.js)      │
└───────────┬─────────────┘
            │ HTTP Requests
            │ to 46.224.108.57:10000/api
            ▼
┌─────────────────────────┐
│   VPS (46.224.108.57)   │
│                          │
│  ┌──────────────────┐   │
│  │  Chat API        │   │
│  │  (Port 10000)    │   │
│  │  systemd service │   │
│  └────────┬─────────┘   │
│           │              │
│           │ SQL Queries  │
│           ▼              │
│  ┌──────────────────┐   │
│  │  PostgreSQL DB    │   │
│  │  (Port 5432)     │   │
│  └──────────────────┘   │
│                          │
│  ┌──────────────────┐   │
│  │  Main Bot        │   │
│  │  (main.py)       │   │
│  └──────────────────┘   │
└─────────────────────────┘
```

The Chat API and Main Bot run independently, both accessing the same PostgreSQL database.

## Next Steps

After deployment:

1. ✅ Verify API is accessible: `curl http://46.224.108.57:10000/health`
2. ✅ Test the web app: Open `https://aauconfessionschat.netlify.app` in Telegram
3. ✅ Monitor logs: `sudo journalctl -u chat-api -f`

## Configuration Reference

Your current setup:
- **API Port**: 10000 (from `CHAT_API_PORT` in `.env`)
- **VPS IP**: 46.224.108.57
- **VPS User**: botuser
- **Project Path**: /home/botuser/AAU-Confessions
- **Web App URL**: https://aauconfessionschat.netlify.app
- **API Endpoint**: http://46.224.108.57:10000/api
- **Database**: PostgreSQL on localhost:5432
```
