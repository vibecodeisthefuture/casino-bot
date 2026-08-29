import discord
from discord.ext import commands

from app.config import config


def is_admin():
    """Command check: allow the bot owner or a member holding the configured admin role.

    The admin role is set via DISCORD_ADMIN_ROLE_ID (config.bot.admin_role_id).
    Raises commands.CheckFailure otherwise (rendered by the Handlers cog).
    """

    async def predicate(ctx: commands.Context) -> bool:
        if await ctx.bot.is_owner(ctx.author):
            return True
        role_id = config.bot.admin_role_id
        author = ctx.author
        if (
            role_id
            and isinstance(author, discord.Member)
            and any(role.id == role_id for role in author.roles)
        ):
            return True
        raise commands.CheckFailure("You need the admin role to use this command.")

    return commands.check(predicate)
