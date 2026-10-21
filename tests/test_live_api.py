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
        app = create_app(service, udp_port=0, database_url="")
        with TestClient(app) as client:
            self.assertIn("Aerial Hazard Streaming Grid", client.get("/").text)
            self.assertIn("Spatial grid (10 m cells)", client.get("/").text)
            health = client.get("/health").json()
            self.assertEqual(health["status"], "healthy")
            self.assertEqual(health["persistence"]["status"], "disabled")
            self.assertEqual(health["udp"]["status"], "healthy")
            self.assertEqual(health["weather"]["status"], "unknown")
            self.assertEqual(client.get("/api/cells").json(), [])
            self.assertEqual(client.get("/api/alerts").json(), [])
            app.state.udp_protocol.queue_dropped = 2
            health = client.get("/health").json()
            self.assertEqual(health["status"], "degraded")
            self.assertEqual(health["udp"]["dropped_frames"], 2)

    def test_health_propagates_database_degraded_and_unavailable_states(self):
        from fastapi.testclient import TestClient

        class FakePersistence:
            current_status = "degraded"

            def health(self):
                return {"status": self.current_status, "queue_depth": 0,
                        "queue_capacity": 10, "queue_saturations": 1,
                        "failed_batches": 1, "failed_readings": 1,
                        "last_error": "ConnectionError"}

            async def close(self):
                return None

        store = FakePersistence()
        app = create_app(self.make_service(), udp_port=0, database_url="")
        with TestClient(app) as client:
            app.state.persistence = store
            health = client.get("/health").json()
            self.assertEqual(health["status"], "degraded")
            self.assertEqual(health["persistence"]["failed_batches"], 1)
            store.current_status = "unavailable"
            response = client.get("/health")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["status"], "unavailable")

    def test_weather_health_degrades_then_recovers(self):
        from fastapi.testclient import TestClient
        service = self.make_service()
        app = create_app(service, udp_port=0, database_url="")
        with TestClient(app) as client:
            service.weather_available = False
            service.weather_failures = 1
            failed = client.get("/health").json()
            self.assertEqual(failed["weather"]["status"], "degraded")
            self.assertEqual(failed["status"], "degraded")
            service.weather_available = True
            recovered = client.get("/health").json()
            self.assertEqual(recovered["weather"]["status"], "healthy")
            self.assertEqual(recovered["status"], "healthy")

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

    def test_invalid_udp_port_fails_application_startup(self):
        from fastapi.testclient import TestClient
        with self.assertRaises(ValueError):
            with TestClient(create_app(self.make_service(), udp_port=65536, database_url="")):
                pass

    def test_unexpected_udp_transport_loss_marks_health_unavailable_and_shuts_down(self):
        import time
        from fastapi.testclient import TestClient

        app = create_app(self.make_service(), udp_port=0, database_url="")
        with TestClient(app) as client:
            protocol = app.state.udp_protocol
            loop = app.state.udp_task.get_loop()
            loop.call_soon_threadsafe(protocol.transport.close)

            deadline = time.monotonic() + 3
            response = None
            while time.monotonic() < deadline:
                response = client.get("/health")
                if response.status_code == 503:
                    break
                time.sleep(0.02)

            self.assertIsNotNone(response)
            self.assertEqual(response.status_code, 503)
            self.assertFalse(protocol.transport_alive)
            self.assertEqual(response.json()["udp"]["status"], "unavailable")
            self.assertEqual(response.json()["udp"]["transport_loss"], "closed")
            self.assertEqual(response.json()["udp"]["listener_failure"], "UDPTransportLost")
            self.assertTrue(app.state.udp_task.done())


if __name__ == "__main__":
    unittest.main()

