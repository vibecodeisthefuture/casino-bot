import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from app.config import config
from app.discord_bot.modules.betting import validate_positive_amount
from app.discord_bot.modules.checks import is_admin
from app.discord_bot.modules.economy import Economy
from app.discord_bot.modules.helpers import (
    InsufficientFundsException,
    format_money,
    make_embed,
)
from app.discord_bot.modules.wallet_logging import log_wallet_change

logger = logging.getLogger(__name__)


class GamblingHelpers(commands.Cog, name="General"):
    def __init__(self, client: commands.Bot) -> None:
        self.client = client
        self.economy = getattr(client, "economy", Economy())

    @commands.command(hidden=True)
    @commands.is_owner()
    async def set(
        self,
        ctx: commands.Context,
        user_id: int | None = None,
        money: int | None = None,
        credits: int | None = None,
    ):
        if user_id is None:
            user_id = ctx.author.id
        before = self.economy.get_entry(user_id)
        if money is not None:
            self.economy.set_money(user_id, money)
        if credits is not None:
            self.economy.set_credits(user_id, credits)
        after = self.economy.get_entry(user_id)
        log_wallet_change(
            logger,
            event="admin_set_wallet",
            user_id=user_id,
            money_delta=after[1] - before[1],
            credits_delta=after[2] - before[2],
            ctx=ctx,
            actor_user_id=ctx.author.id,
        )

    @commands.command(
        brief="(Admin) Add money to a user",
        usage="add [amount] *[@member]",
        hidden=True,
    )
    @is_admin()
    async def add(
        self,
        ctx: commands.Context,
        amount: int,
        member: discord.Member | None = None,
    ):
        target = member or ctx.author
        parsed = validate_positive_amount(amount)
        self.economy.add_money(target.id, parsed)
        new_balance = self.economy.get_entry(target.id)[1]
        log_wallet_change(
            logger,
            event="admin_add_funds",
            user_id=target.id,
            money_delta=parsed,
            ctx=ctx,
            actor_user_id=ctx.author.id,
        )
        embed = make_embed(
            title="Funds added",
            description=(
                f"Added **{format_money(parsed)}** to {target.mention}.\n"
                f"New balance: **{format_money(new_balance)}**"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.command(
        brief="(Admin) Remove money from a user",
        usage="remove [amount] *[@member]",
        hidden=True,
        aliases=["take"],
    )
    @is_admin()
    async def remove(
        self,
        ctx: commands.Context,
        amount: int,
        member: discord.Member | None = None,
    ):
        target = member or ctx.author
        parsed = validate_positive_amount(amount)
        before = self.economy.get_entry(target.id)[1]
        # add_money clamps at 0 (MAX(0, money + ?)), so this removes up to what
        # the target actually has; report the real delta rather than the request.
        self.economy.add_money(target.id, -parsed)
        new_balance = self.economy.get_entry(target.id)[1]
        removed = before - new_balance
        log_wallet_change(
            logger,
            event="admin_remove_funds",
            user_id=target.id,
            money_delta=-removed,
            ctx=ctx,
            actor_user_id=ctx.author.id,
            requested=parsed,
        )
        embed = make_embed(
            title="Funds removed",
            description=(
                f"Removed **{format_money(removed)}** from {target.mention}.\n"
                f"New balance: **{format_money(new_balance)}**"
            ),
            color=discord.Color.orange(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        brief="How much money you or someone else has",
        description="Check your balance or someone else's",
        usage="money *[@member]",
        aliases=["credits"],
    )
    @app_commands.describe(user="Whose balance to check (optional)")
    async def money(self, ctx: commands.Context, user: discord.Member | None = None):
        target_user = user or ctx.author
        profile = self.economy.get_entry(target_user.id)
        embed = make_embed(
            title=target_user.name,
            description=(
                f"**{format_money(profile[1])}**\n**{profile[2]:,}** credits"
            ),
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        brief="Shows the user with the most money",
        description="Show the richest players",
        usage="leaderboard",
        aliases=["top"],
    )
    async def leaderboard(self, ctx: commands.Context):
        entries = self.economy.top_entries(5)
        embed = make_embed(title="Leaderboard:", color=discord.Color.gold())
        for i, entry in enumerate(entries):
            user = self.client.get_user(entry[0])
            name = user.name if user else f"User {entry[0]}"
            embed.add_field(
                name=f"{i+1}. {name}",
                value=format_money(entry[1]),
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        brief=(
            f"Claim your free ${config.bot.daily_amount} "
            f"every {config.bot.daily_cooldown}hrs"
        ),
        description=f"Claim your free ${config.bot.daily_amount} daily reward",
        usage="daily",
    )
    async def daily(self, ctx: commands.Context):
        now = int(time.time())
        cooldown_seconds = config.bot.daily_cooldown * 3600
        last_claim = self.economy.get_last_daily(ctx.author.id)
        elapsed = now - last_claim
        if last_claim and elapsed < cooldown_seconds:
            remaining = cooldown_seconds - elapsed
            hours, rem = divmod(remaining, 3600)
            minutes, seconds = divmod(rem, 60)
            await ctx.send(
                "You've already claimed your daily reward. "
                f"Come back in {hours}hrs {minutes}min {seconds}sec."
            )
            return
        amount = config.bot.daily_amount
        self.economy.add_money(ctx.author.id, amount)
        self.economy.set_last_daily(ctx.author.id, now)
        log_wallet_change(
            logger,
            event="daily_claim",
            user_id=ctx.author.id,
            money_delta=amount,
            ctx=ctx,
        )
        balance = self.economy.get_entry(ctx.author.id)[1]
        embed = make_embed(
            title="Daily reward claimed!",
            description=(
                f"You received **{format_money(amount)}**.\n"
                f"Balance: **{format_money(balance)}**"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        brief="Give some of your money to another member",
        description="Give some of your money to another member",
        usage="pay [@member] [amount]",
        aliases=["give"],
    )
    @app_commands.describe(member="Who to pay", amount="How much to pay")
    async def pay(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ):
        if member.bot:
            await ctx.send("You can't pay bots.")
            return
        if member.id == ctx.author.id:
            await ctx.send("You can't pay yourself.")
            return
        parsed = validate_positive_amount(amount)
        try:
            sender_entry, _ = self.economy.transfer_money(
                ctx.author.id, member.id, parsed
            )
        except RuntimeError:
            current = self.economy.get_entry(ctx.author.id)[1]
            raise InsufficientFundsException(current, parsed)
        log_wallet_change(
            logger,
            event="pay_sent",
            user_id=ctx.author.id,
            money_delta=-parsed,
            ctx=ctx,
            recipient_user_id=member.id,
        )
        log_wallet_change(
            logger,
            event="pay_received",
            user_id=member.id,
            money_delta=parsed,
            ctx=ctx,
            sender_user_id=ctx.author.id,
        )
        embed = make_embed(
            title="Payment sent",
            description=(
                f"{ctx.author.mention} paid **{format_money(parsed)}** to {member.mention}.\n"
                f"Your balance: **{format_money(sender_entry[1])}**"
            ),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)


async def setup(client: commands.Bot):
    await client.add_cog(GamblingHelpers(client))
