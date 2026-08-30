# Changelog

All notable changes to this fork are documented here. This fork is a personal copy
of an upstream casino bot, modernized and extended for the "SIREN_02" deployment.

## [modernize branch] — 2026-08

A packaging repair + hygiene pass, then a run of community-economy and Black Market
features. The application code was already modern (discord.py 2.x / FastAPI
`lifespan` / Pydantic v2 / Pillow-10-safe rendering); the "outdated" symptoms were
entirely in packaging.

### Packaging repair (the build fix)
- **Regenerated `requirements.txt`.** The old file pinned the *original upstream
  project's* 2020-era dependencies (`discord.py==1.7.1`, `Pillow==8.2.0`,
  `multidict==5.1.0`, `yarl==1.6.3`, …) that (a) have no prebuilt wheels for the
  Dockerfile's Python 3.12 and (b) omitted every dependency the current code
  actually imports (`fastapi`, `uvicorn`, `pydantic`, `python-dotenv`). The
  container tried to compile the old pins from source and failed (`gcc`/`zlib`
  missing in `python:3.12-slim`). Regenerated from the real import set — all resolve
  to prebuilt cp312 wheels, so the build no longer needs a compiler.
- **Removed the vestigial `COPY config.yml`** from the `Dockerfile`. The app is 100%
  env-driven (`app/config.py` uses `python-dotenv` + `os.getenv`); it never reads a
  `config.yml`, and none was ever committed, so the `COPY` broke every build.
- **Deleted the dead top-level `discord/` directory** — leftover duplicate code that
  also shadowed the `discord.py` import namespace.

### Fixed
- **`await remove_cog` in `help_command.py`.** In discord.py 2.x `remove_cog` is a
  coroutine; the owner-only `$kill` called it unawaited (RuntimeWarning / 1.x-ism).
  This was the only genuine legacy-API defect found in a full line-by-line audit.

### Added — community economy
- **`$daily`** — flat daily reward (default `$500`, `DISCORD_DAILY_AMOUNT`). The
  cooldown is **persisted in SQLite** (`daily_claims` table, schema v3) so it
  survives restarts — unlike the built-in `$add` bonus, whose `@commands.cooldown`
  is in-memory and resets on every deploy.
- **`$pay` / `$give`** — player-to-player transfer. Single atomic transaction
  (conditional debit + rollback); rejects self-pay, bots, non-positive amounts, and
  insufficient funds.

### Added — slash-command popups (hybrid commands)
- Converted the 12 player-facing commands to `commands.hybrid_command` so they work
  as **both** `$`-prefix and `/` slash commands, with `app_commands.describe`
  parameter hints in Discord's native autocomplete. Admin/owner (`$set`, `$kill`,
  `$add`, market admin) and `$help` stay prefix-only.
- Added `DISCORD_GUILD_ID` — when set, the command tree is guild-synced for
  **instant** updates; otherwise global sync (up to ~1h to first appear).

### Added — money display
- Central `format_money()` helper renders `$1,234` with an optional custom-emoji
  **suffix** (`DISCORD_CASH_EMOJI`), falling back to a plain `$`.
- **`$flip` reworked** from a bare "correct/wrong" into a polished embed showing the
  coin result, your call, bet, net win/loss, and balance, with per-outcome art
  (`flip_heads.jpg` / `flip_tails.jpg`, copied into `app/discord_bot/modules/`).
- Removed the `$roll` dice game entirely.

### Added — admin tooling
- **`$add <amount> [@member]`** — repurposed from a duplicate-of-`$daily` self-bonus
  into an admin-only add-funds command.
- Admin gate via `app/discord_bot/modules/checks.py::is_admin()` — allows the bot
  owner **or** `DISCORD_ADMIN_ROLE_ID`. Raises `CheckFailure`, handled cleanly by the
  error handler (placed after the `MissingPermissions` handlers, which subclass it).

### Added — Bitcoin currency + SIREN's Black Market
- **Bitcoin** — a whole-coin `bitcoin` holding per user (schema v4). Price sourced
  from **CoinGecko's keyless API**, rounded to the nearest whole dollar, persisted
  (`market_state` table), auto-updated once/day at 00:00 UTC + a startup staleness
  fetch + admin `$refreshbtc`. `$bitcoin` / `$btc` shows price + holdings.
- **`$market`** ("SIREN's Black Market") — a **reaction-driven** hub (buy/sell 1 BTC
  via 📈/📉, buy listed roles via number reactions, 🔄 refresh, ❌ close). Uses a
  delete-and-resend menu per cycle so it needs **no Manage Messages** permission.
- **Role sales** — `$addrole <@role> <price> [money|bitcoin]`, `$removerole <@role>`
  (`market_roles` table). Purchases debit atomically and refund on a failed
  `add_roles`. Bitcoin buy/sell mirror the `$pay` atomic-debit pattern.
- Config: `DISCORD_ADMIN_ROLE_ID`, `DISCORD_BITCOIN_EMOJI`; helper `format_bitcoin()`.

### Added — flavor text
- `$bitcoin` / `$btc` now note: *"Bitcoin price fluctuates over time, roughly
  mirroring real BTC crypto market price."*
- The `$market` embed carries introductory in-character context below the title.

### Nuances / lessons encountered
- **Custom emoji rendering.** A bot can only render a custom emoji via its full
  `<:name:id>` form (typing `:name:` only works in a human's client composer). Custom
  emoji also **do not render inside code spans** or in slash-command descriptions —
  so blackjack's backtick-wrapped `` `bet $X` `` line and the `brief=`/`description=`
  help strings deliberately keep a plain `$`.
- **The 3-second slash-command deadline.** Image-rendering games (`/blackjack`,
  `/slots`, `/highcard`) can exceed the 3s interaction window, so they call
  `ctx.defer()` on the slash path before rendering.
- **Manage Messages not assumed.** The reaction-driven `$market` deletes and resends
  its menu each cycle rather than clearing reactions, so it works without Manage
  Messages (the same pattern blackjack already uses).
- **Atomic money.** All currency mutations use conditional `UPDATE ... WHERE col >= ?`
  + rowcount check + rollback, so no crash or race can duplicate or destroy currency.
- **DB-safe passwords (deploy).** Postgres isn't used here (SQLite), but the sibling
  deploy lesson still applies to any DSN: prefer URL-safe secrets (`openssl rand
  -hex`) over base64 (`/`, `+`, `@`, `:` corrupt connection strings).
- **Migrations are additive.** v3/v4 use `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE
  ADD COLUMN`; existing balances are untouched. Snapshot `data/economy.db` before a
  schema-changing deploy anyway.

### Deployment notes (SIREN_02 LXC)
- Runs as a single Docker container on an isolated VLAN (full outbound WAN to
  Discord + CoinGecko, zero inbound, denied the LAN infra plane). The web backend
  (`:8000`) is loopback-bound.
- Required `.env` for full functionality: `DISCORD_TOKEN`, `DISCORD_PREFIX`,
  `DISCORD_OWNER_IDS`, `DISCORD_GUILD_ID`, `DISCORD_ADMIN_ROLE_ID`,
  `DISCORD_CASH_EMOJI`, `DISCORD_BITCOIN_EMOJI`.
- On a ZFS Proxmox host, the container needs Docker's `fuse-overlayfs` storage
  driver and the LXC `fuse=1` feature (plus `nesting=1,keyctl=1`).
