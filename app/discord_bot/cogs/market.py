import asyncio
import logging
import time as _time
from contextlib import suppress
from datetime import time as dtime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from app.config import config
from app.discord_bot.modules.checks import is_admin
from app.discord_bot.modules.economy import Economy
from app.discord_bot.modules.helpers import format_bitcoin, format_money, make_embed
from app.discord_bot.modules.wallet_logging import log_wallet_change

logger = logging.getLogger(__name__)

COINGECKO_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=usd"
)
PRICE_MAX_AGE = 24 * 3600
SESSION_TIMEOUT = 120

BUY_BTC = "📈"
SELL_BTC = "📉"
REFRESH = "🔄"
CLOSE = "❌"
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]


class Market(commands.Cog, name="Black Market"):
    def __init__(self, client: commands.Bot):
        self.client = client
        self.economy = getattr(client, "economy", Economy())

    async def cog_load(self) -> None:
        if not self._daily_price_update.is_running():
            self._daily_price_update.start()

    async def cog_unload(self) -> None:
        self._daily_price_update.cancel()

    # ---------------- Bitcoin price ----------------
    async def _fetch_btc_price(self) -> int | None:
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    COINGECKO_URL, headers={"Accept": "application/json"}
                ) as resp:
                    if resp.status != 200:
                        logger.warning("CoinGecko returned HTTP %s", resp.status)
                        return None
                    data = await resp.json()
                    return int(round(float(data["bitcoin"]["usd"])))
        except (aiohttp.ClientError, KeyError, ValueError, TypeError) as exc:
            logger.warning("CoinGecko price fetch failed: %s", exc)
            return None

    async def _refresh_price(self) -> int | None:
        price = await self._fetch_btc_price()
        if price and price > 0:
            self.economy.set_bitcoin_price(price, int(_time.time()))
            logger.info("Bitcoin price updated: $%s", price)
            return price
        logger.warning("Bitcoin price refresh returned no value")
        return None

    @tasks.loop(time=dtime(hour=0, minute=0, tzinfo=timezone.utc))
    async def _daily_price_update(self):
        await self._refresh_price()

    @_daily_price_update.before_loop
    async def _before_price_update(self):
        await self.client.wait_until_ready()
        # Startup fetch only if the stored price is missing or stale (>24h).
        age = int(_time.time()) - self.economy.get_bitcoin_updated()
        if self.economy.get_bitcoin_price() <= 0 or age >= PRICE_MAX_AGE:
            await self._refresh_price()

    # ---------------- user: bitcoin price view ----------------
    @commands.hybrid_command(
        name="bitcoin",
        aliases=["btc"],
        brief="Show the Bitcoin price and your holdings",
        description="Show the current Bitcoin price and your holdings",
    )
    async def bitcoin(self, ctx: commands.Context):
        price = self.economy.get_bitcoin_price()
        held = self.economy.get_bitcoin(ctx.author.id)
        if price <= 0:
            await ctx.send(
                "The Bitcoin price isn't available yet — an admin can run "
                f"`{config.bot.prefix}refreshbtc`."
            )
            return
        embed = make_embed(
            title="Bitcoin",
            color=discord.Color.gold(),
            description=(
                f"Price: **{format_money(price)}** per coin\n"
                f"You hold: **{format_bitcoin(held)}** "
                f"(worth **{format_money(held * price)}**)\n\n"
                f"Trade at `{config.bot.prefix}market`."
            ),
        )
        await ctx.send(embed=embed)

    # ---------------- user: the reaction-driven market ----------------
    def _market_embed(self, ctx, price, roles, note) -> discord.Embed:
        money = self.economy.get_entry(ctx.author.id)[1]
        btc = self.economy.get_bitcoin(ctx.author.id)
        lines = [
            f"**Your wallet:** {format_money(money)} · {format_bitcoin(btc)}",
            "",
        ]
        if price > 0:
            lines.append(f"**Bitcoin** — {format_money(price)} each")
            lines.append(f"{BUY_BTC} buy 1 · {SELL_BTC} sell 1")
        else:
            lines.append(f"**Bitcoin** — price unavailable (press {REFRESH})")
        lines.append("")
        lines.append("**Roles for sale**")
        if roles:
            for i, (role_id, rprice, currency) in enumerate(roles):
                role = ctx.guild.get_role(role_id) if ctx.guild else None
                name = role.name if role else f"(missing role {role_id})"
                cost = (
                    format_bitcoin(rprice)
                    if currency == "bitcoin"
                    else format_money(rprice)
                )
                lines.append(f"{NUMBER_EMOJIS[i]} {name} — {cost}")
        else:
            lines.append("Nothing listed right now.")
        lines.append("")
        lines.append(f"{REFRESH} refresh · {CLOSE} close")
        embed = make_embed(
            title="🕯️ SIREN's Black Market",
            description="\n".join(lines),
            color=discord.Color.dark_red(),
        )
        if note:
            embed.set_footer(text=note)
        return embed

    @commands.command(
        brief="Browse SIREN's Black Market (buy Bitcoin and roles)",
        usage="market",
        aliases=["blackmarket", "shop"],
    )
    @commands.max_concurrency(1, per=commands.BucketType.user, wait=False)
    async def market(self, ctx: commands.Context):
        user = ctx.author
        note = None
        while True:
            price = self.economy.get_bitcoin_price()
            roles = self.economy.list_market_roles()[: len(NUMBER_EMOJIS)]
            msg = await ctx.send(embed=self._market_embed(ctx, price, roles, note))

            controls = []
            if price > 0:
                controls.extend([BUY_BTC, SELL_BTC])
            controls.extend(NUMBER_EMOJIS[: len(roles)])
            controls.extend([REFRESH, CLOSE])
            for emoji in controls:
                with suppress(discord.HTTPException):
                    await msg.add_reaction(emoji)

            def check(reaction, reactor):
                return (
                    reactor.id == user.id
                    and reactor != self.client.user
                    and reaction.message.id == msg.id
                    and str(reaction.emoji) in controls
                )

            try:
                reaction, _ = await self.client.wait_for(
                    "reaction_add", timeout=SESSION_TIMEOUT, check=check
                )
            except asyncio.TimeoutError:
                with suppress(discord.HTTPException):
                    await msg.delete()
                with suppress(discord.HTTPException):
                    await ctx.send("Black Market closed (timed out).", delete_after=10)
                return

            emoji = str(reaction.emoji)
            with suppress(discord.HTTPException):
                await msg.delete()

            if emoji == CLOSE:
                return
            if emoji == REFRESH:
                note = None
                continue
            if emoji == BUY_BTC:
                note = self._buy_bitcoin(ctx, price)
                continue
            if emoji == SELL_BTC:
                note = self._sell_bitcoin(ctx, price)
                continue
            if emoji in NUMBER_EMOJIS:
                idx = NUMBER_EMOJIS.index(emoji)
                if idx < len(roles):
                    note = await self._buy_role(ctx, roles[idx])
                continue

    def _buy_bitcoin(self, ctx, price) -> str:
        if price <= 0:
            return "Bitcoin price is unavailable right now."
        try:
            cost = self.economy.buy_bitcoin(ctx.author.id, 1, price)
        except RuntimeError:
            return f"You can't afford {format_bitcoin(1)} ({format_money(price)})."
        log_wallet_change(
            logger,
            event="bitcoin_buy",
            user_id=ctx.author.id,
            money_delta=-cost,
            ctx=ctx,
            bitcoin_delta=1,
            unit_price=price,
        )
        return f"Bought {format_bitcoin(1)} for {format_money(cost)}."

    def _sell_bitcoin(self, ctx, price) -> str:
        if price <= 0:
            return "Bitcoin price is unavailable right now."
        try:
            payout = self.economy.sell_bitcoin(ctx.author.id, 1, price)
        except RuntimeError:
            return "You don't have any Bitcoin to sell."
        log_wallet_change(
            logger,
            event="bitcoin_sell",
            user_id=ctx.author.id,
            money_delta=payout,
            ctx=ctx,
            bitcoin_delta=-1,
            unit_price=price,
        )
        return f"Sold {format_bitcoin(1)} for {format_money(payout)}."

    async def _buy_role(self, ctx, listing) -> str:
        role_id, rprice, currency = listing
        guild = ctx.guild
        if guild is None:
            return "Roles can only be bought in the server."
        role = guild.get_role(role_id)
        if role is None:
            return "That role no longer exists."
        member = ctx.author
        if role in getattr(member, "roles", []):
            return f"You already own {role.name}."
        me = guild.me
        if not me.guild_permissions.manage_roles or role >= me.top_role:
            return (
                f"I can't assign {role.name} — check my Manage Roles permission "
                "and that my role sits above it."
            )
        if currency == "bitcoin":
            paid = format_bitcoin(rprice)
            ok = self.economy.debit_bitcoin(member.id, rprice)
        else:
            paid = format_money(rprice)
            ok = self.economy.debit_money(member.id, rprice)
        if not ok:
            return f"You can't afford {role.name} ({paid})."
        try:
            await member.add_roles(role, reason="SIREN Black Market purchase")
        except discord.HTTPException:
            if currency == "bitcoin":
                self.economy.add_bitcoin(member.id, rprice)
            else:
                self.economy.add_money(member.id, rprice)
            return f"Couldn't assign {role.name} — you were refunded {paid}."
        log_wallet_change(
            logger,
            event="role_purchase",
            user_id=member.id,
            money_delta=-rprice if currency == "money" else 0,
            ctx=ctx,
            role_id=role_id,
            price=rprice,
            currency=currency,
        )
        return f"Purchased {role.name} for {paid}!"

    # ---------------- admin: market management ----------------
    @commands.command(
        brief="(Admin) List a role for sale",
        usage="addrole [@role] [price] *[money|bitcoin]",
        hidden=True,
    )
    @is_admin()
    async def addrole(
        self,
        ctx: commands.Context,
        role: discord.Role,
        price: int,
        currency: str = "money",
    ):
        currency = currency.lower()
        if currency in ("btc", "bitcoin"):
            currency = "bitcoin"
        elif currency in ("money", "cash", "dollars", "$"):
            currency = "money"
        else:
            await ctx.send("Currency must be `money` or `bitcoin`.")
            return
        if price <= 0:
            await ctx.send("Price must be a positive whole number.")
            return
        self.economy.set_market_role(role.id, price, currency)
        cost = format_bitcoin(price) if currency == "bitcoin" else format_money(price)
        await ctx.send(
            embed=make_embed(
                title="Role listed",
                color=discord.Color.green(),
                description=f"**{role.name}** is now for sale for **{cost}**.",
            )
        )

    @commands.command(
        brief="(Admin) Remove a role from the market",
        usage="removerole [@role]",
        hidden=True,
    )
    @is_admin()
    async def removerole(self, ctx: commands.Context, role: discord.Role):
        removed = self.economy.remove_market_role(role.id)
        await ctx.send(
            f"Removed **{role.name}** from the market."
            if removed
            else "That role wasn't listed."
        )

    @commands.command(
        brief="(Admin) Force a Bitcoin price refresh",
        usage="refreshbtc",
        hidden=True,
    )
    @is_admin()
    async def refreshbtc(self, ctx: commands.Context):
        price = await self._refresh_price()
        if price:
            await ctx.send(f"Bitcoin price updated: **{format_money(price)}**.")
        else:
            await ctx.send(
                "Couldn't fetch the Bitcoin price right now — try again shortly."
            )


async def setup(client: commands.Bot):
    await client.add_cog(Market(client))
