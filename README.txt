DETECTIVE YAMAHA v5 — BOT-HOSTING FIX

IMPORTANT:
If Bot-Hosting is connected to GitHub with "Pull automatically at every restart"
enabled, changing bot.py only in the Files tab will be overwritten at restart.
UPDATE THE GITHUB REPOSITORY'S bot.py with this v5 file, OR disable automatic
GitHub pulling / use Archive source.

Correct startup line:
Detective Yamaha v5 BOT-HOSTING FIX online as ...

This build fixes:
- duplicate startup scans
- hourly staff refresh starting immediately at the same time as presence scan
- old current-server channel lookup
- exact guild/channel IDs
- excessive 25-ID batches (uses 50, fewer total API requests)
- 429 retry/backoff
- failed scans do not kill the bot
- exactly one live server-list message
- Matrona+ Server Designer alerts

Guild:
1544430688470700133

Server list + alert channel:
1544431033787613204

Start file:
bot.py
