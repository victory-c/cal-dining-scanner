# Cal Dining Scanner

Automatically scans [UC Berkeley Dining menus](https://dining.berkeley.edu/menus/) for foods you like and sends an email alert with locations and meal periods.

## How It Works

1. Scrapes the current Cal Dining menu page.
2. Extracts food items by dining hall and meal period.
3. Matches menu items against your keywords.
4. Sends an HTML email when matches are found.

## Quick Start (one command)

Install once, then launch the setup dashboard in your browser:

```bash
pipx install git+https://github.com/victory-c/cal-dining-scanner.git
cal-dining-setup
```

Don't have `pipx`? `brew install pipx` on macOS, `python3 -m pip install --user pipx` elsewhere — or `uvx --from git+https://github.com/victory-c/cal-dining-scanner.git cal-dining-setup` if you have [uv](https://docs.astral.sh/uv/).

The dashboard opens at <http://127.0.0.1:8765/>. From there you can:

1. Enter your alert email and Gmail app password.
2. Pick keywords, dining halls, meals, and a schedule.
3. Click **Run a test scan** to preview matches against today's menu.
4. Choose how the scan runs on a schedule:
   - **☁ Cloud (GitHub Actions, recommended)** — paste a GitHub token, the dashboard forks this repo into your account, pushes your settings as Actions secrets/variables, and enables the workflow. Your laptop can be off.
   - **💻 Local schedule** — installs a launchd / systemd / Task Scheduler entry that runs every 15 minutes. ⚠ Your computer must be on at scan times.

Settings live in `~/Library/Application Support/cal-dining-scanner/` (macOS), `~/.config/cal-dining-scanner/` (Linux), or `%APPDATA%\cal-dining-scanner\` (Windows).

## Manual Setup (for development or running from source)

```bash
git clone https://github.com/victory-c/cal-dining-scanner.git
cd cal-dining-scanner
pip install -r requirements.txt
cp config.example.yaml config.yaml
```

Edit `config.yaml`:

```yaml
email: "you@example.com"
locations:
  - "Crossroads"
  - "Foothill"
  - "Clark Kerr"
meals:
  - "Breakfast"
  - "Lunch"
  - "Dinner"
schedule:
  timezone: "America/Los_Angeles"
  times: ["07:00"]
  days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
```

Edit `keywords.txt`, one food per line:

```text
pizza
sushi
chicken tikka
pho
clam chowder
wings
```

Set your Gmail app password:

```bash
export GMAIL_APP_PASSWORD="your_app_password_here"
```

Generate the password at [Google App Passwords](https://myaccount.google.com/apppasswords). Your Gmail account must have 2-Step Verification enabled.

## Run Modes

Preview matches without sending email:

```bash
python3 cal_dining_scanner.py --dry-run
```

Run immediately and send an email if matches are found:

```bash
python3 cal_dining_scanner.py --run-now
```

Run only if your configured schedule is due:

```bash
python3 cal_dining_scanner.py --scheduled
```

Running `python3 cal_dining_scanner.py` without a mode is the same as `--run-now`.

## Schedule Examples

Every day at 7 AM Pacific:

```yaml
schedule:
  timezone: "America/Los_Angeles"
  times: ["07:00"]
  days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
```

Weekdays only at 8:30 AM Eastern:

```yaml
schedule:
  timezone: "America/New_York"
  times: ["08:30"]
  days: ["mon", "tue", "wed", "thu", "fri"]
```

Check around lunch and dinner:

```yaml
schedule:
  timezone: "America/Los_Angeles"
  times: ["11:30", "17:30"]
  days: ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
```

Manual-only mode: do not enable the scheduled GitHub Action; run `--dry-run` or `--run-now` yourself whenever you want.

## GitHub Actions Setup

The included workflow runs every 15 minutes. The script checks your configured local schedule and sends at most once per scheduled slot. This avoids editing UTC cron by hand and handles daylight saving time through your configured timezone.

In your fork, add this secret:

- `GMAIL_APP_PASSWORD`: your Gmail app password

Add these repository variables under **Settings -> Secrets and variables -> Actions -> Variables**:

- `ALERT_EMAIL`: where alerts should be sent
- `KEYWORDS`: comma-separated or newline-separated foods, for example `pizza,sushi,clam chowder`
- `LOCATIONS`: optional, for example `Crossroads,Foothill,Clark Kerr`
- `MEALS`: optional, for example `Breakfast,Lunch,Dinner`
- `SCHEDULE_TIMEZONE`: for example `America/Los_Angeles`
- `SCHEDULE_TIMES`: comma-separated local times, for example `07:00` or `11:30,17:30`
- `SCHEDULE_DAYS`: optional, for example `mon,tue,wed,thu,fri`

You can also commit your own private `config.yaml` in a private fork, but the public repo ignores it by default so personal emails and schedules do not leak.

## Environment Overrides

Environment variables override `config.yaml`. This is useful for GitHub Actions and local experiments:

```bash
ALERT_EMAIL="you@example.com" \
KEYWORDS="pizza,clam chowder" \
LOCATIONS="Crossroads,Foothill" \
MEALS="Lunch,Dinner" \
SCHEDULE_TIMEZONE="America/Los_Angeles" \
SCHEDULE_TIMES="11:30,17:30" \
python3 cal_dining_scanner.py --dry-run
```

## Testing

```bash
python3 -m unittest discover -s tests
```

## Notes

Menus can change due to availability or supplier delays. This tool is intended for convenience, not for allergy or medical decisions. Always check posted dining hall signage for current allergen information.

## Project Structure

```text
cal-dining-scanner/
├── cal_dining_scanner.py
├── config.example.yaml
├── keywords.txt
├── requirements.txt
├── tests/
├── .github/
│   └── workflows/
└── README.md
```

## License

MIT
