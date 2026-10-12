import unittest
import os
import shutil
import tempfile
from fastapi.testclient import TestClient
from road_discovery.api import app
from road_discovery.qa import RoadQAEngine


class TestRoadQA(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.qa_file = os.path.join(self.test_dir, "qa_feedback.json")
        self.qa_engine = RoadQAEngine(qa_file=self.qa_file)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_add_and_retrieve_qa_feedback(self):
        record = self.qa_engine.add_feedback(
            road_name="Hawks Nest (NY-97)",
            user_rating=5,
            algo_score=85.0,
            suggested_score=95.0,
            tags=["great_sweepers", "great_elevation"],
            notes="Incredible twisty cliffside road",
            location="Hawks Nest, NY"
        )
        self.assertEqual(record["user_rating"], 5)
        self.assertEqual(record["road_name"], "Hawks Nest (NY-97)")

        summary = self.qa_engine.get_qa_summary()
        self.assertEqual(summary["total_ratings"], 1)
        self.assertIn("great_sweepers", summary["tag_counts"])

    def test_api_qa_rate_and_calibrate(self):
        res = self.client.post("/api/qa/rate", json={
            "road_name": "White Meadow Avenue",
            "user_rating": 2,
            "algo_score": 62.0,
            "suggested_score": 25.0,
            "tags": ["too_residential", "low_speed_limit"],
            "notes": "Too many houses and driveways",
            "location": "Ringwood, NJ"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "success")

        # Test QA Summary endpoint
        summary_res = self.client.get("/api/qa/summary")
        self.assertEqual(summary_res.status_code, 200)
        summary_data = summary_res.json()
        self.assertGreaterEqual(summary_data["total_ratings"], 1)

        # Test Algorithm Calibration endpoint
        cal_res = self.client.post("/api/qa/calibrate")
        self.assertEqual(cal_res.status_code, 200)
        cal_data = cal_res.json()
        self.assertEqual(cal_data["status"], "success")


if __name__ == "__main__":
    unittest.main()
