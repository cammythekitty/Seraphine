#!/usr/bin/env python3
"""
Standalone script to force sync Discord slash commands.
Run: python sync_commands.py
"""
import asyncio
import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="", intents=intents)


async def load_cogs():
    """Load all cogs from the cogs directory."""
    cogs_dir = Path('cogs')
    
    if not cogs_dir.exists():
        print('Cogs directory not found.')
        return
    
    for filename in cogs_dir.glob('*.py'):
        if filename.name.startswith('_'):
            continue
        try:
            cog_name = f'cogs.{filename.stem}'
            await bot.load_extension(cog_name)
            print(f'Loaded cog: {cog_name}')
        except Exception as e:
            print(f'Failed to load cog {filename.stem}: {e}')


@bot.event
async def on_ready():
    """Called when the bot is ready."""
    print(f'{bot.user} has connected to Discord!')
    try:
        synced = await bot.tree.sync()
        print(f'\n✅ Synced {len(synced)} command(s)!')
        for cmd in synced:
            print(f'  - {cmd.name}')
        print('\nCommands updated successfully. You can now close this window.')
    except Exception as e:
        print(f'❌ Failed to sync commands: {e}')
    finally:
        await bot.close()


async def main():
    if not TOKEN:
        print('❌ DISCORD_TOKEN not found in .env file!')
        return
    
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == '__main__':
    print('Starting command sync...')
    asyncio.run(main())
