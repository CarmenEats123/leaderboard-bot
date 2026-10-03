DETECTIVE YAMAHA v4 — IMPORTANT

This is the corrected build.

If your console says:
"Initial Detective Yamaha scan failed"
or
"Detective Yamaha: current-server channel not found."

YOU ARE STILL RUNNING THE OLD bot.py.

This v4 console says:
"Detective Yamaha v4 online as ..."

Expected channel:
#server-list

Matrona+ alert channel ID:
1544431033787613204

Features:
- one single live #server-list message
- updates every 5 minutes
- ranks sorted highest -> lowest
- groups staff by exact DPI server
- attempts to show total server population
- Server Designer ping when Matrona+ newly joins
- staff membership cache refreshed hourly
- 25-ID presence batches
- long 429 backoff
- failed Roblox scan never kills the bot
- /serverscan always returns a useful response
- /yamaha status command

Start file:
bot.py
