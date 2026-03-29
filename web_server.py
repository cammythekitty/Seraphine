"""
Discord Bot Web Server
Provides invite link and Terms & Conditions
"""
from flask import Flask, render_template, redirect
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Bot configuration
BOT_ID = os.getenv('BOT_ID', 'YOUR_BOT_ID_HERE')
INVITE_PERMISSIONS = 8  # Administrator permission (or customize with specific permissions)


@app.route('/')
def home():
    """Home page with invite button."""
    return render_template('index.html', bot_id=BOT_ID)


@app.route('/invite')
def invite():
    """Redirect to Discord bot invite link."""
    invite_url = f'https://discord.com/api/oauth2/authorize?client_id={BOT_ID}&permissions={INVITE_PERMISSIONS}&scope=bot%20applications.commands'
    return redirect(invite_url)


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
        print('Add BOT_ID=your_bot_id_here to your .env file')
    print('🌐 Starting web server at http://localhost:5000')
    app.run(debug=False, port=4384)
