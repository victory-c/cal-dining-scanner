"""Configure a GitHub fork of cal-dining-scanner so Actions runs the scan in the cloud."""
from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests

UPSTREAM_OWNER = "victory-c"
UPSTREAM_REPO = "cal-dining-scanner"
API = "https://api.github.com"
ACCEPT = "application/vnd.github+json"

# Required token scopes for the user-friendly token-creation link.
TOKEN_HELP_URL = (
    "https://github.com/settings/tokens/new"
    "?description=cal-dining-scanner-setup"
    "&scopes=repo,workflow"
)


class GitHubSetupError(RuntimeError):
    """User-facing GitHub configuration error."""


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": ACCEPT,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "cal-dining-scanner-setup",
    }


def _request(method: str, url: str, token: str, **kwargs: Any) -> requests.Response:
    resp = requests.request(method, url, headers=_headers(token), timeout=30, **kwargs)
    if resp.status_code >= 400:
        msg = resp.text
        try:
            msg = resp.json().get("message", msg)
        except (ValueError, KeyError):
            pass
        raise GitHubSetupError(f"GitHub API {method} {url} failed ({resp.status_code}): {msg}")
    return resp


def get_user(token: str) -> dict[str, Any]:
    return _request("GET", f"{API}/user", token).json()


def fork_repo(token: str, owner: str) -> dict[str, Any]:
    """Create a fork under `owner`, or return the existing one. Polls until the fork is ready."""
    existing = requests.get(
        f"{API}/repos/{owner}/{UPSTREAM_REPO}",
        headers=_headers(token),
        timeout=30,
    )
    if existing.status_code == 200:
        return existing.json()

    _request(
        "POST",
        f"{API}/repos/{UPSTREAM_OWNER}/{UPSTREAM_REPO}/forks",
        token,
        json={"default_branch_only": True},
    )

    for _ in range(20):
        time.sleep(2)
        check = requests.get(
            f"{API}/repos/{owner}/{UPSTREAM_REPO}",
            headers=_headers(token),
            timeout=30,
        )
        if check.status_code == 200:
            return check.json()
    raise GitHubSetupError(
        "Fork did not become ready within 40 seconds. Try again, or check your forks list."
    )


def enable_actions(token: str, owner: str) -> None:
    """Actions are disabled on new forks by default; enable workflow runs."""
    _request(
        "PUT",
        f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/permissions",
        token,
        json={"enabled": True, "allowed_actions": "all"},
    )


def _seal(public_key_b64: str, value: str) -> str:
    """Encrypt `value` with a libsodium sealed box for the repo's public key."""
    from nacl.public import PublicKey, SealedBox

    public_key = PublicKey(base64.b64decode(public_key_b64))
    box = SealedBox(public_key)
    return base64.b64encode(box.encrypt(value.encode("utf-8"))).decode("ascii")


def set_secret(token: str, owner: str, name: str, value: str) -> None:
    pubkey = _request(
        "GET",
        f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/secrets/public-key",
        token,
    ).json()
    encrypted = _seal(pubkey["key"], value)
    _request(
        "PUT",
        f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/secrets/{name}",
        token,
        json={"encrypted_value": encrypted, "key_id": pubkey["key_id"]},
    )


def set_variable(token: str, owner: str, name: str, value: str) -> None:
    """Create or update a repo variable. GitHub uses POST to create, PATCH to update."""
    existing = requests.get(
        f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/variables/{name}",
        headers=_headers(token),
        timeout=30,
    )
    if existing.status_code == 200:
        _request(
            "PATCH",
            f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/variables/{name}",
            token,
            json={"name": name, "value": value},
        )
    else:
        _request(
            "POST",
            f"{API}/repos/{owner}/{UPSTREAM_REPO}/actions/variables",
            token,
            json={"name": name, "value": value},
        )


def configure(token: str, settings: dict[str, Any]) -> dict[str, str]:
    """End-to-end: fork, enable actions, push secrets/vars. Returns user-facing info."""
    if not token or not token.strip():
        raise GitHubSetupError("GitHub token is empty.")
    token = token.strip()

    user = get_user(token)
    owner = user.get("login")
    if not owner:
        raise GitHubSetupError("Could not determine GitHub username from token.")

    fork = fork_repo(token, owner)
    enable_actions(token, owner)

    password = (settings.get("gmail_app_password") or "").strip()
    if not password:
        raise GitHubSetupError(
            "Gmail app password is required so GitHub Actions can send the alerts."
        )
    set_secret(token, owner, "GMAIL_APP_PASSWORD", password)

    vars_to_set = {
        "ALERT_EMAIL": settings.get("email", ""),
        "KEYWORDS": ",".join(settings.get("keywords_list") or []),
        "LOCATIONS": ",".join(settings.get("locations") or []),
        "MEALS": ",".join(settings.get("meals") or []),
        "SCHEDULE_TIMEZONE": settings.get("timezone", "America/Los_Angeles"),
        "SCHEDULE_TIMES": ",".join(settings.get("times_list") or []),
        "SCHEDULE_DAYS": ",".join(settings.get("days") or []),
    }
    for name, value in vars_to_set.items():
        if value:
            set_variable(token, owner, name, value)

    return {
        "owner": owner,
        "repo_url": fork.get("html_url", f"https://github.com/{owner}/{UPSTREAM_REPO}"),
        "actions_url": f"https://github.com/{owner}/{UPSTREAM_REPO}/actions",
    }
