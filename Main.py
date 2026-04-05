# A simple discord bot that me and my friends use for custom commands and funnies
import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
import logging
from pathlib import Path
from collections import deque

# Load environment variables
load_dotenv()

# Custom logging handler to store logs in memory
class LogCapture(logging.Handler):
    """Custom handler that stores logs in memory for retrieval."""
    def __init__(self, max_logs=500):
        super().__init__()
        self.logs = deque(maxlen=max_logs)
    
    def emit(self, record):
        self.logs.append(self.format(record))
    
    def get_logs(self, lines=50):
        """Get the last N lines of logs."""
        return list(self.logs)[-lines:]

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add custom log capture handler
log_capture = LogCapture(max_logs=500)
log_capture.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(log_capture)

# Bot configuration
TOKEN = os.getenv('DISCORD_TOKEN')

# Create bot instance (slash commands only)
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message content
intents.members = True

bot = commands.Bot(command_prefix="", intents=intents)
bot.log_capture = log_capture  # Attach log capture handler to bot for cogs to access


# Events
@bot.event
async def on_ready():
    """Called when the bot is ready."""
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot ID: {bot.user.id}')
    try:
        synced = await bot.tree.sync()
        logger.info(f'Synced {len(synced)} command(s)')
    except Exception as e:
        logger.error(f'Failed to sync commands: {e}')


@bot.event
async def on_message(message):
    """Override on_message to prevent prefix command processing (slash commands only)."""
    if message.author == bot.user:
        return
    # Don't process prefix commands - only use slash commands


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle slash command errors."""
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message('You do not have permission to use this command.', ephemeral=True)
    else:
        logger.error(f'Command error: {error}')
        await interaction.response.send_message(f'An error occurred: {error}', ephemeral=True)


async def load_cogs():
    """Load all cogs from the cogs directory."""
    cogs_dir = Path('cogs')
    
    if not cogs_dir.exists():
        logger.warning('Cogs directory not found. Creating it...')
        cogs_dir.mkdir()
        return
    
    for filename in cogs_dir.glob('*.py'):
        if filename.name.startswith('_'):
            continue
        try:
            cog_name = f'cogs.{filename.stem}'
            await bot.load_extension(cog_name)
            logger.info(f'Loaded cog: {cog_name}')
        except Exception as e:
            logger.error(f'Failed to load cog {filename.stem}: {e}')


async def main():
    """Main function to run the bot."""
    if not TOKEN:
        logger.error('DISCORD_TOKEN not found in environment variables!')
        return
    
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

