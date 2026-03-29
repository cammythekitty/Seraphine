from email.mime import message

import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)

# Config file for storing guild settings
CONFIG_FILE = Path('guild_config.json')


def load_config():
    """Load guild configuration from JSON file."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_config(config):
    """Save guild configuration to JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)


class CommandsCog(commands.Cog):
    """Example cog to demonstrate the structure."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='leter-sucks', description='pings letter and says he sucks')
    async def notify(self, interaction: discord.Interaction):
        """Notifies a hardcoded user."""
        LETER_ID = 1289992140323033152  # Replace with actual ID
        await interaction.response.send_message(f'<@{LETER_ID}> - You Suck! :D')

    @app_commands.command(name='sync', description='Force slash commands to update (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        """Force sync slash commands with Discord."""
        await interaction.response.defer()
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f'✅ Synced {len(synced)} command(s)!', ephemeral=True)
            logger.info(f'Commands synced by {interaction.user}: {len(synced)} commands')
        except Exception as e:
            logger.error(f'Sync failed: {e}')
            await interaction.followup.send(f'❌ Sync failed: {e}', ephemeral=True)

    @sync.error
    async def sync_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle sync command errors."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message('You must be an admin to use this command!', ephemeral=True)

    @app_commands.command(name='ping', description='Responds with pong')
    async def ping(self, interaction: discord.Interaction):
        """A simple ping slash command."""
        await interaction.response.send_message(f'Pong! {round(self.bot.latency * 1000)}ms')
    
    @app_commands.command(name='welcome', description='Greets a user')
    async def welcome(self, interaction: discord.Interaction, user: discord.Member):
        """A greeting slash command that pings the specified user."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id in config and 'roles_channel' in config[guild_id]:
            roles_link = config[guild_id]['roles_channel']
            message = f'Welcome {user.mention}! Roles are here >w< : {roles_link}'
        else:
            message = f'Welcome {user.mention}!'
        
        await interaction.response.send_message(message)
    
    @app_commands.command(name='set-roles-channel', description='Set your server\'s roles channel link (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_roles_channel(self, interaction: discord.Interaction, channel_link: str):
        """Set the roles channel link for this guild."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id not in config:
            config[guild_id] = {}
        
        config[guild_id]['roles_channel'] = channel_link
        save_config(config)
        
        await interaction.response.send_message(f'✅ Roles channel link updated!', ephemeral=True)
    
    @set_roles_channel.error
    async def set_roles_channel_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle set_roles_channel errors."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message('You must be an admin to use this command!', ephemeral=True)
    
    @app_commands.command(name='echo', description='Echoes the user input In Channels Hides Who Wrote It (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def echo(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        """Echoes the user input to a specified channel or current channel."""
        target_channel = channel or interaction.channel
        await interaction.response.defer()
        await target_channel.send(message)

    @echo.error
    async def echo_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """Handle echo command errors."""
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message('You must be an admin to use this command!', ephemeral=True)
        else:
            logger.error(f'Echo command error: {error}')
            await interaction.response.send_message(f'An error occurred: {error}', ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a member joins the server."""
        logger.info(f'{member.name} joined the server')


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(CommandsCog(bot))
