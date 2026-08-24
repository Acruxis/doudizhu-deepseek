"""Application settings persisted beside the project or executable.

Stores the selected difficulty and DeepSeek API credentials.
During development the config lives at the repository root. A PyInstaller
single-file build stores it beside the executable.
"""
import configparser
import os
import sys
from pathlib import Path

APP_NAME = "doudizhu"

_DEFAULT = {
    "difficulty": "hard",          # easy / medium / hard / ai
    "api_key": "apikey",           # any OpenAI-compatible API key
    "base_url": "http://192.168.76.43:8888/v1",   # OpenAI-compatible endpoint
    "model": "deepseek-v4-flash",
    "player_name": "玩家",
}


def _config_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pyproject.toml").is_file():
            return str(parent)
    return os.getcwd()


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
