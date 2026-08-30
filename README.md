# Casino Bot
Casino bot is a gambling + community-economy Discord bot. It stores everyone's
money, credits, and Bitcoin on an SQLite3 database.

A Discord casino bot with a FastAPI web host and interactive demo UI. Player-facing
commands work as **both** `$`-prefix commands **and** `/` slash commands (hybrid).

It supports:
- Blackjack (reactions for hit/stand/double/split/surrender, insurance, split hands)
- High card (`highcard` / `war`)
- Slots (animated GIF reels + credit economy)
- Coin flip (`flip`) with a polished result embed
- Community economy: wallet, leaderboard, daily reward, player-to-player transfers
- Bitcoin currency: buy/sell at a live daily price (CoinGecko), whole coins only
- **SIREN's Black Market**: a reaction-driven hub to buy Bitcoin and custom roles
- Admin tooling: add funds to a user, manage the Black Market

The app starts a web server and, if configured, starts the Discord bot in the same process.

> **Note on money display:** amounts render as `$1,234` with an optional custom
> server emoji suffix (see `DISCORD_CASH_EMOJI`); Bitcoin renders as a whole-coin
> count with an optional `:bitcoin:` emoji (see `DISCORD_BITCOIN_EMOJI`).

<img src="./pictures/blackjack.png" alt="blackjack" height="200"/>
<img src="./pictures/slots.gif" alt="slots" width="200"/>

## Architecture

- FastAPI app entrypoint: `app/backend/main.py`
- Discord bot entrypoint: `app/discord_bot/bot.py`
- SQLite economy layer: `app/discord_bot/modules/economy.py`
- Demo UI: `app/backend/static/demo/`

Behavior at startup:
- If `DISCORD_TOKEN` is set, the Discord bot is launched during FastAPI lifespan startup.
- If `DISCORD_TOKEN` is missing, the app runs in web-only mode (demo still works).

## Requirements

- Python 3.10+
- A Discord bot token (only required for Discord mode)

## Quick Start (Local)

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy env template and edit values:

```bash
cp .env.example .env
```

4. Run the app:

```bash
uvicorn app.backend.main:app --host 0.0.0.0 --port 8000
```

5. Open:
- `http://localhost:8000/`
- `http://localhost:8000/demo`

## Environment Variables

Configured in `.env` (or process environment):

- `DISCORD_TOKEN`: Discord bot token. Leave empty for web-only mode.
- `DISCORD_OWNER_IDS`: Comma-separated Discord user IDs for owner-only commands.
- `DISCORD_PREFIX`: Prefix command trigger (default: `$`).
- `DISCORD_DEFAULT_BET`: Default money bet (default: `100`, allowed `1..1000000`).
- `DISCORD_BONUS_MULTIPLIER`: Bonus multiplier for the legacy `add` bonus math (default: `5`, allowed `1..1000`).
- `DISCORD_BONUS_COOLDOWN`: Bonus cooldown in hours (default: `12`, allowed `1..168`).
- `DISCORD_DAILY_AMOUNT`: Flat `$daily` reward (default: `500`).
- `DISCORD_DAILY_COOLDOWN`: `$daily` cooldown in hours (default: `24`).
- `DISCORD_GUILD_ID` (optional): Set to your server ID for **instant** slash-command
  (`/`) registration. Leave blank to register globally (can take up to ~1 hour to
  appear the first time).
- `DISCORD_ADMIN_ROLE_ID` (optional): Role ID that grants access to admin-only
  commands (`$add`, Black Market management). The bot owner is always allowed.
- `DISCORD_CASH_EMOJI` (optional): Full custom-emoji form shown as a suffix next to
  money, e.g. `<:cash:123456789012345678>`. Falls back to a plain `$`.
- `DISCORD_BITCOIN_EMOJI` (optional): Full custom-emoji form for Bitcoin, e.g.
  `<:bitcoin:123456789012345678>`. Falls back to `BTC`.
- `CASINO_DATA_DIR`: Base runtime data directory (default: `./data`).
- `CASINO_DATABASE_PATH` (optional): Override SQLite DB path.
- `CASINO_LOG_PATH` (optional): Override log file path.

See `.env.example` for current defaults.

## Discord Setup Notes

For prefix commands to work correctly, enable these in the Discord Developer Portal:

- Message Content Intent
- Server Members Intent

The bot requests standard intents for guilds, messages, message content, and members.

**Slash commands (`/`):** player-facing commands are registered as hybrid commands,
so they also appear in Discord's native `/` autocomplete popup. Invite the bot with
the `applications.commands` scope for these to register. Set `DISCORD_GUILD_ID` to
your server for instant updates; otherwise global registration can take ~1 hour to
first appear.

**Role purchases:** for the Black Market to grant purchased roles, the bot needs the
**Manage Roles** permission and its own role positioned **above** any role it sells.

## Bot Commands

`$` is your `DISCORD_PREFIX`. Player-facing commands also work as `/` slash commands.

General / economy:
- `$help [command]`
- `$money [@member]` (`$credits` alias)
- `$leaderboard` (`$top` alias)
- `$daily` — claim a flat daily reward (persistent 24h cooldown, survives restarts)
- `$pay <@member> <amount>` (`$give` alias) — atomic player-to-player transfer

Casino:
- `$blackjack [bet]` (`$bj` alias)
- `$highcard [bet]` (`$war` alias)
- `$flip <heads|tails> [bet]` — polished result embed with win/lose art
- `$slots [bet]` (credits, bet range 1-3)
- `$buyc <credits>` (`$buy`, `$b` aliases)
- `$sellc <credits>` (`$sell`, `$s` aliases)

Bitcoin & Black Market:
- `$bitcoin` (`$btc` alias) — show the current Bitcoin price and your holdings
- `$market` (`$blackmarket`, `$shop` aliases) — reaction-driven hub: buy/sell 1 BTC
  (📈/📉), buy listed roles (number reactions), refresh (🔄), close (❌)

Admin-only (bot owner or `DISCORD_ADMIN_ROLE_ID`):
- `$add <amount> [@member]` — add funds to a user (or yourself)
- `$addrole <@role> <price> [money|bitcoin]` — list a role for sale
- `$removerole <@role>` — remove a role listing
- `$refreshbtc` — force a Bitcoin price refresh now

Owner-only:
- `$set [balance|credits] [user_id] [amount]`
- `$kill`

> `$roll` (dice) was removed. The old `$add` self-bonus was repurposed into the
> admin add-funds command above.

## Web Demo

The demo frontend is served at `/` and `/demo` and talks to these API routes:

- `GET /api/demo/config`
- `POST /api/demo/command`
- `POST /api/demo/action`
- `POST /api/demo/reset`
- `GET /api/demo/assets/{asset_id}`

The demo runtime intentionally limits commands to:
- `help`, `money`, `blackjack`, `war`, `slots`

Rate limiting is enabled on demo API endpoints.

## Data, Logging, and Migrations

Runtime data defaults to `./data`:

- Database: `./data/economy.db`
- Logs: `./data/logs/casino-bot.log`

SQLite schema migrations are versioned and applied automatically on startup. The
current schema is **version 4**:
- v2: economy indexes
- v3: `daily_claims` table (persistent `$daily` cooldown that survives restarts)
- v4: `bitcoin` holding column on `economy`, plus `market_roles` and `market_state`
  tables (Bitcoin price + last-update timestamp). Additive/non-destructive.

Money mutations (transfers, buys, sells) use atomic conditional `UPDATE ... WHERE
col >= ?` statements with rowcount checks + rollback, so a crash or race cannot mint
or vaporize currency.

## Bitcoin price

The daily Bitcoin price is fetched from **CoinGecko's keyless public API**
(`/api/v3/simple/price?ids=bitcoin&vs_currencies=usd`), rounded to the nearest whole
dollar, and persisted. It auto-refreshes once per day at 00:00 UTC, with a startup
fetch if the stored price is missing or older than 24h. Admins can force a refresh
with `$refreshbtc`.

## Docker

Build:

```bash
docker build -t casino-bot .
```

Run (with persistent data mount):

```bash
docker run --rm -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" casino-bot
```

Container defaults:
- Exposes port `8000`
- Runs as non-root user
- Healthcheck targets `GET /api/demo/config`
