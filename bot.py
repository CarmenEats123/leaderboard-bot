import os
import json
import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# DETECTIVE YAMAHA — DPI SERVER WATCH v4
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

# Matrona+ alert channel already supplied by you.
ALERT_CHANNEL_ID = int(
    os.getenv("ALERT_CHANNEL_ID", "1544431033787613204")
    or 1544431033787613204
)

# If you know #server-list's ID, set CURRENT_SERVER_CHANNEL_ID in Env.
# Otherwise the bot finds a text channel named "server-list".
CURRENT_SERVER_CHANNEL_ID = int(
    os.getenv("CURRENT_SERVER_CHANNEL_ID", "0") or 0
)

SERVER_DESIGNER_ROLE_ID = int(
    os.getenv("SERVER_DESIGNER_ROLE_ID", "0") or 0
)

ROBLOX_GROUP_ID = 5008654
DPI_PLACE_ID = 3522803956
DPI_UNIVERSE_ID = 1246853548

STAFF_MIN_RANK = 50
PRESENCE_SCAN_MINUTES = 5
STAFF_REFRESH_MINUTES = 60

SERVER_LIST_CHANNEL_NAME = "server-list"
SERVER_DESIGNER_ROLE_NAME = "Server Designer"

STAFF_CACHE_FILE = Path("yamaha_staff_cache.json")
PRESENCE_STATE_FILE = Path("yamaha_presence_state.json")
MESSAGE_STATE_FILE = Path("yamaha_message_state.json")

GROUPS_API = "https://groups.roblox.com"
PRESENCE_API = "https://presence.roblox.com"
GAMES_API = "https://games.roblox.com"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

scan_lock = asyncio.Lock()
staff_lock = asyncio.Lock()

last_scan = None
last_error = None
last_staff_refresh = None


# =========================================================
# UTILITIES
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def profile_url(user_id):
    return f"https://www.roblox.com/users/{user_id}/profile"


def find_server_list_channel(guild):
    if CURRENT_SERVER_CHANNEL_ID:
        channel = guild.get_channel(CURRENT_SERVER_CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            return channel

    for channel in guild.text_channels:
        if channel.name.lower() == SERVER_LIST_CHANNEL_NAME:
            return channel

    return None


def find_alert_channel(guild):
    channel = guild.get_channel(ALERT_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        return channel
    return None


def find_server_designer_role(guild):
    if SERVER_DESIGNER_ROLE_ID:
        role = guild.get_role(SERVER_DESIGNER_ROLE_ID)
        if role:
            return role

    for role in guild.roles:
        if role.name.lower() == SERVER_DESIGNER_ROLE_NAME.lower():
            return role

    return None


async def request_json(
    session,
    method,
    url,
    *,
    params=None,
    payload=None,
    retries=8,
):
    delay = 8.0

    for attempt in range(retries):
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=payload,
            ) as response:
                body = await response.text()

                if response.status == 200:
                    return json.loads(body)

                if response.status == 429 and attempt < retries - 1:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_after)
                    except Exception:
                        wait = delay

                    wait = max(wait, delay) + random.uniform(0.5, 1.5)
                    print(
                        f"Roblox 429 — cooling down {wait:.1f}s "
                        f"({attempt + 1}/{retries})"
                    )
                    await asyncio.sleep(wait)
                    delay = min(delay * 1.8, 90)
                    continue

                if 500 <= response.status <= 599 and attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 1.8, 90)
                    continue

                raise RuntimeError(
                    f"Roblox API {response.status}: {body[:500]}"
                )

        except aiohttp.ClientError as exc:
            if attempt >= retries - 1:
                raise RuntimeError(f"Network error: {exc!r}")
            await asyncio.sleep(delay)
            delay = min(delay * 1.8, 90)

    raise RuntimeError("Roblox request failed after retries.")


# =========================================================
# STAFF CACHE — EXPENSIVE, SO ONLY HOURLY
# =========================================================

async def get_group_roles(session):
    data = await request_json(
        session,
        "GET",
        f"{GROUPS_API}/v1/groups/{ROBLOX_GROUP_ID}/roles",
    )
    return data.get("roles", [])


async def get_users_in_role(session, role_id):
    users = {}
    cursor = None

    while True:
        params = {"limit": 100, "sortOrder": "Asc"}
        if cursor:
            params["cursor"] = cursor

        data = await request_json(
            session,
            "GET",
            f"{GROUPS_API}/v1/groups/{ROBLOX_GROUP_ID}/roles/{role_id}/users",
            params=params,
        )

        for item in data.get("data", []):
            uid = item.get("userId") or item.get("id")
            if uid is None:
                continue

            users[str(uid)] = {
                "username":
                    item.get("username")
                    or item.get("name")
                    or f"User {uid}",
                "display_name":
                    item.get("displayName") or "",
            }

        cursor = data.get("nextPageCursor")
        if not cursor:
            break

        await asyncio.sleep(1.0)

    return users


async def refresh_staff_cache():
    global last_staff_refresh

    async with staff_lock:
        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Watch/4.0"
        }
        timeout = aiohttp.ClientTimeout(total=600)

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:

            roles = await get_group_roles(session)

            matrona_roles = [
                r for r in roles
                if "matrona" in str(r.get("name", "")).lower()
            ]

            if not matrona_roles:
                raise RuntimeError("Matrona role not found.")

            matrona_rank = min(
                int(r.get("rank", 0) or 0)
                for r in matrona_roles
            )

            staff_roles = sorted(
                [
                    r for r in roles
                    if int(r.get("rank", 0) or 0) >= STAFF_MIN_RANK
                ],
                key=lambda r: int(r.get("rank", 0) or 0),
            )

            staff = {}

            for index, role in enumerate(staff_roles, start=1):
                role_id = role.get("id")
                role_name = role.get("name", "Unknown")
                role_rank = int(role.get("rank", 0) or 0)

                print(
                    f"Staff cache {index}/{len(staff_roles)}: {role_name}"
                )

                users = await get_users_in_role(session, role_id)

                for uid, info in users.items():
                    staff[uid] = {
                        "username": info["username"],
                        "display_name": info["display_name"],
                        "role_name": role_name,
                        "role_rank": role_rank,
                        "is_matrona_plus":
                            role_rank >= matrona_rank,
                    }

                # Intentionally gentle on Roblox.
                await asyncio.sleep(2.0)

        snapshot = {
            "staff": staff,
            "matrona_rank": matrona_rank,
            "updated_at": utc_iso(),
        }

        save_json(STAFF_CACHE_FILE, snapshot)
        last_staff_refresh = snapshot["updated_at"]

        print(
            f"Staff cache ready: {len(staff)} staff."
        )
        return snapshot


async def ensure_staff_cache():
    cached = load_json(STAFF_CACHE_FILE, {})
    if cached.get("staff"):
        return cached
    return await refresh_staff_cache()


@tasks.loop(minutes=STAFF_REFRESH_MINUTES)
async def staff_refresh_loop():
    try:
        await refresh_staff_cache()
    except Exception as exc:
        print(
            "Hourly staff refresh failed; old cache kept:",
            repr(exc),
        )


@staff_refresh_loop.before_loop
async def before_staff_refresh_loop():
    await bot.wait_until_ready()


# =========================================================
# PRESENCE — EVERY 5 MINUTES
# =========================================================

async def get_user_presences(session, user_ids):
    if not user_ids:
        return {}

    ids = [int(uid) for uid in user_ids]
    results = {}

    async def fetch_batch(batch):
        if not batch:
            return

        try:
            data = await request_json(
                session,
                "POST",
                f"{PRESENCE_API}/v1/presence/users",
                payload={"userIds": batch},
            )
        except RuntimeError as exc:
            if (
                "too many user ids" in str(exc).lower()
                and len(batch) > 1
            ):
                middle = len(batch) // 2
                await fetch_batch(batch[:middle])
                await asyncio.sleep(1.0)
                await fetch_batch(batch[middle:])
                return
            raise

        for item in data.get("userPresences", []):
            uid = item.get("userId")
            if uid is not None:
                results[str(uid)] = item

    # 25 IDs/batch: more conservative on shared-host IPs.
    for offset in range(0, len(ids), 25):
        await fetch_batch(ids[offset:offset + 25])

        if offset + 25 < len(ids):
            await asyncio.sleep(1.25)

    return results


def is_in_dpi(presence):
    if not presence:
        return False

    return (
        str(presence.get("universeId")) == str(DPI_UNIVERSE_ID)
        or str(presence.get("rootPlaceId")) == str(DPI_PLACE_ID)
        or str(presence.get("placeId")) == str(DPI_PLACE_ID)
    )


def build_servers(snapshot, presences):
    servers = {}

    for uid, info in snapshot.get("staff", {}).items():
        presence = presences.get(str(uid), {})

        if not is_in_dpi(presence):
            continue

        game_id = presence.get("gameId")
        key = str(game_id) if game_id else "server-hidden"

        servers.setdefault(key, []).append({
            "user_id": str(uid),
            "username": info["username"],
            "role_name": info["role_name"],
            "role_rank": int(info["role_rank"]),
            "is_matrona_plus":
                bool(info.get("is_matrona_plus")),
            "game_id": game_id,
        })

    for members in servers.values():
        members.sort(
            key=lambda p: (
                -p["role_rank"],
                p["username"].lower(),
            )
        )

    return servers


async def get_server_counts(session, server_ids):
    wanted = {
        str(sid)
        for sid in server_ids
        if sid and sid != "server-hidden"
    }

    if not wanted:
        return {}

    found = {}
    cursor = None

    # Cap pagination so one scan cannot run wild.
    for _ in range(8):
        params = {
            "sortOrder": "Asc",
            "limit": 100,
        }
        if cursor:
            params["cursor"] = cursor

        data = await request_json(
            session,
            "GET",
            f"{GAMES_API}/v1/games/{DPI_PLACE_ID}/servers/Public",
            params=params,
        )

        for server in data.get("data", []):
            sid = str(server.get("id", ""))

            if sid in wanted:
                found[sid] = {
                    "playing": server.get("playing"),
                    "max_players": server.get("maxPlayers"),
                }

        if wanted.issubset(found.keys()):
            break

        cursor = data.get("nextPageCursor")
        if not cursor:
            break

        await asyncio.sleep(1.0)

    return found


# =========================================================
# ONE LIVE MESSAGE IN #server-list
# =========================================================

def build_live_content(servers, counts):
    total_staff = sum(
        len(members)
        for members in servers.values()
    )

    lines = [
        "## 🛰️ Detective Yamaha — DPI Server List",
        (
            f"Tracked staff visible in DPI: "
            f"**{total_staff}**"
        ),
        (
            "Sorted **highest rank → lowest rank** • "
            "⭐ = Matrona+"
        ),
        "",
    ]

    exact = [
        (sid, members)
        for sid, members in servers.items()
        if sid != "server-hidden"
    ]

    exact.sort(
        key=lambda item: (
            -max(x["role_rank"] for x in item[1]),
            -len(item[1]),
            item[0],
        )
    )

    for index, (sid, members) in enumerate(exact, start=1):
        c = counts.get(str(sid), {})
        playing = c.get("playing")
        maximum = c.get("max_players")

        if playing is None:
            population = "player count unavailable"
        elif maximum is None:
            population = f"{playing} players"
        else:
            population = f"{playing}/{maximum} players"

        lines.extend([
            (
                f"### Server {index} — **{population}** "
                f"• **{len(members)} staff**"
            ),
            f"`{sid}`",
        ])

        for p in members:
            star = " ⭐" if p["is_matrona_plus"] else ""
            lines.append(
                f"• **{p['username']}**{star} — {p['role_name']}"
            )

        lines.append("")

    if "server-hidden" in servers:
        lines.append("### In DPI — exact server hidden")

        for p in servers["server-hidden"]:
            star = " ⭐" if p["is_matrona_plus"] else ""
            lines.append(
                f"• **{p['username']}**{star} — {p['role_name']}"
            )

        lines.append("")

    if not servers:
        lines.extend([
            "No rank-50+ staff are currently "
            "**publicly visible** inside DPI.",
            "",
        ])

    lines.extend([
        (
            f"_Updated <t:{int(utc_now().timestamp())}:R> • "
            "automatic scan every 5 minutes_"
        ),
        (
            "_Roblox public presence only; hidden presence "
            "cannot be detected._"
        ),
    ])

    content = "\n".join(lines)

    # User specifically requested ONE message.
    if len(content) > 1990:
        cut = content[:1900].rsplit("\n", 1)[0]
        content = (
            cut
            + "\n\n_⚠️ More staff were detected, but Discord's "
              "single-message character limit was reached._"
        )

    return content


async def upsert_server_list_message(guild, servers, counts):
    channel = find_server_list_channel(guild)

    if not channel:
        raise RuntimeError(
            'Could not find the Discord channel "server-list". '
            "Set CURRENT_SERVER_CHANNEL_ID in Env if its name differs."
        )

    content = build_live_content(servers, counts)

    saved = load_json(MESSAGE_STATE_FILE, {})
    message_id = saved.get(str(guild.id))
    message = None

    if message_id:
        try:
            message = await channel.fetch_message(int(message_id))
        except Exception:
            message = None

    if message is None:
        try:
            async for old in channel.history(limit=50):
                if (
                    old.author.id == bot.user.id
                    and old.content.startswith(
                        "## 🛰️ Detective Yamaha — DPI Server List"
                    )
                ):
                    message = old
                    break
        except Exception:
            pass

    if message is None:
        message = await channel.send(content)
    else:
        await message.edit(content=content)

    saved[str(guild.id)] = str(message.id)
    save_json(MESSAGE_STATE_FILE, saved)


# =========================================================
# MATRONa+ ALERT
# =========================================================

async def send_matrona_alert(guild, joined):
    if not joined:
        return

    channel = find_alert_channel(guild)
    if not channel:
        print(
            f"Alert channel {ALERT_CHANNEL_ID} not found."
        )
        return

    role = find_server_designer_role(guild)

    mention = (
        role.mention
        if role
        else "**Server Designer**"
    )

    lines = [
        f"{mention} 🚨 **Matrona+ joined DPI**"
    ]

    for p in joined:
        server_label = (
            f"`{p['game_id']}`"
            if p.get("game_id")
            else "server hidden"
        )

        lines.append(
            f"• **{p['username']}** — **{p['role_name']}** "
            f"• {server_label} • "
            f"[profile]({profile_url(p['user_id'])})"
        )

    await channel.send(
        "\n".join(lines),
        allowed_mentions=discord.AllowedMentions(
            roles=True,
            users=False,
            everyone=False,
        ),
    )


# =========================================================
# SCAN
# =========================================================

async def run_scan():
    global last_scan, last_error

    async with scan_lock:
        snapshot = await ensure_staff_cache()

        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Watch/4.0"
        }
        timeout = aiohttp.ClientTimeout(total=300)

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:

            presences = await get_user_presences(
                session,
                list(snapshot.get("staff", {}).keys()),
            )

            servers = build_servers(
                snapshot,
                presences,
            )

            counts = await get_server_counts(
                session,
                [
                    sid
                    for sid in servers
                    if sid != "server-hidden"
                ],
            )

        previous = load_json(PRESENCE_STATE_FILE, {})
        previous_ids = set(
            previous.get("in_dpi", {}).keys()
        )

        current = {}
        joined = []

        for server_id, members in servers.items():
            for p in members:
                uid = p["user_id"]

                current[uid] = {
                    "server_id": server_id,
                    "username": p["username"],
                    "role_name": p["role_name"],
                    "is_matrona_plus":
                        p["is_matrona_plus"],
                }

                # No startup spam: only alert after a baseline exists.
                if (
                    previous
                    and uid not in previous_ids
                    and p["is_matrona_plus"]
                ):
                    joined.append(p)

        save_json(
            PRESENCE_STATE_FILE,
            {
                "in_dpi": current,
                "updated_at": utc_iso(),
            },
        )

        for guild in bot.guilds:
            await upsert_server_list_message(
                guild,
                servers,
                counts,
            )

            if joined:
                await send_matrona_alert(
                    guild,
                    joined,
                )

        last_scan = utc_iso()
        last_error = None

        result = {
            "staff_checked":
                len(snapshot.get("staff", {})),
            "visible":
                sum(len(v) for v in servers.values()),
            "server_groups":
                len(servers),
            "new_matrona_plus":
                len(joined),
        }

        print("Yamaha scan OK:", result)
        return result


@tasks.loop(minutes=PRESENCE_SCAN_MINUTES)
async def presence_loop():
    try:
        await run_scan()
    except Exception as exc:
        global last_error
        last_error = repr(exc)

        print(
            "Yamaha scan failed; bot stays alive and retries next cycle:",
            repr(exc),
        )


@presence_loop.before_loop
async def before_presence_loop():
    await bot.wait_until_ready()


# =========================================================
# COMMANDS
# =========================================================

@bot.tree.command(
    name="serverscan",
    description="Refresh Detective Yamaha's DPI server list now.",
)
async def serverscan(interaction: discord.Interaction):
    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    try:
        result = await run_scan()

        await interaction.followup.send(
            (
                "✅ **Server scan completed.**\n"
                f"Staff checked: **{result['staff_checked']}**\n"
                f"Staff visible in DPI: **{result['visible']}**\n"
                f"Server groups: **{result['server_groups']}**\n"
                f"New Matrona+ joins: **{result['new_matrona_plus']}**"
            ),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            (
                "⚠️ **The bot is online, but Roblox rejected this scan.** "
                "The 5-minute watcher remains active and will retry.\n"
                f"`{type(exc).__name__}: {str(exc)[:350]}`"
            ),
            ephemeral=True,
        )


@bot.tree.command(
    name="yamaha",
    description="Show Detective Yamaha status.",
)
async def yamaha(interaction: discord.Interaction):
    cache = load_json(STAFF_CACHE_FILE, {})

    text = (
        "🕵️ **Detective Yamaha — DPI Server Watch v4**\n"
        f"• Server scan: every **{PRESENCE_SCAN_MINUTES} minutes**\n"
        f"• Staff cache: every **{STAFF_REFRESH_MINUTES} minutes**\n"
        f"• Cached staff: **{len(cache.get('staff', {}))}**\n"
        f"• Server-list channel: **#{SERVER_LIST_CHANNEL_NAME}**\n"
        f"• Alert: **Matrona+ → Server Designer ping**\n"
        f"• Last success: **{last_scan or 'Not yet'}**"
    )

    if last_error:
        text += f"\n• Last error: `{last_error[:450]}`"

    await interaction.response.send_message(
        text,
        ephemeral=True,
    )


# =========================================================
# READY
# =========================================================

startup_task_started = False


@bot.event
async def on_ready():
    global startup_task_started

    print(f"Detective Yamaha v4 online as {bot.user}")

    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            bot.tree.clear_commands(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)

        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global slash commands.")

    except Exception as exc:
        print("Slash sync error:", repr(exc))

    if not presence_loop.is_running():
        presence_loop.start()

    if not staff_refresh_loop.is_running():
        staff_refresh_loop.start()

    # Only schedule this once even if Discord reconnects.
    if not startup_task_started:
        startup_task_started = True
        asyncio.create_task(startup_scan())


async def startup_scan():
    # Give Discord and the host a moment to settle.
    await asyncio.sleep(10)

    try:
        await run_scan()
    except Exception as exc:
        global last_error
        last_error = repr(exc)

        print(
            "Startup scan was rate-limited/failed. "
            "Bot is still running and will retry automatically:",
            repr(exc),
        )


if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

bot.run(TOKEN)
