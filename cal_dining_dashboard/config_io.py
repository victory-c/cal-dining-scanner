"""Read/write user config.yaml, keywords.txt, and .env for the dashboard."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml

import cal_dining_scanner as scanner

DEFAULT_LOCATIONS = ["Crossroads", "Foothill", "Clark Kerr", "Cafe 3"]
DEFAULT_MEALS = ["Breakfast", "Lunch", "Dinner"]
DEFAULT_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def config_dir() -> Path:
    return scanner.user_config_dir()


def config_path() -> Path:
    return config_dir() / "config.yaml"


def env_path() -> Path:
    return config_dir() / ".env"


def keywords_path() -> Path:
    return config_dir() / "keywords.txt"


def ensure_config_dir() -> Path:
    target = config_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_form_state() -> dict[str, Any]:
    """Return the current form values (or defaults) for rendering the dashboard."""
    cfg: dict[str, Any] = {}
    if config_path().exists():
        with open(config_path(), encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    keywords: list[str] = []
    if keywords_path().exists():
        keywords = [
            line.strip()
            for line in keywords_path().read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    elif isinstance(cfg.get("keywords"), list):
        keywords = [str(k).strip() for k in cfg["keywords"] if str(k).strip()]

    schedule = cfg.get("schedule") or {}
    smtp = cfg.get("smtp") or {}

    return {
        "email": cfg.get("email", ""),
        "has_password": bool(_read_env_value("GMAIL_APP_PASSWORD")),
        "keywords": "\n".join(keywords),
        "locations": cfg.get("locations") or ["Crossroads", "Foothill", "Clark Kerr"],
        "meals": cfg.get("meals") or list(DEFAULT_MEALS),
        "timezone": schedule.get("timezone", "America/Los_Angeles"),
        "times": ", ".join(schedule.get("times", ["07:00"])),
        "days": schedule.get("days", list(DEFAULT_DAYS)),
        "smtp_server": smtp.get("server", "smtp.gmail.com"),
        "smtp_port": smtp.get("port", 587),
        "all_locations": DEFAULT_LOCATIONS,
        "all_meals": DEFAULT_MEALS,
        "all_days": DEFAULT_DAYS,
    }


def _read_env_value(key: str) -> str:
    """Return the value of `key` from the user .env file, or empty string."""
    if not env_path().exists():
        return ""
    for line in env_path().read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def read_password() -> str:
    return _read_env_value("GMAIL_APP_PASSWORD")


def save_settings(form: dict[str, Any]) -> Path:
    """Persist the form values. Returns the config.yaml path."""
    ensure_config_dir()

    keywords = _parse_keywords(form.get("keywords", ""))
    keywords_path().write_text("\n".join(keywords) + "\n", encoding="utf-8")

    times = _parse_csv(form.get("times", "07:00"))
    locations = form.get("locations") or []
    meals = form.get("meals") or []
    days = form.get("days") or list(DEFAULT_DAYS)

    config: dict[str, Any] = {
        "email": form.get("email", "").strip(),
        "locations": locations,
        "meals": meals,
        "keywords_file": "keywords.txt",
        "schedule": {
            "timezone": form.get("timezone", "America/Los_Angeles").strip() or "America/Los_Angeles",
            "times": times,
            "days": days,
            "window_minutes": 20,
        },
        "smtp": {
            "server": (form.get("smtp_server") or "smtp.gmail.com").strip(),
            "port": int(form.get("smtp_port") or 587),
            "sender_email": form.get("email", "").strip(),
        },
    }

    with open(config_path(), "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    password = (form.get("gmail_app_password") or "").strip()
    if password:
        _write_env({"GMAIL_APP_PASSWORD": password})

    return config_path()


def _parse_keywords(value: str) -> list[str]:
    lines = [line.strip() for line in value.replace(",", "\n").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def _write_env(updates: dict[str, str]) -> None:
    """Merge `updates` into the .env file, preserving existing keys. Strict 0600 perms."""
    existing: dict[str, str] = {}
    if env_path().exists():
        for line in env_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip().strip('"').strip("'")

    existing.update(updates)
    body = "\n".join(f'{k}="{v}"' for k, v in existing.items()) + "\n"
    env_path().write_text(body, encoding="utf-8")
    try:
        os.chmod(env_path(), stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def env_for_subprocess() -> dict[str, str]:
    """Build an env dict to invoke the scanner with, merging .env on top of os.environ."""
    env = dict(os.environ)
    if env_path().exists():
        for line in env_path().read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env
