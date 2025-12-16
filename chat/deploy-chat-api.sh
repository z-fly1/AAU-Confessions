#!/bin/bash
# Deploy Chat API as a standalone service on VPS

set -e

echo "🚀 Deploying Chat API as standalone service..."

# Step 1: Check if we're on the VPS
if [ ! -f "/root/aau-confessions/main.py" ]; then
    echo "❌ Error: This script must be run on the VPS"
    echo "Please copy this script to your VPS and run it there"
    exit 1
fi

# Step 2: Install dependencies if not already installed
echo "📦 Installing Flask dependencies..."
pip3 install Flask Flask-CORS psycopg2-binary python-dotenv

# Step 3: Copy service file to systemd
echo "📝 Installing systemd service..."
sudo cp /root/aau-confessions/chat/chat-api.service /etc/systemd/system/

# Step 4: Reload systemd
echo "🔄 Reloading systemd..."
sudo systemctl daemon-reload

# Step 5: Enable and start service
echo "▶️  Starting Chat API service..."
sudo systemctl enable chat-api
sudo systemctl restart chat-api

# Step 6: Wait a moment for service to start
sleep 2

# Step 7: Check service status
echo ""
echo "📊 Service Status:"
sudo systemctl status chat-api --no-pager

# Step 8: Test health endpoint
echo ""
echo "🏥 Testing health endpoint..."
sleep 1
if curl -s http://localhost:10000/health | grep -q "ok"; then
    echo "✅ Chat API is running successfully!"
    echo ""
    echo "API is accessible at: http://46.224.108.57:10000/api"
    echo ""
    echo "Useful commands:"
    echo "  - View logs: sudo journalctl -u chat-api -f"
    echo "  - Restart service: sudo systemctl restart chat-api"
    echo "  - Stop service: sudo systemctl stop chat-api"
    echo "  - Check status: sudo systemctl status chat-api"
else
    echo "⚠️  Health check failed. Checking logs..."
    echo ""
    sudo journalctl -u chat-api -n 20 --no-pager
fi
