from datetime import datetime
from pathlib import Path

from discord import Color, Embed

from app.config import config


def format_money(amount: int, *, signed: bool = False) -> str:
    """Render a dollar amount as '$1,234' with the cash emoji as a suffix when set."""
    suffix = f" {config.bot.cash_emoji}" if config.bot.cash_emoji else ""
    magnitude = f"{abs(int(amount)):,}"
    if signed:
        if amount > 0:
            return f"+${magnitude}{suffix}"
        if amount < 0:
            return f"-${magnitude}{suffix}"
        return f"$0{suffix}"
    return f"${magnitude}{suffix}"


class InsufficientFundsException(Exception):
    def __init__(self, current, bet) -> None:
        self.needs = bet - current
        super().__init__()

    def __str__(self) -> str:
        return f"{format_money(self.needs)} more needed to play."


class InsufficientCreditsException(Exception):
    def __init__(self, current: int, required: int) -> None:
        self.needs = required - current
        super().__init__()

    def __str__(self) -> str:
        return f"{self.needs} more credits needed."


ABS_PATH = Path(__file__).resolve().parent.parent
COG_FOLDER = str(ABS_PATH / "cogs")


def make_embed(title=None, description=None, color=None, author=None,
               image=None, link=None, footer=None) -> Embed:
    """Wrapper for making discord embeds"""
    embed = Embed(
        title=title or None,
        description=description or None,
        url=link or None,
        color=color if color else Color.random()
    )
    if author:
        embed.set_author(name=author)
    if image:
        embed.set_image(url=image)
    if footer:
        embed.set_footer(text=footer)
    else:
        embed.set_footer(text=datetime.now().strftime("%m/%d/%Y %H:%M:%S"))
    return embed
