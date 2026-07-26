import asyncio
import json
import logging
import os
import queue
import re
import sys
import threading
import time

from flask import Flask, Response, render_template, jsonify, request

import discord
from discord.ext import commands
from config import BOT
from .commands_catalog import COMMAND_CATALOG

logging.getLogger("werkzeug").setLevel(logging.ERROR)

_pkg_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(_pkg_dir, "static"), static_url_path="/static")

bot_instance = None


# ──────────────────────────────────────────────────────────────────────────────
# Bot reference
# ──────────────────────────────────────────────────────────────────────────────

def set_bot(bot) -> None:
    global bot_instance
    bot_instance = bot


# ──────────────────────────────────────────────────────────────────────────────
# Settings persistence
# ──────────────────────────────────────────────────────────────────────────────

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS = {
    "prefix": BOT.PREFIX,
    "status": "online",
    "activity_type": "playing",
    "activity_name": "",
    "rich_presence": {
        "enabled": False,
        "type": "playing",
        "name": "",
        "details": "",
        "state": "",
        "large_image": "",
        "large_image_text": "",
        "small_image": "",
        "small_image_text": "",
        "start_timestamp": False,
        "buttons": [
            {"label": "", "url": ""},
            {"label": "", "url": ""},
        ],
    },
}


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)
        if "rich_presence" in data:
            rp = dict(DEFAULT_SETTINGS["rich_presence"])
            rp.update(data["rich_presence"])
            merged["rich_presence"] = rp
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Rich Presence helpers
# ──────────────────────────────────────────────────────────────────────────────

def _apply_presence() -> None:
    if bot_instance is None or bot_instance.loop is None:
        return

    settings = load_settings()
    rp = settings.get("rich_presence", {})

    if not rp.get("enabled"):
        coro = bot_instance.change_presence(activity=None)
    else:
        type_map = {
            "playing": discord.ActivityType.playing,
            "watching": discord.ActivityType.watching,
            "listening": discord.ActivityType.listening,
            "competing": discord.ActivityType.competing,
            "streaming": discord.ActivityType.streaming,
        }
        atype = type_map.get(rp.get("type", "playing"), discord.ActivityType.playing)
        kwargs = {"type": atype, "name": rp.get("name", "")}
        if rp.get("details"):
            kwargs["details"] = rp["details"]
        if rp.get("state"):
            kwargs["state"] = rp["state"]

        activity = discord.Activity(**kwargs)

        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        status = status_map.get(settings.get("status", "online"), discord.Status.online)

        coro = bot_instance.change_presence(activity=activity, status=status)

    try:
        future = asyncio.run_coroutine_threadsafe(coro, bot_instance.loop)
        future.result(timeout=10)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Command helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_commands():
    if bot_instance is None:
        return {}
    grouped = {}
    for command in bot_instance.commands:
        cog = command.cog.__class__.__name__ if command.cog else "No Category"
        if cog not in grouped:
            grouped[cog] = []
        grouped[cog].append(command.name)
    for cog in grouped:
        grouped[cog].sort()
    return dict(sorted(grouped.items()))


def _pick_recipient():
    me = bot_instance.user
    for rel in getattr(bot_instance, "friends", []):
        if getattr(rel, "type", None) == discord.RelationshipType.friend:
            user = getattr(rel, "user", None)
            if user is not None and user.id != me.id:
                return user.id
    for guild in bot_instance.guilds:
        for member in guild.members:
            if member.id != me.id and not member.bot:
                return member.id
    return None


async def _resolve_test_group():
    me = bot_instance.user
    for ch in bot_instance.private_channels:
        if ch.type == discord.ChannelType.group and ch.name == "TEST":
            return ch

    recipient = _pick_recipient()
    channel = await bot_instance.http.start_group([me.id, recipient] if recipient else [me.id])
    await asyncio.sleep(1)
    ch = bot_instance.get_channel(channel["id"])
    if ch is None:
        ch = await bot_instance.fetch_channel(channel["id"])
    try:
        await ch.edit(name="TEST")
    except Exception:
        pass
    if recipient is not None:
        try:
            await bot_instance.http.remove_group_recipient(ch.id, recipient)
        except Exception:
            pass
    return ch


async def _invoke_in_test(command_name, args=""):
    channel = await _resolve_test_group()
    if channel is None:
        raise RuntimeError("Could not resolve TEST group chat.")
    content = f"{BOT.PREFIX}{command_name}"
    if args and args.strip():
        content += " " + args.strip()
    params = discord.http.handle_message_parameters(content=content)
    msg = await bot_instance.http.send_message(channel.id, params=params)
    fetched = await channel.fetch_message(int(msg["id"]))
    await bot_instance.process_commands(fetched)
    return channel.id


def execute_command(command_name, args=""):
    if bot_instance is None:
        return {"ok": False, "error": "Bot is not running."}
    if bot_instance.get_command(command_name) is None:
        return {"ok": False, "error": f"Unknown command: {command_name}"}
    try:
        future = asyncio.run_coroutine_threadsafe(
            _invoke_in_test(command_name, args), bot_instance.loop
        )
        channel_id = future.result(timeout=30)
        return {"ok": True, "channel_id": channel_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ──────────────────────────────────────────────────────────────────────────────
# Console log capture
# ──────────────────────────────────────────────────────────────────────────────

log_queue = queue.Queue()
max_logs = 500

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ZNE", "Logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "webui_console.log")
max_log_lines = 500

bot_stats = {
    "username": "Loading...",
    "avatar_url": None,
    "guilds": 0,
    "commands": 0,
    "uptime": "00:00:00",
    "version": "ZNE V4",
    "start_time": None,
    "prefix": BOT.PREFIX,
    "bot_type": BOT.BOT_TYPE,
}

_original_stdout = sys.stdout
_original_stderr = sys.stderr

ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')


class LogCapture:
    def write(self, text):
        if text and text.strip():
            clean = ansi_pattern.sub('', text)
            log_queue.put(clean)
            try:
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(clean + "\n")
                self._trim_log_file()
            except Exception:
                pass
            if log_queue.qsize() > max_logs:
                try:
                    while log_queue.qsize() > max_logs:
                        log_queue.get_nowait()
                except queue.Empty:
                    pass

    def _trim_log_file(self):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > max_log_lines:
                with open(LOG_FILE, "w", encoding="utf-8") as f:
                    f.writelines(lines[-max_log_lines:])
        except Exception:
            pass

    def flush(self):
        pass

    def isatty(self):
        return False


class Tee:
    def __init__(self, original, capture):
        self.original = original
        self.capture = capture

    def write(self, text):
        self.original.write(text)
        self.capture.write(text)

    def flush(self):
        self.original.flush()
        self.capture.flush()

    def isatty(self):
        return self.original.isatty()


log_capture = LogCapture()


def update_stats(**kwargs):
    for key, value in kwargs.items():
        if key in bot_stats:
            bot_stats[key] = value


def read_recent_logs(limit=max_log_lines):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = [ln.rstrip("\n") for ln in f.readlines()]
        return lines[-limit:]
    except Exception:
        return []


def redirect_output():
    sys.stdout = Tee(_original_stdout, log_capture)
    sys.stderr = Tee(_original_stderr, log_capture)


def _run_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)


def start_webui(bot=None):
    if bot is not None:
        set_bot(bot)
    redirect_output()
    thread = threading.Thread(target=_run_flask, daemon=True)
    thread.start()


# ──────────────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/commands")
def commands_page():
    return render_template("commands.html", command_groups=get_commands())


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/presence")
def presence_page():
    return render_template("presence.html")


@app.route("/guilds")
def guilds_page():
    return render_template("guilds.html")


@app.route("/messages")
def messages_page():
    return render_template("messages.html")


@app.route("/cloner")
def cloner_page():
    return render_template("cloner.html")


@app.route("/dump")
def dump_page():
    return render_template("dump.html")


# ──────────────────────────────────────────────────────────────────────────────
# API — Stats & Logs
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/stats")
def stats():
    stats_data = dict(bot_stats)
    if bot_stats.get("start_time"):
        elapsed = time.time() - bot_stats["start_time"]
        hours, rem = divmod(int(elapsed), 3600)
        minutes, seconds = divmod(rem, 60)
        stats_data["uptime"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    stats_data["prefix"] = BOT.PREFIX
    stats_data["bot_type"] = BOT.BOT_TYPE
    return jsonify(stats_data)


@app.route("/api/logs")
def api_logs():
    return jsonify({"logs": read_recent_logs()})


@app.route("/stream")
def stream():
    def event_stream():
        while True:
            try:
                msg = log_queue.get(timeout=1)
                for line in msg.split("\n"):
                    yield f"data: {line}\n"
                yield "\n"
            except queue.Empty:
                yield ": heartbeat\n\n"

    return Response(event_stream(), mimetype="text/event-stream")


# ──────────────────────────────────────────────────────────────────────────────
# API — Commands
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/command/<name>")
def api_command(name):
    entry = dict(COMMAND_CATALOG.get(name, {}))
    entry["name"] = name
    entry["exists"] = bot_instance is not None and bot_instance.get_command(name) is not None
    if not entry.get("category"):
        entry["category"] = "No Category"
    return jsonify(entry)


@app.route("/api/execute", methods=["POST"])
def api_execute():
    data = request.get_json(silent=True) or {}
    name = data.get("command", "").strip()
    args = data.get("args", "")
    if not name:
        return jsonify({"ok": False, "error": "No command provided."})
    return jsonify(execute_command(name, args))


# ──────────────────────────────────────────────────────────────────────────────
# API — Settings
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    for key in ("prefix", "status", "activity_type", "activity_name"):
        if key in data:
            settings[key] = data[key]
    if "rich_presence" in data and isinstance(data["rich_presence"], dict):
        settings["rich_presence"].update(data["rich_presence"])
    save_settings(settings)
    return jsonify({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# API — Rich Presence
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/presence", methods=["GET"])
def api_get_presence():
    settings = load_settings()
    return jsonify(settings.get("rich_presence", DEFAULT_SETTINGS["rich_presence"]))


@app.route("/api/presence", methods=["POST"])
def api_set_presence():
    data = request.get_json(silent=True) or {}
    settings = load_settings()
    rp = settings.get("rich_presence", {})
    for key in ("enabled", "type", "name", "details", "state",
                 "large_image", "large_image_text", "small_image",
                 "small_image_text", "start_timestamp", "buttons"):
        if key in data:
            rp[key] = data[key]
    settings["rich_presence"] = rp
    save_settings(settings)
    _apply_presence()
    return jsonify({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# API — Status
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/status", methods=["POST"])
def api_set_status():
    data = request.get_json(silent=True) or {}
    status = data.get("status", "online")
    settings = load_settings()
    settings["status"] = status
    save_settings(settings)
    _apply_presence()
    return jsonify({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# API — Guilds
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/guilds")
def api_guilds():
    if bot_instance is None:
        return jsonify([])
    guilds = []
    for g in bot_instance.guilds:
        icon_url = None
        try:
            if g.icon:
                icon_url = g.icon.url
        except Exception:
            pass
        guilds.append({
            "id": str(g.id),
            "name": g.name,
            "icon_url": icon_url,
            "member_count": g.member_count or 0,
            "online_count": g.online_count if hasattr(g, "online_count") else 0,
            "owner_id": str(g.owner_id) if g.owner_id else None,
            "is_owner": g.owner_id == bot_instance.user.id if g.owner_id else False,
            "created_at": g.created_at.isoformat() if g.created_at else None,
        })
    return jsonify(guilds)


@app.route("/api/guild/<guild_id>/channels")
def api_guild_channels(guild_id):
    if bot_instance is None:
        return jsonify([])
    try:
        guild = bot_instance.get_guild(int(guild_id))
        if guild is None:
            return jsonify([])
        channels = []
        for ch in guild.channels:
            channels.append({
                "id": str(ch.id),
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
            })
        channels.sort(key=lambda c: c["position"])
        return jsonify(channels)
    except Exception:
        return jsonify([])


# ──────────────────────────────────────────────────────────────────────────────
# API — Messages (from DB)
# ──────────────────────────────────────────────────────────────────────────────

@app.route("/api/messages")
def api_messages():
    if bot_instance is None:
        return jsonify({"messages": []})
    limit = request.args.get("limit", 100, type=int)
    channel_id = request.args.get("channel_id", None)

    async def _fetch():
        import importlib.util
        db_mod_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "core", "database.py")
        spec = importlib.util.spec_from_file_location("core.database", db_mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        Database = mod.Database
        db = await Database.get_db()
        if channel_id:
            cursor = await db.execute(
                "SELECT guild_id, channel_id, author_id, content, timestamp "
                "FROM message_log WHERE channel_id = ? ORDER BY timestamp DESC LIMIT ?",
                (str(channel_id), limit),
            )
        else:
            cursor = await db.execute(
                "SELECT guild_id, channel_id, author_id, content, timestamp "
                "FROM message_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            {
                "guild_id": r[0],
                "channel_id": r[1],
                "author_id": r[2],
                "content": r[3],
                "timestamp": r[4],
            }
            for r in rows
        ]

    try:
        future = asyncio.run_coroutine_threadsafe(_fetch(), bot_instance.loop)
        messages = future.result(timeout=15)
        return jsonify({"messages": messages})
    except Exception as e:
        return jsonify({"messages": [], "error": str(e)})


# ──────────────────────────────────────────────────────────────────────────────
# API — Server Cloner
# ──────────────────────────────────────────────────────────────────────────────

cloner_status = {"running": False, "progress": "", "log": [], "done": False, "error": None}


def _log_clone(msg):
    cloner_status["log"].append(msg)
    if len(cloner_status["log"]) > 200:
        cloner_status["log"] = cloner_status["log"][-200:]
    cloner_status["progress"] = msg


@app.route("/api/cloner", methods=["POST"])
def api_cloner_start():
    if bot_instance is None:
        return jsonify({"ok": False, "error": "Bot is not running."})
    if cloner_status["running"]:
        return jsonify({"ok": False, "error": "Clone already in progress."})

    data = request.get_json(silent=True) or {}
    source_id = data.get("source", "").strip()
    options = data.get("options", {})

    if not source_id:
        return jsonify({"ok": False, "error": "Source server ID required."})

    cloner_status.update({"running": True, "progress": "Starting...", "log": [], "done": False, "error": None})

    async def _clone():
        try:
            source = bot_instance.get_guild(int(source_id))
            if source is None:
                source = await bot_instance.fetch_guild(int(source_id))

            _log_clone(f"Source: {source.name} ({source.id})")

            new_guild = await bot_instance.create_guild(name=f"Clone of {source.name}")
            await asyncio.sleep(2)
            _log_clone(f"Created target: {new_guild.name} ({new_guild.id})")

            if options.get("roles", True):
                roles = sorted(source.roles, key=lambda r: r.position, reverse=True)
                role_map = {}
                for role in roles:
                    if role.is_default():
                        role_map[role.id] = new_guild.default_role
                        continue
                    try:
                        new_role = await new_guild.create_role(
                            name=role.name, permissions=role.permissions,
                            color=role.color, hoist=role.hoist, mentionable=role.mentionable
                        )
                        role_map[role.id] = new_role
                        _log_clone(f"  Role: {role.name}")
                        await asyncio.sleep(0.5)
                    except Exception as e:
                        _log_clone(f"  Role failed: {role.name} - {e}")

            if options.get("channels", True):
                categories = sorted(source.categories, key=lambda c: c.position)
                for cat in categories:
                    try:
                        overwrites = {}
                        for target, perms in cat.permission_overwrites.items():
                            if target.id in role_map:
                                overwrites[role_map[target.id]] = perms
                            elif target.id == source.default_role.id and new_guild.default_role in role_map.values():
                                overwrites[new_guild.default_role] = perms
                        new_cat = await new_guild.create_category(name=cat.name, overwrites=overwrites)
                        _log_clone(f"  Category: {cat.name}")
                        await asyncio.sleep(0.3)

                        for ch in sorted(cat.channels, key=lambda c: c.position):
                            try:
                                ch_overwrites = {}
                                for target, perms in ch.permission_overwrites.items():
                                    if target.id in role_map:
                                        ch_overwrites[role_map[target.id]] = perms
                                    elif target.id == source.default_role.id:
                                        ch_overwrites[new_guild.default_role] = perms

                                if isinstance(ch, discord.TextChannel):
                                    await new_guild.create_text_channel(
                                        name=ch.name, category=new_cat,
                                        topic=ch.topic or "", nsfw=ch.is_nsfw(),
                                        slowmode_delay=ch.slowmode_delay,
                                        overwrites=ch_overwrites
                                    )
                                elif isinstance(ch, discord.VoiceChannel):
                                    await new_guild.create_voice_channel(
                                        name=ch.name, category=new_cat,
                                        bitrate=ch.bitrate, user_limit=ch.user_limit,
                                        overwrites=ch_overwrites
                                    )
                                _log_clone(f"    Channel: {ch.name}")
                                await asyncio.sleep(0.3)
                            except Exception as e:
                                _log_clone(f"    Channel failed: {ch.name} - {e}")
                    except Exception as e:
                        _log_clone(f"  Category failed: {cat.name} - {e}")

            if options.get("emojis", False):
                for emoji in source.emojis:
                    try:
                        emoji_bytes = await emoji.read()
                        await new_guild.create_custom_emoji(name=emoji.name, image=emoji_bytes)
                        _log_clone(f"  Emoji: {emoji.name}")
                        await asyncio.sleep(1)
                    except Exception as e:
                        _log_clone(f"  Emoji failed: {emoji.name} - {e}")

            _log_clone(f"Clone complete! New server: {new_guild.name} ({new_guild.id})")
            cloner_status["done"] = True

        except Exception as e:
            _log_clone(f"Error: {e}")
            cloner_status["error"] = str(e)
        finally:
            cloner_status["running"] = False

    try:
        asyncio.run_coroutine_threadsafe(_clone(), bot_instance.loop)
        return jsonify({"ok": True})
    except Exception as e:
        cloner_status["running"] = False
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/cloner/status")
def api_cloner_status():
    return jsonify(cloner_status)


# ──────────────────────────────────────────────────────────────────────────────
# API — Dump
# ──────────────────────────────────────────────────────────────────────────────

dump_status = {"running": False, "progress": "", "command": "", "done": False, "error": None, "result": ""}


@app.route("/api/dump", methods=["POST"])
def api_dump_start():
    if bot_instance is None:
        return jsonify({"ok": False, "error": "Bot is not running."})
    if dump_status["running"]:
        return jsonify({"ok": False, "error": "Dump already in progress."})

    data = request.get_json(silent=True) or {}
    command = data.get("command", "").strip()
    args = data.get("args", "").strip()

    if not command:
        return jsonify({"ok": False, "error": "No command specified."})

    valid = ["dumpall", "dumpimages", "dumpvideos", "dumpaudio", "dumptext",
             "dumpattachments", "dumpemojis", "dumpstickers", "dumpavatars", "dumpchannels"]
    if command not in valid:
        return jsonify({"ok": False, "error": f"Invalid dump command: {command}"})

    dump_status.update({"running": True, "progress": "Starting...", "command": command, "done": False, "error": None, "result": ""})

    async def _run_dump():
        try:
            channel = None
            if args:
                try:
                    channel = bot_instance.get_channel(int(args))
                except (ValueError, TypeError):
                    pass
            if channel is None:
                for g in bot_instance.guilds:
                    for ch in g.text_channels:
                        try:
                            await ch.send(".")
                            channel = ch
                            break
                        except Exception:
                            continue
                    if channel:
                        break

            if channel is None:
                dump_status["error"] = "No accessible channel found."
                dump_status["running"] = False
                dump_status["done"] = True
                return

            dump_status["progress"] = f"Running {command}..."
            content = f"{BOT.PREFIX}{command}"
            if args:
                content += f" {args}"
            params = discord.http.handle_message_parameters(content=content)
            msg = await bot_instance.http.send_message(channel.id, params=params)
            fetched = await channel.fetch_message(int(msg["id"]))
            await bot_instance.process_commands(fetched)

            dump_status["progress"] = f"{command} finished."
            dump_status["result"] = f"Command sent in #{channel.name}"
            dump_status["done"] = True

        except Exception as e:
            dump_status["error"] = str(e)
            dump_status["progress"] = f"Error: {e}"
        finally:
            dump_status["running"] = False

    try:
        asyncio.run_coroutine_threadsafe(_run_dump(), bot_instance.loop)
        return jsonify({"ok": True})
    except Exception as e:
        dump_status["running"] = False
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/dump/status")
def api_dump_status():
    return jsonify(dump_status)
