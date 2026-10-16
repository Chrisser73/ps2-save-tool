from __future__ import annotations

import unittest
from pathlib import Path

from app.main import APP_VERSION, app, health


class ApiContractTests(unittest.TestCase):
    def test_health_contract_matches_the_web_version(self):
        payload = health()
        version_file = Path(__file__).resolve().parents[2] / "VERSION"
        self.assertEqual(APP_VERSION, version_file.read_text(encoding="utf-8").strip())
        self.assertEqual(payload, {"ok": True, "version": APP_VERSION})

    def test_health_route_is_registered(self):
        routes = {route.path: route.methods for route in app.routes}
        self.assertIn("/api/health", routes)
        self.assertIn("GET", routes["/api/health"])
