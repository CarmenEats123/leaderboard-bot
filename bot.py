import os
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

# =========================================================
# DETECTIVE YAMAHA — DPI SERVER WATCH
# Checks every 5 minutes.
# - Tracks all current Divine Sister staff (rank 50+)
# - Detects who is publicly visible in De Pride Isle
# - Groups them by exact Roblox server gameId
# - Pings the Discord "Server Designer" role when a Matrona+ joins DPI
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)

# Optional IDs. If omitted, the bot finds them by NAME.
ALERT_CHANNEL_ID = int(os.getenv("ALERT_CHANNEL_ID", "0") or 0)
CURRENT_SERVER_CHANNEL_ID = int(os.getenv("CURRENT_SERVER_CHANNEL_ID", "0") or 0)
SERVER_DESIGNER_ROLE_ID = int(os.getenv("SERVER_DESIGNER_ROLE_ID", "0") or 0)

ROBLOX_GROUP_ID = 5008654
DPI_PLACE_ID = 3522803956
DPI_UNIVERSE_ID = 1246853548

STAFF_MIN_RANK = 50
SCAN_MINUTES = 5

ALERT_CHANNEL_NAME = "leadership-presence"
CURRENT_SERVER_CHANNEL_NAME = "current-server"
SERVER_DESIGNER_ROLE_NAME = "Server Designer"

STATE_FILE = Path("yamaha_server_watch_state.json")
ROSTER_MESSAGE_FILE = Path("yamaha_server_roster_messages.json")

GROUPS_API = "https://groups.roblox.com"
PRESENCE_API = "https://presence.roblox.com"

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

scan_lock = asyncio.Lock()
last_scan = None
last_error = None


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
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temp.replace(path)


def find_text_channel(guild, channel_id, fallback_name):
    if channel_id:
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel

    wanted = fallback_name.lower()
    for channel in guild.text_channels:
        if channel.name.lower() == wanted:
            return channel

    return None


def find_role(guild):
    if SERVER_DESIGNER_ROLE_ID:
        role = guild.get_role(SERVER_DESIGNER_ROLE_ID)
        if role:
            return role

    wanted = SERVER_DESIGNER_ROLE_NAME.lower()

    for role in guild.roles:
        if role.name.lower() == wanted:
            return role

    return None


def profile_url(user_id):
    return f"https://www.roblox.com/users/{user_id}/profile"


async def request_json(
    session,
    method,
    url,
    *,
    params=None,
    payload=None,
    retries=4,
):
    delay = 1.0

    for attempt in range(retries):
        async with session.request(
            method,
            url,
            params=params,
            json=payload,
        ) as response:
            text = await response.text()

            if response.status == 200:
                return json.loads(text)

            if response.status == 429 and attempt < retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
                continue

            if 500 <= response.status <= 599 and attempt < retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
                continue

            raise RuntimeError(
                f"Roblox API {response.status}: {text[:500]}"
            )

    raise RuntimeError("Roblox request failed after retries.")


# =========================================================
# ROBLOX GROUP / STAFF
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
            user_id = item.get("userId") or item.get("id")

            if user_id is None:
                continue

            users[str(user_id)] = {
                "username":
                    item.get("username")
                    or item.get("name")
                    or f"User {user_id}",

                "display_name":
                    item.get("displayName")
                    or "",
            }

        cursor = data.get("nextPageCursor")

        if not cursor:
            break

        await asyncio.sleep(0.10)

    return users


async def build_staff_snapshot():
    headers = {
        "User-Agent":
        "Detective-Yamaha-DPI-Server-Watch/2.0"
    }

    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
    ) as session:

        roles = await get_group_roles(session)

        # Find Matrona rank dynamically, so Matrona+ means exactly that role
        # and every role above it in the Roblox group hierarchy.
        matrona_candidates = [
            role for role in roles
            if "matrona" in str(role.get("name", "")).lower()
        ]

        if not matrona_candidates:
            raise RuntimeError(
                "Could not find a Matrona role in Roblox group 5008654."
            )

        matrona_rank = min(
            int(role.get("rank", 0) or 0)
            for role in matrona_candidates
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

        for role in staff_roles:
            role_id = role.get("id")
            role_name = role.get("name", "Unknown Role")
            role_rank = int(role.get("rank", 0) or 0)

            users = await get_users_in_role(
                session,
                role_id,
            )

            for user_id, info in users.items():
                staff[str(user_id)] = {
                    "username":
                        info["username"],

                    "display_name":
                        info["display_name"],

                    "role_id":
                        role_id,

                    "role_name":
                        role_name,

                    "role_rank":
                        role_rank,

                    "is_matrona_plus":
                        role_rank >= matrona_rank,
                }

            await asyncio.sleep(0.10)

    return {
        "staff": staff,
        "matrona_rank": matrona_rank,
        "updated_at": utc_iso(),
    }


# =========================================================
# ROBLOX PRESENCE
# =========================================================

async def get_user_presences(session, user_ids):
    """
    Conservative batching. Roblox rejects large batches, so use 50 max.
    If Roblox lowers the limit, rejected batches automatically split.
    """
    if not user_ids:
        return {}

    ids = [int(user_id) for user_id in user_ids]
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

            if (
                "too many user ids" in message
                and len(batch) > 1
            ):
                middle = len(batch) // 2

                await fetch_batch(batch[:middle])
                await asyncio.sleep(0.15)
                await fetch_batch(batch[middle:])
                return

            raise

        for item in data.get("userPresences", []):
            user_id = item.get("userId")

            if user_id is not None:
                results[str(user_id)] = item

    for offset in range(0, len(ids), 50):
        await fetch_batch(ids[offset:offset + 50])

        if offset + 50 < len(ids):
            await asyncio.sleep(0.25)

    return results


def is_in_dpi(presence):
    if not presence:
        return False

    return (
        str(presence.get("universeId")) == str(DPI_UNIVERSE_ID)
        or str(presence.get("rootPlaceId")) == str(DPI_PLACE_ID)
        or str(presence.get("placeId")) == str(DPI_PLACE_ID)
    )


async def fetch_presence_for_staff(snapshot):
    staff = snapshot.get("staff", {})

    headers = {
        "User-Agent":
        "Detective-Yamaha-DPI-Server-Watch/2.0"
    }

    timeout = aiohttp.ClientTimeout(total=60)

    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
    ) as session:

        return await get_user_presences(
            session,
            list(staff.keys()),
        )


# =========================================================
# SERVER ROSTER
# =========================================================

def current_dpi_players(snapshot, presences):
    result = {}

    for user_id, staff_info in snapshot.get("staff", {}).items():
        presence = presences.get(str(user_id), {})

        if not is_in_dpi(presence):
            continue

        game_id = presence.get("gameId")
        server_key = str(game_id) if game_id else "server-hidden"

        result.setdefault(server_key, []).append({
            "user_id": str(user_id),
            "username": staff_info["username"],
            "role_name": staff_info["role_name"],
            "role_rank": staff_info["role_rank"],
            "is_matrona_plus":
                staff_info.get("is_matrona_plus", False),
            "game_id": game_id,
        })

    for server_key in result:
        result[server_key].sort(
            key=lambda x: (
                -int(x["role_rank"]),
                x["username"].lower(),
            )
        )

    return result


def build_roster_embeds(servers):
    total = sum(
        len(players)
        for players in servers.values()
    )

    blocks = []

    normal_servers = [
        item
        for item in servers.items()
        if item[0] != "server-hidden"
    ]

    normal_servers.sort(
        key=lambda item: (
            -max(
                player["role_rank"]
                for player in item[1]
            ),
            -len(item[1]),
            item[0],
        )
    )

    for index, (server_id, players) in enumerate(
        normal_servers,
        start=1,
    ):
        lines = []

        for player in players:
            marker = " ⭐" if player["is_matrona_plus"] else ""

            lines.append(
                f"• **{player['username']}**{marker}"
                f" — {player['role_name']}"
            )

        blocks.append(
            f"### 🛰️ Server {index}"
            f" — {len(players)} tracked staff\n"
            f"`gameId: {server_id}`\n"
            + "\n".join(lines)
        )

    if "server-hidden" in servers:
        lines = []

        for player in servers["server-hidden"]:
            marker = " ⭐" if player["is_matrona_plus"] else ""

            lines.append(
                f"• **{player['username']}**{marker}"
                f" — {player['role_name']}"
            )

        blocks.append(
            "### 👁️ In DPI — exact server hidden\n"
            + "\n".join(lines)
        )

    if not blocks:
        blocks = [
            "No tracked rank-50+ staff are currently "
            "**publicly visible** inside De Pride Isle."
        ]

    descriptions = []
    current = ""

    for block in blocks:
        piece = block + "\n\n"

        if current and len(current) + len(piece) > 3800:
            descriptions.append(current.rstrip())
            current = ""

        current += piece

    if current:
        descriptions.append(current.rstrip())

    embeds = []

    for index, description in enumerate(
        descriptions,
        start=1,
    ):
        suffix = (
            f" • {index}/{len(descriptions)}"
            if len(descriptions) > 1
            else ""
        )

        embed = discord.Embed(
            title=f"🛰️ CURRENT DPI SERVERS{suffix}",
            description=(
                f"Tracked staff visible in DPI: **{total}**\n"
                "Sorted highest rank → lowest rank.\n"
                "⭐ = Matrona+\n\n"
                + description
            ),
            colour=discord.Colour.blurple(),
            timestamp=utc_now(),
        )

        embed.set_footer(
            text=(
                "Detective Yamaha • refreshes every 5 min • "
                "public Roblox presence only"
            )
        )

        embeds.append(embed)

    return embeds


async def publish_server_roster(guild, servers):
    channel = find_text_channel(
        guild,
        CURRENT_SERVER_CHANNEL_ID,
        CURRENT_SERVER_CHANNEL_NAME,
    )

    if not channel:
        print(
            "Detective Yamaha: current-server channel not found."
        )
        return

    embeds = build_roster_embeds(servers)

    saved = load_json(
        ROSTER_MESSAGE_FILE,
        {},
    )

    ids = saved.get(str(guild.id), [])

    if isinstance(ids, (str, int)):
        ids = [str(ids)]

    existing = []

    for raw_id in ids:
        try:
            existing.append(
                await channel.fetch_message(int(raw_id))
            )
        except Exception:
            pass

    final_messages = []

    for index, embed in enumerate(embeds):
        if index < len(existing):
            await existing[index].edit(
                embed=embed
            )
            final_messages.append(
                existing[index]
            )
        else:
            final_messages.append(
                await channel.send(embed=embed)
            )

    for message in existing[len(embeds):]:
        try:
            await message.delete()
        except Exception:
            pass

    saved[str(guild.id)] = [
        str(message.id)
        for message in final_messages
    ]

    save_json(
        ROSTER_MESSAGE_FILE,
        saved,
    )


# =========================================================
# MATRONa+ JOIN ALERTS
# =========================================================

async def send_matrona_plus_alert(
    guild,
    joined_people,
):
    if not joined_people:
        return

    channel = find_text_channel(
        guild,
        ALERT_CHANNEL_ID,
        ALERT_CHANNEL_NAME,
    )

    if not channel:
        print(
            "Detective Yamaha: leadership-presence channel not found."
        )
        return

    role = find_role(guild)

    mention = (
        role.mention
        if role
        else f"@{SERVER_DESIGNER_ROLE_NAME}"
    )

    lines = []

    for person in joined_people:
        server_text = (
            f"`{person['game_id']}`"
            if person.get("game_id")
            else "server instance hidden"
        )

        lines.append(
            f"**{person['username']}**"
            f" — **{person['role_name']}**\n"
            f"↳ Joined **De Pride Isle Sanatorium**\n"
            f"↳ Server: {server_text}\n"
            f"↳ [Roblox profile]"
            f"({profile_url(person['user_id'])})"
        )

    embed = discord.Embed(
        title=(
            "🚨 MATRONa+ JOIN DETECTED"
            if len(joined_people) == 1
            else f"🚨 MATRONa+ JOINS DETECTED — {len(joined_people)}"
        ),
        description="\n\n".join(lines),
        colour=discord.Colour.red(),
        timestamp=utc_now(),
    )

    embed.set_footer(
        text=(
            "Detective Yamaha • 5-minute DPI server watch • "
            "public Roblox presence only"
        )
    )

    allowed_mentions = discord.AllowedMentions(
        roles=True,
        users=False,
        everyone=False,
    )

    await channel.send(
        content=mention if role else None,
        embed=embed,
        allowed_mentions=allowed_mentions,
    )


# =========================================================
# MAIN SCAN
# =========================================================

async def run_server_scan():
    global last_scan, last_error

    async with scan_lock:
        snapshot = await build_staff_snapshot()
        presences = await fetch_presence_for_staff(
            snapshot
        )
        servers = current_dpi_players(
            snapshot,
            presences,
        )

        previous = load_json(
            STATE_FILE,
            {},
        )

        previous_in_dpi = previous.get(
            "in_dpi",
            {},
        )

        current_in_dpi = {}
        joined_matrona_plus = []

        for server_id, players in servers.items():
            for player in players:
                uid = player["user_id"]

                current_in_dpi[uid] = {
                    "server_id": server_id,
                    "username": player["username"],
                    "role_name": player["role_name"],
                    "role_rank": player["role_rank"],
                    "is_matrona_plus":
                        player["is_matrona_plus"],
                }

                was_in_dpi = uid in previous_in_dpi

                if (
                    previous
                    and not was_in_dpi
                    and player["is_matrona_plus"]
                ):
                    joined_matrona_plus.append(
                        player
                    )

        # Save first, so a Discord-send error cannot make a join alert repeat.
        save_json(
            STATE_FILE,
            {
                "in_dpi": current_in_dpi,
                "updated_at": utc_iso(),
                "staff_count":
                    len(snapshot.get("staff", {})),
                "matrona_rank":
                    snapshot.get("matrona_rank"),
            },
        )

        for guild in bot.guilds:
            await publish_server_roster(
                guild,
                servers,
            )

            if joined_matrona_plus:
                await send_matrona_plus_alert(
                    guild,
                    joined_matrona_plus,
                )

        last_scan = utc_iso()
        last_error = None

        print(
            f"Detective Yamaha scan OK: "
            f"{len(snapshot.get('staff', {}))} staff checked, "
            f"{sum(len(v) for v in servers.values())} visible in DPI, "
            f"{len(joined_matrona_plus)} new Matrona+ join(s)."
        )

        return {
            "staff_checked":
                len(snapshot.get("staff", {})),
            "visible_in_dpi":
                sum(len(v) for v in servers.values()),
            "matrona_plus_joins":
                len(joined_matrona_plus),
        }


@tasks.loop(minutes=SCAN_MINUTES)
async def server_watch():
    try:
        await run_server_scan()
    except Exception as exc:
        global last_error
        last_error = repr(exc)
        print(
            "Detective Yamaha server-watch error:",
            repr(exc),
        )


@server_watch.before_loop
async def before_server_watch():
    await bot.wait_until_ready()


# =========================================================
# COMMANDS
# =========================================================

@bot.tree.command(
    name="serverscan",
    description="Force Detective Yamaha to refresh the DPI server watch now.",
)
async def serverscan(
    interaction: discord.Interaction,
):
    await interaction.response.defer(
        ephemeral=True,
        thinking=True,
    )

    try:
        result = await run_server_scan()

        await interaction.followup.send(
            (
                f"✅ DPI server scan finished.\n"
                f"Staff checked: **{result['staff_checked']}**\n"
                f"Visible in DPI: **{result['visible_in_dpi']}**\n"
                f"New Matrona+ joins: **{result['matrona_plus_joins']}**"
            ),
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            f"❌ Server scan failed: `{type(exc).__name__}`",
            ephemeral=True,
        )


@bot.tree.command(
    name="yamaha",
    description="Show Detective Yamaha server-watch status.",
)
async def yamaha(
    interaction: discord.Interaction,
):
    embed = discord.Embed(
        title="🕵️ Detective Yamaha — DPI Server Watch",
        colour=discord.Colour.gold(),
        timestamp=utc_now(),
    )

    embed.add_field(
        name="Scan interval",
        value=f"Every {SCAN_MINUTES} minutes",
        inline=True,
    )

    embed.add_field(
        name="Alert threshold",
        value="Matrona+",
        inline=True,
    )

    embed.add_field(
        name="Ping role",
        value=SERVER_DESIGNER_ROLE_NAME,
        inline=True,
    )

    embed.add_field(
        name="Last scan",
        value=last_scan or "Not yet",
        inline=False,
    )

    if last_error:
        embed.add_field(
            name="Last error",
            value=last_error[:1000],
            inline=False,
        )

    await interaction.response.send_message(
        embed=embed,
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

            # Remove old guild copies to avoid duplicate slash commands.
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
            "Slash-command sync error:",
            repr(exc),
        )

    if not server_watch.is_running():
        # Immediate refresh when the bot starts.
        # First startup creates a baseline and DOES NOT ping old occupants.
        try:
            await run_server_scan()
        except Exception as exc:
            print(
                "Initial Detective Yamaha scan failed:",
                repr(exc),
            )

        server_watch.start()


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN is missing."
    )

bot.run(TOKEN)
