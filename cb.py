
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Callable, Iterator

CONFIG_FILE = Path(__file__).with_name("config.yml")
DATA_FILE = Path(__file__).with_name("data.json")


def default_log_path(roaming: Path) -> Path:
    profiles = roaming / "ModrinthApp" / "profiles"
    profile_logs = list(profiles.glob("*/logs/latest.log")) if profiles.is_dir() else []
    if profile_logs:
        return max(profile_logs, key=lambda path: path.stat().st_mtime)
    return roaming / ".minecraft" / "logs" / "latest.log"


def load_config(path: Path = CONFIG_FILE) -> dict:
    import yaml

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        config = {
            "latest.log": str(default_log_path(roaming)),
            "typing delay": 0.01,
            "minecraft title": "Minecraft",
            "whisper format": "/w {player} {message}",
        }
        path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        return config

    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_data(path: Path = DATA_FILE) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"blacklist": []}
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return data

    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("blacklist"), list):
        data["blacklist"] = []
    data["blacklist"] = [name for name in data["blacklist"] if isinstance(name, str)]
    return data

LOG_PREFIX_PATTERN = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]\s+\[[^\]]+\]:\s*")
CHAT_WRAPPER_PATTERN = re.compile(r"^(?:\[(?:System|CHAT)\]\s*)+", re.IGNORECASE)
RESOURCE_EVENT_PATTERN = re.compile(
    r"(?=.*\b(?:atlas(?:es)?|sprites?|textures?|resources?)\b)"
    r"(?=.*\b(?:creat(?:e|es|ed|ing|ion)|stitch(?:es|ed|ing)?|load(?:s|ed|ing)?|reload(?:s|ed|ing)?|bak(?:e|es|ed|ing))\b).+",
    re.IGNORECASE,
)
RANK_CHAT_PATTERN = re.compile(
    r"^(?:\[[^\]]+\]\s*)?(?P<group>[A-Za-z][A-Za-z0-9_-]*)\s*\|\s*"
    r"(?P<player>[^|»]{1,32}?)\s*»\s*(?P<message>.+)$"
)
ROLE_CHAT_PATTERN = re.compile(
    r"^(?P<group>Member|Owner|Admin|Moderator|Mod|VIP|MVP|Helper|Staff)\s+"
    r"(?P<player>[A-Za-z0-9_]{1,16})(?::\s*|\s+)(?P<message>\S.*)$",
    re.IGNORECASE,
)
ANGLE_CHAT_PATTERN = re.compile(r"^(?:\[[^\]]+\]\s*)*<(?P<player>[^<>]{1,32})>\s*(?P<message>.+)$")
TAGGED_CHAT_PATTERN = re.compile(
    r"^(?:\[[^\]]+\]\s*)?(?P<player>[\w]{1,32})\s*(?::|»|>)\s*(?P<message>.+)$",
    re.UNICODE,
)
NON_PLAYER_CHAT_PATTERN = re.compile(r"^(?:TELEPORT|Minehut)\s*(?:»|\||:)\s*", re.IGNORECASE)


def parse_log_line(line) -> dict[str, str | None] | None:
    text = LOG_PREFIX_PATTERN.sub("", line.strip(), count=1)
    if RESOURCE_EVENT_PATTERN.search(text):
        return {"kind": "event", "group": None, "player": None, "message": text}

    text = CHAT_WRAPPER_PATTERN.sub("", text, count=1).strip()
    if NON_PLAYER_CHAT_PATTERN.match(text):
        return {"kind": "system", "group": None, "player": None, "message": text}

    for pattern in (RANK_CHAT_PATTERN, ROLE_CHAT_PATTERN, ANGLE_CHAT_PATTERN, TAGGED_CHAT_PATTERN):
        match = pattern.match(text)
        if match:
            values = match.groupdict()
            return {
                "kind": "chat",
                "group": values.get("group") or "Unknown",
                "player": values["player"].strip(),
                "message": values["message"].strip(),
            }

    return {"kind": "system", "group": None, "player": None, "message": text} if text else None


def detect_message(message: str | dict[str, str | None] | None, message_type: str = "any") -> bool:
    parsed = parse_log_line(message) if isinstance(message, str) else message
    if parsed is None or parsed.get("kind") == "tick":
        return False
    return message_type.casefold() == "any" or parsed.get("kind") == message_type.casefold()


class MinecraftChatBot:
    """Importable interface for monitoring Minecraft chat and sending messages."""

    parse_log_line = staticmethod(parse_log_line)
    detect_message = staticmethod(detect_message)

    def __init__(self, config_file: Path = CONFIG_FILE, data_file: Path = DATA_FILE):
        self.config = load_config(Path(config_file))
        self.data_file = Path(data_file)
        self.log_path = Path(self.config["latest.log"])
        self.window_title = str(self.config["minecraft title"])
        self.typing_delay = float(self.config["typing delay"])
        self.whisper_format = str(self.config.get("whisper format", "/w {player} {message}"))
        self.blacklist = {name.casefold() for name in load_data(self.data_file)["blacklist"]}
        self._message_handlers: list[tuple[str, Callable[[dict[str, str | None]], None]]] = []

    def when_message_in_log(self, callback: Callable[[dict[str, str | None]], None], message_type: str = "any") -> None:
        message_type = message_type.casefold()
        if message_type not in {"any", "chat", "system", "event"}:
            raise ValueError("message_type must be 'any', 'chat', 'system', or 'event'")
        self._message_handlers.append((message_type, callback))

    def _notify_message_handlers(self, message: dict[str, str | None]) -> None:
        if message["kind"] == "tick":
            return
        for message_type, callback in self._message_handlers:
            if self.detect_message(message, message_type):
                callback(message)

    def add_to_blacklist(self, player: str) -> bool:
        player = player.strip()
        if not player or player.casefold() in self.blacklist:
            return False
        data = load_data(self.data_file)
        data["blacklist"].append(player)
        self.data_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        self.blacklist.add(player.casefold())
        return True

    def monitor_log(self) -> Iterator[dict[str, str | None]]:
        try:
            log_file = self.log_path.open(encoding="utf-8", errors="replace")
        except OSError as exception:
            print(f"Could not open latest.log at {self.log_path}: {exception}")
            return

        with log_file:
            log_file.seek(0, 2)
            print("Following chat. Press Ctrl+C to stop.")
            last_chat = None
            last_chat_at = 0.0
            while True:
                line = log_file.readline()
                if not line:
                    try:
                        if self.log_path.stat().st_size < log_file.tell():
                            log_file.seek(0)
                    except OSError:
                        pass
                    time.sleep(0.2)
                    yield {"kind": "tick", "group": None, "player": None, "message": None}
                    continue

                message = parse_log_line(line)
                if message and message["kind"] == "chat":
                    signature = (message["player"], message["message"])
                    now = time.monotonic()
                    if signature == last_chat and now - last_chat_at < 0.5:
                        continue
                    last_chat, last_chat_at = signature, now
                if message:
                    self._notify_message_handlers(message)
                    yield message

    def send_message(self, message: str) -> None:
        import pyautogui
        import pygetwindow

        windows = pygetwindow.getWindowsWithTitle(self.window_title)
        if not windows:
            print(f"Minecraft window not found: {self.window_title}")
            return
        window = windows[0]
        was_minimized = window.isMinimized
        if was_minimized:
            window.restore()
        window.maximize()
        window.activate()
        if was_minimized:
            pyautogui.press("esc")
            time.sleep(1)
        pyautogui.press("t")
        pyautogui.typewrite(message, interval=self.typing_delay)
        pyautogui.press("enter")

    def whisper(self, player: str, message: str) -> None:
        self.send_message(self.whisper_format.format(player=player, message=message))

    def display(self, message: dict[str, str | None]) -> None:
        if message["kind"] == "chat":
            print(f"[CHAT] {message['group']} | {message['player']}: {message['message']}", flush=True)
        elif message["kind"] == "event":
            print(f"[RESOURCE] {message['message']}", flush=True)

    def _handle_chat(self, message: dict[str, str | None]) -> None:
        player = (message["player"] or "Unknown").strip()
        player_key = player.casefold()
        chat = (message["message"] or "").strip()
        normalized = chat.casefold()

        if player_key == "th4creator" and (normalized == "!blacklist" or normalized.startswith("!blacklist ")):
            parts = chat.split(maxsplit=1)
            if len(parts) == 1:
                self.whisper(player, "Usage: !blacklist <player>")
            else:
                target = parts[1].strip()
                try:
                    added = self.add_to_blacklist(target)
                    reply = f"{target} added to the blacklist." if added else f"{target} is already blacklisted."
                    self.whisper(player, reply)
                except OSError as exception:
                    print(f"Could not save blacklist: {exception}")
            return

    def run(self, display_messages: bool = False) -> None:
        for message in self.monitor_log():
            if display_messages:
                self.display(message)
            if message["kind"] == "chat":
                self._handle_chat(message)


def main() -> None:
    bot = MinecraftChatBot()
    if sys.argv[1:] == ["monitor", "latestlog"]:
        for message in bot.monitor_log():
            bot.display(message)
        return
    bot.run(display_messages=True)


__all__ = ["MinecraftChatBot", "parse_log_line", "detect_message", "load_config", "load_data"]


if __name__ == "__main__":
    main()

