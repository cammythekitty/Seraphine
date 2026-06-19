# cogs/lanyard.py
# Lanyard-compatible presence API cog.
#
# Tracks Discord presences in-memory via the bot's gateway connection and
# serves them over a small aiohttp HTTP server on LANYARD_PORT (default 5000).
#
# HTTP endpoints:
#   GET /v1/users/:id          → unified presence + user info (Lanyard-ish shape)
#   GET /v1/users/:id/presence → presence only
#   GET /v1/users/:id/profile  → user info only
#
# Slash commands:
#   /presence [user]  → show someone's current presence card
#   /lanyard-status   → show API server status (owner only)
#
# Required .env additions:
#   LANYARD_PORT=5000          (optional, defaults to 5000)
#   LANYARD_HOST=0.0.0.0       (optional, defaults to 127.0.0.1)
#
# Required intents (add to Main.py):
#   intents.presences = True

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

DISCORD_CDN = "https://cdn.discordapp.com"

# ── CDN helpers ────────────────────────────────────────────────────────────────

def avatar_url(user_id: str, avatar: str | None) -> str:
    if not avatar:
        discriminator = 0
        return f"{DISCORD_CDN}/embed/avatars/{int(user_id) >> 22 % 6}.png"
    ext = "gif" if avatar.startswith("a_") else "png"
    return f"{DISCORD_CDN}/avatars/{user_id}/{avatar}.{ext}?size=256"


def banner_url(user_id: str, banner: str | None) -> str | None:
    if not banner:
        return None
    ext = "gif" if banner.startswith("a_") else "png"
    return f"{DISCORD_CDN}/banners/{user_id}/{banner}.{ext}?size=512"


def emoji_url(emoji_id: str, animated: bool) -> str:
    ext = "gif" if animated else "png"
    return f"{DISCORD_CDN}/emojis/{emoji_id}.{ext}"


# ── Presence builder ───────────────────────────────────────────────────────────

def _extract_spotify(activities: list) -> dict | None:
    for a in activities:
        if a.get("type") == 2 and a.get("name") == "Spotify" and a.get("sync_id"):
            large_image = a.get("assets", {}).get("large_image", "")
            if large_image.startswith("spotify:"):
                album_art = f"https://i.scdn.co/image/{large_image[len('spotify:'):]}"
            else:
                album_art = None
            ts = a.get("timestamps", {})
            return {
                "track_id": a.get("sync_id"),
                "song": a.get("details", ""),
                "artist": a.get("state", ""),
                "album": a.get("assets", {}).get("large_text", ""),
                "album_art_url": album_art,
                "timestamps": {
                    "start": ts.get("start"),
                    "end": ts.get("end"),
                } if ts else None,
            }
    return None


def _extract_custom_status(activities: list) -> dict | None:
    for a in activities:
        if a.get("type") == 4:
            text = a.get("state")
            e = a.get("emoji")
            has_emoji = e and (e.get("id") or e.get("name"))
            if not text and not has_emoji:
                return None
            return {
                "text": text,
                "emoji": {
                    "id": e.get("id"),
                    "name": e.get("name"),
                    "animated": bool(e.get("animated")),
                    "url": emoji_url(e["id"], bool(e.get("animated"))) if e and e.get("id") else None,
                } if has_emoji else None,
            }
    return None


def _status_str(member: discord.Member) -> str:
    s = member.status
    if s == discord.Status.online:
        return "online"
    if s == discord.Status.idle:
        return "idle"
    if s == discord.Status.do_not_disturb:
        return "dnd"
    return "offline"


def build_presence(member: discord.Member) -> dict:
    raw_activities = []
    for a in member.activities:
        d: dict = {
            "type": a.type.value,
            "name": a.name,
        }
        if isinstance(a, discord.Activity):
            d.update({
                "details": a.details,
                "state": a.state,
                "sync_id": getattr(a, "sync_id", None),
                "assets": {
                    "large_image": a.large_image_url and a.large_image_url.split("/")[-1].split("?")[0] if a.large_image_url else None,
                    "large_text": a.large_image_text,
                    "small_image": a.small_image_url and a.small_image_url.split("/")[-1].split("?")[0] if a.small_image_url else None,
                    "small_text": a.small_image_text,
                } if hasattr(a, "large_image_url") else None,
                "timestamps": {
                    "start": int(a.start.timestamp() * 1000) if a.start else None,
                    "end": int(a.end.timestamp() * 1000) if a.end else None,
                } if hasattr(a, "start") else None,
            })
        elif isinstance(a, discord.Spotify):
            d.update({
                "details": a.title,
                "state": a.artists[0] if a.artists else None,
                "sync_id": a.track_id,
                "assets": {
                    "large_image": f"spotify:{a.album_cover_url.split('/')[-1]}" if a.album_cover_url else None,
                    "large_text": a.album,
                },
                "timestamps": {
                    "start": int(a.start.timestamp() * 1000) if a.start else None,
                    "end": int(a.end.timestamp() * 1000) if a.end else None,
                },
            })
        elif isinstance(a, discord.CustomActivity):
            d.update({
                "state": a.name,
                "emoji": {
                    "id": str(a.emoji.id) if a.emoji and a.emoji.id else None,
                    "name": a.emoji.name if a.emoji else None,
                    "animated": a.emoji.animated if a.emoji else False,
                } if a.emoji else None,
            })
        raw_activities.append(d)

    # Filter custom status out of the plain activities list
    plain_activities = [x for x in raw_activities if x.get("type") != 4]

    spotify = _extract_spotify(raw_activities)
    # Also catch discord.Spotify directly
    if not spotify:
        for a in member.activities:
            if isinstance(a, discord.Spotify):
                spotify = {
                    "track_id": a.track_id,
                    "song": a.title,
                    "artist": ", ".join(a.artists),
                    "album": a.album,
                    "album_art_url": a.album_cover_url,
                    "timestamps": {
                        "start": int(a.start.timestamp() * 1000) if a.start else None,
                        "end": int(a.end.timestamp() * 1000) if a.end else None,
                    },
                }
                break

    cs = _extract_custom_status(raw_activities)
    if not cs:
        for a in member.activities:
            if isinstance(a, discord.CustomActivity) and (a.name or a.emoji):
                cs = {
                    "text": a.name,
                    "emoji": {
                        "id": str(a.emoji.id) if a.emoji and a.emoji.id else None,
                        "name": a.emoji.name if a.emoji else None,
                        "animated": a.emoji.animated if a.emoji else False,
                        "url": emoji_url(str(a.emoji.id), a.emoji.animated) if a.emoji and a.emoji.id else None,
                    } if a.emoji else None,
                }
                break

    status = _status_str(member)
    mobile = member.mobile_status != discord.Status.offline
    desktop = member.desktop_status != discord.Status.offline
    web_status = member.web_status != discord.Status.offline

    return {
        "user_id": str(member.id),
        "status": status,
        "online": status != "offline",
        "platform": {
            "desktop": desktop,
            "mobile": mobile,
            "web": web_status,
        },
        "activities": plain_activities,
        "custom_status": cs,
        "listening_to_spotify": bool(spotify),
        "spotify": spotify,
        "updated_at": int(datetime.now(timezone.utc).timestamp() * 1000),
    }

async def get_all_user_badges_and_profile(bot, member: discord.Member) -> dict:
    badges = [flag.name for flag in member.public_flags.all()]
    
    if member.premium_since is not None:
        badges.append("premium_guild_subscriber")

    # Default fallback to member's standard cached avatar layout
    # Using your file's custom avatar_url formatter helper function
    avatar_hash = member.avatar.key if member.avatar else None
    final_avatar_url = avatar_url(str(member.id), avatar_hash)
    avatar_decoration_url = None
    
    try:
        full_user = await bot.fetch_user(member.id)
        
        # Pull correct image string if user has an updated global avatar asset
        if full_user.avatar:
            final_avatar_url = avatar_url(str(member.id), full_user.avatar.key)
            
        # Extract the exact string URL path for the decoration asset
        if full_user.avatar_decoration:
            avatar_decoration_url = full_user.avatar_decoration.url
            
        has_nitro = (
            full_user.banner is not None or 
            full_user.avatar_decoration is not None or 
            member.premium_since is not None
        )
        if has_nitro:
            badges.append("nitro")
            
    except Exception as e:
        logger.error(f"Error checking HTTP profile elements: {e}")

    return {
        "avatar_url": final_avatar_url,
        "badges": list(set(badges)),
        "avatar_decoration_url": avatar_decoration_url
    }

def build_user(member: discord.Member):
    badges = [flag.name for flag in member.public_flags.all()]
    
    is_boosting = member.premium_since is not None
    if is_boosting:
        badges.append("premium_guild_subscriber")

    # Use getattr to safely check for avatar_decoration attributes
    deco = getattr(member, "avatar_decoration", None)
    decoration_url = deco.url if deco else None

    return {
        "id": str(member.id),
        "username": member.name,
        "discriminator": member.discriminator,
        "avatar": member.display_avatar.url, 
        "avatar_decoration": decoration_url,
        "badges": badges,
        "nitro": is_boosting or (decoration_url is not None)
    }

# ── Presence cache ─────────────────────────────────────────────────────────────

class PresenceCache:
    """In-memory store: user_id (str) → (member snapshot, presence dict)."""

    def __init__(self):
        self._store: dict[str, dict] = {}

    def update(self, member: discord.Member):
        self._store[str(member.id)] = {
            "presence": build_presence(member),
            "user": build_user(member),
        }

    def get(self, user_id: str) -> dict | None:
        return self._store.get(user_id)

    def all(self) -> dict:
        return dict(self._store)

    def count(self) -> int:
        return len(self._store)


# ── HTTP API ───────────────────────────────────────────────────────────────────

class LanyardAPI:
    def __init__(self, bot: commands.Bot, cache: PresenceCache):
        self.bot = bot
        self.cache = cache
        self.app = web.Application()
        self.app.router.add_get("/", self._root)
        self.app.router.add_get("/v1/users/{user_id}", self._unified)
        self.app.router.add_get("/v1/users/{user_id}/presence", self._presence_only)
        self.app.router.add_get("/v1/users/{user_id}/profile", self._profile_only)
        self.app.router.add_get("/{user_id}", self._unified)
        self.runner: web.AppRunner | None = None

    def _json(self, data, status=200):
        return web.Response(
            text=json.dumps({"success": True, "data": data}, default=str),
            status=status,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    def _error(self, code: str, message: str, status=404):
        return web.Response(
            text=json.dumps({"success": False, "error": {"code": code, "message": message}}),
            status=status,
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"},
        )

    async def _root(self, _req):
        return self._json({
            "service": "bot-lanyard",
            "tracked": self.cache.count(),
            "endpoints": ["/v1/users/:id", "/v1/users/:id/presence", "/v1/users/:id/profile"],
        })

    async def _unified(self, req):
        uid = req.match_info["user_id"]
        entry = self.cache.get(uid)
        if not entry:
            return self._error("not_monitored", "User is not cached (not in a shared guild or no presence seen yet).")

        # Look up the member across guilds to query their active profile properties
        member = None
        for guild in self.bot.guilds:
            member = guild.get_member(int(uid))
            if member:
                break

        if member:
            try:
                # Force-fetch the heavy HTTP profile data (decorations, Nitro flags)
                rich_profile = await get_all_user_badges_and_profile(self.bot, member)
                
                # OVERWRITE/MERGE the data with the fresh API URLs!
                entry["user"]["avatar"] = rich_profile["avatar_url"]
                entry["user"]["badges"] = rich_profile["badges"]
                entry["user"]["avatar_decoration"] = rich_profile["avatar_decoration_url"]
                entry["user"]["nitro"] = "nitro" in rich_profile["badges"]
            except Exception as e:
                logger.error(f"Failed to append heavy profile flags for {uid}: {e}")

        return self._json({**entry, "updated_at": entry["presence"]["updated_at"]})

    async def _profile_only(self, req):
        uid = req.match_info["user_id"]
        entry = self.cache.get(uid)
        if not entry:
            return self._error("not_found", "User not found in cache.")

        # Look up the member across guilds
        member = None
        for guild in self.bot.guilds:
            member = guild.get_member(int(uid))
            if member:
                break

        if member:
            try:
                # Force-fetch the heavy HTTP profile data
                rich_profile = await get_all_user_badges_and_profile(self.bot, member)
                
                # OVERWRITE/MERGE the data with the fresh API URLs!
                entry["user"]["avatar"] = rich_profile["avatar_url"]
                entry["user"]["badges"] = rich_profile["badges"]
                entry["user"]["avatar_decoration"] = rich_profile["avatar_decoration_url"]
                entry["user"]["nitro"] = "nitro" in rich_profile["badges"]
            except Exception:
                logger.error(f"Endpoint merge crash: {e}")
                pass

        return self._json(entry["user"])
    async def _presence_only(self, req):
        uid = req.match_info["user_id"]
        entry = self.cache.get(uid)
        if not entry:
            return self._error("not_monitored", "User not found in presence cache.")
        return self._json(entry["presence"])

    async def start(self, host: str, port: int):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        await site.start()
        logger.info(f"Lanyard API running on http://{host}:{port}")

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()


# ── Cog ───────────────────────────────────────────────────────────────────────

STATUS_COLORS = {
    "online": discord.Color.green(),
    "idle": discord.Color.orange(),
    "dnd": discord.Color.red(),
    "offline": discord.Color.dark_gray(),
}

STATUS_EMOJI = {
    "online": "🟢",
    "idle": "🌙",
    "dnd": "⛔",
    "offline": "⚫",
}


class LanyardCog(commands.Cog, name="Lanyard"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.cache = PresenceCache()
        self.api = LanyardAPI(self.bot, self.cache)
        self._host = os.getenv("LANYARD_HOST", "127.0.0.1")
        self._port = int(os.getenv("LANYARD_PORT", "5000"))
        self._api_task: asyncio.Task | None = None

    async def cog_load(self):
        self._api_task = asyncio.create_task(self._start_api())

    async def cog_unload(self):
        await self.api.stop()
        if self._api_task:
            self._api_task.cancel()

    async def _start_api(self):
        try:
            await self.api.start(self._host, self._port)
        except Exception as e:
            logger.error(f"Failed to start Lanyard API: {e}")

    # ── Gateway events ─────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild):
        """Seeds the cache dynamically as each server becomes available to the bot."""
        count = 0
        for member in guild.members:
            if not member.bot:
                # Remove the ", None" from build_user()
                self.cache._store[str(member.id)] = {
                    "presence": build_presence(member),
                    "user": build_user(member), 
                }
                count += 1
        logger.info(f"Lanyard: seeded {count} members from guild: {guild.name}")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        if not after.bot:
            self.cache.update(after)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not after.bot:
            self.cache.update(after)

    # ── Slash commands ─────────────────────────────────────────────────────────

    @app_commands.command(name="presence", description="Show a user's current Discord presence")
    @app_commands.describe(user="The user to look up (defaults to you)")
    async def presence(self, interaction: discord.Interaction, user: discord.Member = None):
        target = user or interaction.user
        entry = self.cache.get(str(target.id))

        if not entry:
            # Try seeding from the member object directly if we have it
            if isinstance(target, discord.Member):
                self.cache.update(target)
                entry = self.cache.get(str(target.id))

        if not entry:
            await interaction.response.send_message(
                f"No presence data cached for {target.mention} yet. They may need to be in a shared server.",
                ephemeral=True,
            )
            return

        p = entry["presence"]
        u = entry["user"]
        status = p["status"]

        embed = discord.Embed(
            color=STATUS_COLORS.get(status, discord.Color.default()),
            timestamp=datetime.fromtimestamp(p["updated_at"] / 1000, tz=timezone.utc),
        )
        embed.set_author(
            name=u.get("display_name") or u.get("username", str(target)),
            icon_url=u.get("avatar_url"),
        )
        embed.set_thumbnail(url=u.get("avatar_url"))

        # Status line
        status_line = f"{STATUS_EMOJI.get(status, '⚫')} **{status.upper()}**"
        platforms = [k for k, v in p["platform"].items() if v]
        if platforms:
            status_line += f"  ·  {', '.join(platforms)}"
        embed.add_field(name="Status", value=status_line, inline=False)

        # Custom status
        if p.get("custom_status"):
            cs = p["custom_status"]
            parts = []
            if cs.get("emoji") and cs["emoji"].get("name"):
                parts.append(cs["emoji"]["name"])
            if cs.get("text"):
                parts.append(cs["text"])
            if parts:
                embed.add_field(name="Custom Status", value=" ".join(parts), inline=False)

        # Spotify
        if p.get("listening_to_spotify") and p.get("spotify"):
            sp = p["spotify"]
            embed.add_field(
                name="<:spotify:0> Listening to Spotify",
                value=f"**{sp['song']}**\nby {sp['artist']}\non *{sp['album']}*",
                inline=False,
            )
            if sp.get("album_art_url"):
                embed.set_thumbnail(url=sp["album_art_url"])

        # Other activities
        for a in p.get("activities", []):
            if a.get("type") != 2:  # skip spotify (already shown)
                name = a.get("name", "Unknown")
                detail = a.get("details") or a.get("state") or ""
                embed.add_field(name=f"Playing {name}", value=detail or "\u200b", inline=True)

        embed.set_footer(text=f"User ID: {target.id}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="lanyard-status", description="Show Lanyard API server status (Owner/Co-owner only)")
    async def lanyard_status(self, interaction: discord.Interaction):
        # Owner check — reuse the helper pattern from commands.py
        cog = self.bot.cogs.get("CommandsCog") or self.bot.cogs.get("Commands")
        if cog and hasattr(cog, "_is_owner_or_coowner"):
            if not await cog._is_owner_or_coowner(str(interaction.user.id)):
                await interaction.response.send_message("Owner/co-owner only.", ephemeral=True)
                return
        else:
            # Fallback: check bot owner
            app_info = await self.bot.application_info()
            if interaction.user.id != app_info.owner.id:
                await interaction.response.send_message("Owner only.", ephemeral=True)
                return

        embed = discord.Embed(title="Lanyard API Status", color=discord.Color.blurple())
        embed.add_field(name="Address", value=f"`http://{self._host}:{self._port}`", inline=False)
        embed.add_field(name="Tracked users", value=str(self.cache.count()), inline=True)
        embed.add_field(name="Guilds watched", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Endpoints",
            value="`GET /v1/users/:id`\n`GET /v1/users/:id/presence`\n`GET /v1/users/:id/profile`",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(LanyardCog(bot))
