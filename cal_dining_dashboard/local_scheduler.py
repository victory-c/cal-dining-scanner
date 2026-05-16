"""Install and remove an OS-level scheduled task that runs the scanner.

This requires the user's machine to be on at scheduled times. The dashboard makes
that tradeoff explicit before calling install().
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config_io

LABEL = "com.victoryc.cal-dining-scanner"
SCHEDULER_INTERVAL_MINUTES = 15  # match the GitHub Actions cadence


class SchedulerError(RuntimeError):
    """User-facing scheduler error."""


def scanner_executable() -> str:
    """Find the cal-dining-scanner CLI shim, or fall back to `python -m cal_dining_scanner`."""
    found = shutil.which("cal-dining-scanner")
    if found:
        return found
    # Fall back to the python interpreter that's running us — useful when invoked
    # via `python -m cal_dining_dashboard.app` without pipx in PATH.
    return f"{sys.executable} {os.fspath(_scanner_module_path())}"


def _scanner_module_path() -> Path:
    import cal_dining_scanner
    return Path(cal_dining_scanner.__file__)


def status() -> dict[str, object]:
    """Return whether a schedule is currently installed."""
    if sys.platform == "darwin":
        return _status_launchd()
    if sys.platform == "win32":
        return _status_schtasks()
    return _status_systemd()


def install() -> dict[str, str]:
    if sys.platform == "darwin":
        return _install_launchd()
    if sys.platform == "win32":
        return _install_schtasks()
    return _install_systemd()


def uninstall() -> dict[str, str]:
    if sys.platform == "darwin":
        return _uninstall_launchd()
    if sys.platform == "win32":
        return _uninstall_schtasks()
    return _uninstall_systemd()


# ── macOS (launchd) ──────────────────────────────────────────────────────────

def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _launchd_plist_body() -> str:
    config = config_io.config_path()
    exe = scanner_executable()
    program_args = exe.split() + ["--scheduled", "--config", str(config)]
    args_xml = "\n".join(f"      <string>{x}</string>" for x in program_args)
    log_dir = config_io.config_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = log_dir / "scheduler.out.log"
    stderr = log_dir / "scheduler.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args_xml}
  </array>
  <key>StartInterval</key><integer>{SCHEDULER_INTERVAL_MINUTES * 60}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{stdout}</string>
  <key>StandardErrorPath</key><string>{stderr}</string>
</dict>
</plist>
"""


def _status_launchd() -> dict[str, object]:
    plist = _launchd_plist_path()
    if not plist.exists():
        return {"installed": False, "details": "no launchd plist"}
    loaded = subprocess.run(
        ["launchctl", "list", LABEL], capture_output=True, text=True
    )
    return {
        "installed": True,
        "loaded": loaded.returncode == 0,
        "details": f"plist at {plist}",
    }


def _install_launchd() -> dict[str, str]:
    plist = _launchd_plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    plist.write_text(_launchd_plist_body(), encoding="utf-8")
    # Reload (unload first in case of a stale entry)
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    result = subprocess.run(
        ["launchctl", "load", str(plist)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise SchedulerError(
            f"launchctl load failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return {"plist": str(plist)}


def _uninstall_launchd() -> dict[str, str]:
    plist = _launchd_plist_path()
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink()
    return {"plist": str(plist)}


# ── Linux (systemd-user) ─────────────────────────────────────────────────────

def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemd_service() -> Path:
    return _systemd_dir() / f"{LABEL}.service"


def _systemd_timer() -> Path:
    return _systemd_dir() / f"{LABEL}.timer"


def _status_systemd() -> dict[str, object]:
    return {
        "installed": _systemd_timer().exists(),
        "details": f"timer at {_systemd_timer()}" if _systemd_timer().exists() else "no timer",
    }


def _install_systemd() -> dict[str, str]:
    _systemd_dir().mkdir(parents=True, exist_ok=True)
    exe = scanner_executable()
    config = config_io.config_path()
    service_body = f"""[Unit]
Description=Cal Dining Scanner scheduled run
After=network-online.target

[Service]
Type=oneshot
ExecStart={exe} --scheduled --config {config}
"""
    timer_body = f"""[Unit]
Description=Cal Dining Scanner timer

[Timer]
OnBootSec=2min
OnUnitActiveSec={SCHEDULER_INTERVAL_MINUTES}min
Persistent=true

[Install]
WantedBy=timers.target
"""
    _systemd_service().write_text(service_body, encoding="utf-8")
    _systemd_timer().write_text(timer_body, encoding="utf-8")

    for cmd in (
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", _systemd_timer().name],
    ):
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SchedulerError(
                f"`{' '.join(cmd)}` failed: {result.stderr.strip() or result.stdout.strip()}"
            )
    return {"timer": str(_systemd_timer())}


def _uninstall_systemd() -> dict[str, str]:
    if _systemd_timer().exists():
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _systemd_timer().name],
            capture_output=True,
        )
        _systemd_timer().unlink()
    if _systemd_service().exists():
        _systemd_service().unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    return {}


# ── Windows (schtasks) ───────────────────────────────────────────────────────

def _status_schtasks() -> dict[str, object]:
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", LABEL], capture_output=True, text=True
    )
    return {"installed": result.returncode == 0, "details": result.stdout.strip()}


def _install_schtasks() -> dict[str, str]:
    config = config_io.config_path()
    exe = scanner_executable()
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", LABEL,
        "/SC", "MINUTE",
        "/MO", str(SCHEDULER_INTERVAL_MINUTES),
        "/TR", f'{exe} --scheduled --config "{config}"',
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SchedulerError(
            f"schtasks create failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return {"task": LABEL}


def _uninstall_schtasks() -> dict[str, str]:
    subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", LABEL], capture_output=True
    )
    return {"task": LABEL}
