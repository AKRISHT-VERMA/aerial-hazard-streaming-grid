import importlib.util
import unittest

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.live_api import create_app
from backend.risk import RiskPrediction
from backend.service import HazardService


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
                     "install requirements.txt and httpx for HTTP integration checks")
class TestLiveAPI(unittest.TestCase):
    def make_service(self):
        return HazardService(StreamingHazardGrid(18.5204, 73.8567))

    def test_dashboard_and_live_endpoints(self):
        from fastapi.testclient import TestClient
        service = self.make_service()
        with TestClient(create_app(service, udp_port=0, database_url="")) as client:
            self.assertIn("Aerial Hazard Streaming Grid", client.get("/").text)
            self.assertIn("Spatial grid (10 m cells)", client.get("/").text)
            self.assertEqual(client.get("/health").json()["status"], "ok")
            self.assertEqual(client.get("/api/cells").json(), [])
            self.assertEqual(client.get("/api/alerts").json(), [])

    def test_alert_acknowledgement_and_websocket_snapshot(self):
        from fastapi.testclient import TestClient
        service = self.make_service()
        alert = service.alerts.observe((0, 0), RiskPrediction(.8, "high", "test", False))
        with TestClient(create_app(service, udp_port=0, database_url="")) as client:
            response = client.post(
                f"/api/alerts/{alert.alert_id}/acknowledge", json={"user": "operator"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["acknowledged_by"], "operator")
            with client.websocket_connect("/ws") as websocket:
                state = websocket.receive_json()
            self.assertIn("prediction_notice", state)
            self.assertEqual(state["alerts"][0]["acknowledged_by"], "operator")


if __name__ == "__main__":
    unittest.main()
