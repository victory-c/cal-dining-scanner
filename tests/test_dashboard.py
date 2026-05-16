import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cal_dining_dashboard import app as dashboard_app
from cal_dining_dashboard import local_scheduler


class DashboardSecurityTests(unittest.TestCase):
    def test_post_rejects_missing_setup_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.post("/save", data={"email": "friend@example.com"})

                self.assertEqual(resp.status_code, 403)
                self.assertFalse((Path(tmpdir) / "config.yaml").exists())

    def test_post_accepts_setup_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                token = app.config["SETUP_CSRF_TOKEN"]
                client = app.test_client()

                resp = client.post(
                    "/save",
                    headers={"X-Setup-Token": token},
                    data={
                        "email": "friend@example.com",
                        "keywords": "pizza\nsoup",
                        "locations": ["Crossroads"],
                        "meals": ["Lunch"],
                        "timezone": "America/Los_Angeles",
                        "times": "07:00",
                        "days": ["mon"],
                    },
                )

                self.assertEqual(resp.status_code, 200)
                self.assertTrue((Path(tmpdir) / "config.yaml").exists())


class LocalSchedulerTests(unittest.TestCase):
    def test_launchd_plist_keeps_spaced_paths_as_single_arguments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_dir = Path(tmpdir) / "Application Support" / "cal & dining"
            config_path = config_dir / "config.yaml"
            with (
                patch(
                    "cal_dining_dashboard.local_scheduler.scanner_executable",
                    return_value=["/Users/Test User/.local/bin/cal-dining-scanner"],
                ),
                patch(
                    "cal_dining_dashboard.config_io.config_path",
                    return_value=config_path,
                ),
                patch(
                    "cal_dining_dashboard.config_io.config_dir",
                    return_value=config_dir,
                ),
            ):
                body = local_scheduler._launchd_plist_body()

        self.assertIn(
            "<string>/Users/Test User/.local/bin/cal-dining-scanner</string>",
            body,
        )
        self.assertIn("<string>--config</string>", body)
        self.assertIn(
            "<string>"
            + str(config_path).replace("&", "&amp;")
            + "</string>",
            body,
        )


if __name__ == "__main__":
    unittest.main()
