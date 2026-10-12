"""Integration tests for FastAPI REST API endpoints."""

import unittest
from fastapi.testclient import TestClient

from road_discovery.api import app


class TestAPIEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_health_check(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_list_presets(self):
        response = self.client.get("/api/v1/presets")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tail-of-the-dragon", data)
        self.assertIn("angeles-crest", data)

    def test_serve_navigation_guide(self):
        response = self.client.get("/navigation_guide.html")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("Road Discovery", response.text)

    def test_discover_api_post(self):
        # Discover roads around Warwick, NY (cached in test suite)
        response = self.client.post(
            "/api/discover",
            json={
                "address": "Warwick, NY",
                "radius": 10.0,
                "min_length": 2.5,
                "target_speed": 50.0,
            },
        )
        self.assertEqual(response.status_code, 200)
        geojson = response.json()
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)

        # Verify feature properties structure
        props = geojson["features"][0]["properties"]
        self.assertIn("road_name", props)
        self.assertIn("composite_driving_score", props)
        self.assertIn("length_km", props)
        self.assertIn("google_maps_url", props)
        self.assertIn("rank", props)

    def test_discover_api_empty_address(self):
        response = self.client.post(
            "/api/discover",
            json={
                "address": "   ",
                "radius": 10.0,
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_loop_route_api_post(self):
        # Generate 25 mile loop route around Warwick, NY
        response = self.client.post(
            "/api/route/loop",
            json={
                "address": "Warwick, NY",
                "loop_miles": 25.0,
                "target_speed": 45.0,
            },
        )
        self.assertEqual(response.status_code, 200)
        geojson = response.json()
        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertGreater(len(geojson["features"]), 0)

        # Verify loop properties
        props = geojson["features"][0]["properties"]
        self.assertIn("road_name", props)
        self.assertIn("composite_driving_score", props)
        self.assertIn("cop_risk_pct", props)
        self.assertEqual(geojson["metadata"]["route_mode"], "loop")

    def test_loop_route_api_invalid_address(self):
        # Non-existent geocoding query should return clean HTTP 400 Bad Request
        response = self.client.post(
            "/api/route/loop",
            json={
                "address": "deepark ny non_existent_place_xyz_12345",
                "loop_miles": 25.0,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Could not locate", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
