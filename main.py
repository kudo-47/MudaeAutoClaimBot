import discord
import asyncio
import os
import ast
import random
import re
import time
from dotenv import load_dotenv

# -------------------------
# Configuration / constants
# -------------------------
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TIMER = int(os.getenv("TIMER", 10))  # base delay in seconds for claiming actions
CHARACTER_CHANNEL_ID = int(os.getenv("CHARACTER_CHANNEL_ID"))
OWNER_ID = int(os.getenv("OWNER_ID"))
COMMANDS_CHANNEL_ID = int(os.getenv("COMMANDS_CHANNEL_ID"))
USERNAME = os.getenv("USERNAME")

MUDAE_ID = 432610292342587392  # Mudae bot id
allowed_channels_str = os.getenv("ALLOWED_CHANNELS", "")
ALLOWED_CHANNELS = {int(ch.strip()) for ch in allowed_channels_str.split(",") if ch.strip().isdigit()}
EMOJI_LIST = ['❤️', '💕', '💘', '💖', '💓','💗']
MIN_KAKERA = int(os.getenv("MIN_KAKERA", 0))

# Click retry config (tweak if needed)
CLICK_RETRIES = int(os.getenv("CLICK_RETRIES", 3))
CLICK_RETRY_DELAY = float(os.getenv("CLICK_RETRY_DELAY", 0.8))
ROLL_WAIT_EVENT_TIMEOUT = float(os.getenv("ROLL_WAIT_EVENT_TIMEOUT", 6.0))
DELAY_BETWEEN_ROLLS = int(os.getenv("DELAY_BETWEEN_ROLLS", 3))

rolling_commands_str = os.getenv("ROLLING_COMMANDS", "$wa")
ROLLING_COMMANDS = [cmd.strip() for cmd in rolling_commands_str.split(",") if cmd.strip()]

def parse_env_list(env_value: str):
    """Parse comma or Python-list like env values into a list of strings."""
    if not env_value:
        return []
    env_value = env_value.strip()
    if env_value.startswith("[") and env_value.endswith("]"):
        try:
            return [item.strip() for item in ast.literal_eval(env_value)]
        except Exception:
            print(f"⚠️ Failed to parse Python list: {env_value}")
            return []
    return [item.strip() for item in env_value.split(",") if item.strip()]

KAKERA_LIST = [k.lower() for k in parse_env_list(os.getenv("KAKERA_LIST"))]

# -------------------------
# Utilities
# -------------------------
def parse_time_segment(seg: str) -> int:
    """
    Parse a time segment from Mudae $tu lines.
    Accepts examples: '1h 18', '1h 18 min', '28', '28 min', '49 m'.
    Returns seconds.
    """
    if not seg:
        return 0
    t = re.sub(r"\*", "", seg).strip().lower()
    h = 0
    m = 0

    mh = re.search(r"(\d+)\s*h", t)
    if mh:
        h = int(mh.group(1))

    mm = re.search(r"(\d+)\s*m(?:in(?:ute)?s?)?", t)
    if mm:
        m = int(mm.group(1))
    else:
        nums = [int(n) for n in re.findall(r"\d+", t)]
        if mh and len(nums) >= 2:
            m = nums[1]
        elif not mh and nums:
            m = nums[0]

    return h * 3600 + m * 60

# -------------------------
# Bot client
# -------------------------
class MyClient(discord.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Characters to auto-claim (loaded from CHARACTER_CHANNEL_ID)
        self.character_list: list[str] = []

        # timers_per_channel[channel_id] -> dict of timers and flags for that channel
        # e.g. { 'claim_available': True/False, 'claim_in_progress': True/False, 'claim': seconds,
        #        'rolls_left': int, 'rolls': seconds, '_fetched_at': timestamp, 'rt_available': bool, 'rt': seconds }
        self.timers_per_channel: dict[int, dict] = {}

        # global timers (daily / vote)
        self.global_timers: dict[str, int] = {}

        # ensure only one $tu is ever in-flight at a time
        self.tu_lock = asyncio.Lock()

        # per-channel locks to protect timers modifications
        self.channel_locks: dict[int, asyncio.Lock] = {}

        # per-channel events so auto_roll can be notified immediately when a claim starts/ends
        self.claim_events: dict[int, asyncio.Event] = {}

    async def on_ready(self) -> None:
        """Called when the bot connected and ready."""
        print(f"✅ Logged in as {self.user}!")
        await self.load_character_list()
        print("🎯 Watching for characters:", self.character_list)
        print("💠 Watching for kakera:", KAKERA_LIST)

        # On startup fetch timers sequentially (first channel also fetches global timers)
        allowed_channels = [self.get_channel(cid) for cid in ALLOWED_CHANNELS if self.get_channel(cid)]
        if not allowed_channels:
            print("⚠️ No valid channels found for $tu")
        else:
            first = allowed_channels[0]
            await asyncio.sleep(3)
            print(f"\n🌍 Fetching timers in #{first.name} (global + per-channel)")
            await self.fetch_startup_timers(first, include_global=True)
            await asyncio.sleep(3)
            for ch in allowed_channels[1:]:
                print(f"\n📡 Fetching timers in #{ch.name} (per-channel only)")
                await self.fetch_startup_timers(ch, include_global=False)
                await asyncio.sleep(3)

        # Start background auto-roller
        self.loop.create_task(self.auto_roll())

    async def _get_channel_lock(self, channel_id: int) -> asyncio.Lock:
        """Return a per-channel lock, creating if needed."""
        lock = self.channel_locks.get(channel_id)
        if lock is None:
            lock = asyncio.Lock()
            self.channel_locks[channel_id] = lock
        return lock

    def _get_claim_event(self, channel_id: int) -> asyncio.Event:
        """Return per-channel claim event, create if needed."""
        ev = self.claim_events.get(channel_id)
        if ev is None:
            ev = asyncio.Event()
            self.claim_events[channel_id] = ev
        return ev

    async def fetch_startup_timers(self, channel: discord.TextChannel, include_global: bool):
        """
        Send $tu in `channel`, wait for Mudae reply, parse timers, and populate
        self.timers_per_channel[channel.id]. This function uses a global tu_lock
        so only one $tu is sent across the bot at any given time.
        It will retry up to 3 times on timeouts.
        """
        chan_label = f"#{channel.name}"

        for attempt in range(1, 4):
            async with self.tu_lock:
                try:
                    await channel.send("$tu")

                    def check(m: discord.Message):
                        return m.author.id == MUDAE_ID and m.channel.id == channel.id

                    msg = await self.wait_for("message", timeout=15, check=check)
                    raw = msg.content or ""
                    lc = raw.lower()
                    # debug output of the raw $tu reply

                    timers: dict = {}

                    # ----- Claim -----
                    if re.search(r"you\s+__can__\s+claim right now", lc):
                        m = re.search(r"next claim reset is in \*\*(.+?)\*\*", raw, re.I)
                        timers["claim"] = parse_time_segment(m.group(1)) if m else 0
                        timers["claim_available"] = True
                        timers["claim_in_progress"] = False
                        print(f"[{chan_label}] ✅ Claim available (reset {timers['claim']//60} min)")
                    else:
                        m = re.search(r"you can'?t claim for another \*\*(.+?)\*\*", raw, re.I)
                        if m:
                            timers["claim"] = parse_time_segment(m.group(1))
                            timers["claim_available"] = False
                            timers["claim_in_progress"] = False
                            print(f"[{chan_label}] ❌ Claim cooldown: {timers['claim']//60} min")

                    # ----- Rolls -----
                    m = re.search(r"you have \*\*(\d+)\*\* rolls?.*?next rolls reset in \*\*(.+?)\*\*", raw, re.I | re.S)
                    if m:
                        timers["rolls_left"] = int(m.group(1))
                        timers["rolls"] = parse_time_segment(m.group(2))
                        print(f"[{chan_label}] 🎲 Rolls left: {timers['rolls_left']} (reset {timers['rolls']//60} min)")

                    # ----- Kakera availability/cooldown -----
                    if "you __can__ react to kakera right now" in lc:
                        timers["kakera_available"] = True
                        timers["kakera"] = 0
                        print(f"[{chan_label}] 💎 Kakera available now")
                    else:
                        m = re.search(r"react to kakera for \*\*(.+?)\*\*", raw, re.I)
                        if m:
                            timers["kakera"] = parse_time_segment(m.group(1))
                            timers["kakera_available"] = False
                            print(f"[{chan_label}] 💎 Kakera cooldown: {timers['kakera']//60} min")

                    # ----- Kakera power / consumption / stock -----
                    m = re.search(r"power:\s*\*\*(\d+)%\*\*", raw, re.I)
                    if m:
                        timers["power"] = int(m.group(1))
                    m = re.search(r"consumes\s*(\d+)%\s*of your reaction power", raw, re.I)
                    if m:
                        timers["consumption"] = int(m.group(1))
                    m = re.search(r"stock:\s*\*\*([\d,]+)\*\*<:kakera", raw, re.I)
                    if m:
                        timers["stock"] = int(m.group(1).replace(",", ""))
                        print(f"[{chan_label}] 💎 Kakera stock: {timers['stock']}")

                    # ----- RT availability/cooldown -----
                    # Various Mudae $tu outputs may include "$rt is available!" or a "Time left: ..." line.
                    if re.search(r"\$rt is available", raw, re.I):
                        timers["rt_available"] = True
                        timers["rt"] = 0
                        print(f"[{chan_label}] 🔁 $rt available")
                    else:
                        # try to capture "Time left: 5h 02 min" patterns for $rt cooldown
                        m = re.search(r"the cooldown of \$rt is not over.*?time left[:\s]*\*\*(.+?)\*\*", raw, re.I)
                        if not m:
                            m = re.search(r"time left[:\s]*([\dhm\s:]+)\s*(?:\.\s*\(\$rtu\))?", raw, re.I)
                        if m:
                            try:
                                timers["rt"] = parse_time_segment(m.group(1))
                                timers["rt_available"] = False
                                print(f"[{chan_label}] 🔁 $rt cooldown: {timers['rt']//60} min")
                            except Exception:
                                timers["rt_available"] = False
                                timers["rt"] = None
                        else:
                            # not present in this $tu
                            timers.setdefault("rt_available", False)
                            timers.setdefault("rt", None)

                    # ----- DK ready -----
                    timers["dk_ready"] = "$dk is ready" in lc

                    # ----- Global timers (only from first channel in cycle) -----
                    if include_global:
                        if "$daily is available" in lc:
                            self.global_timers["daily"] = 0
                            print(f"[{chan_label}] 🌍 Daily available now — sending $daily!")
                            try:
                                await channel.send("$daily")
                            except Exception as exc:
                                print(f"[{chan_label}] ❗ Failed to send $daily: {exc}")
                        else:
                            m = re.search(r"next \$daily reset in \*\*(.+?)\*\*", raw, re.I)
                            if m:
                                self.global_timers["daily"] = parse_time_segment(m.group(1))
                                print(f"[{chan_label}] 🌍 Daily reset in {self.global_timers['daily']//60} min")

                        if "you may vote right now" in lc:
                            self.global_timers["vote"] = 0
                            print(f"[{chan_label}] 🌍 Vote available now")
                        else:
                            m = re.search(r"may vote again in \*\*(.+?)\*\*", raw, re.I)
                            if m:
                                self.global_timers["vote"] = parse_time_segment(m.group(1))
                                print(f"[{chan_label}] 🌍 Vote reset in {self.global_timers['vote']//60} min")

                    # store timers and ensure an event exists
                    # add a timestamp so we can compute elapsed time later
                    timers["_fetched_at"] = time.time()
                    # fill sensible defaults
                    timers.setdefault("claim_available", timers.get("claim_available", False))
                    timers.setdefault("claim_in_progress", timers.get("claim_in_progress", False))
                    timers.setdefault("rolls_left", timers.get("rolls_left", 0))
                    timers.setdefault("rt_available", timers.get("rt_available", False))
                    timers.setdefault("rt", timers.get("rt", None))

                    self.timers_per_channel[channel.id] = timers
                    self._get_claim_event(channel.id)  # ensure an Event exists

                    # print a concise summary
                    print("\n📋 Timer Summary")
                    print(f"  Channel: {chan_label}")
                    for k, v in timers.items():
                        if k == "_fetched_at":
                            continue
                        if isinstance(v, int) and k in {"claim", "rolls", "kakera", "rt"} and v is not None:
                            print(f"   • {k}: {v//60} min")
                        else:
                            print(f"   • {k}: {v}")
                    if include_global and self.global_timers:
                        print("  🌍 Global:")
                        for k, v in self.global_timers.items():
                            print(f"   • {k}: {v//60} min")
                    print("")
                    return  # success - exit retry loop

                except asyncio.TimeoutError:
                    print(f"[{chan_label}] ⚠ Timeout waiting for $tu (attempt {attempt}/3)")
                    await asyncio.sleep(1 + attempt)
                except Exception as exc:
                    print(f"[{chan_label}] ❗ Error parsing $tu: {exc}")
                    return

        print(f"[{chan_label}] ❌ Failed to fetch timers after 3 retries.")
        # ensure defaults so other code doesn't KeyError
        self.timers_per_channel.setdefault(channel.id, {"claim_available": False, "claim_in_progress": False, "_fetched_at": time.time(), "rt_available": False, "rt": None})

    async def load_character_list(self):
        """Load character names (to auto-claim) from CHARACTER_CHANNEL_ID messages."""
        channel = self.get_channel(CHARACTER_CHANNEL_ID)
        if not channel:
            print("⚠️ Character channel not found!")
            return
        messages = [m async for m in channel.history(limit=200)]
        characters = []
        for msg in messages:
            for line in msg.content.splitlines():
                name = line.strip()
                if name:
                    characters.append(name.lower())
        self.character_list = list(dict.fromkeys(characters))  # preserve order, dedupe
        print(f"📜 Loaded {len(self.character_list)} characters from #{channel.name}")

    async def auto_roll(self) -> None:
        """
        Main background worker:
        - Iterates allowed channels selecting the next channel to check based on earliest event.
        - Rolls if claim is available OR $rt is available and rolls_left > 0.
        - Stops rolling immediately when a claim is triggered (on_message flips flags and sets event).
        """
        await self.wait_until_ready()
        channel_ids = list(ALLOWED_CHANNELS)

        # index pointer is chosen each iteration according to earliest-event policy
        idx = 0

        while not self.is_closed():
            # pick current channel by idx (wrap)
            if not channel_ids:
                await asyncio.sleep(5)
                continue

            channel_id = channel_ids[idx % len(channel_ids)]
            channel = self.get_channel(channel_id)
            if not channel:
                # skip if channel not available
                idx += 1
                await asyncio.sleep(1)
                continue

            print(f"\n🔍 Checking #{channel.name}...")
            include_global = False  # keep False here (we fetch global only from first startup)
            await asyncio.sleep(11)  # small delay to avoid spamming
            await self.fetch_startup_timers(channel, include_global=include_global)

            # snapshot timers under the channel lock
            channel_lock = await self._get_channel_lock(channel_id)
            async with channel_lock:
                timers_snapshot = self.timers_per_channel.get(channel_id, {}).copy()

            rolls_left = timers_snapshot.get("rolls_left", 0)
            # allow rolling if claim is available OR $rt is available (rt resets claim)
            claim_available = timers_snapshot.get("claim_available", False)
            rt_available = timers_snapshot.get("rt_available", False)
            can_roll_here = (claim_available or rt_available)

            if can_roll_here:
                if rolls_left > 0:
                    print(f"🎯 Claim or $rt available in #{channel.name}! Rolling up to {rolls_left} times...")
                    claim_triggered = False
                    claim_event = self._get_claim_event(channel_id)
                    claim_event.clear()

                    for i in range(rolls_left):
                        # if someone already started a claim, stop
                        async with channel_lock:
                            current = self.timers_per_channel.get(channel_id, {})
                            # allow rolls to continue if rt_available or claim_available
                            if current.get("claim_in_progress", False) or not (current.get("claim_available", True) or current.get("rt_available", False)):
                                claim_triggered = True
                                print("🛑 Claim already in progress/used — stopping further rolls.")
                                break

                        # send a roll
                        try:
                            cmd = random.choice(ROLLING_COMMANDS)
                            await channel.send(cmd)
                            print(f"📩 Sent roll {i+1}/{rolls_left} in #{channel.name}")
                        except Exception as exc:
                            print(f"[#{channel.name}] ❗ Failed to send roll command: {exc}")
                            break

                        # wait a bit for on_message to trigger claim or rt flow, but don't block too long
                        try:
                            await asyncio.wait_for(claim_event.wait(), timeout=ROLL_WAIT_EVENT_TIMEOUT)
                        except asyncio.TimeoutError:
                            # no claim attempt detected in small window
                            pass

                        # re-check state after wait
                        async with channel_lock:
                            current = self.timers_per_channel.get(channel_id, {})
                            if current.get("claim_in_progress", False) or not (current.get("claim_available", True) or current.get("rt_available", False)):
                                claim_triggered = True
                                print("🛑 Claim/rt detected during rolls — stopping further rolls.")
                                break

                        # small delay between rolls
                        delay = random.uniform(max(0.5, DELAY_BETWEEN_ROLLS - 1), DELAY_BETWEEN_ROLLS + 1)
                        await asyncio.sleep(delay)

                    if claim_triggered:
                        print(f"➡ Moving to next channel after claim in #{channel.name}.")
                    else:
                        print(f"✅ Finished rolling in #{channel.name} (no claim triggered).")
                else:
                    print(f"✅ Claim or $rt indicated in #{channel.name} but no rolls left.")
            else:
                print(f"⏳ Claim not ready and no $rt in #{channel.name}. Skipping rolls here.")

            # ---------- EARLIEST-EVENT selection ----------
            # Examine timers across all channels and pick the earliest remaining time.
            now = time.time()
            best_remaining = None
            best_index = None
            fallback_sleep = 5  # seconds minimum if nothing scheduled
            MAX_SLEEP = 24 * 3600  # cap (24h)

            # Prioritize channels where claim_available OR rt_available and rolls_left > 0
            for i, cid in enumerate(channel_ids):
                lock = await self._get_channel_lock(cid)
                async with lock:
                    t = self.timers_per_channel.get(cid, {})
                    if (t.get("claim_available") or t.get("rt_available")) and t.get("rolls_left", 0) > 0 and not t.get("claim_in_progress", False):
                        best_remaining = 0.0
                        best_index = i
                        break

            if best_remaining is None:
                # otherwise compute remaining times using rolls/claim timers
                for i, cid in enumerate(channel_ids):
                    lock = await self._get_channel_lock(cid)
                    async with lock:
                        t = self.timers_per_channel.get(cid, {})
                        fetched = t.get("_fetched_at")
                        if fetched and "rolls" in t and t.get("rolls") is not None:
                            remaining = t["rolls"] - (now - fetched)
                        elif fetched and "claim" in t and t.get("claim") is not None:
                            remaining = t["claim"] - (now - fetched)
                        else:
                            remaining = None

                        if remaining is None:
                            continue
                        remaining = max(0.0, remaining)
                        if best_remaining is None or remaining < best_remaining:
                            best_remaining = remaining
                            best_index = i

            # decide how long to sleep and which channel to process next
            if best_remaining is None:
                next_sleep = fallback_sleep
                idx = (idx + 1) % len(channel_ids)
            else:
                if best_remaining <= 1.0:
                    next_sleep = 0.0
                else:
                    next_sleep = max(fallback_sleep, best_remaining + 1.5)
                idx = best_index if best_index is not None else (idx + 1) % len(channel_ids)

            next_sleep = min(next_sleep, MAX_SLEEP)

            if next_sleep > 0:
                print(f"💤 Sleeping {int(next_sleep)}s until next expected event (channel: {self.get_channel(channel_ids[idx]).name})")
                await asyncio.sleep(next_sleep)
            else:
                await asyncio.sleep(0)

    async def on_message(self, message: discord.Message) -> None:
        """
        Handle owner admin commands and Mudae embeds (claims).
        Now supports $rt flow: if claim not available but $rt is available, send $rt then attempt claim.
        After clicking, fetch the message and print post-claim embed footer to confirm "Belongs to ...".
        """
        # ---- Owner-only commands (character list management) ----
        if message.author.id == OWNER_ID and message.channel.id == COMMANDS_CHANNEL_ID:
            content = message.content.strip()

            if content.lower() == "$reloadchars":
                await self.load_character_list()
                await message.channel.send(f"✅ Reloaded character list. Now watching **{len(self.character_list)}** characters.")
                return

            if content.lower().startswith("$addchars"):
                parts = content.split(maxsplit=1)
                if len(parts) < 2:
                    await message.channel.send("⚠️ Usage: `$addchars name1, name2, ...`")
                    return
                new_chars = [c.strip().lower() for c in parts[1].split(",") if c.strip()]
                added = [c for c in new_chars if c not in self.character_list]
                self.character_list.extend(added)
                self.character_list = list(dict.fromkeys(self.character_list))
                ch = self.get_channel(CHARACTER_CHANNEL_ID)
                if ch and added:
                    await ch.send("\n".join(added))
                await message.channel.send(f"✅ Added {len(added)} characters. Now watching **{len(self.character_list)}** characters.")
                return

            if content.lower().startswith("$removechars"):
                parts = content.split(maxsplit=1)
                if len(parts) < 2:
                    await message.channel.send("⚠️ Usage: `$removechars name1, name2, ...`")
                    return
                remove_chars = [c.strip().lower() for c in parts[1].split(",") if c.strip()]
                removed = [c for c in remove_chars if c in self.character_list]
                self.character_list = [c for c in self.character_list if c not in remove_chars]
                ch = self.get_channel(CHARACTER_CHANNEL_ID)
                if ch:
                    await ch.purge(limit=100)
                    if self.character_list:
                        chunk_size = 50
                        for i in range(0, len(self.character_list), chunk_size):
                            await ch.send("\n".join(self.character_list[i:i+chunk_size]))
                await message.channel.send(f"🗑️ Removed {len(removed)} characters. Now watching **{len(self.character_list)}** characters.")
                return

            if content.lower() == "$listchars":
                if not self.character_list:
                    await message.channel.send("⚠️ Character list is empty.")
                    return
                chunk_size = 50
                chunks = [self.character_list[i:i + chunk_size] for i in range(0, len(self.character_list), chunk_size)]
                for idx, chunk in enumerate(chunks, start=1):
                    formatted = "\n".join(f"{i+1}. {name}" for i, name in enumerate(chunk))
                    await message.channel.send(f"📜 **Character List (Page {idx}/{len(chunks)})**\n```{formatted}```")
                return

            if content.lower() == "$clearallchars":
                await message.channel.send("⚠️ Are you sure you want to **clear all characters**? Type `y` or `yes` within 15 seconds to confirm.")
                def check_confirm(m: discord.Message):
                    return m.author.id == OWNER_ID and m.channel == message.channel and m.content.strip().lower() in {"y", "yes"}
                try:
                    confirm_msg = await self.wait_for("message", timeout=15.0, check=check_confirm)
                    if confirm_msg:
                        self.character_list.clear()
                        ch = self.get_channel(CHARACTER_CHANNEL_ID)
                        if ch:
                            await ch.purge(limit=100)
                        await message.channel.send("🧹 Cleared all characters. Character list is now empty.")
                except asyncio.TimeoutError:
                    await message.channel.send("❌ Cancelled. Character list not cleared.")
                return

            if content.lower() == "!help":
                help_text = (
                    "📖 **Bot Command Help**\n\n"
                    "🌀 **Character Management**\n"
                    "`$reloadchars`, `$addchars name1, name2, ...`, `$removechars ...`, `$listchars`, `$clearallchars`\n\n"
                    "✅ Only the bot owner can use these commands."
                )
                await message.channel.send(help_text)
                return

        # ---- Mudae embed handling (attempt claims when embed rolls happen) ----
        if (
            message.channel.id in ALLOWED_CHANNELS
            and message.author.id == MUDAE_ID
            and message.embeds
            and message.components
        ):
            embed: discord.Embed = message.embeds[0]
            char_name = embed.author.name if embed.author else "Unknown"
            kakera_text = embed.description or ""
            kakera_match = re.search(r'\*\*(\d+)\*\*\s*<:kakera:', kakera_text.replace(',', ''))
            kakera_value = int(kakera_match.group(1)) if kakera_match else 0

            char_lower = char_name.lower()
            print(f"🎲 Rolled character in #{message.channel.name}: {char_name} (kakera {kakera_value})")

            # compute claim conditions
            claim_character = char_lower in self.character_list
            claim_kakera = kakera_value >= MIN_KAKERA

            # load channel timers (may be slightly stale but good enough)
            ch_timers = self.timers_per_channel.get(message.channel.id, {})
            # if timers say claim not available and not in progress, note it (but we may use $rt)
            if ch_timers.get("claim_available") is False and not ch_timers.get("claim_in_progress", False):
                print(f"⚠️ Claim currently not available in #{message.channel.name} per last $tu (claim={ch_timers.get('claim')}).")

            # If either condition is met, attempt to press a claim emoji
            if claim_character or claim_kakera:
                for row in message.components:
                    for button in row.children:
                        try:
                            if not button.emoji:
                                continue
                            if str(button.emoji) not in EMOJI_LIST:
                                continue

                            # re-read channel timers under lock
                            lock = await self._get_channel_lock(message.channel.id)
                            async with lock:
                                ch_timers = self.timers_per_channel.setdefault(message.channel.id, {})

                            claim_available_now = ch_timers.get("claim_available", False)
                            rt_available_now = ch_timers.get("rt_available", False)

                            # If claim isn't available but $rt is, attempt the $rt flow first
                            if not claim_available_now and rt_available_now:
                                async with lock:
                                    ch_timers["claim_in_progress"] = True
                                    ch_timers["rt_in_progress"] = True
                                ev = self._get_claim_event(message.channel.id)
                                ev.set()
                                print(f"🔁 $rt available in #{message.channel.name}. Sending $rt to reset claim cooldown before attempting claim for {char_name}...")

                                # small human-like pause
                                await asyncio.sleep(random.uniform(0.3, 0.9))

                                try:
                                    await message.channel.send("$rt")
                                except Exception as exc:
                                    print(f"[#{message.channel.name}] ❗ Failed to send $rt: {exc}")
                                    async with lock:
                                        ch_timers["claim_in_progress"] = False
                                        ch_timers.pop("rt_in_progress", None)
                                    ev.set()
                                    return

                                # wait briefly for a Mudae reply to $rt (non-blocking)
                                try:
                                    def rt_check(m: discord.Message):
                                        return m.author.id == MUDAE_ID and m.channel.id == message.channel.id
                                    rt_msg = await self.wait_for("message", timeout=8.0, check=rt_check)
                                    print(f"[#{message.channel.name}] 📩 Received Mudae reply after $rt: {rt_msg.content[:200]!s}")
                                except asyncio.TimeoutError:
                                    print(f"[#{message.channel.name}] ⚠ Timeout waiting for Mudae response to $rt (will refresh timers).")

                                # refresh timers so we know if claim became available
                                try:
                                    await self.fetch_startup_timers(message.channel, include_global=False)
                                except Exception as exc:
                                    print(f"[#{message.channel.name}] ❗ Error while refreshing timers after $rt: {exc}")

                                # re-check state after refresh
                                async with lock:
                                    post = self.timers_per_channel.get(message.channel.id, {})
                                    became_available = post.get("claim_available", False)
                                    post.pop("rt_in_progress", None)

                                if became_available:
                                    print(f"[#{message.channel.name}] ✅ Claim became available after $rt — attempting claim for {char_name}.")
                                    # attempt claim click (resilient)
                                    clicked = False
                                    last_exc = None
                                    for attempt_i in range(1, CLICK_RETRIES + 1):
                                        try:
                                            await button.click()
                                            clicked = True
                                            break
                                        except Exception as exc:
                                            last_exc = exc
                                            print(f"[#{message.channel.name}] ⚠ Claim click attempt {attempt_i}/{CLICK_RETRIES} after $rt failed: {exc}")
                                            await asyncio.sleep(CLICK_RETRY_DELAY)

                                    # after clicking (or failing), fetch message and print embed/footer for confirmation
                                    await asyncio.sleep(0.7)
                                    try:
                                        new_msg = await message.channel.fetch_message(message.id)
                                        new_embed = new_msg.embeds[0] if new_msg.embeds else None
                                        footer_text = new_embed.footer.text if new_embed and new_embed.footer else ""
                                        print(f"[#{message.channel.name}] 🔎 Post-claim embed footer: {footer_text!r}")
                                        if footer_text and f"Belongs to {USERNAME}" in footer_text:
                                            print(f"[#{message.channel.name}] ✅ Confirmed claim via embed footer for {char_name}")
                                        else:
                                            print(f"[#{message.channel.name}] ⚠ Post-claim embed footer doesn't contain 'Belongs to' (footer: {footer_text!r})")
                                    except Exception as exc:
                                        print(f"[#{message.channel.name}] ⚠ Failed to fetch post-claim message for confirmation: {exc}")

                                    async with lock:
                                        self.timers_per_channel.setdefault(message.channel.id, {})["claim_in_progress"] = False
                                        # keep claim_available False until next $tu
                                    ev.set()
                                    if not clicked:
                                        print(f"[#{message.channel.name}] ❌ Clicks after $rt all failed. Last error: {last_exc}")
                                        # refresh timers to recover
                                        try:
                                            await self.fetch_startup_timers(message.channel, include_global=False)
                                        except Exception as exc:
                                            print(f"[#{message.channel.name}] ❗ Error refreshing timers after failed click: {exc}")
                                    return
                                else:
                                    print(f"[#{message.channel.name}] ❌ $rt did not make claim available for {char_name}. Aborting claim attempt.")
                                    async with lock:
                                        self.timers_per_channel.setdefault(message.channel.id, {})["claim_in_progress"] = False
                                    ev.set()
                                    # refresh timers for correctness
                                    try:
                                        await self.fetch_startup_timers(message.channel, include_global=False)
                                    except Exception as exc:
                                        print(f"[#{message.channel.name}] ❗ Error while refreshing timers after $rt no-op: {exc}")
                                    return

                            # If claim is available normally (no $rt required), proceed with normal claim flow:
                            if ch_timers.get("claim_available", False):
                                # mark claim_in_progress immediately
                                lock = await self._get_channel_lock(message.channel.id)
                                async with lock:
                                    timers = self.timers_per_channel.setdefault(message.channel.id, {})
                                    timers["claim_available"] = False
                                    timers["claim_in_progress"] = True

                                ev = self._get_claim_event(message.channel.id)
                                ev.set()

                                # human-like delay before clicking
                                delay = random.uniform(max(0.5, TIMER - 1), TIMER + 1)
                                print(f"⏳ Waiting {delay:.2f}s before attempting claim for {char_name} in #{message.channel.name}...")
                                await asyncio.sleep(delay)

                                clicked = False
                                last_exc = None
                                for attempt in range(1, CLICK_RETRIES + 1):
                                    try:
                                        await button.click()
                                        clicked = True
                                        break
                                    except Exception as exc:
                                        last_exc = exc
                                        print(f"[#{message.channel.name}] ⚠ Click attempt {attempt}/{CLICK_RETRIES} failed: {exc}")
                                        await asyncio.sleep(CLICK_RETRY_DELAY)

                                # after clicking, try to confirm via embed footer
                                await asyncio.sleep(0.7)
                                try:
                                    new_msg = await message.channel.fetch_message(message.id)
                                    new_embed = new_msg.embeds[0] if new_msg.embeds else None
                                    footer_text = new_embed.footer.text if new_embed and new_embed.footer else ""
                                    print(f"[#{message.channel.name}] 🔎 Post-claim embed footer: {footer_text!r}")
                                    if footer_text and "Belongs to" in footer_text:
                                        print(f"[#{message.channel.name}] ✅ Confirmed claim via embed footer for {char_name}")
                                    else:
                                        print(f"[#{message.channel.name}] ⚠ Post-claim embed footer doesn't contain 'Belongs to' (footer: {footer_text!r})")
                                except Exception as exc:
                                    print(f"[#{message.channel.name}] ⚠ Failed to fetch post-claim message for confirmation: {exc}")

                                if clicked:
                                    async with lock:
                                        self.timers_per_channel.setdefault(message.channel.id, {})["claim_in_progress"] = False
                                        self.timers_per_channel.setdefault(message.channel.id, {})["claim_available"] = False
                                    ev.set()
                                    print(f"✅ Character claimed in #{message.channel.name}: {char_name} (reason: {'list' if claim_character else 'kakera'})")
                                    return
                                else:
                                    print(f"[#{message.channel.name}] ❌ All click attempts failed for {char_name}. Refreshing timers to recover. Last error: {last_exc}")
                                    async with lock:
                                        self.timers_per_channel.setdefault(message.channel.id, {})["claim_in_progress"] = False
                                    ev.set()
                                    try:
                                        await self.fetch_startup_timers(message.channel, include_global=False)
                                    except Exception as exc:
                                        print(f"[#{message.channel.name}] ❗ Error while refreshing timers after failed click: {exc}")
                                    ev.set()
                                    return
                        except Exception as exc:
                            print(f"[#{message.channel.name}] ❗ Unexpected error when trying to claim button: {exc}")
                            continue

            # If not claimed via character logic, optionally handle kakera-only buttons (stock etc.)
            if not self.timers_per_channel.get(message.channel.id, {}).get("kakera_available", True):
                # kakera not available per $tu; skip kakera reactions
                pass
            else:
                for row in message.components:
                    for button in row.children:
                        if not button.emoji:
                            continue
                        emoji_str = str(button.emoji).lower()
                        if any(k in emoji_str for k in KAKERA_LIST):
                            delay = random.uniform(max(0.5, TIMER - 1), TIMER + 1)
                            print(f"⏳ Waiting {delay:.2f}s before claiming kakera button {emoji_str} in #{message.channel.name}...")
                            await asyncio.sleep(delay)
                            try:
                                await button.click()
                                print(f"✅ Kakera reaction clicked in #{message.channel.name}: {emoji_str}")

                                # --- Confirmation handling for kakera ---
                                def kakera_check(m: discord.Message):
                                    return (
                                        m.author.id == MUDAE_ID
                                        and m.channel.id == message.channel.id
                                        and "<:kakera" in (m.content or "").lower()
                                        and str(self.user) in (m.content or "")
                                    )
                                try:
                                    conf_msg = await self.wait_for("message", timeout=10.0, check=kakera_check)
                                    snippet = conf_msg.content[:120].replace("\n", " ")
                                    print(f"[#{message.channel.name}] 🔎 Kakera confirmation: {snippet}")
                                except asyncio.TimeoutError:
                                    print(f"[#{message.channel.name}] ⚠ No kakera confirmation detected (timeout).")

                            except Exception as exc:
                                print(f"[#{message.channel.name}] ❗ Failed clicking kakera button: {exc}")
                            return


# run the client
client = MyClient()
client.run(TOKEN)
