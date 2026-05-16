#!/usr/bin/env python3
"""
Cal Dining Scanner
Scrapes UC Berkeley dining menus and sends email alerts when foods matching
your keywords are being served.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import smtplib
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
import yaml
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

MENU_URL = "https://dining.berkeley.edu/menus/"
PROJECT_ROOT = Path(__file__).resolve().parent


def user_config_dir() -> Path:
    """Per-OS config dir for pipx/uvx installs. ~/.config/cal-dining-scanner on Linux,
    ~/Library/Application Support/cal-dining-scanner on macOS, %APPDATA%/cal-dining-scanner on Windows."""
    import sys
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "cal-dining-scanner"


def default_config_path() -> Path:
    """Prefer ./config.yaml (repo-local dev), else fall back to user config dir."""
    local = PROJECT_ROOT / "config.yaml"
    if local.exists():
        return local
    return user_config_dir() / "config.yaml"


def default_state_path(config_path: Path) -> Path:
    """State lives next to the config file."""
    return config_path.parent / ".cal_dining_scanner_state.json"


CONFIG_PATH = default_config_path()
STATE_PATH = default_state_path(CONFIG_PATH)

DEFAULT_CONFIG: dict[str, Any] = {
    "locations": ["Crossroads", "Foothill", "Clark Kerr"],
    "meals": ["Breakfast", "Lunch", "Dinner"],
    "keywords_file": "keywords.txt",
    "schedule": {
        "timezone": "America/Los_Angeles",
        "times": ["07:00"],
        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "window_minutes": 20,
    },
    "smtp": {
        "server": "smtp.gmail.com",
        "port": 587,
    },
}

PLACEHOLDER_EMAILS = {"you@example.com", "your_email@example.com"}

ENV_OVERRIDES = {
    "ALERT_EMAIL": ("email", "scalar"),
    "KEYWORDS": ("keywords", "list"),
    "LOCATIONS": ("locations", "list"),
    "MEALS": ("meals", "list"),
    "SCHEDULE_TIMEZONE": ("schedule.timezone", "scalar"),
    "SCHEDULE_TIMES": ("schedule.times", "list"),
    "SCHEDULE_DAYS": ("schedule.days", "list"),
    "SMTP_SERVER": ("smtp.server", "scalar"),
    "SMTP_PORT": ("smtp.port", "int"),
    "SMTP_SENDER_EMAIL": ("smtp.sender_email", "scalar"),
}

DAY_INDEX = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tues": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


class ScannerError(RuntimeError):
    """A user-fixable scanner error."""


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def split_env_list(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[\n,;]+", value)
        if item.strip()
    ]


def set_nested(config: dict[str, Any], dotted_path: str, value: Any) -> None:
    keys = dotted_path.split(".")
    target = config
    for key in keys[:-1]:
        target = target.setdefault(key, {})
    target[keys[-1]] = value


def has_env_config() -> bool:
    return any(os.environ.get(name, "").strip() for name in ENV_OVERRIDES)


def apply_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    for env_name, (path, value_type) in ENV_OVERRIDES.items():
        raw_value = os.environ.get(env_name, "").strip()
        if not raw_value:
            continue
        if value_type == "list":
            value: Any = split_env_list(raw_value)
        elif value_type == "int":
            try:
                value = int(raw_value)
            except ValueError as exc:
                raise ScannerError(f"{env_name} must be an integer.") from exc
        else:
            value = raw_value
        set_nested(config, path, value)
    return config


def load_dotenv(env_path: Path) -> None:
    """Tiny .env loader. Only sets keys that aren't already in os.environ."""
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


def load_config(config_path: Path = CONFIG_PATH) -> dict[str, Any]:
    load_dotenv(config_path.parent / ".env")
    config = deepcopy(DEFAULT_CONFIG)
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            file_config = yaml.safe_load(f) or {}
        if not isinstance(file_config, dict):
            raise ScannerError(f"{config_path} must contain a YAML mapping.")
        config = deep_merge(config, file_config)
        config.setdefault("_config_dir", str(config_path.parent))
    elif not has_env_config():
        raise ScannerError(
            "No config.yaml found. Run `cal-dining-setup` to launch the setup dashboard, "
            "or set ALERT_EMAIL and other environment variables."
        )
    return apply_env_overrides(config)


def normalize_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = split_env_list(value)
    elif isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise ScannerError(f"{field_name} must be a list or comma-separated string.")
    return items


def validate_config(config: dict[str, Any], *, require_email: bool) -> None:
    config["locations"] = normalize_list(config.get("locations"), "locations")
    config["meals"] = normalize_list(config.get("meals"), "meals")
    if not config["locations"]:
        raise ScannerError("At least one location must be configured.")
    if not config["meals"]:
        raise ScannerError("At least one meal period must be configured.")

    email = str(config.get("email", "")).strip()
    if require_email and (not email or email.lower() in PLACEHOLDER_EMAILS):
        raise ScannerError(
            "Missing alert email. Set your real email in config.yaml or ALERT_EMAIL "
            "in GitHub variables."
        )

    smtp_cfg = config.setdefault("smtp", {})
    if "sender_email" not in smtp_cfg and config.get("email"):
        smtp_cfg["sender_email"] = config["email"]
    try:
        smtp_cfg["port"] = int(smtp_cfg.get("port", 587))
    except ValueError as exc:
        raise ScannerError("smtp.port must be an integer.") from exc

    validate_schedule(config.get("schedule", {}))


def load_keywords(config: dict[str, Any]) -> list[str]:
    configured_keywords = normalize_list(config.get("keywords"), "keywords")
    if configured_keywords:
        keywords = configured_keywords
    else:
        config_dir = Path(config.get("_config_dir") or PROJECT_ROOT)
        kw_name = str(config.get("keywords_file", "keywords.txt"))
        kw_path = Path(kw_name)
        if not kw_path.is_absolute():
            kw_path = config_dir / kw_name
        if not kw_path.exists() and (PROJECT_ROOT / kw_name).exists():
            kw_path = PROJECT_ROOT / kw_name
        if not kw_path.exists():
            raise ScannerError(
                f"No keywords found. Create {kw_path} or set KEYWORDS."
            )
        keywords = []
        with open(kw_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    keywords.append(line)

    keywords = [keyword.lower() for keyword in dict.fromkeys(keywords)]
    log.info("Loaded %d keywords: %s", len(keywords), keywords)
    return keywords


def fetch_menu_html(menu_url: str = MENU_URL) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(menu_url, headers=headers, timeout=30)
    resp.raise_for_status()
    log.info("Fetched menu page (%d bytes)", len(resp.text))
    return resp.text


def normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def normalize_meal_name(label: str) -> str | None:
    for meal in ["Breakfast", "Lunch", "Dinner", "All Day"]:
        if re.search(rf"\b{re.escape(meal)}\b", label, re.IGNORECASE):
            return meal
    return None


def parse_menus(
    html_text: str,
    target_locations: list[str],
    target_meals: list[str] | None = None,
) -> dict[str, dict[str, list[str]]]:
    """
    Parse the Cal Dining menu HTML.

    Returns:
    {
        "Crossroads": {
            "Breakfast": ["item1", "item2"],
            "Lunch": ["item3"],
        }
    }
    """
    soup = BeautifulSoup(html_text, "html.parser")
    location_lookup = {normalize_key(location): location for location in target_locations}
    meal_lookup = (
        {normalize_key(meal): meal for meal in target_meals}
        if target_meals
        else None
    )
    menus: dict[str, dict[str, list[str]]] = {}

    location_blocks = soup.select("li.location-name")
    if not location_blocks:
        raise ScannerError("Could not find Cal Dining location blocks in the menu page.")

    for block in location_blocks:
        title_node = block.select_one(".cafe-title")
        if not title_node:
            continue
        page_location = title_node.get_text(" ", strip=True)
        configured_location = location_lookup.get(normalize_key(page_location))
        if not configured_location:
            continue

        for period in block.select("li.preiod-name, li.period-name"):
            header_node = period.find("span", recursive=False)
            meal = normalize_meal_name(
                header_node.get_text(" ", strip=True) if header_node else ""
            )
            if not meal:
                continue
            configured_meal = meal_lookup.get(normalize_key(meal), meal) if meal_lookup else meal
            if meal_lookup and normalize_key(meal) not in meal_lookup:
                continue

            items: list[str] = []
            for recipe in period.select("li.recip"):
                item_node = recipe.find("span", recursive=False)
                if not item_node:
                    continue
                item_text = item_node.get_text(" ", strip=True)
                if item_text:
                    items.append(item_text)

            if items:
                deduped_items = list(dict.fromkeys(items))
                menus.setdefault(configured_location, {}).setdefault(configured_meal, [])
                menus[configured_location][configured_meal].extend(deduped_items)

    for location, meals in menus.items():
        for meal, items in meals.items():
            meals[meal] = list(dict.fromkeys(items))

    total_items = sum(len(items) for meals in menus.values() for items in meals.values())
    if total_items == 0:
        raise ScannerError(
            "Parsed zero menu items. The Cal Dining page structure may have changed, "
            "or the configured locations/meals are not available today."
        )

    missing_locations = [
        location for location in target_locations if location not in menus
    ]
    if missing_locations:
        log.warning("No menu items found for configured locations: %s", missing_locations)

    return menus


def find_matches(
    menus: dict[str, dict[str, list[str]]],
    keywords: list[str],
) -> dict[str, dict[str, list[tuple[str, str]]]]:
    matches: dict[str, dict[str, list[tuple[str, str]]]] = {}
    seen: set[tuple[str, str, str, str]] = set()
    for location, meals in menus.items():
        for meal, items in meals.items():
            for item in items:
                item_lower = item.lower()
                for keyword in keywords:
                    if keyword in item_lower:
                        match_key = (location, meal, item, keyword)
                        if match_key in seen:
                            continue
                        seen.add(match_key)
                        matches.setdefault(location, {}).setdefault(meal, []).append(
                            (item, keyword)
                        )
    return matches


def format_matches_text(matches: dict[str, dict[str, list[tuple[str, str]]]]) -> str:
    if not matches:
        return "No keyword matches found."

    lines: list[str] = []
    for location in sorted(matches):
        lines.append(location)
        for meal in sorted(matches[location]):
            for item, keyword in matches[location][meal]:
                lines.append(f"  - {meal}: {item} (matched: {keyword})")
    return "\n".join(lines)


def build_email_html(matches: dict[str, dict[str, list[tuple[str, str]]]]) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    html_parts = [
        f"<h2>Cal Dining Alert &mdash; {html.escape(today)}</h2>",
        "<p>Foods from your keyword list are being served today:</p>",
    ]

    for location in sorted(matches):
        html_parts.append(
            f'<h3 style="color:#003262;">{html.escape(location)}</h3>'
        )
        html_parts.append("<ul>")
        for meal in ["Breakfast", "Lunch", "Dinner", "All Day"]:
            if meal in matches[location]:
                for item, keyword in matches[location][meal]:
                    html_parts.append(
                        f"<li><b>{html.escape(meal)}:</b> {html.escape(item)} "
                        f'<span style="color:#888;">'
                        f'(matched: "{html.escape(keyword)}")'
                        "</span></li>"
                    )
        html_parts.append("</ul>")

    html_parts.append(
        '<p style="color:#888; font-size:12px;">'
        "Sent by Cal Dining Scanner &bull; "
        '<a href="https://dining.berkeley.edu/menus/">View full menus</a></p>'
    )
    return "\n".join(html_parts)


def send_email(config: dict[str, Any], subject: str, html_body: str) -> None:
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        raise ScannerError(
            "GMAIL_APP_PASSWORD is not set. Add it as an environment variable "
            "or GitHub Actions secret before sending email."
        )

    smtp_cfg = config.get("smtp", {})
    sender = smtp_cfg.get("sender_email", config.get("email"))
    recipient = config.get("email")
    if not sender or not recipient:
        raise ScannerError("Email sender and recipient must be configured.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(
        smtp_cfg.get("server", "smtp.gmail.com"),
        smtp_cfg.get("port", 587),
    ) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())

    log.info("Email sent to %s", recipient)


def parse_schedule_time(value: str) -> time:
    if not re.fullmatch(r"\d{2}:\d{2}", value):
        raise ScannerError(f"Schedule time must be HH:MM, got {value!r}.")
    hour, minute = [int(part) for part in value.split(":")]
    if hour > 23 or minute > 59:
        raise ScannerError(f"Schedule time is out of range: {value!r}.")
    return time(hour=hour, minute=minute)


def normalize_days(days: Any) -> set[int]:
    day_values = normalize_list(days, "schedule.days")
    if not day_values:
        raise ScannerError("schedule.days must include at least one day.")
    indexes: set[int] = set()
    for day in day_values:
        key = day.lower()
        if key not in DAY_INDEX:
            raise ScannerError(f"Unknown schedule day: {day!r}.")
        indexes.add(DAY_INDEX[key])
    return indexes


def validate_schedule(schedule: dict[str, Any]) -> None:
    if not isinstance(schedule, dict):
        raise ScannerError("schedule must be a mapping.")

    timezone_name = str(schedule.get("timezone", "")).strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ScannerError(f"Unknown schedule timezone: {timezone_name!r}.") from exc

    times = normalize_list(schedule.get("times"), "schedule.times")
    if not times:
        raise ScannerError("schedule.times must include at least one HH:MM time.")
    for schedule_time in times:
        parse_schedule_time(schedule_time)

    normalize_days(schedule.get("days"))
    try:
        window_minutes = int(schedule.get("window_minutes", 20))
    except ValueError as exc:
        raise ScannerError("schedule.window_minutes must be an integer.") from exc
    if window_minutes < 1:
        raise ScannerError("schedule.window_minutes must be at least 1.")
    schedule["window_minutes"] = window_minutes


def load_state(state_path: Path = STATE_PATH) -> dict[str, Any]:
    if not state_path.exists():
        return {"sent_slots": []}
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScannerError(f"Could not read state file {state_path}: {exc}") from exc
    if not isinstance(state, dict):
        return {"sent_slots": []}
    state.setdefault("sent_slots", [])
    return state


def save_state(state: dict[str, Any], state_path: Path = STATE_PATH) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def build_slot_id(slot_date: date, slot_time: time, timezone_name: str) -> str:
    return f"{slot_date.isoformat()}T{slot_time.strftime('%H:%M')}@{timezone_name}"


def due_schedule_slot(
    schedule: dict[str, Any],
    state: dict[str, Any],
    now: datetime | None = None,
) -> str | None:
    timezone_name = str(schedule["timezone"])
    local_tz = ZoneInfo(timezone_name)
    current = (now or datetime.now(timezone.utc)).astimezone(local_tz)
    allowed_days = normalize_days(schedule["days"])
    schedule_times = [parse_schedule_time(value) for value in normalize_list(schedule["times"], "schedule.times")]
    window = timedelta(minutes=int(schedule.get("window_minutes", 20)))
    sent_slots = set(state.get("sent_slots", []))

    candidate_dates = [current.date(), (current - timedelta(days=1)).date()]
    for candidate_date in candidate_dates:
        if candidate_date.weekday() not in allowed_days:
            continue
        for candidate_time in schedule_times:
            candidate = datetime.combine(candidate_date, candidate_time, tzinfo=local_tz)
            delta = current - candidate
            if timedelta(0) <= delta < window:
                slot_id = build_slot_id(candidate_date, candidate_time, timezone_name)
                if slot_id not in sent_slots:
                    return slot_id
    return None


def mark_slot_sent(state: dict[str, Any], slot_id: str) -> None:
    sent_slots = list(dict.fromkeys(state.get("sent_slots", [])))
    if slot_id not in sent_slots:
        sent_slots.append(slot_id)
    state["sent_slots"] = sent_slots[-200:]


def run_scan(
    config: dict[str, Any],
    *,
    dry_run: bool = False,
    fetcher: Callable[[], str] = fetch_menu_html,
    email_sender: Callable[[dict[str, Any], str, str], None] = send_email,
) -> dict[str, dict[str, list[tuple[str, str]]]]:
    keywords = load_keywords(config)
    if not keywords:
        raise ScannerError("No keywords found. Add foods to keywords.txt or KEYWORDS.")

    menu_html = fetcher()
    menus = parse_menus(menu_html, config["locations"], config["meals"])
    total_items = sum(len(items) for meals in menus.values() for items in meals.values())
    log.info("Parsed %d menu items across %d locations", total_items, len(menus))

    matches = find_matches(menus, keywords)
    log.info("Match preview:\n%s", format_matches_text(matches))

    if dry_run:
        log.info("Dry run enabled; no email will be sent.")
        return matches

    if matches:
        subject = "Cal Dining Alert - Foods You Like Today!"
        html_body = build_email_html(matches)
        email_sender(config, subject, html_body)
    else:
        log.info("No email sent because there were no keyword matches.")
    return matches


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan Cal Dining menus for keyword matches.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--run-now",
        action="store_true",
        help="Fetch menus and send an email immediately. This is the default mode.",
    )
    mode.add_argument(
        "--scheduled",
        action="store_true",
        help="Send only if the configured local schedule is due.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch menus and print matches without sending email.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config YAML. Defaults to ./config.yaml, then the user config dir.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Path to scheduled-run state file. Defaults to alongside the config file.",
    )
    args = parser.parse_args(argv)
    if args.config is None:
        args.config = default_config_path()
    if args.state_file is None:
        args.state_file = default_state_path(args.config)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    log.info("=== Cal Dining Scanner started ===")

    try:
        config = load_config(args.config)
        dry_run = bool(args.dry_run)

        if args.scheduled:
            validate_config(config, require_email=False)
            state = load_state(args.state_file)
            slot_id = due_schedule_slot(config["schedule"], state)
            if not slot_id:
                log.info("No configured schedule is due right now.")
                return 0
            log.info("Schedule slot is due: %s", slot_id)
            validate_config(config, require_email=True)
            run_scan(config)
            mark_slot_sent(state, slot_id)
            save_state(state, args.state_file)
            return 0

        validate_config(config, require_email=not dry_run)
        run_scan(config, dry_run=dry_run)
        return 0
    except requests.RequestException as exc:
        log.error("Could not fetch Cal Dining menus: %s", exc)
        return 1
    except ScannerError as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
