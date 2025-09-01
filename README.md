# 🎲 Mudae Auto-Claimer Bot

A Discord selfbot-style client that helps with auto-rolling, claiming characters, and kakera reactions for the Mudae bot.

⚠️ **Disclaimer**  
This script automates interaction with Mudae. Using automation can violate server rules or Discord’s ToS.  
Use only in private servers or with permission. You are solely responsible for how you use this.

---

## 🚀 Features
- ✅ Auto-claim characters from your watchlist  
- ✅ Claim characters based on minimum kakera value  
- ✅ Supports `$rt` flow (auto uses `$rt` when claim is on cooldown)  
- ✅ Auto-reacts to kakera buttons (with optional confirmation)  
- ✅ Parses `$tu` for timers (claim, rolls, kakera cooldown, `$rt`, daily, vote)  
- ✅ Retries failed clicks and avoids duplicate claims  
- ✅ Per-channel timers, locks, and claim events for safe concurrency  

---

## ⚙️ Setup

### Requirements
- Python **3.13+**
- [`discord.py-self`](https://pypi.org/project/discord.py-self/)  
- `python-dotenv`  
- `audioop-lts`  

Install them with:

```bash
pip install -U discord.py-self python-dotenv audioop-lts
```


## ⚙️ Configuration

Set these values in your `.env` file:

| Variable                  | Example Value                                                                 | Description                                                                 |
|---------------------------|-------------------------------------------------------------------------------|-----------------------------------------------------------------------------|
| `DISCORD_TOKEN`           | `mfa.your_discord_token_here`                                                 | Your Discord account token.                                                 |
| `TIMER`                   | `10`                                                                          | Base delay (seconds) before claim/reaction.                                 |
| `CHARACTER_CHANNEL_ID`    | `123456789012345678`                                                          | Channel ID where your character list is stored.                             |
| `COMMANDS_CHANNEL_ID`     | `123456789012345678`                                                          | Channel ID for bot owner-only commands.                                     |
| `OWNER_ID`                | `123456789012345678`                                                          | Your Discord user ID.                                                       |
| `ALLOWED_CHANNELS`        | `1163895503143043135,1310112309850406946`                                     | Comma-separated list of channel IDs where Mudae rolls are allowed.          |
| `MIN_KAKERA`              | `200`                                                                         | Minimum kakera value required to auto-claim a character.                    |
| `KAKERA_LIST`             | `["kakera","kakeraT","kakeraG","kakeraY","kakeraO","kakeraR","kakeraW","kakeraL"]` | Kakera reaction emojis.                                                |
| `CLICK_RETRIES`           | `3`                                                                           | Number of times to retry clicking claim/kakera buttons.                     |
| `CLICK_RETRY_DELAY`       | `0.8`                                                                         | Delay (in seconds) between click retries.                                   |
| `ROLL_WAIT_EVENT_TIMEOUT` | `6.0`                                                                         | Timeout (in seconds) for waiting on claim/kakera confirmation events.       |
| `ROLLING_COMMANDS`        | `$wa,$ha,$ma`                                                                 | Comma-separated list of rolling commands (used randomly).                   |
| `DELAY_BETWEEN_ROLLS`     | `3`                                                                           | Seconds between each roll, randomized a bit for more human-like behavior    |
---

### 📂 Example `.env` file

```env
# Discord token (mfa. if 2FA enabled)
DISCORD_TOKEN=mfa.your_discord_token_here

# Base timer delay in seconds
TIMER=10

# Channels & owner setup
CHARACTER_CHANNEL_ID=123456789012345678
COMMANDS_CHANNEL_ID=123456789012345678
OWNER_ID=123456789012345678

# Allowed rolling channels
ALLOWED_CHANNELS=1163895503143043135,1310112309850406946

# Claiming settings
MIN_KAKERA=200
KAKERA_LIST=["kakera","kakeraT","kakeraG","kakeraY","kakeraO","kakeraR","kakeraW","kakeraL"]

# Retry/timeout settings
CLICK_RETRIES=3
CLICK_RETRY_DELAY=0.8
ROLL_WAIT_EVENT_TIMEOUT=6.0

# Rolling commands (randomized use)
ROLLING_COMMANDS=$wa,$ha,$ma
```

3. Run the Bot
```bash
python main.py
```


You should see output like:
```

✅ Logged in as USERNAME!
📜 Loaded 123 characters
🌍 Fetching timers in #games
🎯 Watching for characters: ['rem', 'megumin', 'asuna']
💠 Watching for kakera: ['kakeray','kakeral',...]
```

## 🔑 Owner Commands

Owner commands must be typed in `COMMANDS_CHANNEL_ID` by `OWNER_ID`.

| Command            | Description                                  |
|--------------------|----------------------------------------------|
| `$reloadchars`     | Reload list from `CHARACTER_CHANNEL_ID`.     |
| `$addchars rem, asuna` | Add characters to list.                 |
| `$removechars rem, asuna` | Remove characters from list.         |
| `$listchars`       | Display current list of characters.          |
| `$clearallchars`   | Wipe all characters after confirmation.      |
| `!help`            | Show help.                                   |


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
```
🧩 Example Console Logs
📡 Fetching timers in #games-2 (per-channel only)
🎲 Rolled character in #games-2: Kagari Hosho (kakera 42)
⏳ Waiting 9.80s before attempting claim for Kagari Hosho in #games-2...
🛑 Claim/rt detected during rolls — stopping further rolls.
➡ Moving to next channel after claim in #games-2.
```

## ❓ FAQ

**Q:** The bot keeps timing out when fetching `$tu`.  
**A:** Increase timeout in `ROLL_WAIT_EVENT_TIMEOUT`, or reduce frequency of checks.  

**Q:** It didn’t claim even though character is in my list.  
**A:** Check spacing — names must exactly match Mudae’s output (case ignored).  

**Q:** Can it double-claim?  
**A:** No — locks prevent multiple claim attempts.  
