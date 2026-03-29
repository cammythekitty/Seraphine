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
COMMAND_PREFIX=!
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

Commands are organized into **cogs**. Each cog is a separate file in the `cogs/` directory.

### Example Cog Structure

See `cogs/example.py` for a template. To create a new command:

1. Create a new file in `cogs/` (e.g., `cogs/mycommands.py`)
2. Define your commands in a class that inherits from `commands.Cog`
3. Use the `@commands.command()` decorator
4. The cog will be automatically loaded when the bot starts

```python
class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='mycommand')
    async def my_command(self, ctx):
        await ctx.send('Hello!')

async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

## Features

- 🔄 Automatic cog loading from the `cogs/` directory
- 📋 Command error handling
- 🔗 Slash command support (slash commands synced on startup)
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
5. Enable the required intents
6. Generate an OAuth2 URL with permissions and invite your bot to your server
