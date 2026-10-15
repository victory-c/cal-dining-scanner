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


@patch(
    "cal_dining_dashboard.local_scheduler.status",
    return_value={"installed": False, "details": "test"},
)
class DashboardHostCheckTests(unittest.TestCase):
    """DNS rebinding: a page on attacker.example that re-resolves to 127.0.0.1 is
    same-origin with the dashboard, but its requests still carry its own Host."""

    REBOUND = "http://attacker.example:8765"

    def test_rebound_host_cannot_read_setup_token(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.get("/", base_url=self.REBOUND)

                self.assertEqual(resp.status_code, 403)
                self.assertNotIn(
                    app.config["SETUP_CSRF_TOKEN"], resp.get_data(as_text=True)
                )

    def test_rebound_host_cannot_send_stored_password_to_github(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / ".env").write_text('GMAIL_APP_PASSWORD="stored-secret"\n')
            with (
                patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)),
                patch(
                    "cal_dining_dashboard.github_setup.configure",
                    return_value={"repo_url": "x", "actions_url": "x"},
                ) as configure,
            ):
                app = dashboard_app.create_app()
                client = app.test_client()

                resp = client.post(
                    "/github/configure",
                    base_url=self.REBOUND,
                    headers={"X-Setup-Token": app.config["SETUP_CSRF_TOKEN"]},
                    data={"email": "friend@example.com", "github_token": "attacker"},
                )

                self.assertEqual(resp.status_code, 403)
                configure.assert_not_called()
                self.assertFalse((Path(tmpdir) / "config.yaml").exists())

    def test_loopback_hosts_are_allowed(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app()
                client = app.test_client()

                for base_url in (
                    "http://127.0.0.1:8765",
                    "http://localhost:8765",
                    "http://[::1]:8765",
                ):
                    with self.subTest(base_url=base_url):
                        resp = client.get("/", base_url=base_url)
                        self.assertEqual(resp.status_code, 200)
                        self.assertIn(
                            app.config["SETUP_CSRF_TOKEN"],
                            resp.get_data(as_text=True),
                        )

    def test_explicit_bind_host_is_allowed(self, _status):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("cal_dining_scanner.user_config_dir", return_value=Path(tmpdir)):
                app = dashboard_app.create_app(bind_host="192.168.1.20")
                client = app.test_client()

                ok = client.get("/", base_url="http://192.168.1.20:8765")
                rebound = client.get("/", base_url=self.REBOUND)

                self.assertEqual(ok.status_code, 200)
                self.assertEqual(rebound.status_code, 403)


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
