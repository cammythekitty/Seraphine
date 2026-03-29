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
    
    @app_commands.command(name='ban-sync-add', description='Set up ban synchronization with another guild (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def ban_sync_add(self, interaction: discord.Interaction, sync_guild_id: str):
        """Set up ban synchronization with another guild."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id not in config:
            config[guild_id] = {}
        
        if 'sync_guilds' not in config[guild_id]:
            config[guild_id]['sync_guilds'] = []
        config[guild_id]['sync_guilds'].append(sync_guild_id)
        save_config(config)
        await interaction.response.send_message(f'✅ Ban synchronization set up with guild {sync_guild_id}.', ephemeral=True)

    @app_commands.command(name='ban-sync-remove', description='Remove a guild from ban synchronization (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def ban_sync_remove(self, interaction: discord.Interaction, sync_guild_id: str):
        """Remove a guild from ban synchronization."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id in config and 'sync_guilds' in config[guild_id] and sync_guild_id in config[guild_id]['sync_guilds']:
            config[guild_id]['sync_guilds'].remove(sync_guild_id)
            save_config(config)
            await interaction.response.send_message(f'✅ Ban synchronization removed for guild {sync_guild_id}.', ephemeral=True)
        else:
            await interaction.response.send_message(f'Guild {sync_guild_id} is not currently set up for ban synchronization.', ephemeral=True)

    

    @app_commands.command(name='sync-bans', description='syncs the bans across set guilds (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_bans(self, interaction: discord.Interaction):
        """Syncs bans across guilds."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            await interaction.response.send_message('No sync guilds configured for this server.', ephemeral=True)
            return
        
        sync_guild_ids = config[guild_id]['sync_guilds']
        current_guild = interaction.guild
        
        for sync_id in sync_guild_ids:
            try:
                sync_guild = self.bot.get_guild(int(sync_id))
                if sync_guild:
                    # Sync bans from current guild to sync guild
                    current_bans = await current_guild.bans()
                    sync_bans = await sync_guild.bans()
                    
                    # Create sets of banned user IDs for comparison
                    current_banned_ids = {ban.user.id for ban in current_bans}
                    sync_banned_ids = {ban.user.id for ban in sync_bans}
                    
                    # Ban users that are banned in current guild but not in sync guild
                    for user_id in current_banned_ids - sync_banned_ids:
                        user = await self.bot.fetch_user(user_id)
                        await sync_guild.ban(user, reason='Ban synced from another server')
                    
                    # Unban users that are banned in sync guild but not in current guild
                    for user_id in sync_banned_ids - current_banned_ids:
                        user = await self.bot.fetch_user(user_id)
                        await sync_guild.unban(user, reason='Unban synced from another server')
                    
                    logger.info(f'Synced bans between {current_guild.name} and {sync_guild.name}')
                else:
                    logger.warning(f'Guild with ID {sync_id} not found for syncing bans.')
            except Exception as e:
                logger.error(f'Error syncing bans with guild ID {sync_id}: {e}')
        
        await interaction.response.send_message('✅ Ban synchronization complete!', ephemeral=True)

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
