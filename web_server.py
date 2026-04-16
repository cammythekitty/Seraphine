"""
Discord Bot Web Server with OAuth2
Provides invite link, user authentication, and Terms & Conditions
"""
from flask import Flask, render_template, redirect, url_for, request, session
import os
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'discord-bot-secret-key')

PROFILE_FILE = Path('profiles.json')

def load_profiles():
    if PROFILE_FILE.exists():
        with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_profiles(data):
    with open(PROFILE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

# Bot configuration
BOT_ID = os.getenv('BOT_ID', 'YOUR_BOT_ID_HERE')
CLIENT_SECRET = os.getenv('CLIENT_SECRET', 'YOUR_CLIENT_SECRET_HERE')
REDIRECT_URI = os.getenv('REDIRECT_URI', 'http://localhost:4384/callback')
INVITE_PERMISSIONS = 8  # Administrator permission

DISCORD_API_BASE = 'https://discord.com/api/v10'
DISCORD_AUTH_URL = 'https://discord.com/api/oauth2/authorize'
DISCORD_TOKEN_URL = f'{DISCORD_API_BASE}/oauth2/token'


@app.route('/')
def home():
    """Home page with invite button."""
    return render_template('index.html', bot_id=BOT_ID)


@app.route('/invite')
def invite():
    """Redirect to Discord bot invite link (no OAuth needed)."""
    invite_url = f'{DISCORD_AUTH_URL}?client_id={BOT_ID}&permissions={INVITE_PERMISSIONS}&scope=bot%20applications.commands'
    return redirect(invite_url)


@app.route('/login')
def login():
    """Initiate OAuth2 login with Discord."""
    if CLIENT_SECRET == 'YOUR_CLIENT_SECRET_HERE':
        return 'Error: CLIENT_SECRET not set in .env file', 400
    
    auth_url = f'{DISCORD_AUTH_URL}?client_id={BOT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds'
    return redirect(auth_url)


@app.route('/callback')
def callback():
    """Handle Discord OAuth2 callback."""
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        return f'Error: {error}', 400
    
    if not code:
        return 'Error: No authorization code received', 400
    
    if CLIENT_SECRET == 'YOUR_CLIENT_SECRET_HERE':
        return 'Error: CLIENT_SECRET not set in .env file', 500
    
    try:
        # Exchange code for access token
        data = {
            'client_id': BOT_ID,
            'client_secret': CLIENT_SECRET,
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': REDIRECT_URI,
            'scope': 'identify'
        }
        
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        response = requests.post(DISCORD_TOKEN_URL, data=data, headers=headers)
        
        if response.status_code != 200:
            return f'OAuth Error {response.status_code}: {response.text}', 400
        
        token_data = response.json()
        access_token = token_data.get('access_token')
        
        if not access_token:
            return 'Error: Failed to get access token', 400
        
        # Get user info
        headers = {'Authorization': f'Bearer {access_token}'}
        user_response = requests.get(f'{DISCORD_API_BASE}/users/@me', headers=headers)
        
        if user_response.status_code != 200:
            return f'Error getting user info: {user_response.text}', 400
        
        user_data = user_response.json()
        
        # Store in session
        session['user_id'] = user_data.get('id')
        session['username'] = user_data.get('username')
        session['avatar'] = user_data.get('avatar')
        session['access_token'] = access_token
        
        return redirect(url_for('dashboard'))
    
    except Exception as e:
        return f'Error during authentication: {str(e)}', 500


@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    """User dashboard after OAuth2 login."""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session.get('user_id')
    profiles = load_profiles()
    profile = profiles.get(user_id, {})
    message = None

    if request.method == 'POST':
        profile_text = request.form.get('profile_text', '').strip()
        profile['text'] = profile_text
        profile['updated_at'] = datetime.utcnow().isoformat() + 'Z'
        profiles[user_id] = profile
        save_profiles(profiles)
        message = 'Your profile has been updated.'

    return render_template('dashboard.html', 
                         username=session.get('username'),
                         user_id=user_id,
                         profile=profile,
                         message=message)


@app.route('/logout')
def logout():
    """Logout and clear session."""
    session.clear()
    return redirect(url_for('home'))


@app.route('/terms')
def terms():
    """Terms and Conditions page."""
    return render_template('terms.html')


@app.route('/privacy')
def privacy():
    """Privacy Policy page."""
    return render_template('privacy.html')


if __name__ == '__main__':
    if BOT_ID == 'YOUR_BOT_ID_HERE':
        print('⚠️  Warning: BOT_ID not set in .env file!')
    if CLIENT_SECRET == 'YOUR_CLIENT_SECRET_HERE':
        print('⚠️  Warning: CLIENT_SECRET not set in .env file!')
        print('   (Required for OAuth2 login to work)')
    if REDIRECT_URI == 'http://localhost:4384/callback':
        print('ℹ️  Using localhost REDIRECT_URI - update for production!')
    
    print('🌐 Starting web server at http://localhost:4384')
    app.run(debug=False, port=4384, host='0.0.0.0')
