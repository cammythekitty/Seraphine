# Discord Bot Framework

A customizable Discord bot framework for your server with easy command and event management.

## Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env` and add your bot token:
```bash
cp .env.example .env
```

Edit `.env` and add your Discord bot token:
```
DISCORD_TOKEN=your_actual_bot_token_here
```

### 3. Run the Bot
```bash
python Main.py
```

## Project Structure

```
Discord-Bot/
├── Main.py              # Main bot file with setup and event handlers
├── requirements.txt     # Python dependencies
├── .env                 # Environment variables (create from .env.example)
├── .env.example         # Environment variables template
├── README.md           # This file
└── cogs/               # Directory for command cogs
    ├── __init__.py
    └── example.py      # Example cog with sample commands
```

## Creating Commands

Commands are organized into **cogs** and use **slash commands** (`/command`). Each cog is a separate file in the `cogs/` directory.

### Example Cog Structure

See `cogs/example.py` for a template. To create a new slash command:

1. Create a new file in `cogs/` (e.g., `cogs/mycommands.py`)
2. Define your commands in a class that inherits from `commands.Cog`
3. Use the `@app_commands.command()` decorator
4. The cog will be automatically loaded when the bot starts
5. Commands sync with Discord on startup, so they'll appear with `/` in the chat

```python
from discord import app_commands
from discord.ext import commands

class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='mycommand', description='Does something cool')
    async def my_command(self, interaction: discord.Interaction):
        await interaction.response.send_message('Hello!')

async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

## Features

- ⚡ **Slash commands only** - Type `/` to see all available commands
- 🔄 Automatic cog loading from the `cogs/` directory
- 📋 Slash command error handling
- 🪵 Built-in logging
- ⚙️ Environment variable configuration

## Bot Permissions

Your bot needs the following intents enabled in the [Discord Developer Portal](https://discord.com/developers/applications):
- Message Content Intent
- Server Members Intent

## Getting Your Bot Token

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to "Bot" tab and click "Add Bot"
4. Copy the token and paste it into your `.env` file
5. Enable the required intents (Message Content Intent and Server Members Intent)
6. Generate an OAuth2 URL with permissions and invite your bot to your server

## Using Slash Commands

Once your bot is running, you can use slash commands in Discord:

- Type `/` in any channel where the bot is present
- You'll see a list of available commands with descriptions
- Select a command and press Enter
- Your friends will be able to see the commands you're using!
