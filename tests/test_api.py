import unittest

from fastapi.testclient import TestClient

from raceguard.api import app


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_csv_upload_returns_analysis(self) -> None:
        with open("examples/sample_race.csv", "rb") as sample:
            response = self.client.post(
                "/api/analyze",
                files={"file": ("sample_race.csv", sample, "text/csv")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["assessment"], "review_recommended")

    def test_fit_rider_id_is_read_from_upload_form(self) -> None:
        response = self.client.post(
            "/api/analyze",
            data={"rider_id": "142"},
            files={"file": ("activity.fit", b"not-a-fit-file", "application/octet-stream")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn("rider_id is required", response.text)
        self.assertIn("invalid FIT file", response.text)


if __name__ == "__main__":
    unittest.main()
