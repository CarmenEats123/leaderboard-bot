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
# DETECTIVE YAMAHA — DPI SERVER WATCH (LOW-API EDITION)
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

ALERT_CHANNEL_ID = int(
    os.getenv("ALERT_CHANNEL_ID", "1544431033787613204")
    or 1544431033787613204
)
CURRENT_SERVER_CHANNEL_ID = int(
    os.getenv("CURRENT_SERVER_CHANNEL_ID", "1544431033787613204")
    or 1544431033787613204
)
SERVER_DESIGNER_ROLE_ID = int(os.getenv("SERVER_DESIGNER_ROLE_ID", "0") or 0)

ROBLOX_GROUP_ID = 5008654
DPI_PLACE_ID = 3522803956
DPI_UNIVERSE_ID = 1246853548

STAFF_MIN_RANK = 50
PRESENCE_SCAN_MINUTES = 5
STAFF_REFRESH_MINUTES = 60

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


def find_text_channel(guild, channel_id):
    channel = guild.get_channel(channel_id)
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
    retries=7,
):
    """
    Shared-hosting Roblox IPs can hit 429s.
    Respect Retry-After when supplied and use progressively longer waits.
    """
    delay = 5.0

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

                if response.status == 429:
                    retry_header = response.headers.get("Retry-After")
                    try:
                        wait = float(retry_header)
                    except Exception:
                        wait = delay

                    wait = max(wait, delay) + random.uniform(0.25, 1.0)

                    if attempt < retries - 1:
                        print(
                            f"Roblox 429; waiting {wait:.1f}s before retry "
                            f"({attempt + 1}/{retries})..."
                        )
                        await asyncio.sleep(wait)
                        delay = min(delay * 2, 60)
                        continue

                if 500 <= response.status <= 599 and attempt < retries - 1:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)
                    continue

                raise RuntimeError(
                    f"Roblox API {response.status}: {body[:500]}"
                )

        except aiohttp.ClientError as exc:
            if attempt >= retries - 1:
                raise RuntimeError(f"Network error: {exc!r}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    raise RuntimeError("Roblox request failed after retries.")


# =========================================================
# STAFF CACHE
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
            user_id = item.get("userId") or item.get("id")
            if user_id is None:
                continue

            users[str(user_id)] = {
                "username":
                    item.get("username")
                    or item.get("name")
                    or f"User {user_id}",
                "display_name":
                    item.get("displayName") or "",
            }

        cursor = data.get("nextPageCursor")
        if not cursor:
            break

        # Avoid hammering Roblox while paging.
        await asyncio.sleep(0.8)

    return users


async def refresh_staff_cache():
    global last_staff_refresh

    async with staff_lock:
        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Server-Watch/3.0"
        }
        timeout = aiohttp.ClientTimeout(total=300)

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
                raise RuntimeError(
                    "Could not identify a Matrona role in the Roblox group."
                )

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
                role_name = role.get("name", "Unknown Role")
                role_rank = int(role.get("rank", 0) or 0)

                print(
                    f"Staff cache: role {index}/{len(staff_roles)} "
                    f"— {role_name}"
                )

                users = await get_users_in_role(session, role_id)

                for user_id, info in users.items():
                    staff[user_id] = {
                        "username": info["username"],
                        "display_name": info["display_name"],
                        "role_name": role_name,
                        "role_rank": role_rank,
                        "is_matrona_plus": role_rank >= matrona_rank,
                    }

                # This is deliberately slow: role membership barely changes
                # compared with presence, and shared-host IPs rate-limit hard.
                await asyncio.sleep(1.25)

        snapshot = {
            "staff": staff,
            "matrona_rank": matrona_rank,
            "updated_at": utc_iso(),
        }

        save_json(STAFF_CACHE_FILE, snapshot)
        last_staff_refresh = snapshot["updated_at"]

        print(
            f"Staff cache refreshed: {len(staff)} staff, "
            f"Matrona threshold {matrona_rank}."
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
        print("Staff-cache refresh failed:", repr(exc))


@staff_refresh_loop.before_loop
async def before_staff_refresh_loop():
    await bot.wait_until_ready()


# =========================================================
# PRESENCE — LIGHTWEIGHT 5-MINUTE CHECK
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
            message = str(exc).lower()

            if "too many user ids" in message and len(batch) > 1:
                mid = len(batch) // 2
                await fetch_batch(batch[:mid])
                await asyncio.sleep(0.5)
                await fetch_batch(batch[mid:])
                return

            raise

        for item in data.get("userPresences", []):
            user_id = item.get("userId")
            if user_id is not None:
                results[str(user_id)] = item

    # 35 is intentionally conservative for shared hosting.
    for offset in range(0, len(ids), 35):
        await fetch_batch(ids[offset:offset + 35])

        if offset + 35 < len(ids):
            await asyncio.sleep(0.75)

    return results


def is_in_dpi(presence):
    if not presence:
        return False

    return (
        str(presence.get("universeId")) == str(DPI_UNIVERSE_ID)
        or str(presence.get("rootPlaceId")) == str(DPI_PLACE_ID)
        or str(presence.get("placeId")) == str(DPI_PLACE_ID)
    )


async def get_public_server_counts(session, wanted_game_ids):
    """
    One low-frequency lookup for current public DPI servers.
    Maps Roblox gameId -> current total server population.
    Stops once all wanted server IDs are found or pagination ends.
    """
    wanted = {
        str(game_id)
        for game_id in wanted_game_ids
        if game_id and game_id != "server-hidden"
    }

    if not wanted:
        return {}

    found = {}
    cursor = None

    for _page in range(10):
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

        await asyncio.sleep(0.6)

    return found


def build_server_groups(snapshot, presences):
    servers = {}

    for user_id, info in snapshot.get("staff", {}).items():
        presence = presences.get(str(user_id), {})

        if not is_in_dpi(presence):
            continue

        game_id = presence.get("gameId")
        server_key = str(game_id) if game_id else "server-hidden"

        servers.setdefault(server_key, []).append({
            "user_id": str(user_id),
            "username": info["username"],
            "role_name": info["role_name"],
            "role_rank": int(info["role_rank"]),
            "is_matrona_plus":
                bool(info.get("is_matrona_plus")),
            "game_id": game_id,
        })

    for members in servers.values():
        members.sort(
            key=lambda x: (
                -x["role_rank"],
                x["username"].lower(),
            )
        )

    return servers


# =========================================================
# ONE SINGLE LIVE MESSAGE
# =========================================================

def build_single_message_content(
    servers,
    server_counts,
):
    total_staff = sum(
        len(members)
        for members in servers.values()
    )

    lines = [
        "## 🛰️ Detective Yamaha — Live DPI Server List",
        (
            f"Tracked staff publicly visible in DPI: "
            f"**{total_staff}**"
        ),
        (
            "Sorted **highest rank → lowest rank**. "
            "⭐ = **Matrona+**"
        ),
        "",
    ]

    normal_servers = [
        (sid, members)
        for sid, members in servers.items()
        if sid != "server-hidden"
    ]

    normal_servers.sort(
        key=lambda item: (
            -max(m["role_rank"] for m in item[1]),
            -len(item[1]),
            item[0],
        )
    )

    for index, (server_id, members) in enumerate(
        normal_servers,
        start=1,
    ):
        count = server_counts.get(
            str(server_id),
            {},
        )

        playing = count.get("playing")
        max_players = count.get("max_players")

        if playing is None:
            population = "population unavailable"
        elif max_players is None:
            population = f"{playing} players"
        else:
            population = f"{playing}/{max_players} players"

        lines.extend([
            (
                f"### Server {index} — "
                f"**{population}** • "
                f"**{len(members)} tracked staff**"
            ),
            f"`{server_id}`",
        ])

        for person in members:
            star = " ⭐" if person["is_matrona_plus"] else ""

            lines.append(
                f"• **{person['username']}**{star} "
                f"— {person['role_name']}"
            )

        lines.append("")

    if "server-hidden" in servers:
        lines.append(
            "### In DPI — exact server hidden"
        )

        for person in servers["server-hidden"]:
            star = " ⭐" if person["is_matrona_plus"] else ""

            lines.append(
                f"• **{person['username']}**{star} "
                f"— {person['role_name']}"
            )

        lines.append("")

    if not servers:
        lines.extend([
            "No tracked rank-50+ staff are currently "
            "**publicly visible** inside DPI.",
            "",
        ])

    lines.extend([
        (
            f"_Updated <t:{int(utc_now().timestamp())}:R> • "
            f"automatic refresh every {PRESENCE_SCAN_MINUTES} minutes_"
        ),
        (
            "_Public Roblox presence only; hidden/offline presence "
            "cannot be bypassed._"
        ),
    ])

    content = "\n".join(lines)

    # Discord normal-message limit is 2000 chars.
    # If ever too large, keep all server headers and as many staff as fit.
    if len(content) > 1950:
        content = content[:1900].rsplit("\n", 1)[0]
        content += (
            "\n\n_⚠️ Roster exceeded Discord's single-message limit; "
            "remaining entries were omitted._"
        )

    return content


async def upsert_live_message(
    guild,
    servers,
    server_counts,
):
    channel = find_text_channel(
        guild,
        CURRENT_SERVER_CHANNEL_ID,
    )

    if not channel:
        print(
            f"Channel {CURRENT_SERVER_CHANNEL_ID} not found."
        )
        return

    content = build_single_message_content(
        servers,
        server_counts,
    )

    state = load_json(MESSAGE_STATE_FILE, {})
    message_id = state.get(str(guild.id))
    message = None

    if message_id:
        try:
            message = await channel.fetch_message(
                int(message_id)
            )
        except Exception:
            message = None

    if message is None:
        # Recover the previous Yamaha live message after a host restart.
        try:
            async for old in channel.history(limit=50):
                if (
                    old.author.id == bot.user.id
                    and old.content.startswith(
                        "## 🛰️ Detective Yamaha — Live DPI Server List"
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

    state[str(guild.id)] = str(message.id)
    save_json(MESSAGE_STATE_FILE, state)


# =========================================================
# MATRONa+ JOIN ALERT
# =========================================================

async def send_join_alert(
    guild,
    joined,
):
    if not joined:
        return

    channel = find_text_channel(
        guild,
        ALERT_CHANNEL_ID,
    )

    if not channel:
        return

    role = find_server_designer_role(guild)

    if role:
        mention = role.mention
    else:
        mention = "**Server Designer**"

    lines = [
        f"{mention} 🚨 **Matrona+ joined DPI**"
    ]

    for person in joined:
        server_text = (
            f"`{person['game_id']}`"
            if person.get("game_id")
            else "server hidden"
        )

        lines.append(
            f"• **{person['username']}** — "
            f"**{person['role_name']}** • {server_text} • "
            f"[profile]({profile_url(person['user_id'])})"
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
# MAIN SCAN
# =========================================================

async def run_presence_scan():
    global last_scan, last_error

    async with scan_lock:
        snapshot = await ensure_staff_cache()

        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Server-Watch/3.0"
        }

        timeout = aiohttp.ClientTimeout(total=180)

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:

            presences = await get_user_presences(
                session,
                list(snapshot.get("staff", {}).keys()),
            )

            servers = build_server_groups(
                snapshot,
                presences,
            )

            exact_server_ids = [
                sid
                for sid in servers.keys()
                if sid != "server-hidden"
            ]

            # Only one public-server lookup sequence per 5-minute scan.
            server_counts = await get_public_server_counts(
                session,
                exact_server_ids,
            )

        previous = load_json(
            PRESENCE_STATE_FILE,
            {},
        )

        previous_ids = set(
            previous.get("in_dpi", {}).keys()
        )

        current = {}
        joined_matrona_plus = []

        for server_id, members in servers.items():
            for person in members:
                uid = str(person["user_id"])

                current[uid] = {
                    "server_id": server_id,
                    "username": person["username"],
                    "role_name": person["role_name"],
                    "is_matrona_plus":
                        person["is_matrona_plus"],
                }

                if (
                    previous
                    and uid not in previous_ids
                    and person["is_matrona_plus"]
                ):
                    joined_matrona_plus.append(person)

        # Save state before Discord output to prevent duplicate alerts.
        save_json(
            PRESENCE_STATE_FILE,
            {
                "in_dpi": current,
                "updated_at": utc_iso(),
            },
        )

        for guild in bot.guilds:
            await upsert_live_message(
                guild,
                servers,
                server_counts,
            )

            if joined_matrona_plus:
                await send_join_alert(
                    guild,
                    joined_matrona_plus,
                )

        last_scan = utc_iso()
        last_error = None

        result = {
            "staff_checked":
                len(snapshot.get("staff", {})),
            "visible":
                sum(len(v) for v in servers.values()),
            "servers":
                len(servers),
            "matrona_plus_joins":
                len(joined_matrona_plus),
        }

        print(
            "Yamaha scan OK:",
            result,
        )

        return result


@tasks.loop(
    minutes=PRESENCE_SCAN_MINUTES
)
async def presence_loop():
    try:
        await run_presence_scan()
    except Exception as exc:
        global last_error
        last_error = repr(exc)

        # IMPORTANT: one failed scan does NOT kill the loop.
        print(
            "Yamaha presence scan failed; "
            "will retry next cycle:",
            repr(exc),
        )


@presence_loop.before_loop
async def before_presence_loop():
    await bot.wait_until_ready()


# =========================================================
# COMMANDS — ALWAYS REPLY
# =========================================================

@bot.tree.command(
    name="serverscan",
    description="Refresh the DPI server list now.",
)
async def serverscan(
    interaction: discord.Interaction,
):
    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    try:
        result = await run_presence_scan()

        await interaction.followup.send(
            (
                "✅ **Detective Yamaha scan completed.**\n"
                f"Staff checked: **{result['staff_checked']}**\n"
                f"Visible staff: **{result['visible']}**\n"
                f"Detected server groups: **{result['servers']}**\n"
                f"New Matrona+ joins: **{result['matrona_plus_joins']}**"
            ),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            (
                "⚠️ **Roblox temporarily rejected the scan.** "
                "The automatic watcher is still alive and will retry.\n"
                f"`{type(exc).__name__}: {str(exc)[:250]}`"
            ),
            ephemeral=True,
        )


@bot.tree.command(
    name="yamaha",
    description="Show Detective Yamaha status.",
)
async def yamaha(
    interaction: discord.Interaction,
):
    cache = load_json(STAFF_CACHE_FILE, {})

    message = (
        "🕵️ **Detective Yamaha — DPI Server Watch**\n"
        f"• Presence scan: every **{PRESENCE_SCAN_MINUTES} min**\n"
        f"• Staff cache refresh: every **{STAFF_REFRESH_MINUTES} min**\n"
        f"• Cached staff: **{len(cache.get('staff', {}))}**\n"
        f"• Matrona+ alert: **Server Designer**\n"
        f"• Last successful scan: **{last_scan or 'Not yet'}**"
    )

    if last_error:
        message += (
            "\n• Last error: "
            f"`{last_error[:500]}`"
        )

    await interaction.response.send_message(
        message,
        ephemeral=True,
    )


# =========================================================
# READY
# =========================================================

@bot.event
async def on_ready():
    print(
        f"Detective Yamaha online as {bot.user}"
    )

    try:
        if GUILD_ID:
            guild_obj = discord.Object(
                id=GUILD_ID
            )
            bot.tree.clear_commands(
                guild=guild_obj
            )
            await bot.tree.sync(
                guild=guild_obj
            )

        synced = await bot.tree.sync()

        print(
            f"Synced {len(synced)} global slash commands."
        )

    except Exception as exc:
        print(
            "Slash sync error:",
            repr(exc),
        )

    # Start background loops immediately. They survive failed scans.
    if not presence_loop.is_running():
        presence_loop.start()

    if not staff_refresh_loop.is_running():
        staff_refresh_loop.start()

    # Populate the one live message once on startup, but do not crash
    # if Roblox is rate-limiting the shared host.
    asyncio.create_task(run_startup_scan())


async def run_startup_scan():
    await asyncio.sleep(3)

    try:
        await run_presence_scan()
    except Exception as exc:
        global last_error
        last_error = repr(exc)

        print(
            "Startup scan delayed by Roblox; "
            "automatic loop remains active:",
            repr(exc),
        )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing."
    )

bot.run(TOKEN)
