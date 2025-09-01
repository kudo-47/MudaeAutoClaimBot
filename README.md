🎲 Mudae Auto-Claimer Bot

A Discord selfbot-style client that helps with auto-rolling, claiming characters, and kakera reactions for the Mudae bot


⚠️ Disclaimer
This script automates interaction with Mudae. Using automation can violate server rules or Discord’s ToS. Use only in private servers or with permission. You are solely responsible for how you use this.

🚀 Features

✅ Auto-claim characters from your watchlist.

✅ Claim characters based on minimum kakera value.

✅ Supports $rt flow (auto uses $rt when claim is on cooldown).

✅ Auto-reacts to kakera buttons (with optional confirmation).

✅ Parses $tu for timers (claim, rolls, kakera cooldown, $rt, daily, vote).

✅ Retries failed clicks and avoids duplicate claims.

✅ Per-channel timers, locks, and claim events for safe concurrency.

⚙️ Setup
1. Requirements

Python 3.13+

discord.py-self
 (selfbot variant)

python-dotenv

audioop-lts

pip install -U discord.py-self python-dotenv audioop-lts

2. Configuration

**Make a new server and create two channels. one is for storing the character names(CHARACTER_CHANNEL_ID) and another is for commands channel(COMMANDS_CHANNEL_ID)**

Character List

The bot loads claim targets from messages inside the CHARACTER_CHANNEL_ID.

Each line in that channel = one character’s name.

Example:

rem
megumin
asuna

3. Run the Bot
python main.py


You should see output like:

✅ Logged in as USERNAME!
📜 Loaded 123 characters
🌍 Fetching timers in #games (global + per-channel)
🎯 Watching for characters: ['rem', 'megumin', 'asuna']
💠 Watching for kakera: ['kakeray','kakeral',...]

🔑 Owner Commands

Owner commands must be typed in COMMANDS_CHANNEL_ID by OWNER_ID.

$reloadchars → Reload list from CHARACTER_CHANNEL_ID.

$addchars rem, asuna → Add characters to list.

$removechars rem, asuna → Remove characters.

$listchars → Display current list.

$clearallchars → Wipe all characters after confirmation.

!help → Show help.

📋 Flow Overview

Startup → Fetch $tu timers from each allowed channel.

Auto-roll → When claim is available (or $rt usable), rolls up to remaining rolls.

Auto-claim → On new rolls, if character is in list or kakera ≥ MIN_KAKERA, tries to claim.

If claim unavailable but $rt is, bot sends $rt, refreshes timers, then claims.

Confirms success via embed footer (Belongs to ...).

Kakera reaction → If kakera reaction is available, clicks reaction buttons with human-like delay.

🛠️ Notes & Tips

Click retries: By default, bot retries failed click actions up to 3 times (CLICK_RETRIES).

Random delays: Introduces random ±1s offsets before clicks to mimic human behavior.

Timeouts: $tu fetch, claim events, and kakera confirmations have explicit timeout handling.

Character names are matched case-insensitive.

🧩 Example Console Logs
📡 Fetching timers in #games-2 (per-channel only)
🎲 Rolled character in #games-2: Kagari Hosho (kakera 42)
⏳ Waiting 9.80s before attempting claim for Kagari Hosho in #games-2...
🛑 Claim/rt detected during rolls — stopping further rolls.
➡ Moving to next channel after claim in #games-2.

❓ FAQ

Q: The bot keeps timing out when fetching $tu.
A: Increase the timeout in fetch_startup_timers, or reduce frequency of checks.

Q: It didn’t claim even though character is in my list.
A: Check that character name in CHARACTER_CHANNEL_ID exactly matches Mudae’s output (case ignored, but spacing must match).

Q: Can it double-claim?
A: No — claim_in_progress locks prevent multiple attempts.
