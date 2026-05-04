# Cal Dining Scanner

Automatically scans [UC Berkeley Dining menus](https://dining.berkeley.edu/menus/) for foods you like and sends you an email alert with locations and meal periods.

## How It Works

1. Scrapes the daily menu from `dining.berkeley.edu/menus/`
2. Matches menu items against your keyword list (case-insensitive, partial match)
3. Sends an HTML email with matched foods organized by dining hall and meal period

## Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/cal-dining-scanner.git
cd cal-dining-scanner
pip install -r requirements.txt
```

### 2. Configure your keywords

Edit `keywords.txt` — one food per line:

```
pizza
sushi
chicken tikka
pho
clam chowder
wings
```

Lines starting with `#` are ignored.

### 3. Configure dining halls

Edit `config.yaml` to set your email and which dining halls to monitor:

```yaml
email: "you@berkeley.edu"
locations:
  - "Crossroads"
  - "Foothill"
  - "Clark Kerr"
```

### 4. Set up Gmail App Password

1. Go to [Google App Passwords](https://myaccount.google.com/apppasswords)
2. Generate a new app password for "Mail"
3. Set it as an environment variable:

```bash
export GMAIL_APP_PASSWORD="your_app_password_here"
```

### 5. Run it

```bash
python cal_dining_scanner.py
```

## Automated Daily Runs (GitHub Actions)

This repo includes a GitHub Actions workflow that runs the scanner daily at 7:00 AM Pacific.

To enable it:

1. Push this repo to GitHub
2. Go to **Settings → Secrets and variables → Actions**
3. Add a secret: `GMAIL_APP_PASSWORD` with your Gmail app password
4. The workflow runs automatically every morning, or trigger it manually from the **Actions** tab

## Project Structure

```
cal-dining-scanner/
├── cal_dining_scanner.py    # Main scanner script
├── config.yaml              # Configuration (email, locations, meals)
├── keywords.txt             # Your food keywords (edit this!)
├── requirements.txt         # Python dependencies
├── .env.example             # Environment variable template
├── .github/
│   └── workflows/
│       └── daily_scan.yml   # GitHub Actions daily workflow
└── README.md
```

## License

MIT
