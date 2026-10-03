DETECTIVE YAMAHA — LOW-API FINAL BUILD

Key fixes:
- Roblox staff membership is cached and refreshed only once per hour.
- Presence is checked every 5 minutes.
- Presence batches are only 35 IDs per request.
- Strong 429 Retry-After / exponential backoff handling.
- A failed scan does NOT crash/stop the bot.
- One single live Discord message is edited every scan.
- Each exact DPI server lists tracked staff highest-rank first.
- Attempts to show the total public server player count.
- Server Designer role is pinged only when a Matrona+ transitions into DPI.
- First baseline does not ping people who were already in-game.
- Channel ID: 1544431033787613204

Commands:
/serverscan
/yamaha

Required env:
DISCORD_TOKEN
GUILD_ID

Start:
python bot.py
