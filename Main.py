# A simple discord bot that me and my friends use for custom commands and funnies
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
from pathlib import Path

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration
TOKEN = os.getenv('DISCORD_TOKEN')
COMMAND_PREFIX = os.getenv('COMMAND_PREFIX', '!')

# Create bot instance
intents = discord.Intents.default()
intents.message_content = True  # Required for reading message content
intents.members = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)


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
async def on_command_error(ctx, error):
    """Handle command errors."""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f'Missing required argument: {error.param}')
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send('You do not have permission to use this command.')
    else:
        logger.error(f'Command error: {error}')
        await ctx.send(f'An error occurred: {error}')


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

