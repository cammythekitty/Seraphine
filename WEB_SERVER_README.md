# Web Server Setup

The bot includes a Flask web server for inviting the bot and displaying Terms & Conditions.

## Features

- 🔗 **Bot Invite Page** - Easy button to add the bot to your server
- 📋 **Terms & Conditions** - Legal terms for bot usage
- 🔒 **Privacy Policy** - Data privacy information
- 🎨 **Beautiful UI** - Modern, responsive design

## Setup

### 1. Update .env File

Add your Bot ID to your `.env` file:

```
DISCORD_TOKEN=your_bot_token_here
BOT_ID=your_bot_id_here
```

**How to find your Bot ID:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Select your application
3. Copy the **CLIENT ID** - this is your BOT_ID

### 2. Run the Web Server

```bash
python web_server.py
```

The server will start at `http://localhost:5000`

### 3. Access the Pages

- **Home/Invite:** http://localhost:5000 (users click the invite button)
- **Terms:** http://localhost:5000/terms
- **Privacy:** http://localhost:5000/privacy

## Deployment

To deploy to the internet (Heroku, Replit, etc.):

1. Change `debug=True` to `debug=False` in `web_server.py`
2. Change `port=5000` to match your hosting environment
3. Update firewall/port settings if needed

### Heroku Example

```bash
pip install gunicorn
echo "web: gunicorn web_server:app" > Procfile
git push heroku main
```

### Replit Example

Run `web_server.py` directly in Replit, then use the provided URL.

## Customization

Edit the HTML templates in the `templates/` folder to customize:
- Colors and styling
- Terms & Conditions content
- Privacy Policy content
- Bot features description

Files:
- `templates/index.html` - Home/invite page
- `templates/terms.html` - Terms & Conditions
- `templates/privacy.html` - Privacy Policy

## Permissions

The invite link uses the following permission:
- **Administrator (8)** - Full permissions

To customize permissions, edit `INVITE_PERMISSIONS` in `web_server.py`:

```python
# Common permission values:
# 0 = No permissions
# 8 = Administrator
# 2048 = Send Messages
# 4096 = Read Message History
# 8192 = Read Messages/View Channels
```

[Permissions Calculator](https://discordapi.com/permissions.html)

## Troubleshooting

**"BOT_ID not set" warning:**
- Add `BOT_ID=your_bot_id` to your `.env` file

**Invite link doesn't work:**
- Verify BOT_ID is correct
- Check that the bot is in Developer Portal

**Port 5000 already in use:**
- Change port: `app.run(port=8000)` in `web_server.py`
