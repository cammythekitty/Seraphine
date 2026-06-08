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
        self._allowed_owner_ids = self._load_env_owner_ids()

    def _load_env_owner_ids(self):
        owner_ids = set()
        owner_id = os.getenv('BOT_OWNER_ID')
        co_owner_ids = os.getenv('BOT_CO_OWNER_IDS') or os.getenv('BOT_CO_OWNER_ID')

        if owner_id:
            owner_ids.add(owner_id.strip())
        if co_owner_ids:
            for co_id in co_owner_ids.replace(';', ',').split(','):
                co_id = co_id.strip()
                if co_id:
                    owner_ids.add(co_id)

        # Also load co-owners persisted via /set-co-owner
        config = load_config()
        for co_id in config.get('co_owners', []):
            owner_ids.add(str(co_id))

        return owner_ids

    async def _get_allowed_owner_ids(self):
        if self._allowed_owner_ids:
            return self._allowed_owner_ids

        try:
            app_info = await self.bot.application_info()
            if app_info and app_info.owner:
                self._allowed_owner_ids.add(str(app_info.owner.id))
        except Exception as e:
            logger.warning(f'Could not resolve application owner: {e}')

        return self._allowed_owner_ids

    async def _is_owner_or_coowner(self, user_id: str) -> bool:
        allowed = await self._get_allowed_owner_ids()
        return str(user_id) in allowed
    
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
        

    @app_commands.command(name='update-site', description='Git pull and reload the site (Owner/Co-owner Only)')
    async def update_site(self, interaction: discord.Interaction):
        """Pull latest changes from GitHub and reload nginx."""
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            proc = await asyncio.create_subprocess_shell(
                'git -C /usr/share/nginx/html pull && nginx -s reload',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode(errors='replace').strip() if stdout else '(no output)'
            await interaction.followup.send(f'```\n{output[:1800]}\n```', ephemeral=True)
            logger.info(f'Site update triggered by {interaction.user}: {output[:200]}')
        except asyncio.TimeoutError:
            await interaction.followup.send('git pull timed out after 60 seconds.', ephemeral=True)
        except Exception as e:
            logger.error(f'update-site error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)
    
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
    
    @app_commands.command(name='echo', description='Echoes the user input In Channels (Admin Only)')
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
        await self._post_mod_log(guild, 'Ban', user, self.bot.user, 'User banned (auto-logged)')

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
        if not await self._is_owner_or_coowner(interaction.user.id):
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
        if not await self._is_owner_or_coowner(interaction.user.id):
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
        if not await self._is_owner_or_coowner(interaction.user.id):
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
        if not await self._is_owner_or_coowner(interaction.user.id):
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


    # -------------------------------------------------------------------------
    # Admin commands
    # -------------------------------------------------------------------------

    @app_commands.command(name='kick', description='Kick a member from the server (Admin Only)')
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = 'No reason provided'):
        """Kick a member with an optional reason."""
        if member == interaction.user:
            await interaction.response.send_message('You cannot kick yourself.', ephemeral=True)
            return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message('I cannot kick that member — their role is equal to or higher than mine.', ephemeral=True)
            return
        try:
            await member.kick(reason=f'{reason} (kicked by {interaction.user})')
            await interaction.response.send_message(f'Kicked **{member}** — {reason}', ephemeral=True)
            logger.info(f'{interaction.user} kicked {member} for: {reason}')
            await self._post_mod_log(interaction.guild, 'Kick', member, interaction.user, reason)
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to kick that member.', ephemeral=True)
        except Exception as e:
            logger.error(f'Kick error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='mute', description='Timeout (mute) a member for a duration (Admin Only)')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = 'No reason provided'):
        """Timeout a member using Discord's native timeout. Max 40320 minutes (28 days)."""
        if member == interaction.user:
            await interaction.response.send_message('You cannot mute yourself.', ephemeral=True)
            return
        if minutes < 1 or minutes > 40320:
            await interaction.response.send_message('Duration must be between 1 and 40320 minutes (28 days).', ephemeral=True)
            return
        try:
            import datetime as dt
            until = discord.utils.utcnow() + dt.timedelta(minutes=minutes)
            await member.timeout(until, reason=f'{reason} (by {interaction.user})')
            await interaction.response.send_message(f'Muted **{member}** for {minutes} minute(s) — {reason}', ephemeral=True)
            logger.info(f'{interaction.user} muted {member} for {minutes}m: {reason}')
            await self._post_mod_log(interaction.guild, f'Mute ({minutes}m)', member, interaction.user, reason)
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to timeout that member.', ephemeral=True)
        except Exception as e:
            logger.error(f'Mute error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='unmute', description='Remove a timeout from a member (Admin Only)')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: str = 'No reason provided'):
        """Remove an active timeout from a member."""
        try:
            await member.timeout(None, reason=f'{reason} (by {interaction.user})')
            await interaction.response.send_message(f'Removed timeout from **{member}**.', ephemeral=True)
            logger.info(f'{interaction.user} unmuted {member}')
            await self._post_mod_log(interaction.guild, 'Unmute', member, interaction.user, reason)
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to remove that timeout.', ephemeral=True)
        except Exception as e:
            logger.error(f'Unmute error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='purge', description='Delete messages from a channel, optionally from a specific user (Admin Only)')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int, member: discord.Member = None, channel: discord.TextChannel = None):
        """Bulk-delete messages. If member is provided, only their messages are deleted.
        Scans up to 500 messages to find the requested amount from that user."""
        if amount < 1 or amount > 200:
            await interaction.response.send_message('Amount must be between 1 and 200.', ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            if member is None:
                # No filter — standard purge
                deleted = await target.purge(limit=amount)
                await interaction.followup.send(f'Deleted {len(deleted)} message(s) in {target.mention}.', ephemeral=True)
                logger.info(f'{interaction.user} purged {len(deleted)} messages in #{target.name}')
            else:
                # Scan up to 500 messages and collect the most recent `amount` from this member
                scan_limit = max(amount * 10, 500)
                to_delete = []
                async for msg in target.history(limit=scan_limit):
                    if msg.author == member:
                        to_delete.append(msg)
                    if len(to_delete) >= amount:
                        break

                if not to_delete:
                    await interaction.followup.send(f'No messages from {member.mention} found in the last {scan_limit} messages.', ephemeral=True)
                    return

                # discord.py bulk delete requires messages < 14 days old; filter just in case
                import datetime as dt
                cutoff = discord.utils.utcnow() - dt.timedelta(days=14)
                bulk = [m for m in to_delete if m.created_at > cutoff]
                old_msgs = [m for m in to_delete if m.created_at <= cutoff]

                if bulk:
                    await target.delete_messages(bulk)
                for msg in old_msgs:
                    await msg.delete()
                    await asyncio.sleep(0.5)  # rate-limit safe

                total = len(bulk) + len(old_msgs)
                suffix = f' ({len(old_msgs)} were older than 14 days and deleted individually.)' if old_msgs else ''
                await interaction.followup.send(
                    f'Deleted {total} message(s) from {member.mention} in {target.mention}.{suffix}',
                    ephemeral=True
                )
                logger.info(f'{interaction.user} purged {total} messages from {member} in #{target.name}')
        except discord.Forbidden:
            await interaction.followup.send('I do not have permission to delete messages in that channel.', ephemeral=True)
        except Exception as e:
            logger.error(f'Purge error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='ban-list', description='Show this guild\'s ban list (Admin Only)')
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_list(self, interaction: discord.Interaction):
        """Display the current guild's ban list in pages of 20."""
        await interaction.response.defer(ephemeral=True)
        try:
            bans = [entry async for entry in interaction.guild.bans()]
            if not bans:
                await interaction.followup.send('No bans found in this server.', ephemeral=True)
                return

            page_size = 20
            pages = [bans[i:i + page_size] for i in range(0, len(bans), page_size)]
            embeds = []
            for i, page in enumerate(pages):
                embed = discord.Embed(
                    title=f'Ban List — {interaction.guild.name}',
                    description='\n'.join(f'`{e.user.id}` **{e.user}** — {e.reason or "No reason"}' for e in page),
                    color=discord.Color.red()
                )
                embed.set_footer(text=f'Page {i+1}/{len(pages)} • {len(bans)} total ban(s)')
                embeds.append(embed)

            # Send first page; additional pages follow as separate messages
            await interaction.followup.send(embed=embeds[0], ephemeral=True)
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send('I do not have permission to view the ban list.', ephemeral=True)
        except Exception as e:
            logger.error(f'ban-list error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='ban-sync-list', description='List guilds linked for ban sync (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def ban_sync_list(self, interaction: discord.Interaction):
        """Show which guilds are currently linked for ban sync."""
        config = load_config()
        guild_id = str(interaction.guild.id)

        linked = config.get(guild_id, {}).get('sync_guilds', [])
        if not linked:
            await interaction.response.send_message('No guilds are linked for ban sync on this server.', ephemeral=True)
            return

        lines = []
        for gid in linked:
            g = self.bot.get_guild(int(gid))
            name = g.name if g else '(bot not in this guild)'
            lines.append(f'`{gid}` — {name}')

        embed = discord.Embed(
            title='Ban Sync Linked Guilds',
            description='\n'.join(lines),
            color=discord.Color.blurple()
        )
        embed.set_footer(text=f'{len(linked)} linked guild(s)')
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='set-log-channel', description='Set the moderation log channel (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Store the mod-log channel for this guild."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]['log_channel'] = str(channel.id)
        save_config(config)
        await interaction.response.send_message(f'Mod log channel set to {channel.mention}.', ephemeral=True)
        logger.info(f'Log channel set to #{channel.name} by {interaction.user}')

    async def _post_mod_log(self, guild: discord.Guild, action: str, target: discord.User, moderator: discord.User, reason: str):
        """Post a moderation action embed to the configured log channel."""
        config = load_config()
        guild_id = str(guild.id)
        channel_id = config.get(guild_id, {}).get('log_channel')
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return
        color_map = {
            'Ban': discord.Color.dark_red(),
            'Kick': discord.Color.orange(),
            'Mute': discord.Color.gold(),
            'Unmute': discord.Color.green(),
        }
        color = next((v for k, v in color_map.items() if action.startswith(k)), discord.Color.blurple())
        embed = discord.Embed(title=f'Mod Action: {action}', color=color, timestamp=discord.utils.utcnow())
        embed.add_field(name='User', value=f'{target.mention} (`{target.id}`)', inline=True)
        embed.add_field(name='Moderator', value=moderator.mention, inline=True)
        embed.add_field(name='Reason', value=reason, inline=False)
        try:
            await channel.send(embed=embed)
        except Exception as e:
            logger.error(f'Failed to post mod log: {e}')

    # -------------------------------------------------------------------------
    # Owner / Co-owner commands
    # -------------------------------------------------------------------------

    @app_commands.command(name='cpu-graph', description='Show a CPU usage snapshot over 5 seconds (Owner/Co-owner Only)')
    async def cpu_graph(self, interaction: discord.Interaction):
        """Sample CPU usage 5 times over 5 seconds and display a mini bar chart."""
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            samples = []
            for _ in range(5):
                samples.append(psutil.cpu_percent(interval=1))

            bar_chars = 20
            lines = []
            for i, pct in enumerate(samples, 1):
                filled = int(pct / 100 * bar_chars)
                bar = '█' * filled + '░' * (bar_chars - filled)
                lines.append(f't+{i}s  [{bar}] {pct:.1f}%')

            avg = sum(samples) / len(samples)
            lines.append(f'\nAvg: {avg:.1f}%  |  Cores: {psutil.cpu_count()}')

            embed = discord.Embed(title='CPU Usage (5s sample)', description=f'```\n' + '\n'.join(lines) + '\n```', color=discord.Color.teal())
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            logger.error(f'cpu-graph error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='update', description='Git pull and reboot the bot (Owner/Co-owner Only)')
    async def update(self, interaction: discord.Interaction):
        """Run git pull then restart the bot."""
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            proc = await asyncio.create_subprocess_shell(
                'git pull',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
            output = stdout.decode(errors='replace').strip() if stdout else '(no output)'
            await interaction.followup.send(f'```\n{output[:1800]}\n```\nRebooting...', ephemeral=True)
            logger.info(f'Update + reboot initiated by {interaction.user}: {output[:200]}')
            await self.bot.close()
        except asyncio.TimeoutError:
            await interaction.followup.send('git pull timed out after 60 seconds.', ephemeral=True)
        except Exception as e:
            logger.error(f'Update error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='set-co-owner', description='Add or remove a co-owner by user ID (Owner Only)')
    async def set_co_owner(self, interaction: discord.Interaction, user_id: str, action: str = 'add'):
        """Dynamically add or remove co-owner IDs from the config. action: 'add' or 'remove'."""
        # Only the primary owner (from env) may change co-owners
        primary_id = os.getenv('BOT_OWNER_ID', '').strip()
        if str(interaction.user.id) != primary_id:
            await interaction.response.send_message('Only the primary bot owner can manage co-owners.', ephemeral=True)
            return
        action = action.lower()
        if action not in ('add', 'remove'):
            await interaction.response.send_message('Action must be `add` or `remove`.', ephemeral=True)
            return
        config = load_config()
        co_owners = config.get('co_owners', [])
        if action == 'add':
            if user_id not in co_owners:
                co_owners.append(user_id)
                self._allowed_owner_ids.add(user_id)
            msg = f'Added `{user_id}` as co-owner.'
        else:
            if user_id in co_owners:
                co_owners.remove(user_id)
                self._allowed_owner_ids.discard(user_id)
            msg = f'Removed `{user_id}` from co-owners.'
        config['co_owners'] = co_owners
        save_config(config)
        await interaction.response.send_message(msg, ephemeral=True)
        logger.info(f'{action.capitalize()} co-owner {user_id} by {interaction.user}')

    # -------------------------------------------------------------------------
    # General / informational commands
    # -------------------------------------------------------------------------

    @app_commands.command(name='userinfo', description='Show info about a member')
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member = None):
        """Display account info, join date, roles, and avatar for a member."""
        member = member or interaction.user
        roles = [r.mention for r in reversed(member.roles) if r != interaction.guild.default_role]
        embed = discord.Embed(title=f'{member} — User Info', color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name='ID', value=str(member.id), inline=True)
        embed.add_field(name='Nickname', value=member.nick or 'None', inline=True)
        embed.add_field(name='Bot?', value='Yes' if member.bot else 'No', inline=True)
        embed.add_field(name='Account Created', value=discord.utils.format_dt(member.created_at, 'R'), inline=True)
        embed.add_field(name='Joined Server', value=discord.utils.format_dt(member.joined_at, 'R'), inline=True)
        embed.add_field(name=f'Roles ({len(roles)})', value=' '.join(roles[:10]) or 'None', inline=False)
        if member.timed_out_until and member.timed_out_until > discord.utils.utcnow():
            embed.add_field(name='Timed Out Until', value=discord.utils.format_dt(member.timed_out_until, 'R'), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='serverinfo', description='Show info about this server')
    async def serverinfo(self, interaction: discord.Interaction):
        """Display guild stats: member count, creation date, boost level, owner."""
        guild = interaction.guild
        embed = discord.Embed(title=guild.name, color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name='ID', value=str(guild.id), inline=True)
        embed.add_field(name='Owner', value=guild.owner.mention if guild.owner else 'Unknown', inline=True)
        embed.add_field(name='Created', value=discord.utils.format_dt(guild.created_at, 'R'), inline=True)
        embed.add_field(name='Members', value=str(guild.member_count), inline=True)
        embed.add_field(name='Channels', value=str(len(guild.channels)), inline=True)
        embed.add_field(name='Roles', value=str(len(guild.roles)), inline=True)
        embed.add_field(name='Boost Level', value=str(guild.premium_tier), inline=True)
        embed.add_field(name='Boosts', value=str(guild.premium_subscription_count), inline=True)
        embed.add_field(name='Verification Level', value=str(guild.verification_level).capitalize(), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name='announce', description='Send a formatted announcement embed (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def announce(self, interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel = None):
        """Send a rich embed announcement to a channel."""
        target = channel or interaction.channel
        embed = discord.Embed(title=title, description=message, color=discord.Color.gold(), timestamp=discord.utils.utcnow())
        embed.set_footer(text=f'Announced by {interaction.user.display_name}', icon_url=interaction.user.display_avatar.url)
        try:
            await target.send(embed=embed)
            await interaction.response.send_message(f'Announcement sent to {target.mention}.', ephemeral=True)
            logger.info(f'{interaction.user} announced in #{target.name}: {title}')
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to send messages in that channel.', ephemeral=True)
        except Exception as e:
            logger.error(f'Announce error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(CommandsCog(bot))