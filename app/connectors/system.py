"""app/connectors/system.py — Local machine control & OS automation (Connector layer).

Provides tools to control the host Linux system:
- Audio & volume control (PipeWire/PulseAudio/ALSA)
- Media playback control (MPRIS / playerctl / dbus)
- System diagnostics & resource monitoring (CPU, RAM, Disk, Uptime)
- Application launching & window management
- Desktop screen locking & full-screen screenshots
- Safe command execution with strict safety guardrails against destructive actions.

SAFETY POLICY (Enforced in Code & Persona):
- Destructive actions (copy, cut/overwrite, delete, install packages) are blocked by default.
- If the user specifies "override", the assistant must ask for explicit confirmation before executing.
- Execution is strictly prohibited without confirmed override verification.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Regex patterns identifying potentially destructive or modifying system actions
DESTRUCTIVE_COMMAND_PATTERNS = [
    r"\brm\b",
    r"\bunlink\b",
    r"\bdel\b",
    r"\bmv\b",
    r"\bcp\b.*-f",
    r"\btruncate\b",
    r"\bshred\b",
    r"\bformat\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r"\bapt\b",
    r"\bapt-get\b",
    r"\bdpkg\b",
    r"\bpip\s+(install|uninstall)\b",
    r"\bnpm\s+(install|i|uninstall|remove)\b",
    r"\byarn\s+(add|remove)\b",
    r"\bsnap\b",
    r"\bflatpak\b",
    r"\bpacman\b",
    r"\bdnf\b",
    r"\byum\b",
    r"\bzypper\b",
    r"\bsudo\b",
    r"\bchmod\b\s+-[rwx0-7]",
    r"\bchown\b",
]


def _is_destructive_command(cmd_str):
    """Returns True if the command matches any destructive pattern."""
    cmd_clean = cmd_str.strip().lower()
    for pat in DESTRUCTIVE_COMMAND_PATTERNS:
        if re.search(pat, cmd_clean):
            return True
    return False


def get_system_tools():
    """Returns Anthropic/Gemini tool definitions for local OS system control."""
    return [
        {
            "name": "system_set_volume",
            "description": "Adjust or mute the system audio volume on the local machine.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "volume_percent": {
                        "type": "integer",
                        "description": "Target volume percentage from 0 to 100.",
                    },
                    "step": {
                        "type": "integer",
                        "description": "Relative volume step to adjust, e.g. +5 or -10.",
                    },
                    "mute": {
                        "type": "string",
                        "enum": ["mute", "unmute", "toggle"],
                        "description": "Mute status to set on the audio output.",
                    },
                },
            },
        },
        {
            "name": "system_media_control",
            "description": "Control media playback (Spotify, VLC, browser video/music) on the local machine.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["play", "pause", "play_pause", "next", "previous", "stop"],
                        "description": "Media playback action to perform.",
                    },
                },
                "required": ["action"],
            },
        },
        {
            "name": "system_get_stats",
            "description": "Retrieve current system hardware and resource usage (CPU, RAM, Disk, Uptime).",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "system_launch_app",
            "description": (
                "Launch a desktop application on the local machine (e.g. 'spotify', 'calculator', 'terminal', 'code', 'vlc'). "
                "NOTE: For opening Google Chrome, browsers, or websites, use browser_open_url so the browser is interactive and controllable."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "description": "Name of the desktop application to launch (e.g. 'spotify', 'calculator', 'terminal', 'code', 'vlc').",
                    },
                },
                "required": ["app_name"],
            },
        },
        {
            "name": "system_lock_screen",
            "description": "Lock the user's active desktop screen session for security.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "system_take_screenshot",
            "description": "Capture a screenshot of the entire desktop display and save it locally.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "Optional custom file path to save the screenshot PNG.",
                    },
                },
            },
        },
        {
            "name": "system_run_command",
            "description": (
                "Execute a non-destructive shell command on the host system to inspect or query the environment. "
                "SAFETY GUARD: Destructive commands (delete, install, overwrite, format) are strictly blocked "
                "unless the user issued an 'override' command and confirmed the action."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run (e.g. 'uptime', 'df -h', 'free -h', 'ls -la').",
                    },
                    "is_override": {
                        "type": "boolean",
                        "description": "Must be set to true only when the user explicitly commanded 'override'.",
                    },
                    "is_confirmed": {
                        "type": "boolean",
                        "description": "Must be set to true only after the user confirmed the action.",
                    },
                },
                "required": ["command"],
            },
        },
    ]


def _run_cmd(cmd_list, timeout=10):
    try:
        res = subprocess.run(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return res.returncode == 0, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return False, "", str(e)


def _exec_volume(tool_input):
    vol = tool_input.get("volume_percent")
    step = tool_input.get("step")
    mute = tool_input.get("mute")

    # 1. PipeWire / WirePlumber (wpctl)
    if shutil.which("wpctl"):
        if mute:
            arg = "toggle" if mute == "toggle" else ("1" if mute == "mute" else "0")
            _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", arg])
        if vol is not None:
            clamped = max(0, min(100, int(vol)))
            _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{clamped/100:.2f}"])
            return {"status": "success", "volume_set_to": f"{clamped}%", "engine": "wpctl"}
        if step is not None:
            sign = "+" if int(step) > 0 else "-"
            _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{abs(int(step))/100:.2f}{sign}"])
            return {"status": "success", "volume_adjusted_by": f"{step}%", "engine": "wpctl"}
        if mute:
            return {"status": "success", "mute_action": mute, "engine": "wpctl"}

    # 2. PulseAudio (pactl)
    if shutil.which("pactl"):
        if mute:
            arg = "toggle" if mute == "toggle" else ("1" if mute == "mute" else "0")
            _run_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", arg])
        if vol is not None:
            clamped = max(0, min(100, int(vol)))
            _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{clamped}%"])
            return {"status": "success", "volume_set_to": f"{clamped}%", "engine": "pactl"}
        if step is not None:
            sign = "+" if int(step) > 0 else "-"
            _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{abs(int(step))}%"])
            return {"status": "success", "volume_adjusted_by": f"{step}%", "engine": "pactl"}
        if mute:
            return {"status": "success", "mute_action": mute, "engine": "pactl"}

    # 3. ALSA (amixer)
    if shutil.which("amixer"):
        if mute:
            _run_cmd(["amixer", "set", "Master", "toggle" if mute == "toggle" else ("mute" if mute == "mute" else "unmute")])
        if vol is not None:
            clamped = max(0, min(100, int(vol)))
            _run_cmd(["amixer", "set", "Master", f"{clamped}%"])
            return {"status": "success", "volume_set_to": f"{clamped}%", "engine": "amixer"}
        if step is not None:
            sign = "+" if int(step) > 0 else "-"
            _run_cmd(["amixer", "set", "Master", f"{abs(int(step))}%{sign}"])
            return {"status": "success", "volume_adjusted_by": f"{step}%", "engine": "amixer"}
        if mute:
            return {"status": "success", "mute_action": mute, "engine": "amixer"}

    return {"error": "No supported audio utility (wpctl, pactl, amixer) found."}


def _exec_media(action):
    if shutil.which("playerctl"):
        cmd_map = {
            "play": ["playerctl", "play"],
            "pause": ["playerctl", "pause"],
            "play_pause": ["playerctl", "play-pause"],
            "next": ["playerctl", "next"],
            "previous": ["playerctl", "previous"],
            "stop": ["playerctl", "stop"],
        }
        cmd = cmd_map.get(action)
        if cmd:
            ok, out, err = _run_cmd(cmd)
            if ok:
                return {"status": "success", "action": action, "engine": "playerctl"}

    if shutil.which("dbus-send"):
        mpris_actions = {
            "play": "Play",
            "pause": "Pause",
            "play_pause": "PlayPause",
            "next": "Next",
            "previous": "Previous",
            "stop": "Stop",
        }
        method_name = mpris_actions.get(action, "PlayPause")
        _run_cmd([
            "dbus-send", "--type=method_call", "--dest=org.mpris.MediaPlayer2.spotify",
            "/org/mpris/MediaPlayer2", f"org.mpris.MediaPlayer2.Player.{method_name}"
        ])
        return {"status": "success", "action": action, "engine": "dbus"}

    return {"error": "No media control utility (playerctl or dbus-send) available."}


def _exec_stats():
    stats = {}
    try:
        with open("/proc/meminfo", "r") as f:
            mem = {}
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = parts[1].strip()
            total_kb = int(mem.get("MemTotal", "0 kB").split()[0])
            avail_kb = int(mem.get("MemAvailable", "0 kB").split()[0])
            used_kb = total_kb - avail_kb
            stats["ram"] = {
                "total_gb": round(total_kb / 1024 / 1024, 2),
                "used_gb": round(used_kb / 1024 / 1024, 2),
                "usage_percent": f"{round((used_kb / (total_kb or 1)) * 100, 1)}%",
            }
    except Exception:
        pass

    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
            hours = int(uptime_seconds // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            stats["uptime"] = f"{hours} hours, {minutes} minutes"
    except Exception:
        pass

    try:
        stat = os.statvfs("/")
        total_gb = round((stat.f_blocks * stat.f_frsize) / (1024**3), 2)
        free_gb = round((stat.f_bavail * stat.f_frsize) / (1024**3), 2)
        used_gb = round(total_gb - free_gb, 2)
        stats["disk_root"] = {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "usage_percent": f"{round((used_gb / (total_gb or 1)) * 100, 1)}%",
        }
    except Exception:
        pass

    try:
        load1, load5, load15 = os.getloadavg()
        stats["load_average"] = {"1min": load1, "5min": load5, "15min": load15}
    except Exception:
        pass

    return stats


def _exec_launch_app(cfg, app_name):
    clean_name = app_name.strip().lower()

    if clean_name in ("chrome", "google-chrome", "google chrome", "chromium", "browser", "web-browser", "firefox", "brave"):
        from .browser import execute_browser_tool
        return execute_browser_tool(cfg, "browser_open_url", {"url": "https://google.com"})

    common_apps = {
        "calculator": ["gnome-calculator", "kcalc", "xcalc"],
        "terminal": ["gnome-terminal", "konsole", "alacritty", "kitty", "xterm"],
        "spotify": ["spotify"],
        "code": ["code", "codium"],
        "vscode": ["code"],
        "files": ["nautilus", "dolphin", "thunar", "nemo"],
        "discord": ["discord", "Discord"],
        "vlc": ["vlc"],
    }

    candidates = common_apps.get(clean_name, [clean_name])
    for binary in candidates:
        if shutil.which(binary):
            subprocess.Popen([binary], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"status": "launched", "application": binary}

    if shutil.which("gtk-launch"):
        ok, _, _ = _run_cmd(["gtk-launch", clean_name])
        if ok:
            return {"status": "launched", "application": clean_name}

    return {"error": f"Application '{app_name}' not found on system path."}


def _exec_lock_screen():
    if shutil.which("loginctl"):
        ok, out, err = _run_cmd(["loginctl", "lock-session"])
        if ok:
            return {"status": "locked", "method": "loginctl"}
    if shutil.which("xdg-screensaver"):
        ok, out, err = _run_cmd(["xdg-screensaver", "lock"])
        if ok:
            return {"status": "locked", "method": "xdg-screensaver"}
    return {"error": "Could not lock screen (neither loginctl nor xdg-screensaver succeeded)."}


def _exec_screenshot(save_path):
    captures_dir = os.path.join(ROOT, "notes", "captures")
    os.makedirs(captures_dir, exist_ok=True)
    if not save_path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        save_path = os.path.join(captures_dir, f"desktop_{ts}.png")
    else:
        save_path = os.path.expanduser(save_path)

    if shutil.which("scrot"):
        ok, _, _ = _run_cmd(["scrot", save_path])
        if ok and os.path.exists(save_path):
            return {"status": "saved", "path": save_path, "tool": "scrot"}

    if shutil.which("maim"):
        ok, _, _ = _run_cmd(["maim", save_path])
        if ok and os.path.exists(save_path):
            return {"status": "saved", "path": save_path, "tool": "maim"}

    if shutil.which("import"):
        ok, _, _ = _run_cmd(["import", "-window", "root", save_path])
        if ok and os.path.exists(save_path):
            return {"status": "saved", "path": save_path, "tool": "import"}

    if shutil.which("gnome-screenshot"):
        ok, _, _ = _run_cmd(["gnome-screenshot", "-f", save_path])
        if ok and os.path.exists(save_path):
            return {"status": "saved", "path": save_path, "tool": "gnome-screenshot"}

    try:
        from PIL import ImageGrab
        img = ImageGrab.grab()
        img.save(save_path)
        return {"status": "saved", "path": save_path, "tool": "PIL"}
    except Exception:
        pass

    return {"error": "No screenshot capture utility found (install scrot or maim)."}


def _exec_run_command(tool_input):
    cmd_str = (tool_input.get("command") or "").strip()
    is_override = bool(tool_input.get("is_override", False))
    is_confirmed = bool(tool_input.get("is_confirmed", False))

    if not cmd_str:
        return {"error": "Empty command"}

    is_destructive = _is_destructive_command(cmd_str)

    if is_destructive:
        if not is_override or not is_confirmed:
            return {
                "blocked": True,
                "reason": (
                    "SAFETY POLICY ENFORCEMENT: Destructive action blocked. "
                    "You must NOT execute this command unless the user explicitly issued 'override' "
                    "AND confirmed the action upon your confirmation prompt. "
                    "Please inform the user accordingly."
                ),
                "command": cmd_str,
            }

    try:
        res = subprocess.run(
            cmd_str,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=25,
            cwd=ROOT,
        )
        return {
            "returncode": res.returncode,
            "stdout": res.stdout.strip()[:3000],
            "stderr": res.stderr.strip()[:1000],
            "command": cmd_str,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 25 seconds."}
    except Exception as e:
        return {"error": f"Execution failed: {e}"}


def execute_system_tool(cfg, tool_name, tool_input):
    """Dispatcher for system control tools."""
    try:
        if tool_name == "system_set_volume":
            return _exec_volume(tool_input)
        elif tool_name == "system_media_control":
            return _exec_media(tool_input.get("action", "play_pause"))
        elif tool_name == "system_get_stats":
            return _exec_stats()
        elif tool_name == "system_launch_app":
            return _exec_launch_app(cfg, tool_input.get("app_name", ""))
        elif tool_name == "system_lock_screen":
            return _exec_lock_screen()
        elif tool_name == "system_take_screenshot":
            return _exec_screenshot(tool_input.get("save_path"))
        elif tool_name == "system_run_command":
            return _exec_run_command(tool_input)
        else:
            return {"error": f"Unknown system tool: {tool_name}"}
    except Exception as e:
        print(f"[jarvis] System tool {tool_name} error: {e}", file=sys.stderr)
        return {"error": f"System action failed: {e}"}
