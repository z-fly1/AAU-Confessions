# Quick Deployment Reference

## Your VPS Configuration
- **User**: `botuser`
- **Server**: `46.224.108.57`
- **Project Path**: `/home/botuser/AAU-Confessions`
- **Service Name**: `chat-api`
- **Port**: `10000`

## One-Command Deploy

SSH into your VPS as botuser and run:

```bash
cd ~/AAU-Confessions && bash chat/deploy-chat-api.sh
```

## Useful Commands

### Check Service Status
```bash
sudo systemctl status chat-api
```

### View Live Logs
```bash
sudo journalctl -u chat-api -f
```

### Restart Service
```bash
sudo systemctl restart chat-api
```

### Test API
```bash
# Test locally
curl http://localhost:10000/health

# Test from outside
curl http://46.224.108.57:10000/health
```

### Open Firewall (if needed)
```bash
sudo ufw allow 10000/tcp
```

## After Deployment

1. ✅ Verify health endpoint works
2. ✅ Test web app: https://aauconfessionschat.netlify.app
3. ✅ Monitor logs for any errors

## Troubleshooting

**Service won't start?**
```bash
sudo journalctl -u chat-api -n 50
```

**Can't access from web?**
```bash
# Check firewall
sudo ufw status

# If port 10000 not listed, add it:
sudo ufw allow 10000/tcp
```

**Need to update code?**
```bash
cd ~/AAU-Confessions
git pull  # or copy new files
sudo systemctl restart chat-api
```
