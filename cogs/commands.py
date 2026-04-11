from email.mime import message

import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
import os
from pathlib import Path
from datetime import datetime
import subprocess
import asyncio
import psutil

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
    def __init__(self, bot):
        self.bot = bot

    def _get_allowed_owner_ids(self):
        owner_id = os.getenv('BOT_OWNER_ID')
        co_owner_ids = os.getenv('BOT_CO_OWNER_IDS') or os.getenv('BOT_CO_OWNER_ID')
        allowed = set()
        if owner_id:
            allowed.add(owner_id.strip())
        if co_owner_ids:
            for co_id in co_owner_ids.replace(';', ',').split(','):
                co_id = co_id.strip()
                if co_id:
                    allowed.add(co_id)
        return allowed

    def _is_owner_or_coowner(self, user_id: str) -> bool:
        return str(user_id) in self._get_allowed_owner_ids()
    
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
        await interaction.response.send_message(f'Ban synchronization set up with guild {sync_guild_id}.', ephemeral=True)

    @app_commands.command(name='ban-sync-remove', description='Remove a guild from ban synchronization (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def ban_sync_remove(self, interaction: discord.Interaction, sync_guild_id: str):
        """Remove a guild from ban synchronization."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id in config and 'sync_guilds' in config[guild_id] and sync_guild_id in config[guild_id]['sync_guilds']:
            config[guild_id]['sync_guilds'].remove(sync_guild_id)
            save_config(config)
            await interaction.response.send_message(f'Ban synchronization removed for guild {sync_guild_id}.', ephemeral=True)
        else:
            await interaction.response.send_message(f'Guild {sync_guild_id} is not currently set up for ban synchronization.', ephemeral=True)

    

    def _get_all_connected_guilds(self, guild_id: str, config: dict, visited=None):
        """Recursively find all guilds connected to the given guild through sync relationships."""
        if visited is None:
            visited = set()
        
        if guild_id in visited:
            return set()
        
        visited.add(guild_id)
        connected = set()
        
        # Add guilds this guild syncs to
        if guild_id in config and 'sync_guilds' in config[guild_id]:
            for sync_id in config[guild_id]['sync_guilds']:
                if sync_id not in visited:
                    connected.add(sync_id)
                    # Recursively find guilds connected to this synced guild
                    connected.update(self._get_all_connected_guilds(sync_id, config, visited))
        
        # Add guilds that sync to this guild (bidirectional)
        for other_guild_id, other_config in config.items():
            if 'sync_guilds' in other_config and guild_id in other_config['sync_guilds']:
                if other_guild_id not in visited:
                    connected.add(other_guild_id)
                    # Recursively find guilds connected to this guild
                    connected.update(self._get_all_connected_guilds(other_guild_id, config, visited))
        
        return connected

    async def _sync_ban_to_guilds(self, user_ids, guild_ids: list, reason: str = 'Ban synced from another server'):
        """Ban user(s) across multiple guilds. user_ids can be a single int or a list of ints."""
        # Normalize to list
        if isinstance(user_ids, int):
            user_ids = [user_ids]
        
        ban_count = 0
        for user_id in user_ids:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                logger.warning(f'User with ID {user_id} not found')
                continue
            
            for guild_id in guild_ids:
                try:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        # Check if user is already banned
                        try:
                            await guild.fetch_ban(user)
                            # Already banned, skip
                        except discord.NotFound:
                            # Not banned, proceed with banning
                            await guild.ban(user, reason=reason)
                            ban_count += 1
                            logger.info(f'Banned user {user_id} in guild {guild.name}')
                    else:
                        logger.warning(f'Guild with ID {guild_id} not found')
                except Exception as e:
                    logger.error(f'Error banning user {user_id} in guild {guild_id}: {e}')
        
        return ban_count

    async def _sync_unban_to_guilds(self, user_ids, guild_ids: list, reason: str = 'Unban synced from another server'):
        """Unban user(s) across multiple guilds. user_ids can be a single int or a list of ints."""
        if isinstance(user_ids, int):
            user_ids = [user_ids]

        unban_count = 0
        for user_id in user_ids:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                logger.warning(f'User with ID {user_id} not found')
                continue

            for guild_id in guild_ids:
                try:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        try:
                            await guild.fetch_ban(user)
                            # Is banned, proceed with unbanning
                            await guild.unban(user, reason=reason)
                            unban_count += 1
                            logger.info(f'Unbanned user {user_id} in guild {guild.name}')
                        except discord.NotFound:
                            # Not banned, skip
                            pass
                    else:
                        logger.warning(f'Guild with ID {guild_id} not found')
                except Exception as e:
                    logger.error(f'Error unbanning user {user_id} in guild {guild_id}: {e}')

        return unban_count

    @app_commands.command(name='sync-bans', description='syncs the bans across set guilds (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_bans(self, interaction: discord.Interaction):
        """Syncs bans across all connected guilds in the sync network."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            await interaction.response.send_message('No sync guilds configured for this server.', ephemeral=True)
            return
        
        await interaction.response.defer()
        current_guild = interaction.guild
        
        # Get all connected guilds in the network
        all_connected = self._get_all_connected_guilds(guild_id, config)
        
        if not all_connected:
            await interaction.followup.send('No connected guilds found.', ephemeral=True)
            return
        
        total_bans_synced = 0
        
        try:
            # Get all bans from current guild
            current_bans = [ban async for ban in current_guild.bans()]
            current_banned_ids = {ban.user.id for ban in current_bans}
            
            # Ban all users from current guild in all connected guilds
            synced = await self._sync_ban_to_guilds(
                list(current_banned_ids),
                list(all_connected),
                f'Ban synced from {current_guild.name}'
            )
            total_bans_synced += synced
            
            # Also sync bans from all other connected guilds to current guild
            for connected_id in all_connected:
                try:
                    connected_guild = self.bot.get_guild(int(connected_id))
                    if connected_guild:
                        connected_bans = [ban async for ban in connected_guild.bans()]
                        connected_banned_ids = {ban.user.id for ban in connected_bans}
                        
                        # Ban users from connected guild that aren't already banned in current guild
                        for user_id in connected_banned_ids - current_banned_ids:
                            try:
                                user = await self.bot.fetch_user(user_id)
                                try:
                                    await current_guild.fetch_ban(user)
                                except discord.NotFound:
                                    await current_guild.ban(user, reason=f'Ban synced from {connected_guild.name}')
                                    total_bans_synced += 1
                            except Exception as e:
                                logger.error(f'Error processing ban from {connected_guild.name}: {e}')
                except Exception as e:
                    logger.error(f'Error syncing bans from guild {connected_id}: {e}')
            
            logger.info(f'Ban sync complete: {total_bans_synced} bans synced')
            await interaction.followup.send(f'Ban synchronization complete! Synced {total_bans_synced} ban(s) across {len(all_connected) + 1} guild(s).', ephemeral=True)
        except Exception as e:
            logger.error(f'Error during ban sync: {e}')
            await interaction.followup.send(f'Error during ban sync: {e}', ephemeral=True)

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
            await interaction.followup.send(f'Synced {len(synced)} command(s)!', ephemeral=True)
            logger.info(f'Commands synced by {interaction.user}: {len(synced)} commands')
        except Exception as e:
            logger.error(f'Sync failed: {e}')
            await interaction.followup.send(f'Sync failed: {e}', ephemeral=True)

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
        
        await interaction.response.send_message(f'Roles channel link updated!', ephemeral=True)
    
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
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Called when a user is banned. Automatically syncs the ban across all connected guilds."""
        config = load_config()
        guild_id = str(guild.id)
        
        # Check if this guild has sync guilds configured
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            logger.info(f'No sync guilds configured for {guild.name}, no ban sync triggered')
            return
        
        # Get all connected guilds in the network
        all_connected = self._get_all_connected_guilds(guild_id, config)
        
        if not all_connected:
            logger.info(f'No connected guilds found for ban sync from {guild.name}')
            return
        
        logger.info(f'Ban detected in {guild.name} for user {user.name} (ID: {user.id}). Syncing to {len(all_connected)} connected guild(s)...')
        
        # Ban the user across all connected guilds
        await self._sync_ban_to_guilds(
            user.id,
            list(all_connected),
            f'Ban synced from {guild.name} (ID: {guild.id})'
        )

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        """Called when a user is unbanned. Automatically syncs the unban across all connected guilds."""
        config = load_config()
        guild_id = str(guild.id)

        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            logger.info(f'No sync guilds configured for {guild.name}, no unban sync triggered')
            return

        all_connected = self._get_all_connected_guilds(guild_id, config)

        if not all_connected:
            logger.info(f'No connected guilds found for unban sync from {guild.name}')
            return

        logger.info(f'Unban detected in {guild.name} for user {user.name} (ID: {user.id}). Syncing to {len(all_connected)} connected guild(s)...')

        await self._sync_unban_to_guilds(
            user.id,
            list(all_connected),
            f'Unban synced from {guild.name} (ID: {guild.id})'
        )

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a member joins the server."""
        logger.info(f'{member.name} joined the server')
    
    @app_commands.command(name='bot-logs', description='Dump recent console logs to your DMs (Owner/Co-owner Only)')
    async def logs(self, interaction: discord.Interaction, lines: int = 50):
        """Retrieve and send recent console logs to the user's DMs."""
        await interaction.response.defer(ephemeral=True)
        owner_id = os.getenv('BOT_OWNER_ID')
        
        if not owner_id:
            logger.error('BOT_OWNER_ID not set in environment variables')
            await interaction.response.send_message('Owner ID not configured. Contact the bot administrator.', ephemeral=True)
            return
        
        if not self._is_owner_or_coowner(interaction.user.id):
            logger.warning(f'{interaction.user.name} (ID: {interaction.user.id}) attempted to access logs without permission')
            await interaction.response.send_message('You do not have permission to access bot logs. Only the owner or co-owner can use this command.', ephemeral=True)
            return
        
        try:
            # Validate lines parameter
            if lines < 1:
                await interaction.followup.send('Lines must be at least 1.', ephemeral=True)
                return
            if lines > 500:
                await interaction.followup.send('Lines cannot exceed 500.', ephemeral=True)
                return
            
            # Get logs from the capture handler
            log_lines = self.bot.log_capture.get_logs(lines)
            
            if not log_lines:
                await interaction.followup.send('No logs available yet.', ephemeral=True)
                return
            
            # Format logs into a code block
            logs_text = '\n'.join(log_lines)
            
            # Discord has a message limit of 2000 characters
            # Split into chunks if necessary
            chunks = []
            current_chunk = []
            current_length = 0
            
            for line in log_lines:
                line_with_newline = line + '\n'
                if current_length + len(line_with_newline) > 1900:  # Leave room for code block markers
                    if current_chunk:
                        chunks.append('\n'.join(current_chunk))
                    current_chunk = [line]
                    current_length = len(line) + 1
                else:
                    current_chunk.append(line)
                    current_length += len(line_with_newline)
            
            if current_chunk:
                chunks.append('\n'.join(current_chunk))
            
            # Send logs to user's DMs
            try:
                dm_channel = await interaction.user.create_dm()
                for i, chunk in enumerate(chunks):
                    chunk_num = f' (Part {i+1}/{len(chunks)})' if len(chunks) > 1 else ''
                    message = f'```\n{chunk}\n```'
                    await dm_channel.send(f'**Last {lines} log lines{chunk_num}:**\n{message}')
                
                await interaction.followup.send(f'Sent {lines} log line(s) to your DMs!', ephemeral=True)
                logger.info(f'{interaction.user.name} retrieved {lines} log lines')
            except discord.Forbidden:
                await interaction.followup.send('I cannot send you DMs. Please check your privacy settings.', ephemeral=True)
        except Exception as e:
            logger.error(f'Error retrieving logs: {e}')
            await interaction.followup.send(f'Error retrieving logs: {e}', ephemeral=True)


    @app_commands.command(name='pi-stats', description='Show CPU temp, RAM usage, and uptime of the Pi (Owner/Co-owner Only)')
    async def pi_stats(self, interaction: discord.Interaction):
        """Returns Raspberry Pi system stats. Owner or co-owner only."""
        owner_id = os.getenv('BOT_OWNER_ID')

        if not owner_id:
            await interaction.response.send_message('Owner ID not configured.', ephemeral=True)
            return

        if not self._is_owner_or_coowner(interaction.user.id):
            logger.warning(f'{interaction.user.name} (ID: {interaction.user.id}) attempted to access pi-stats without permission')
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # CPU temperature
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    cpu_temp = int(f.read().strip()) / 1000.0
                temp_str = f'{cpu_temp:.1f}°C'
            except FileNotFoundError:
                temp_str = 'N/A (not on Pi?)'

            # RAM usage
            mem = psutil.virtual_memory()
            ram_used = mem.used / (1024 ** 2)
            ram_total = mem.total / (1024 ** 2)
            ram_percent = mem.percent
            ram_str = f'{ram_used:.0f} MB / {ram_total:.0f} MB ({ram_percent}%)'

            # Uptime
            boot_time = psutil.boot_time()
            uptime_seconds = int(datetime.now().timestamp() - boot_time)
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f'{days}d {hours}h {minutes}m {seconds}s'

            # Disk Usage
            disk = psutil.disk_usage('/')
            disk_used = disk.used / (1024 ** 3)
            disk_total = disk.total / (1024 ** 3)
            disk_percent = disk.percent
            disk_str = f'{disk_used:.1f} GB / {disk_total:.1f} GB ({disk_percent}%)'

            embed = discord.Embed(title='Raspberry Pi Stats', color=discord.Color.green())
            embed.add_field(name='CPU Temp', value=temp_str, inline=True)
            embed.add_field(name='RAM Usage', value=ram_str, inline=True)
            embed.add_field(name='Disk Utilization', value=disk_str, inline=True)
            embed.add_field(name='Uptime', value=uptime_str, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
            logger.info(f'pi-stats retrieved by {interaction.user.name}')
        except Exception as e:
            logger.error(f'Error retrieving pi-stats: {e}')
            await interaction.followup.send(f'Error retrieving stats: {e}', ephemeral=True)

    @app_commands.command(name='reboot', description='Reboot the bot (Owner/Co-owner Only)')
    async def reboot(self, interaction: discord.Interaction):
        """Reboot the bot. Only the owner or co-owner can use this command."""
        # Get the owner UID from environment variable
        owner_id = os.getenv('BOT_OWNER_ID')
        
        if not owner_id:
            logger.error('BOT_OWNER_ID not set in environment variables')
            await interaction.response.send_message('Owner ID not configured. Contact the bot administrator.', ephemeral=True)
            return
        
        if not self._is_owner_or_coowner(interaction.user.id):
            logger.warning(f'{interaction.user.name} (ID: {interaction.user.id}) attempted reboot without permission')
            await interaction.response.send_message('You do not have permission to reboot the bot. Only the owner or co-owner can use this command.', ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send('Rebooting bot...', ephemeral=True)
        logger.info(f'Bot reboot initiated by {interaction.user.name} (ID: {interaction.user.id})')
        
        # Close the bot connection, which will trigger the shutdown and allow the process manager to restart it
        await self.bot.close()

    @app_commands.command(name='shell', description='Run a shell command on the Pi (Owner/Co-owner Only)')
    async def shell(self, interaction: discord.Interaction, command: str):
        """Execute a shell command on the Pi and return the output. Owner or co-owner only."""
        owner_id = os.getenv('BOT_OWNER_ID')

        if not owner_id:
            await interaction.response.send_message('Owner ID not configured.', ephemeral=True)
            return

        if not self._is_owner_or_coowner(interaction.user.id):
            logger.warning(f'{interaction.user.name} (ID: {interaction.user.id}) attempted shell access without permission')
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        logger.info(f'Shell command executed by {interaction.user.name}: {command}')

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                await interaction.followup.send('Command timed out after 30 seconds.', ephemeral=True)
                return

            output = stdout.decode(errors='replace').strip() if stdout else '(no output)'
            exit_code = proc.returncode

            # Split into 1900-char chunks for Discord's limit
            header = f'`$ {command}` (exit {exit_code})\n'
            chunks = []
            remaining = output
            while remaining:
                chunk, remaining = remaining[:1900], remaining[1900:]
                chunks.append(chunk)

            try:
                dm = await interaction.user.create_dm()
                for i, chunk in enumerate(chunks):
                    part = f' (part {i+1}/{len(chunks)})' if len(chunks) > 1 else ''
                    await dm.send(f'{header if i == 0 else ""}`{part}`\n```\n{chunk}\n```')
                await interaction.followup.send('Output sent to your DMs.', ephemeral=True)
            except discord.Forbidden:
                # Fallback: send ephemeral (truncated to first chunk only)
                await interaction.followup.send(f'{header}```\n{chunks[0][:1800]}\n```', ephemeral=True)

        except Exception as e:
            logger.error(f'Shell command error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(CommandsCog(bot))
