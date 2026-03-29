import discord
from discord.ext import commands
from discord import app_commands
import logging

logger = logging.getLogger(__name__)


class ExampleCog(commands.Cog):
    """Example cog to demonstrate the structure."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @app_commands.command(name='ping', description='Responds with pong')
    async def ping(self, interaction: discord.Interaction):
        """A simple ping slash command."""
        await interaction.response.send_message(f'Pong! {round(self.bot.latency * 1000)}ms')
    
    @app_commands.command(name='hello', description='Greets the user')
    async def hello(self, interaction: discord.Interaction):
        """A simple greeting slash command."""
        await interaction.response.send_message(f'Hello {interaction.user.name}!')
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a member joins the server."""
        logger.info(f'{member.name} joined the server')


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(ExampleCog(bot))
