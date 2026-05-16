"""Flask dashboard for configuring Cal Dining Scanner.

Run with `cal-dining-setup` (installed via pipx/uvx) or `python -m cal_dining_dashboard.app`.
The dashboard opens at http://127.0.0.1:8765/ and writes config + .env to the user
config dir (~/.config/cal-dining-scanner on Linux, ~/Library/Application Support/... on macOS).
"""
from __future__ import annotations

import argparse
import logging
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, render_template, request

import cal_dining_scanner as scanner

from . import config_io, github_setup, local_scheduler

log = logging.getLogger("cal_dining_dashboard")

DEFAULT_PORT = 8765


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = secrets.token_hex(16)

    @app.get("/")
    def index() -> str:
        state = config_io.load_form_state()
        sched_status = local_scheduler.status()
        return render_template(
            "index.html",
            state=state,
            sched_status=sched_status,
            config_path=str(config_io.config_path()),
            token_help_url=github_setup.TOKEN_HELP_URL,
            platform=sys.platform,
        )

    @app.post("/save")
    def save():
        form = _form_to_settings(request.form)
        try:
            path = config_io.save_settings(form)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "message": f"Saved to {path}"})

    @app.post("/test-scan")
    def test_scan():
        try:
            cfg = scanner.load_config(config_io.config_path())
            scanner.validate_config(cfg, require_email=False)
            matches = scanner.run_scan(cfg, dry_run=True)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({
            "ok": True,
            "matches": scanner.format_matches_text(matches),
        })

    @app.post("/github/configure")
    def github_configure():
        form = _form_to_settings(request.form)
        token = (request.form.get("github_token") or "").strip()
        try:
            config_io.save_settings(form)
            settings = {
                "email": form["email"],
                "gmail_app_password": form.get("gmail_app_password") or config_io.read_password(),
                "keywords_list": _keywords_list_from_form(form["keywords"]),
                "locations": form["locations"],
                "meals": form["meals"],
                "timezone": form["timezone"],
                "times_list": _csv_list(form["times"]),
                "days": form["days"],
            }
            result = github_setup.configure(token, settings)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "result": result})

    @app.post("/local/install")
    def local_install():
        try:
            form = _form_to_settings(request.form)
            config_io.save_settings(form)
            info = local_scheduler.install()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "info": info})

    @app.post("/local/uninstall")
    def local_uninstall():
        try:
            info = local_scheduler.uninstall()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "info": info})

    return app


def _form_to_settings(form) -> dict:
    return {
        "email": (form.get("email") or "").strip(),
        "gmail_app_password": (form.get("gmail_app_password") or "").strip(),
        "keywords": form.get("keywords") or "",
        "locations": form.getlist("locations") or [],
        "meals": form.getlist("meals") or [],
        "timezone": (form.get("timezone") or "America/Los_Angeles").strip(),
        "times": (form.get("times") or "07:00").strip(),
        "days": form.getlist("days") or [],
        "smtp_server": (form.get("smtp_server") or "smtp.gmail.com").strip(),
        "smtp_port": (form.get("smtp_port") or "587").strip(),
    }


def _keywords_list_from_form(blob: str) -> list[str]:
    return [
        line.strip()
        for line in blob.replace(",", "\n").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _csv_list(blob: str) -> list[str]:
    return [item.strip() for item in blob.replace("\n", ",").split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="Cal Dining Scanner setup dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser.")
    args = parser.parse_args(argv)

    config_io.ensure_config_dir()
    app = create_app()
    url = f"http://{args.host}:{args.port}/"
    log.info("Cal Dining Setup running at %s", url)
    log.info("Config dir: %s", config_io.config_dir())

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    # Single-user local dashboard; Flask's dev server is fine here.
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
