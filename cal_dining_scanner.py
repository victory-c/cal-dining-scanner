#!/usr/bin/env python3
"""
Cal Dining Scanner
Scrapes UC Berkeley dining menus and sends email alerts
when foods matching your keywords are being served.
"""

import os
import re
import sys
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path

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


def load_config() -> dict:
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_keywords(config: dict) -> list[str]:
    kw_path = PROJECT_ROOT / config.get("keywords_file", "keywords.txt")
    keywords = []
    with open(kw_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                keywords.append(line.lower())
    log.info("Loaded %d keywords: %s", len(keywords), keywords)
    return keywords


def fetch_menu_html() -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )
    }
    resp = requests.get(MENU_URL, headers=headers, timeout=30)
    resp.raise_for_status()
    log.info("Fetched menu page (%d bytes)", len(resp.text))
    return resp.text


def parse_menus(html: str, target_locations: list[str]) -> dict:
    """
    Parse the dining menu HTML and return a nested dict:
    {
        "Crossroads": {
            "Breakfast": ["item1", "item2", ...],
            "Lunch": [...],
            "Dinner": [...]
        },
        ...
    }
    """
    soup = BeautifulSoup(html, "html.parser")
    menus = {}

    # The dining page uses location headings followed by meal-period accordions.
    # We look for location blocks (h2/h3 with location names) and then collect
    # meal sections and their menu items underneath.

    # Strategy: walk through all text content and use structural cues
    # to identify locations and meal periods.
    location_targets = [loc.lower() for loc in target_locations]

    # Try parsing by structured elements first
    # The page typically has sections per location with class patterns
    location_blocks = soup.find_all(
        ["h2", "h3"],
        string=re.compile("|".join(re.escape(loc) for loc in target_locations), re.I),
    )

    if location_blocks:
        log.info("Found %d location headers in HTML", len(location_blocks))
        for loc_header in location_blocks:
            loc_name = None
            header_text = loc_header.get_text(strip=True)
            for target in target_locations:
                if target.lower() in header_text.lower():
                    loc_name = target
                    break
            if not loc_name:
                continue

            menus[loc_name] = {}

            # Collect sibling elements until next location header
            sibling = loc_header.find_next_sibling()
            current_meal = None
            while sibling:
                sib_text = sibling.get_text(strip=True)
                # Check if this is a new location header — stop
                if sibling.name in ("h2", "h3"):
                    is_new_loc = any(
                        t.lower() in sib_text.lower() for t in target_locations
                    )
                    if is_new_loc:
                        break

                # Check for meal period headers
                for meal in ["Breakfast", "Lunch", "Dinner"]:
                    if meal.lower() in sib_text.lower():
                        current_meal = meal
                        if current_meal not in menus[loc_name]:
                            menus[loc_name][current_meal] = []
                        break

                # Collect menu items — typically in list items, spans, or divs
                if current_meal:
                    items = sibling.find_all(["li", "span", "div", "p"])
                    for item in items:
                        item_text = item.get_text(strip=True)
                        # Filter out non-food text
                        if (
                            item_text
                            and len(item_text) > 1
                            and len(item_text) < 200
                            and not any(
                                skip in item_text.lower()
                                for skip in [
                                    "a.m.",
                                    "p.m.",
                                    "now open",
                                    "closed",
                                    "spring -",
                                    "summer -",
                                    "fall -",
                                    "filter",
                                    "menu",
                                    "location",
                                ]
                            )
                        ):
                            menus[loc_name][current_meal].append(item_text)

                sibling = sibling.find_next_sibling()

    # Fallback: text-based parsing if structured parsing yields nothing
    if not any(meals for meals in menus.values() if any(meals.values())):
        log.info("Structured parsing found no items, falling back to text parsing")
        menus = _parse_by_text(soup, target_locations)

    # Deduplicate items
    for loc in menus:
        for meal in menus[loc]:
            menus[loc][meal] = list(dict.fromkeys(menus[loc][meal]))

    return menus


def _parse_by_text(soup: BeautifulSoup, target_locations: list[str]) -> dict:
    """Fallback parser that works on the raw text content of the page."""
    full_text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]

    menus = {loc: {} for loc in target_locations}
    current_location = None
    current_meal = None

    for line in lines:
        # Check for location
        for loc in target_locations:
            if loc.lower() in line.lower() and len(line) < 50:
                current_location = loc
                current_meal = None
                break

        # Check for meal period
        if current_location:
            for meal in ["Breakfast", "Lunch", "Dinner"]:
                if meal.lower() in line.lower() and len(line) < 60:
                    current_meal = meal
                    if current_meal not in menus[current_location]:
                        menus[current_location][current_meal] = []
                    break
            else:
                # Potential food item
                if (
                    current_meal
                    and len(line) > 1
                    and len(line) < 200
                    and not any(
                        skip in line.lower()
                        for skip in [
                            "a.m.",
                            "p.m.",
                            "now open",
                            "closed",
                            "spring -",
                            "summer -",
                            "fall -",
                            "filter",
                            "menu",
                            "location",
                            "date",
                            "home",
                            "special note",
                        ]
                    )
                ):
                    menus[current_location][current_meal].append(line)

    return menus


def find_matches(menus: dict, keywords: list[str]) -> dict:
    """
    Return matches in the form:
    {
        "Crossroads": {
            "Lunch": [("Pepperoni Pizza", "pizza"), ...],
        },
        ...
    }
    """
    matches = {}
    for location, meals in menus.items():
        for meal, items in meals.items():
            for item in items:
                for kw in keywords:
                    if kw in item.lower():
                        matches.setdefault(location, {}).setdefault(meal, []).append(
                            (item, kw)
                        )
    return matches


def build_email_html(matches: dict) -> str:
    today = datetime.now().strftime("%A, %B %d, %Y")
    html_parts = [
        f"<h2>Cal Dining Alert &mdash; {today}</h2>",
        "<p>Foods from your keyword list are being served today:</p>",
    ]

    for location in sorted(matches):
        html_parts.append(f'<h3 style="color:#003262;">&#128205; {location}</h3>')
        html_parts.append("<ul>")
        for meal in ["Breakfast", "Lunch", "Dinner"]:
            if meal in matches[location]:
                for item, keyword in matches[location][meal]:
                    html_parts.append(
                        f"<li><b>{meal}:</b> {item} "
                        f'<span style="color:#888;">(matched: "{keyword}")</span></li>'
                    )
        html_parts.append("</ul>")

    html_parts.append(
        '<p style="color:#888; font-size:12px;">'
        "Sent by Cal Dining Scanner &bull; "
        '<a href="https://dining.berkeley.edu/menus/">View full menus</a></p>'
    )
    return "\n".join(html_parts)


def send_email(config: dict, subject: str, html_body: str):
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not password:
        log.error("GMAIL_APP_PASSWORD environment variable not set. Cannot send email.")
        log.info("Email that would have been sent:\nSubject: %s\n%s", subject, html_body)
        sys.exit(1)

    smtp_cfg = config.get("smtp", {})
    sender = smtp_cfg.get("sender_email", config["email"])
    recipient = config["email"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_cfg.get("server", "smtp.gmail.com"), smtp_cfg.get("port", 587)) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, [recipient], msg.as_string())

    log.info("Email sent to %s", recipient)


def main():
    log.info("=== Cal Dining Scanner started ===")

    config = load_config()
    keywords = load_keywords(config)

    if not keywords:
        log.warning("No keywords found. Add foods to keywords.txt and try again.")
        return

    html = fetch_menu_html()
    target_locations = config.get("locations", ["Crossroads", "Foothill", "Clark Kerr"])
    menus = parse_menus(html, target_locations)

    total_items = sum(len(items) for meals in menus.values() for items in meals.values())
    log.info("Parsed %d menu items across %d locations", total_items, len(menus))

    matches = find_matches(menus, keywords)

    if matches:
        total_matches = sum(
            len(items) for meals in matches.values() for items in meals.values()
        )
        log.info("Found %d matches!", total_matches)

        subject = "Cal Dining Alert - Foods You Like Today!"
        html_body = build_email_html(matches)
        send_email(config, subject, html_body)
    else:
        log.info("No keyword matches found today.")


if __name__ == "__main__":
    main()
