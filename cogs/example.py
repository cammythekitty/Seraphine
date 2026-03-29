import discord
from discord.ext import commands
import logging

logger = logging.getLogger(__name__)


class ExampleCog(commands.Cog):
    """Example cog to demonstrate the structure."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='ping', help='Responds with pong')
    async def ping(self, ctx):
        """A simple ping command."""
        await ctx.send(f'Pong! {round(self.bot.latency * 1000)}ms')
    
    @commands.command(name='hello', help='Greets the user')
    async def hello(self, ctx):
        """A simple greeting command."""
        await ctx.send(f'Hello {ctx.author.name}!')
    
    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a member joins the server."""
        logger.info(f'{member.name} joined the server')


async def setup(bot):
    """Called when the cog is loaded."""
    await bot.add_cog(ExampleCog(bot))
