import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)


class CommandsCog(commands.Cog):
    """Example cog to demonstrate the structure."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='ping', description='Responds with pong')
    async def ping(self, interaction: discord.Interaction):
        """A simple ping slash command."""
        await interaction.response.send_message(f'Pong! {round(self.bot.latency * 1000)}ms')
    
    @app_commands.command(name='welcome', description='Greets the user')
    async def welcome(self, interaction: discord.Interaction):
        """A simple greeting slash command."""
        await interaction.response.send_message(f'Welcome {interaction.user.name}! Roles here https://discordapp.com/channels/1428756621957529781/1443868228362440714')
    
    @app_commands.command(name='echo', description='Echoes the user input In Channels Hides Who Wrote It')
    async def echo(self, interaction: discord.Interaction, message: str):
        """Echoes the user input."""
        await interaction.response.defer()
        await interaction.channel.send(message)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a member joins the server."""
        logger.info(f'{member.name} joined the server')


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(CommandsCog(bot))
