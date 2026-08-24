"""Application settings persisted to a user-level INI file.

Stores the selected AI difficulty, and (optional) DeepSeek API credentials.
Config lives at `~/.doudizhu/config.ini` so it survives across runs and works
with a PyInstaller single-file exe (no bundled files needed).
"""
import configparser
import os

APP_NAME = "doudizhu"

_DEFAULT = {
    "difficulty": "medium",        # easy / medium / hard
    "ai_enabled": "false",         # use an LLM to back the hard AI
    "api_key": "apikey",           # any OpenAI-compatible API key
    "base_url": "http://192.168.76.43:8888/v1",   # OpenAI-compatible endpoint
    "model": "deepseek-v4-flash",
    "player_name": "玩家",
}


def _config_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".doudizhu")
    return d


def config_path():
    return os.path.join(_config_dir(), "config.ini")


def load_config():
    """Return a dict of effective settings (defaults merged with file values)."""
    cfg = configparser.ConfigParser()
    path = config_path()
    if os.path.exists(path):
        cfg.read(path, encoding="utf-8")
    section = cfg["game"] if cfg.has_section("game") else cfg["DEFAULT"]
    values = dict(_DEFAULT)
    for k in values:
        if section.get(k):
            values[k] = section[k]
    return values


def save_config(settings):
    """Persist `settings` dict to the user config file."""
    cfg = configparser.ConfigParser()
    if not cfg.has_section("game"):
        cfg.add_section("game")
    for k, v in settings.items():
        cfg.set("game", k, str(v))
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)


def save_defaults(force=False):
    """Write default config if none exists (used on first launch if needed)."""
    if force or not os.path.exists(config_path()):
        save_config(dict(_DEFAULT))
