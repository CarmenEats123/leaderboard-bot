import os
import json
import asyncio
import random
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# DETECTIVE YAMAHA v5 — BOT-HOSTING FRIENDLY
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

GUILD_ID = int(
    os.getenv("GUILD_ID", "1544430688470700133")
    or 1544430688470700133
)

SERVER_LIST_CHANNEL_ID = int(
    os.getenv("CURRENT_SERVER_CHANNEL_ID", "1544431033787613204")
    or 1544431033787613204
)

ALERT_CHANNEL_ID = int(
    os.getenv("ALERT_CHANNEL_ID", "1544431033787613204")
    or 1544431033787613204
)

SERVER_DESIGNER_ROLE_ID = int(
    os.getenv("SERVER_DESIGNER_ROLE_ID", "0") or 0
)

ROBLOX_GROUP_ID = 5008654
DPI_PLACE_ID = 3522803956
DPI_UNIVERSE_ID = 1246853548

STAFF_MIN_RANK = 50

PRESENCE_SCAN_SECONDS = 300          # 5 min
STAFF_REFRESH_SECONDS = 3600         # 1 hour
PRESENCE_BATCH_SIZE = 50             # fewer requests than 25
PRESENCE_BATCH_PAUSE = 2.0           # spread requests out

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

presence_task = None
staff_task = None

last_scan = None
last_error = None
last_staff_refresh = None


# =========================================================
# HELPERS
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


async def resolve_text_channel(channel_id):
    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot access Discord channel {channel_id}: {exc!r}"
            )

    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError(
            f"Discord channel {channel_id} is not a text channel."
        )

    return channel


async def request_json(
    session,
    method,
    url,
    *,
    params=None,
    payload=None,
    retries=6,
):
    delay = 10.0

    for attempt in range(retries):
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
                retry_after = response.headers.get("Retry-After")

                try:
                    wait = float(retry_after)
                except Exception:
                    wait = delay

                wait = max(wait, delay) + random.uniform(0.5, 1.5)

                if attempt < retries - 1:
                    print(
                        f"Roblox 429 — waiting {wait:.1f}s "
                        f"before retry {attempt + 2}/{retries}"
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

    raise RuntimeError("Roblox API retries exhausted.")


# =========================================================
# STAFF CACHE — ONLY ONCE PER HOUR
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
        params = {
            "limit": 100,
            "sortOrder": "Asc",
        }

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
        print("Refreshing staff cache...")

        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Watch/5.0"
        }

        timeout = aiohttp.ClientTimeout(total=600)

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:

            roles = await get_group_roles(session)

            matrona_roles = [
                role
                for role in roles
                if "matrona" in str(role.get("name", "")).lower()
            ]

            if not matrona_roles:
                raise RuntimeError("Matrona role not found.")

            matrona_rank = min(
                int(role.get("rank", 0) or 0)
                for role in matrona_roles
            )

            staff_roles = sorted(
                [
                    role
                    for role in roles
                    if int(role.get("rank", 0) or 0) >= STAFF_MIN_RANK
                ],
                key=lambda role:
                    int(role.get("rank", 0) or 0),
            )

            staff = {}

            for index, role in enumerate(staff_roles, start=1):
                role_id = role.get("id")
                role_name = role.get("name", "Unknown")
                role_rank = int(role.get("rank", 0) or 0)

                print(
                    f"Staff role {index}/{len(staff_roles)}: "
                    f"{role_name}"
                )

                users = await get_users_in_role(
                    session,
                    role_id,
                )

                for uid, info in users.items():
                    staff[uid] = {
                        "username": info["username"],
                        "display_name": info["display_name"],
                        "role_name": role_name,
                        "role_rank": role_rank,
                        "is_matrona_plus":
                            role_rank >= matrona_rank,
                    }

                await asyncio.sleep(2.0)

        snapshot = {
            "staff": staff,
            "matrona_rank": matrona_rank,
            "updated_at": utc_iso(),
        }

        save_json(STAFF_CACHE_FILE, snapshot)

        last_staff_refresh = snapshot["updated_at"]

        print(
            f"Staff cache complete: {len(staff)} staff."
        )

        return snapshot


async def ensure_staff_cache():
    cached = load_json(STAFF_CACHE_FILE, {})

    if cached.get("staff"):
        return cached

    return await refresh_staff_cache()


async def staff_refresh_worker():
    # CRITICAL: do not hit Roblox again immediately on startup.
    await asyncio.sleep(STAFF_REFRESH_SECONDS)

    while not bot.is_closed():
        try:
            await refresh_staff_cache()
        except Exception as exc:
            print(
                "Staff refresh failed; retaining old cache:",
                repr(exc),
            )

        await asyncio.sleep(STAFF_REFRESH_SECONDS)


# =========================================================
# PRESENCE — EVERY 5 MINUTES, NO DUPLICATE STARTUP SCAN
# =========================================================

async def get_user_presences(session, user_ids):
    if not user_ids:
        return {}

    ids = [int(uid) for uid in user_ids]
    results = {}

    async def fetch_batch(batch):
        try:
            data = await request_json(
                session,
                "POST",
                f"{PRESENCE_API}/v1/presence/users",
                payload={"userIds": batch},
            )

        except RuntimeError as exc:
            # Future-proof against Roblox lowering the accepted batch size.
            if (
                "too many user ids" in str(exc).lower()
                and len(batch) > 1
            ):
                middle = len(batch) // 2
                await fetch_batch(batch[:middle])
                await asyncio.sleep(PRESENCE_BATCH_PAUSE)
                await fetch_batch(batch[middle:])
                return

            raise

        for item in data.get("userPresences", []):
            uid = item.get("userId")

            if uid is not None:
                results[str(uid)] = item

    batches = [
        ids[i:i + PRESENCE_BATCH_SIZE]
        for i in range(0, len(ids), PRESENCE_BATCH_SIZE)
    ]

    for index, batch in enumerate(batches, start=1):
        await fetch_batch(batch)

        if index < len(batches):
            await asyncio.sleep(PRESENCE_BATCH_PAUSE)

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
            key=lambda person: (
                -person["role_rank"],
                person["username"].lower(),
            )
        )

    return servers


async def get_server_counts(session, server_ids):
    wanted = {
        str(server_id)
        for server_id in server_ids
        if server_id != "server-hidden"
    }

    if not wanted:
        return {}

    found = {}
    cursor = None

    for _ in range(6):
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
# ONE SINGLE LIVE MESSAGE
# =========================================================

def build_live_content(servers, counts):
    total = sum(
        len(members)
        for members in servers.values()
    )

    lines = [
        "## 🛰️ Detective Yamaha — DPI Server List",
        f"Tracked staff visible in DPI: **{total}**",
        "Sorted **highest rank → lowest rank** • ⭐ = Matrona+",
        "",
    ]

    exact = [
        (sid, members)
        for sid, members in servers.items()
        if sid != "server-hidden"
    ]

    exact.sort(
        key=lambda item: (
            -max(person["role_rank"] for person in item[1]),
            -len(item[1]),
            item[0],
        )
    )

    for index, (sid, members) in enumerate(exact, start=1):
        server_info = counts.get(sid, {})

        playing = server_info.get("playing")
        maximum = server_info.get("max_players")

        if playing is None:
            population = "player count unavailable"
        elif maximum is None:
            population = f"{playing} players"
        else:
            population = f"{playing}/{maximum} players"

        lines.extend([
            (
                f"### Server {index} — **{population}** "
                f"• **{len(members)} tracked staff**"
            ),
            f"`{sid}`",
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
            "No rank-50+ staff are currently "
            "**publicly visible** inside DPI.",
            "",
        ])

    lines.extend([
        (
            f"_Updated <t:{int(utc_now().timestamp())}:R> • "
            "refreshes every 5 minutes_"
        ),
        "_Public Roblox presence only._",
    ])

    content = "\n".join(lines)

    if len(content) > 1990:
        content = (
            content[:1880].rsplit("\n", 1)[0]
            + "\n\n_⚠️ Additional entries omitted because Discord "
              "limits a single message to 2000 characters._"
        )

    return content


async def upsert_live_message(servers, counts):
    channel = await resolve_text_channel(
        SERVER_LIST_CHANNEL_ID
    )

    content = build_live_content(
        servers,
        counts,
    )

    state = load_json(MESSAGE_STATE_FILE, {})
    message_id = state.get(str(GUILD_ID))
    message = None

    if message_id:
        try:
            message = await channel.fetch_message(
                int(message_id)
            )
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

    state[str(GUILD_ID)] = str(message.id)
    save_json(MESSAGE_STATE_FILE, state)


# =========================================================
# MATRONa+ ALERT
# =========================================================

async def send_matrona_alert(joined):
    if not joined:
        return

    channel = await resolve_text_channel(
        ALERT_CHANNEL_ID
    )

    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        raise RuntimeError(
            f"Bot is not connected to guild {GUILD_ID}."
        )

    role = None

    if SERVER_DESIGNER_ROLE_ID:
        role = guild.get_role(
            SERVER_DESIGNER_ROLE_ID
        )

    if role is None:
        role = discord.utils.find(
            lambda item:
                item.name.lower()
                == SERVER_DESIGNER_ROLE_NAME.lower(),
            guild.roles,
        )

    mention = (
        role.mention
        if role
        else "**Server Designer**"
    )

    lines = [
        f"{mention} 🚨 **Matrona+ joined DPI**"
    ]

    for person in joined:
        server_label = (
            f"`{person['game_id']}`"
            if person.get("game_id")
            else "server hidden"
        )

        lines.append(
            f"• **{person['username']}** — "
            f"**{person['role_name']}** • "
            f"{server_label} • "
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
# SCAN
# =========================================================

async def run_scan():
    global last_scan, last_error

    async with scan_lock:
        snapshot = await ensure_staff_cache()

        headers = {
            "User-Agent":
            "Detective-Yamaha-DPI-Watch/5.0"
        }

        timeout = aiohttp.ClientTimeout(total=360)

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
                uid = person["user_id"]

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
                    joined_matrona_plus.append(
                        person
                    )

        # Save before Discord output so alerts cannot repeat.
        save_json(
            PRESENCE_STATE_FILE,
            {
                "in_dpi": current,
                "updated_at": utc_iso(),
            },
        )

        await upsert_live_message(
            servers,
            counts,
        )

        if joined_matrona_plus:
            await send_matrona_alert(
                joined_matrona_plus
            )

        last_scan = utc_iso()
        last_error = None

        result = {
            "staff_checked":
                len(snapshot.get("staff", {})),
            "visible":
                sum(len(x) for x in servers.values()),
            "servers":
                len(servers),
            "matrona_plus_joins":
                len(joined_matrona_plus),
        }

        print(
            "Detective Yamaha v5 scan OK:",
            result,
        )

        return result


async def presence_worker():
    # Exactly one startup scan, not two/three simultaneous scans.
    while not bot.is_closed():
        try:
            await run_scan()
        except Exception as exc:
            global last_error
            last_error = repr(exc)

            print(
                "Detective Yamaha v5 scan failed; "
                "bot remains online and will retry in 5 minutes:",
                repr(exc),
            )

        await asyncio.sleep(PRESENCE_SCAN_SECONDS)


# =========================================================
# COMMANDS
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
        result = await run_scan()

        await interaction.followup.send(
            (
                "✅ **Server scan completed.**\n"
                f"Staff checked: **{result['staff_checked']}**\n"
                f"Visible staff: **{result['visible']}**\n"
                f"Server groups: **{result['servers']}**\n"
                f"New Matrona+ joins: "
                f"**{result['matrona_plus_joins']}**"
            ),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            (
                "⚠️ **The bot is online, but Roblox rejected this scan.** "
                "The automatic watcher will retry in 5 minutes.\n"
                f"`{type(exc).__name__}: {str(exc)[:350]}`"
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
    cache = load_json(
        STAFF_CACHE_FILE,
        {},
    )

    text = (
        "🕵️ **Detective Yamaha v5 — DPI Server Watch**\n"
        "• Presence scan: every **5 minutes**\n"
        "• Staff cache: every **60 minutes**\n"
        f"• Cached staff: **{len(cache.get('staff', {}))}**\n"
        f"• Guild: `{GUILD_ID}`\n"
        f"• Server-list channel: <#{SERVER_LIST_CHANNEL_ID}>\n"
        "• Alert: **Matrona+ → Server Designer ping**\n"
        f"• Last success: **{last_scan or 'Not yet'}**"
    )

    if last_error:
        text += (
            f"\n• Last error: "
            f"`{last_error[:450]}`"
        )

    await interaction.response.send_message(
        text,
        ephemeral=True,
    )


# =========================================================
# READY
# =========================================================

started_once = False


@bot.event
async def on_ready():
    global started_once, presence_task, staff_task

    print(
        f"Detective Yamaha v5 BOT-HOSTING FIX online as {bot.user}"
    )
    print(
        f"Guild ID {GUILD_ID} | "
        f"Server-list channel {SERVER_LIST_CHANNEL_ID}"
    )

    try:
        synced = await bot.tree.sync()
        print(
            f"Synced {len(synced)} global slash commands."
        )
    except Exception as exc:
        print(
            "Slash command sync error:",
            repr(exc),
        )

    if started_once:
        return

    started_once = True

    # ONE presence worker. No duplicate startup scanner.
    presence_task = asyncio.create_task(
        presence_worker()
    )

    # Staff refresh waits one full hour before touching Roblox again.
    staff_task = asyncio.create_task(
        staff_refresh_worker()
    )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing."
    )

bot.run(TOKEN)
