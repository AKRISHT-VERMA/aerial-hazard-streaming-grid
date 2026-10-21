import json
import unittest
from unittest.mock import patch

from backend.grid.spatial_grid import GridCapacityError, StreamingHazardGrid
from backend.service import HazardService
from backend.telemetry.protocol import TelemetryFrame
from backend.weather import DemoWeatherProvider, OpenMeteoWeatherProvider


class TestBoundedPipelineState(unittest.IsolatedAsyncioTestCase):
    async def test_stale_cell_eviction_preserves_alert_and_aligns_predictions(self):
        now = [1_000.0]
        grid = StreamingHazardGrid(
            0, 0, max_cells=2, cell_retention_seconds=10,
            stale_after_seconds=1, clock=lambda: now[0],
        )
        service = HazardService(
            grid, weather=DemoWeatherProvider(),
            prediction_retention_seconds=10, clock=lambda: now[0],
        )

        def frame(sequence, longitude, sensors):
            return TelemetryFrame(1, sequence, int(now[0]), 0, longitude, sensors)

        await service.process_frame(frame(1, 0, (3_000,) * 6))
        alert = service.alerts.snapshot()[0]
        await service.process_frame(frame(2, .001, (100,) * 6))
        self.assertEqual(len(service.snapshot()["cells"]), 2)

        now[0] = 1_012.0
        await service.process_frame(frame(3, .002, (200,) * 6))
        state = service.snapshot()
        retained_ids = {tuple(cell["cell_id"]) for cell in state["cells"]}
        prediction_ids = {tuple(map(int, key.split(","))) for key in state["predictions"]}
        self.assertEqual(retained_ids, prediction_ids)
        self.assertIn(tuple(alert["cell_id"]), retained_ids)
        self.assertEqual(state["alerts"][0]["alert_id"], alert["alert_id"])
        self.assertEqual(grid.cell_evictions, 1)
        self.assertEqual(service.prediction_evictions, 1)

        # The protected active-alert cell and a fresh cell cannot be evicted.
        now[0] = 1_013.0
        dropped = await service.process_frame(frame(4, .003, (100,) * 6))
        self.assertIsNone(dropped)
        self.assertEqual(grid.dropped_updates, 1)
        self.assertEqual(len(grid.cells), 2)
        self.assertEqual(service.alerts.snapshot()[0]["alert_id"], alert["alert_id"])

    async def test_grid_reading_limit_drops_without_growth(self):
        now = [100.0]
        grid = StreamingHazardGrid(
            0, 0, max_cells=1, max_drones_per_cell=1,
            cell_retention_seconds=500, stale_after_seconds=200,
            clock=lambda: now[0],
        )
        grid.update(0, 0, 0, (1,) * 6, timestamp=100)
        with self.assertRaises(GridCapacityError):
            grid.update(1, 0, 0, (2,) * 6, timestamp=100)
        self.assertEqual(len(grid.cells), 1)
        self.assertEqual(len(next(iter(grid.cells.values())).readings), 6)
        self.assertEqual(grid.dropped_updates, 1)

    def test_weather_cache_expires_and_enforces_lru_capacity(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self):
                return json.dumps({"current": {
                    "temperature_2m": 20, "wind_speed_10m": 2,
                    "wind_direction_10m": 180,
                }}).encode()

        provider = OpenMeteoWeatherProvider(cache_seconds=60, cache_max_entries=2)
        with patch("backend.weather.urlopen", return_value=Response()), \
             patch("backend.weather.time.monotonic", return_value=0):
            provider.get(1, 1)
            provider.get(2, 2)
            provider.get(3, 3)
            self.assertEqual(provider.health()["cache_size"], 2)
            self.assertEqual(provider.capacity_evictions, 1)
        with patch("backend.weather.time.monotonic", return_value=61):
            self.assertEqual(provider.health()["cache_size"], 0)
            self.assertEqual(provider.expired_evictions, 2)


if __name__ == "__main__":
    unittest.main()

