DETECTIVE YAMAHA v7 — EMBED MODE

This is the non-thread version.

Behavior:
- Uses ONE normal Discord message in #server-list.
- The message is a neat Discord EMBED (the boxed card).
- It is created after the first successful startup scan.
- Every 5 minutes, the SAME embed message is edited/refreshed.
- /serverscan manually refreshes the same embed.
- Staff are grouped by exact DPI server and sorted highest rank -> lowest.
- Shows tracked staff per server and attempts to show total server population.
- Matrona+ users are marked with a star.
- When a Matrona+ newly joins DPI, Server Designer is pinged in the alert channel.
- Roblox 429s are retried/backed off and do not kill the bot.
- Staff membership cache refreshes hourly.

Expected startup line:
Detective Yamaha v7 EMBED MODE online as ...

If your host still prints THREAD MODE or plain Detective Yamaha online,
it is still running an older bot.py.
