DETECTIVE YAMAHA — DPI SERVER WATCH

WHAT THIS VERSION DOES
- Checks Divine Sister staff every 5 minutes.
- Tracks rank 50+ users.
- Checks their PUBLIC Roblox presence.
- Detects who is inside De Pride Isle Sanatorium.
- Groups visible staff by exact server gameId.
- Updates #current-server automatically.
- Detects transitions from NOT in DPI -> IN DPI.
- If the person is Matrona rank or higher, sends an alert in
  #leadership-presence and pings the Discord role "Server Designer".
- Matrona rank is discovered dynamically from group 5008654.
- Presence requests are batched in groups of 50 to avoid Roblox API 400 errors.

IMPORTANT PRIVACY LIMIT
If Roblox does not publicly expose a user's game presence, the bot cannot
reliably detect that user or their server and does not attempt to bypass it.

EXPECTED DISCORD NAMES
Channel: leadership-presence
Channel: current-server
Role: Server Designer

ENVIRONMENT VARIABLES
Required:
DISCORD_TOKEN
GUILD_ID

Optional IDs if you prefer IDs instead of name lookup:
ALERT_CHANNEL_ID
CURRENT_SERVER_CHANNEL_ID
SERVER_DESIGNER_ROLE_ID

START COMMAND
python bot.py

SLASH COMMANDS
/serverscan
/yamaha
