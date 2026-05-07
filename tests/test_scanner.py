import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import cal_dining_scanner as scanner


FIXTURE = Path(__file__).parent / "fixtures" / "cal_menu_sample.html"


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.html = FIXTURE.read_text(encoding="utf-8")

    def test_parse_menus_uses_cal_dining_selectors(self):
        menus = scanner.parse_menus(
            self.html,
            ["Crossroads", "Foothill", "Clark Kerr"],
            ["Breakfast", "Lunch", "Dinner"],
        )

        self.assertEqual(
            menus["Crossroads"]["Lunch"],
            ["New England Clam Chowder"],
        )
        self.assertEqual(menus["Foothill"]["Lunch"], ["Cheese Pizza"])
        self.assertEqual(
            menus["Clark Kerr"]["Dinner"],
            [
                "Halal Honey Mustard Baked Chicken Thigh",
                "Grilled Paprika Tofu",
            ],
        )

    def test_parse_menus_excludes_non_food_page_text(self):
        menus = scanner.parse_menus(
            self.html,
            ["Crossroads", "Foothill", "Clark Kerr"],
            ["Breakfast", "Lunch", "Dinner"],
        )
        all_items = {
            item
            for meals in menus.values()
            for items in meals.values()
            for item in items
        }

        self.assertNotIn("Today", all_items)
        self.assertNotIn("Center Plate", all_items)
        self.assertNotIn("Medium Carbon Footprint", all_items)
        self.assertNotIn("Vegetarian Option", all_items)

    def test_matches_do_not_bleed_between_locations(self):
        menus = scanner.parse_menus(
            self.html,
            ["Crossroads", "Foothill"],
            ["Lunch"],
        )
        matches = scanner.find_matches(menus, ["pizza", "clam chowder"])

        self.assertEqual(
            matches,
            {
                "Crossroads": {
                    "Lunch": [("New England Clam Chowder", "clam chowder")]
                },
                "Foothill": {
                    "Lunch": [("Cheese Pizza", "pizza")]
                },
            },
        )


class ScheduleTests(unittest.TestCase):
    def test_due_schedule_slot_converts_timezone_and_checks_days(self):
        schedule = {
            "timezone": "America/Los_Angeles",
            "times": ["07:00", "17:45"],
            "days": ["wed"],
            "window_minutes": 20,
        }
        now = datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc)

        slot_id = scanner.due_schedule_slot(schedule, {"sent_slots": []}, now)

        self.assertEqual(slot_id, "2026-05-06T07:00@America/Los_Angeles")

    def test_due_schedule_slot_returns_none_when_not_due(self):
        schedule = {
            "timezone": "America/Los_Angeles",
            "times": ["07:00"],
            "days": ["wed"],
            "window_minutes": 20,
        }
        now = datetime(2026, 5, 6, 13, 59, tzinfo=timezone.utc)

        self.assertIsNone(scanner.due_schedule_slot(schedule, {"sent_slots": []}, now))

    def test_due_schedule_slot_prevents_duplicates(self):
        schedule = {
            "timezone": "America/Los_Angeles",
            "times": ["07:00"],
            "days": ["wed"],
            "window_minutes": 20,
        }
        state = {"sent_slots": ["2026-05-06T07:00@America/Los_Angeles"]}
        now = datetime(2026, 5, 6, 14, 5, tzinfo=timezone.utc)

        self.assertIsNone(scanner.due_schedule_slot(schedule, state, now))


class ConfigAndEmailTests(unittest.TestCase):
    def test_example_config_loads(self):
        config = scanner.load_config(Path("config.example.yaml"))
        scanner.validate_config(config, require_email=False)

        self.assertEqual(config["locations"], ["Crossroads", "Foothill", "Clark Kerr"])
        self.assertEqual(config["schedule"]["times"], ["07:00"])

    def test_placeholder_email_fails_when_sending_required(self):
        config = scanner.load_config(Path("config.example.yaml"))

        with self.assertRaisesRegex(scanner.ScannerError, "real email"):
            scanner.validate_config(config, require_email=True)

    def test_environment_variables_override_config(self):
        with patch.dict(
            os.environ,
            {
                "ALERT_EMAIL": "friend@example.com",
                "KEYWORDS": "pizza\nsushi",
                "LOCATIONS": "Crossroads,Foothill",
                "MEALS": "Lunch,Dinner",
                "SCHEDULE_TIMEZONE": "America/New_York",
                "SCHEDULE_TIMES": "08:30,17:45",
            },
            clear=False,
        ):
            config = scanner.load_config(Path("missing-config.yaml"))
            scanner.validate_config(config, require_email=True)

        self.assertEqual(config["email"], "friend@example.com")
        self.assertEqual(config["keywords"], ["pizza", "sushi"])
        self.assertEqual(config["locations"], ["Crossroads", "Foothill"])
        self.assertEqual(config["meals"], ["Lunch", "Dinner"])
        self.assertEqual(config["schedule"]["timezone"], "America/New_York")
        self.assertEqual(config["schedule"]["times"], ["08:30", "17:45"])

    def test_missing_email_credentials_fail_clearly(self):
        config = {
            "email": "friend@example.com",
            "smtp": {
                "server": "smtp.gmail.com",
                "port": 587,
                "sender_email": "friend@example.com",
            },
        }
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(scanner.ScannerError, "GMAIL_APP_PASSWORD"):
                scanner.send_email(config, "Subject", "<p>Hello</p>")

    def test_email_html_escapes_matches(self):
        body = scanner.build_email_html(
            {"Crossroads": {"Lunch": [("<Pizza & Soup>", 'pizza "special"')]}}
        )

        self.assertIn("&lt;Pizza &amp; Soup&gt;", body)
        self.assertIn("pizza &quot;special&quot;", body)

    def test_dry_run_does_not_send_email(self):
        config = {
            "email": "",
            "locations": ["Crossroads"],
            "meals": ["Lunch"],
            "keywords": ["clam chowder"],
            "schedule": scanner.DEFAULT_CONFIG["schedule"],
            "smtp": scanner.DEFAULT_CONFIG["smtp"],
        }

        def fail_sender(*_args):
            raise AssertionError("dry run should not send email")

        matches = scanner.run_scan(
            config,
            dry_run=True,
            fetcher=lambda: FIXTURE.read_text(encoding="utf-8"),
            email_sender=fail_sender,
        )

        self.assertEqual(
            matches,
            {"Crossroads": {"Lunch": [("New England Clam Chowder", "clam chowder")]}},
        )

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state = {"sent_slots": []}
            scanner.mark_slot_sent(state, "2026-05-06T07:00@America/Los_Angeles")
            scanner.save_state(state, state_path)

            self.assertEqual(
                scanner.load_state(state_path),
                {"sent_slots": ["2026-05-06T07:00@America/Los_Angeles"]},
            )


if __name__ == "__main__":
    unittest.main()
