# Web Server Setup with OAuth2

The bot includes a Flask web server for inviting the bot, user authentication via Discord OAuth2, and displaying Terms & Conditions.

## Features

- 🔗 **Bot Invite Page** - Easy button to add the bot to your server
- 🔐 **Discord OAuth2 Login** - Users can login with their Discord account
- 📋 **Terms & Conditions** - Legal terms for bot usage
- 🔒 **Privacy Policy** - Data privacy information
- 👤 **User Dashboard** - Personalized dashboard for logged-in users
- 🎨 **Beautiful UI** - Modern, responsive design

## Setup

### Step 1: Get Your Credentials

Go to [Discord Developer Portal](https://discord.com/developers/applications):

1. **Get BOT_ID (Client ID):**
   - Select your application
   - Copy the **CLIENT ID** from the General Information page

2. **Get CLIENT_SECRET:**
   - Go to the **OAuth2** tab
   - Copy the **CLIENT SECRET** from the OAuth2 section

### Step 2: Configure OAuth2 Redirect URI

In the Discord Developer Portal:

1. Go to **OAuth2** → **General**
2. Add a **Redirect URL**: `http://localhost:4384/callback`
3. For production, also add your actual domain: `https://yourdomain.com/callback`
4. Save changes

### Step 3: Update .env File

Copy `.env.example` to `.env` and fill in your credentials:

```
DISCORD_TOKEN=your_bot_token_here
BOT_ID=your_bot_id_here
CLIENT_SECRET=your_client_secret_here
REDIRECT_URI=http://localhost:4384/callback
SECRET_KEY=your-flask-secret-key-here
```

**Generate a SECRET_KEY:**
```python
python -c "import secrets; print(secrets.token_hex(32))"
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 5: Run the Web Server

```bash
python web_server.py
```

Server runs at: `http://localhost:4384`

## Usage

### Pages Available

- **`/`** - Home page with invite button
- **`/invite`** - Direct bot invite (no login needed)
- **`/login`** - Login with Discord
- **`/callback`** - OAuth2 callback (automatic)
- **`/dashboard`** - User dashboard (requires login)
- **`/logout`** - Logout
- **`/terms`** - Terms & Conditions
- **`/privacy`** - Privacy Policy

## File Structure

```
/templates/
├── index.html       # Home/invite page
├── dashboard.html   # User dashboard (after login)
├── terms.html       # Terms & Conditions
└── privacy.html     # Privacy Policy
web_server.py        # Flask application
.env                 # Configuration (create from .env.example)
.env.example         # Configuration template
```

## Deployment to Production

### Update Configuration

Before deploying, update your `.env` with production values:

```
REDIRECT_URI=https://yourdomain.com/callback
SECRET_KEY=your-production-secret-key
```

### Heroku Deployment

1. **Create `Procfile`:**
```
web: gunicorn web_server:app
```

2. **Install Gunicorn:**
```bash
pip install gunicorn
```

3. **Deploy:**
```bash
git push heroku main
```

4. **Add environment variables in Heroku:**
```bash
heroku config:set BOT_ID=...
heroku config:set CLIENT_SECRET=...
heroku config:set REDIRECT_URI=https://yourapp.herokuapp.com/callback
heroku config:set SECRET_KEY=...
```

### Replit Deployment

1. Upload files to Replit
2. Install dependencies
3. Run `web_server.py`
4. Use the provided Replit URL

### VPS/DigitalOcean Deployment

1. **Setup Nginx:**
```nginx
server {
    server_name yourdomain.com;
    location / {
        proxy_pass http://127.0.0.1:4384;
    }
}
```

2. **Run with Gunicorn:**
```bash
gunicorn -w 4 -b 127.0.0.1:4384 web_server:app
```

3. **Add SSL with Let's Encrypt:**
```bash
sudo certbot --nginx -d yourdomain.com
```

## Troubleshooting

### "CLIENT_SECRET not set" Error
- Add `CLIENT_SECRET=...` to your `.env` file
- Get it from Discord Developer Portal → OAuth2

### OAuth2 Callback Fails
- Verify `REDIRECT_URI` matches in both `.env` and Discord Developer Portal
- For localhost, use `http://localhost:4384/callback`

### Port Already in Use
- Change port in `web_server.py`: `app.run(port=5000)`
- Or kill the process: `lsof -i :4384 | kill -9`

### Sessions Not Persisting
- Update `SECRET_KEY` in `.env`
- Generate new: `python -c "import secrets; print(secrets.token_hex(32))"`

## Security Notes

- ✅ Never commit `.env` to Git
- ✅ Change `SECRET_KEY` in production
- ✅ Use HTTPS in production (add SSL certificate)
- ✅ Be careful with `CLIENT_SECRET` - don't share it
- ✅ Keep dependencies updated: `pip install --upgrade -r requirements.txt`

## Support

For OAuth2 issues, check:
- [Discord OAuth2 Documentation](https://discord.com/developers/docs/topics/oauth2)
- [Flask Documentation](https://flask.palletsprojects.com/)
