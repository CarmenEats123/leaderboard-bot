DETECTIVE YAMAHA v6 — THREAD MODE

IMPORTANT:
Your screenshot/log still showed OLD CODE:
"Detective Yamaha online as ..."
"current-server channel not found"

This v6 must print:
"Detective Yamaha v6 THREAD MODE online as ..."

Behavior:
- Posts immediately after startup (after ~3 seconds), if Roblox allows the scan.
- Scans every 5 minutes.
- Each successful scan posts ONE message in ONE persistent Discord thread.
- The message lists visible DPI staff grouped by exact server, sorted by rank.
- Shows total tracked staff per server and tries to show total server population.
- Matrona+ join triggers Server Designer ping in the alert channel.
- Manual /serverscan really runs the same scan and posts a new thread message.
- Manual scan has 45-second cooldown to avoid accidental rate-limit spam.
- 429 errors are handled gracefully and do not kill the bot.
- Full staff membership refresh only happens hourly.

Discord IDs:
Guild: 1544430688470700133
Channel: 1544431033787613204

VERY IMPORTANT FOR BOT-HOSTING + GITHUB:
If "Pull automatically at every restart" is enabled, update bot.py IN GITHUB.
Otherwise the host will overwrite your new local bot.py with the old GitHub one.
