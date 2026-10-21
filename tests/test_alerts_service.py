import asyncio
import unittest

from backend.alerts import AlertManager
from backend.grid.spatial_grid import StreamingHazardGrid
from backend.risk import RiskPrediction
from backend.service import HazardService
from backend.telemetry.protocol import TelemetryFrame
from backend.weather import DemoWeatherProvider


class TestAlertManager(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.manager = AlertManager(clock=lambda: self.now)

    def prediction(self, value):
        return RiskPrediction(value, "high" if value >= .65 else "low", "test", False)

    def test_activation_hysteresis_escalation_and_clear(self):
        key = (2, 3)
        self.assertIsNone(self.manager.observe(key, self.prediction(.64)))
        alert = self.manager.observe(key, self.prediction(.7))
        self.assertEqual(alert.severity, "warning")
        self.manager.acknowledge(alert.alert_id, "operator")
        self.now += 1
        alert = self.manager.observe(key, self.prediction(.9))
        self.assertEqual(alert.severity, "critical")
        self.assertIsNone(alert.acknowledged_by)
        self.assertEqual(alert.state, "active")
        still_active = self.manager.observe(key, self.prediction(.5))
        self.assertEqual(still_active.state, "active")
        cleared = self.manager.observe(key, self.prediction(.39))
        self.assertEqual(cleared.state, "cleared")
        self.assertEqual(self.manager.snapshot(), [])

    def test_acknowledgement_requires_operator(self):
        alert = self.manager.observe((0, 0), self.prediction(.8))
        with self.assertRaises(ValueError):
            self.manager.acknowledge(alert.alert_id, " ")
        with self.assertRaises(ValueError):
            self.manager.acknowledge(alert.alert_id, 12)
        with self.assertRaises(ValueError):
            self.manager.acknowledge(alert.alert_id, "x" * 129)


class TestHazardService(unittest.IsolatedAsyncioTestCase):
    async def test_weather_failure_keeps_telemetry_live_with_missing_features(self):
        class OfflineWeather:
            def get(self, latitude, longitude):
                raise OSError("offline")

        service = HazardService(StreamingHazardGrid(18.5204, 73.8567), weather=OfflineWeather())
        frame = TelemetryFrame(1, 2, 1_800_000_001, 18.5204, 73.8567,
                               (100, 200, 300, 400, 500, 600))
        result = await service.process_frame(frame)
        self.assertEqual(result["weather"]["source"], "unavailable")
        self.assertIsNone(result["weather"]["temperature"])
        self.assertEqual(len(service.snapshot()["cells"]), 1)

    async def test_process_snapshot_labels_demo_scores_and_weather(self):
        service = HazardService(
            StreamingHazardGrid(18.5204, 73.8567),
            weather=DemoWeatherProvider(),
        )
        frame = TelemetryFrame(1, 1, 1_800_000_000, 18.5204, 73.8567,
                               (1900, 1800, 1700, 1600, 1550, 1500))
        result = await service.process_frame(frame)
        state = service.snapshot()
        self.assertTrue(result["synthetic_prediction"])
        self.assertTrue(result["weather"]["synthetic"])
        self.assertIn("no safety-validated model", state["prediction_notice"])
        self.assertEqual(state["revision"], 1)
        self.assertEqual(len(state["cells"]), 1)

    async def test_persistence_acknowledgement_is_awaited(self):
        class FailingStore:
            async def acknowledge_alert(self, alert):
                await asyncio.sleep(0)
                raise RuntimeError("unavailable")

        service = HazardService(StreamingHazardGrid(18.5204, 73.8567), persistence=FailingStore())
        alert = service.alerts.observe((0, 0), RiskPrediction(.8, "high", "test", False))
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            await service.acknowledge(alert.alert_id, "operator")
        self.assertIsNone(service.alerts.snapshot()[0]["acknowledged_by"])


if __name__ == "__main__":
    unittest.main()
