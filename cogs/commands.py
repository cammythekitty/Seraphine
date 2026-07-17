import discord
from discord.ext import commands
from discord import app_commands
import logging
import json
import os
import sys
from pathlib import Path
from datetime import datetime
import asyncio
import psutil
import datetime as dt
import websockets

logger = logging.getLogger(__name__)

# Config file using cross-platform path resolution
CONFIG_FILE = Path('guild_config.json')


def load_config():
    """Load guild configuration from JSON file."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_config(config):
    """Save guild configuration to JSON file."""
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


class CommandsCog(commands.Cog):    
    def __init__(self, bot):
        self.bot = bot
        self._allowed_owner_ids = self._load_env_owner_ids()
        self.ai_enabled = False
        self.SERAPHBYTE_WS = 'ws://127.0.0.1:8543'
        self.CONTEXT_LIMIT = 10  # messages of context to pass

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
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id in config and 'sync_guilds' in config[guild_id] and sync_guild_id in config[guild_id]['sync_guilds']:
            config[guild_id]['sync_guilds'].remove(sync_guild_id)
            save_config(config)
            await interaction.response.send_message(f'Ban synchronization removed for guild {sync_guild_id}.', ephemeral=True)
        else:
            await interaction.response.send_message(f'Guild {sync_guild_id} is not currently set up for ban synchronization.', ephemeral=True)

    def _get_all_connected_guilds(self, guild_id: str, config: dict, visited=None):
        if visited is None:
            visited = set()
        
        if guild_id in visited:
            return set()
        
        visited.add(guild_id)
        connected = set()
        
        if guild_id in config and 'sync_guilds' in config[guild_id]:
            for sync_id in config[guild_id]['sync_guilds']:
                if sync_id not in visited:
                    connected.add(sync_id)
                    connected.update(self._get_all_connected_guilds(sync_id, config, visited))
        
        for other_guild_id, other_config in config.items():
            if 'sync_guilds' in other_config and guild_id in other_config['sync_guilds']:
                if other_guild_id not in visited:
                    connected.add(other_guild_id)
                    connected.update(self._get_all_connected_guilds(other_guild_id, config, visited))
        
        return connected

    async def _sync_ban_to_guilds(self, user_ids, guild_ids: list, reason: str = 'Ban synced from another server'):
        if isinstance(user_ids, int):
            user_ids = [user_ids]
        
        ban_count = 0
        for user_id in user_ids:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                continue
            
            for guild_id in guild_ids:
                try:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        try:
                            await guild.fetch_ban(user)
                        except discord.NotFound:
                            await guild.ban(user, reason=reason)
                            ban_count += 1
                except Exception as e:
                    logger.error(f'Error banning user {user_id} in guild {guild_id}: {e}')
        return ban_count

    async def _sync_unban_to_guilds(self, user_ids, guild_ids: list, reason: str = 'Unban synced from another server'):
        if isinstance(user_ids, int):
            user_ids = [user_ids]

        unban_count = 0
        for user_id in user_ids:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.NotFound:
                continue

            for guild_id in guild_ids:
                try:
                    guild = self.bot.get_guild(int(guild_id))
                    if guild:
                        try:
                            await guild.fetch_ban(user)
                            await guild.unban(user, reason=reason)
                            unban_count += 1
                        except discord.NotFound:
                            pass
                except Exception as e:
                    logger.error(f'Error unbanning user {user_id} in guild {guild_id}: {e}')
        return unban_count

    @app_commands.command(name='sync-bans', description='Syncs the bans across set guilds (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync_bans(self, interaction: discord.Interaction):
        config = load_config()
        guild_id = str(interaction.guild.id)
        
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            await interaction.response.send_message('No sync guilds configured for this server.', ephemeral=True)
            return
        
        await interaction.response.defer()
        current_guild = interaction.guild
        all_connected = self._get_all_connected_guilds(guild_id, config)
        
        if not all_connected:
            await interaction.followup.send('No connected guilds found.', ephemeral=True)
            return
        
        total_bans_synced = 0
        try:
            current_bans = [ban async for ban in current_guild.bans()]
            current_banned_ids = {ban.user.id for ban in current_bans}
            
            synced = await self._sync_ban_to_guilds(list(current_banned_ids), list(all_connected), f'Ban synced from {current_guild.name}')
            total_bans_synced += synced
            
            for connected_id in all_connected:
                try:
                    connected_guild = self.bot.get_guild(int(connected_id))
                    if connected_guild:
                        connected_bans = [ban async for ban in connected_guild.bans()]
                        connected_banned_ids = {ban.user.id for ban in connected_bans}
                        
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
            
            await interaction.followup.send(f'Ban synchronization complete! Synced {total_bans_synced} ban(s) across {len(all_connected) + 1} guild(s).', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'Error during ban sync: {e}', ephemeral=True)
        
    @app_commands.command(name='sync', description='Force slash commands to update (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f'Synced {len(synced)} command(s)!', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'Sync failed: {e}', ephemeral=True)

    @app_commands.command(name='ping', description='Responds with pong')
    async def ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(f'Pong! {round(self.bot.latency * 1000)}ms')
    
    @app_commands.command(name='welcome', description='Greets a user')
    async def welcome(self, interaction: discord.Interaction, user: discord.Member):
        config = load_config()
        guild_id = str(interaction.guild.id)
        if guild_id in config and 'roles_channel' in config[guild_id]:
            roles_link = config[guild_id]['roles_channel']
            msg = f'Welcome {user.mention}! Roles are here >w< : {roles_link}'
        else:
            msg = f'Welcome {user.mention}!'
        await interaction.response.send_message(msg)
    
    @app_commands.command(name='set-roles-channel', description='Set your server\'s roles channel link (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_roles_channel(self, interaction: discord.Interaction, channel_link: str):
        config = load_config()
        guild_id = str(interaction.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]['roles_channel'] = channel_link
        save_config(config)
        await interaction.response.send_message('Roles channel link updated!', ephemeral=True)
    
    @app_commands.command(name='echo', description='Echoes the user input In Channels (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def echo(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        target_channel = channel or interaction.channel
        await interaction.response.defer()
        await target_channel.send(message)

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        config = load_config()
        guild_id = str(guild.id)
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            return
        all_connected = self._get_all_connected_guilds(guild_id, config)
        if not all_connected:
            return
        await self._sync_ban_to_guilds(user.id, list(all_connected), f'Ban synced from {guild.name} (ID: {guild.id})')
        await self._post_mod_log(guild, 'Ban', user, self.bot.user, 'User banned (auto-logged)')

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        config = load_config()
        guild_id = str(guild.id)
        if guild_id not in config or 'sync_guilds' not in config[guild_id]:
            return
        all_connected = self._get_all_connected_guilds(guild_id, config)
        if not all_connected:
            return
        await self._sync_unban_to_guilds(user.id, list(all_connected), f'Unban synced from {guild.name} (ID: {guild.id})')

    @app_commands.command(name='bot-logs', description='Dump recent console logs to your DMs (Owner/Co-owner Only)')
    async def logs(self, interaction: discord.Interaction, lines: int = 50):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.followup.send('You do not have permission to access bot logs.', ephemeral=True)
            return
        
        if lines < 1 or lines > 500:
            await interaction.followup.send('Lines must be between 1 and 500.', ephemeral=True)
            return
            
        log_lines = self.bot.log_capture.get_logs(lines)
        if not log_lines:
            await interaction.followup.send('No logs available yet.', ephemeral=True)
            return
            
        chunks = []
        current_chunk = []
        current_length = 0
        for line in log_lines:
            if current_length + len(line) + 1 > 1900:
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_length = len(line)
            else:
                current_chunk.append(line)
                current_length += len(line) + 1
        if current_chunk:
            chunks.append('\n'.join(current_chunk))
            
        try:
            dm_channel = await interaction.user.create_dm()
            for i, chunk in enumerate(chunks):
                chunk_num = f' (Part {i+1}/{len(chunks)})' if len(chunks) > 1 else ''
                await dm_channel.send(f'**Last {lines} log lines{chunk_num}:**\n```\n{chunk}\n```')
            await interaction.followup.send(f'Sent {lines} log line(s) to your DMs!', ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send('I cannot send you DMs. Please check your privacy settings.', ephemeral=True)

    @app_commands.command(name='system-stats', description='Show CPU temp, RAM usage, and uptime (Owner/Co-owner Only)')
    async def system_stats(self, interaction: discord.Interaction):
        """Cross-platform hardware statistics command (renamed from pi-stats)."""
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Cross-platform CPU temperature resolution
            temp_str = 'N/A'
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
                # Try finding common hardware labels across Linux/macOS/Windows systems
                for key in ['cpu_thermal', 'coretemp', 'cpu', 'acpitz']:
                    if key in temps and temps[key]:
                        temp_str = f'{temps[key][0].current:.1f}°C'
                        break
            
            # Fallback pathing check for legacy Linux machines if psutil lookup failed
            if temp_str == 'N/A' and os.path.exists('/sys/class/thermal/thermal_zone0/temp'):
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp_str = f'{int(f.read().strip()) / 1000.0:.1f}°C'

            # RAM usage
            mem = psutil.virtual_memory()
            ram_str = f'{mem.used / (1024 ** 2):.0f} MB / {mem.total / (1024 ** 2):.0f} MB ({mem.percent}%)'

            # Uptime computation
            uptime_seconds = int(datetime.now().timestamp() - psutil.boot_time())
            days, remainder = divmod(uptime_seconds, 86400)
            hours, remainder = divmod(remainder, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f'{days}d {hours}h {minutes}m {seconds}s'

            # Disk Usage
            disk = psutil.disk_usage('/')
            disk_str = f'{disk.used / (1024 ** 3):.1f} GB / {disk.total / (1024 ** 3):.1f} GB ({disk.percent}%)'

            embed = discord.Embed(title='System Diagnostic Stats', color=discord.Color.green())
            embed.add_field(name='OS Platform', value=sys.platform.capitalize(), inline=True)
            embed.add_field(name='CPU Temp', value=temp_str, inline=True)
            embed.add_field(name='RAM Usage', value=ram_str, inline=True)
            embed.add_field(name='Disk Utilization', value=disk_str, inline=True)
            embed.add_field(name='System Uptime', value=uptime_str, inline=False)

            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'Error retrieving stats: {e}', ephemeral=True)

    @app_commands.command(name='reboot', description='Reboot the bot (Owner/Co-owner Only)')
    async def reboot(self, interaction: discord.Interaction):
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('You do not have permission to reboot the bot.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send('Rebooting bot...', ephemeral=True)
        await self.bot.close()

    @app_commands.command(name='shell', description='Run a command inside the host terminal shell (Owner/Co-owner Only)')
    async def shell(self, interaction: discord.Interaction, command: str):
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner or co-owner can use this command.', ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Cross-platform execution flag adjustments
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
            header = f'`$ {command}` (exit {proc.returncode})\n'
            
            chunks = []
            remaining = output
            while remaining:
                chunks.append(remaining[:1900])
                remaining = remaining[1900:]

            try:
                dm = await interaction.user.create_dm()
                for i, chunk in enumerate(chunks):
                    part = f' (part {i+1}/{len(chunks)})' if len(chunks) > 1 else ''
                    await dm.send(f'{header if i == 0 else ""}`{part}`\n```\n{chunk}\n```')
                await interaction.followup.send('Output sent to your DMs.', ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(f'{header}\n```\n{output}\n```', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'Error executing command: {e}', ephemeral=True)
    @app_commands.command(name='kick', description='Kick a member from the server (Admin Only)')
    @app_commands.checks.has_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = 'No reason provided'):
        """Kick a member from the server."""
        if member == interaction.user:
            await interaction.response.send_message('You cannot kick yourself.', ephemeral=True)
            return
        if member.top_role >= interaction.guild.me.top_role:
            await interaction.response.send_message('I cannot kick that member — their role is equal to or higher than mine.', ephemeral=True)
            return
        try:
            await member.kick(reason=f'{reason} (kicked by {interaction.user})')
            await interaction.response.send_message(f'Kicked **{member}** — {reason}', ephemeral=True)
            await self._post_mod_log(interaction.guild, 'Kick', member, interaction.user, reason)
            logger.info(f'{interaction.user} kicked {member} from {interaction.guild.name}. Reason: {reason}')
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to kick that member.', ephemeral=True)
        except Exception as e:
            logger.error(f'Kick error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='mute', description='Timeout (mute) a member for a duration (Admin Only)')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member, minutes: int, reason: str = 'No reason provided'):
        """Apply a native timeout to a member."""
        if member == interaction.user:
            await interaction.response.send_message('You cannot mute yourself.', ephemeral=True)
            return
        if minutes < 1 or minutes > 40320:
            await interaction.response.send_message('Duration must be between 1 and 40320 minutes (28 days).', ephemeral=True)
            return
        try:
            until = discord.utils.utcnow() + dt.timedelta(minutes=minutes)
            await member.timeout(until, reason=f'{reason} (by {interaction.user})')
            await interaction.response.send_message(f'Muted **{member}** for {minutes} minute(s) — {reason}', ephemeral=True)
            await self._post_mod_log(interaction.guild, f'Mute ({minutes}m)', member, interaction.user, reason)
            logger.info(f'{interaction.user} muted {member} for {minutes}m. Reason: {reason}')
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to timeout that member.', ephemeral=True)
        except Exception as e:
            logger.error(f'Mute error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='unmute', description='Remove a timeout from a member (Admin Only)')
    @app_commands.checks.has_permissions(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member, reason: str = 'No reason provided'):
        """Remove a timeout from a member early."""
        try:
            await member.timeout(None, reason=f'{reason} (by {interaction.user})')
            await interaction.response.send_message(f'Removed timeout from **{member}**.', ephemeral=True)
            await self._post_mod_log(interaction.guild, 'Unmute', member, interaction.user, reason)
            logger.info(f'{interaction.user} unmuted {member} in {interaction.guild.name}.')
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to remove that timeout.', ephemeral=True)
        except Exception as e:
            logger.error(f'Unmute error: {e}')
            await interaction.response.send_message(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='purge', description='Delete messages from a channel, optionally from a specific user (Admin Only)')
    @app_commands.checks.has_permissions(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: int, member: discord.Member = None, channel: discord.TextChannel = None):
        """Purge messages with bulk and single-delete fallbacks for items older than 14 days."""
        if amount < 1 or amount > 200:
            await interaction.response.send_message('Amount must be between 1 and 200.', ephemeral=True)
            return
        target = channel or interaction.channel
        await interaction.response.defer(ephemeral=True)
        try:
            if member is None:
                deleted = await target.purge(limit=amount)
                await interaction.followup.send(f'Deleted {len(deleted)} message(s) in {target.mention}.', ephemeral=True)
                logger.info(f'{interaction.user} purged {len(deleted)} messages in #{target.name}.')
            else:
                scan_limit = max(amount * 10, 500)
                to_delete = []
                async for msg in target.history(limit=scan_limit):
                    if msg.author == member:
                        to_delete.append(msg)
                    if len(to_delete) >= amount:
                        break

                if not to_delete:
                    await interaction.followup.send(f'No messages from {member.mention} found.', ephemeral=True)
                    return

                cutoff = discord.utils.utcnow() - dt.timedelta(days=14)
                bulk = [m for m in to_delete if m.created_at > cutoff]
                old_msgs = [m for m in to_delete if m.created_at <= cutoff]

                if bulk:
                    await target.delete_messages(bulk)
                for msg in old_msgs:
                    await msg.delete()
                    await asyncio.sleep(0.5)

                total = len(bulk) + len(old_msgs)
                suffix = f' ({len(old_msgs)} were old and deleted manually.)' if old_msgs else ''
                await interaction.followup.send(f'Deleted {total} message(s) from {member.mention}.{suffix}', ephemeral=True)
                logger.info(f'{interaction.user} purged {total} messages from {member} in #{target.name}.')
        except discord.Forbidden:
            await interaction.followup.send('I do not have permission to delete messages.', ephemeral=True)
        except Exception as e:
            logger.error(f'Purge error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='ban-list', description='Show this guild\'s ban list (Admin Only)')
    @app_commands.checks.has_permissions(ban_members=True)
    async def ban_list(self, interaction: discord.Interaction):
        """Display a multi-page list of banned users."""
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

            await interaction.followup.send(embed=embeds[0], ephemeral=True)
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send('I do not have permission to view the ban list.', ephemeral=True)
        except Exception as e:
            logger.error(f'Ban-list error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='ban-sync-list', description='List guilds linked for ban sync (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def ban_sync_list(self, interaction: discord.Interaction):
        """List out all the numerical server IDs synced into this network node."""
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

        embed = discord.Embed(title='Ban Sync Linked Guilds', description='\n'.join(lines), color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name='set-log-channel', description='Set the moderation log channel (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        """Identify where embedded receipt logs should be posted."""
        config = load_config()
        guild_id = str(interaction.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]['log_channel'] = str(channel.id)
        save_config(config)
        await interaction.response.send_message(f'Mod log channel set to {channel.mention}.', ephemeral=True)

    async def _post_mod_log(self, guild: discord.Guild, action: str, target: discord.User, moderator: discord.User, reason: str):
        """Dispatches structured moderation events to the channel saved in config."""
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

    @app_commands.command(name='cpu-graph', description='Show a CPU usage snapshot over 5 seconds (Owner/Co-owner Only)')
    async def cpu_graph(self, interaction: discord.Interaction):
        """Samples real-time system process metrics over 5 sequential intervals."""
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
            logger.error(f'CPU Graph error: {e}')
            await interaction.followup.send(f'Error: {e}', ephemeral=True)

    @app_commands.command(name='update', description='Git pull and reboot the bot (Owner/Co-owner Only)')
    async def update(self, interaction: discord.Interaction):
        """Pull fresh repository commits and shutdown client loop for host reloading."""
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
            await interaction.followup.send(f'Update result:\n```\n{output}\n```', ephemeral=True)
            logger.info(f'Bot updated: {output}')
            await self.bot.close()
        except asyncio.TimeoutError:
            await interaction.followup.send('Update took too long (> 60s). Manual git pull may be needed.', ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f'Update failed: {e}', ephemeral=True)

    @app_commands.command(name='set-co-owner', description='Add or remove co-owner status (Owner Only)')
    async def set_co_owner(self, interaction: discord.Interaction, user_id: str, action: str):
        """Manage co-owner IDs dynamically (Primary Owner Only)."""
        if not await self._is_owner_or_coowner(interaction.user.id):
            await interaction.response.send_message('Only the owner can use this command.', ephemeral=True)
            return
        
        # Verify they're the primary owner (not just co-owner)
        primary_owner = os.getenv('BOT_OWNER_ID')
        if str(interaction.user.id) != primary_owner:
            await interaction.response.send_message('Only the primary owner can manage co-owners.', ephemeral=True)
            return
        
        config = load_config()
        if 'co_owners' not in config:
            config['co_owners'] = []
        
        if action.lower() == 'add':
            if user_id not in config['co_owners']:
                config['co_owners'].append(user_id)
                save_config(config)
                self._allowed_owner_ids.add(user_id)
                await interaction.response.send_message(f'Added `{user_id}` as co-owner.', ephemeral=True)
            else:
                await interaction.response.send_message(f'`{user_id}` is already a co-owner.', ephemeral=True)
        
        elif action.lower() == 'remove':
            if user_id in config['co_owners']:
                config['co_owners'].remove(user_id)
                save_config(config)
                self._allowed_owner_ids.discard(user_id)
                await interaction.response.send_message(f'Removed `{user_id}` from co-owners.', ephemeral=True)
            else:
                await interaction.response.send_message(f'`{user_id}` is not a co-owner.', ephemeral=True)
        else:
            await interaction.response.send_message('Action must be "add" or "remove".', ephemeral=True)

    @app_commands.command(name='announce', description='Send a styled announcement to a channel (Admin Only)')
    @app_commands.checks.has_permissions(administrator=True)
    async def announce(self, interaction: discord.Interaction, title: str, message: str, channel: discord.TextChannel = None):
        """Send a golden-styled announcement embed to a channel."""
        target_channel = channel or interaction.channel
        embed = discord.Embed(
            title=title,
            description=message,
            color=discord.Color.gold()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        embed.timestamp = discord.utils.utcnow()
        
        try:
            await target_channel.send(embed=embed)
            await interaction.response.send_message(f'Announcement sent to {target_channel.mention}!', ephemeral=True)
            logger.info(f'{interaction.user} posted announcement to {target_channel.name}')
        except discord.Forbidden:
            await interaction.response.send_message('I do not have permission to send messages in that channel.', ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f'Error sending announcement: {e}', ephemeral=True)

    @app_commands.command(name='userinfo', description='Display detailed information about a user')
    async def userinfo(self, interaction: discord.Interaction, user: discord.Member = None):
        """Show comprehensive user profile card."""
        target = user or interaction.user
        
        embed = discord.Embed(
            title=f'User Information',
            description=f'{target.mention}',
            color=target.color or discord.Color.default()
        )
        
        # Account age
        account_age = discord.utils.utcnow() - target.created_at
        embed.add_field(name='Account Created', value=f'{target.created_at.strftime("%Y-%m-%d %H:%M:%S")} UTC\n({account_age.days} days old)', inline=False)
        
        # Join date
        if isinstance(target, discord.Member):
            join_age = discord.utils.utcnow() - target.joined_at
            embed.add_field(name='Joined Server', value=f'{target.joined_at.strftime("%Y-%m-%d %H:%M:%S")} UTC\n({join_age.days} days ago)', inline=False)
        
        # User ID
        embed.add_field(name='User ID', value=f'`{target.id}`', inline=True)
        
        # Bot status
        embed.add_field(name='Bot', value='Yes' if target.bot else 'No', inline=True)
        
        # Timeout status
        if isinstance(target, discord.Member) and target.timed_out:
            embed.add_field(name='Timed Out', value=f'Until {target.timed_out_until.strftime("%Y-%m-%d %H:%M:%S")} UTC', inline=True)
        elif isinstance(target, discord.Member):
            embed.add_field(name='Timed Out', value='No', inline=True)
        
        # Roles (if member)
        if isinstance(target, discord.Member) and target.roles:
            roles = [role.mention for role in target.roles[1:]]  # Skip @everyone
            if roles:
                embed.add_field(name='Roles', value=' '.join(roles[:10]) + (' ...' if len(roles) > 10 else ''), inline=False)
        
        embed.set_thumbnail(url=target.display_avatar.url)
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name='serverinfo', description='Display information about the current server')
    async def serverinfo(self, interaction: discord.Interaction):
        """Generate a full report card about the Discord server."""
        guild = interaction.guild
        
        embed = discord.Embed(
            title=f'{guild.name}',
            description=f'ID: `{guild.id}`',
            color=discord.Color.blurple()
        )
        
        # Server creation
        embed.add_field(name='Created', value=f'{guild.created_at.strftime("%Y-%m-%d %H:%M:%S")} UTC', inline=True)
        
        # Member count
        embed.add_field(name='Members', value=str(guild.member_count), inline=True)
        
        # Owner
        owner = guild.owner or await self.bot.fetch_user(guild.owner_id)
        embed.add_field(name='Owner', value=owner.mention if owner else f'`{guild.owner_id}`', inline=True)
        
        # Channel count
        channel_count = len(guild.channels)
        embed.add_field(name='Channels', value=str(channel_count), inline=True)
        
        # Role count
        embed.add_field(name='Roles', value=str(len(guild.roles)), inline=True)
        
        # Boosts
        boost_level = guild.premium_tier
        boost_count = guild.premium_subscription_count or 0
        embed.add_field(name='Boosts', value=f'Level {boost_level} ({boost_count} boost{"s" if boost_count != 1 else ""})', inline=True)
        
        embed.set_thumbnail(url=guild.icon.url if guild.icon else '')
        embed.set_footer(text=f'Shard ID: {guild.shard_id or "N/A"}')
        
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bots and DMs
        if message.author.bot or not message.guild:
            return
        if not self.ai_enabled:
            return

        is_mention = self.bot.user in message.mentions
        is_reply = (
            message.reference is not None
            and message.reference.resolved is not None
            and isinstance(message.reference.resolved, discord.Message)
            and message.reference.resolved.author == self.bot.user
        )

        if not (is_mention or is_reply):
            return

        # strip the bot mention from the message
        prompt = message.content.replace(f'<@{self.bot.user.id}>', '').replace(f'<@!{self.bot.user.id}>', '').strip()
        if not prompt:
            prompt = '(no message)'

        # grab context from channel history
        context_lines = []
        try:
            async for msg in message.channel.history(limit=self.CONTEXT_LIMIT + 1, before=message):
                who = 'Seraphine' if msg.author == self.bot.user else msg.author.display_name
                context_lines.insert(0, f'{who}: {msg.content}')
        except Exception:
            pass

        context_str = '\n'.join(context_lines)
        full_prompt = f'{context_str}\n{message.author.display_name}: {prompt}'.strip() if context_str else f'{message.author.display_name}: {prompt}'

        async with message.channel.typing():
            try:
                response_tokens = []
                async with websockets.connect(self.SERAPHBYTE_WS, open_timeout=5) as ws:
                    import json as _json
                    frame = _json.dumps({
                        'prompt': full_prompt,
                        'temperature': 0.7,
                        'top_p': 0.9,
                        'max_tokens': 1024,
                    })
                    await ws.send(frame)
                    async for token in ws:
                        try:
                            parsed = _json.loads(token)
                            if isinstance(parsed, dict):
                                continue  # skip model_info frames etc.
                        except Exception:
                            pass
                        response_tokens.append(token)

                response = ''.join(response_tokens).strip()
                if not response:
                    response = '*(no response)*'

                # Discord has a 2000 char limit
                if len(response) > 1990:
                    response = response[:1990] + '…'

                await message.reply(response)

            except (OSError, websockets.exceptions.WebSocketException) as e:
                logger.error(f'SeraphByte WS error: {e}')
                await message.reply('⚠️ Seraph is offline — is SeraphByte running?')
            except Exception as e:
                logger.error(f'AI on_message error: {e}')
                await message.reply(f'⚠️ Something went wrong: {e}')


async def setup(bot):
    """Setup function to register the cog with the bot."""
    await bot.add_cog(CommandsCog(bot))