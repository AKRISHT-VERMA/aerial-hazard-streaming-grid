
import json
import math
import unittest

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.telemetry.protocol import TelemetryFrame


ORIGIN_LAT = 18.5204
ORIGIN_LON = 73.8567
SENSORS_A = (100, 200, 300, 400, 500, 600)
SENSORS_B = (200, 300, 400, 500, 600, 700)


class TestStreamingHazardGrid(unittest.TestCase):
    def setUp(self):
        self.grid = StreamingHazardGrid(
            origin_latitude=ORIGIN_LAT,
            origin_longitude=ORIGIN_LON,
            cell_size_meters=10,
            confidence_half_life_seconds=30,
            stale_after_seconds=10,
        )

    def update(self, sensors=SENSORS_A, timestamp=100, drone_id=1):
        return self.grid.update(
            drone_id=drone_id,
            latitude=ORIGIN_LAT,
            longitude=ORIGIN_LON,
            sensor_values=sensors,
            timestamp=timestamp,
        )

    def test_update_creates_one_cell_with_six_readings(self):
        cell = self.update()
        self.assertEqual(len(self.grid.cells), 1)
        self.assertEqual(len(cell.readings), 6)
        self.assertEqual(cell.cell_id, (0, 0))

    def test_same_coordinates_map_to_same_cell(self):
        first = self.grid.cell_for(ORIGIN_LAT, ORIGIN_LON)
        second = self.grid.cell_for(ORIGIN_LAT + 0.000001, ORIGIN_LON)
        self.assertEqual(first, second)

    def test_points_ten_metres_apart_cross_cell_boundary(self):
        # At this latitude, 0.0001 longitude degrees is roughly 10.5 m.
        first = self.grid.cell_for(ORIGIN_LAT, ORIGIN_LON)
        second = self.grid.cell_for(ORIGIN_LAT, ORIGIN_LON + 0.0001)
        self.assertNotEqual(first, second)

    def test_multiple_drones_are_kept_separately(self):
        self.update(sensors=SENSORS_A, drone_id=1)
        cell = self.update(
            sensors=SENSORS_B, timestamp=101, drone_id=2
        )

        self.assertEqual(len(cell.readings), 12)
        self.assertEqual(cell.readings[(1, 0)].value, 100)
        self.assertEqual(cell.readings[(2, 0)].value, 200)

    def test_older_reading_does_not_overwrite_newer(self):
        self.update(sensors=SENSORS_A, timestamp=200)
        cell = self.update(
            sensors=(1, 2, 3, 4, 5, 6), timestamp=150
        )

        self.assertEqual(cell.readings[(1, 0)].value, 100)
        self.assertEqual(cell.readings[(1, 0)].timestamp, 200)

    def test_confidence_is_one_at_reading_time(self):
        cell = self.update(timestamp=100)
        reading = cell.readings[(1, 0)]
        self.assertAlmostEqual(self.grid.confidence(reading, now=100), 1.0)

    def test_confidence_halves_after_one_half_life(self):
        cell = self.update(timestamp=100)
        reading = cell.readings[(1, 0)]
        self.assertAlmostEqual(self.grid.confidence(reading, now=130), 0.5)

    def test_confidence_never_exceeds_one_for_future_reading(self):
        cell = self.update(timestamp=200)
        reading = cell.readings[(1, 0)]
        self.assertAlmostEqual(self.grid.confidence(reading, now=100), 1.0)

    def test_cell_stale_threshold(self):
        cell = self.update(timestamp=100)
        self.assertFalse(self.grid.is_stale(cell, now=110))
        self.assertTrue(self.grid.is_stale(cell, now=110.01))

    def test_neighbour_lookup_finds_adjacent_cell(self):
        origin_id = self.grid.cell_for(ORIGIN_LAT, ORIGIN_LON)
        adjacent_id = (origin_id[0] + 1, origin_id[1])
        lat, lon = self.grid._cell_center(adjacent_id)

        self.grid.update(
            drone_id=2,
            latitude=lat,
            longitude=lon,
            sensor_values=SENSORS_A,
            timestamp=100,
        )

        neighbours = self.grid.neighbours(origin_id)
        self.assertIn(adjacent_id, [cell.cell_id for cell in neighbours])

    def test_fused_features_include_sensor_and_weather_data(self):
        cell = self.update(timestamp=100)
        features = self.grid.fused_features(
            cell.cell_id,
            weather_temperature=29.5,
            weather_wind_speed=4.0,
            weather_wind_direction=180.0,
            now=100,
        )

        self.assertEqual(features["center_sensor_values"], list(SENSORS_A))
        self.assertEqual(features["neighbour_cell_count"], 0)
        self.assertEqual(features["weather_temperature"], 29.5)
        self.assertEqual(features["weather_wind_speed"], 4.0)
        self.assertEqual(features["weather_wind_direction"], 180.0)

    def test_snapshot_is_json_serializable_and_shows_stale(self):
        self.update(timestamp=100)
        snapshot = self.grid.snapshot(now=111)

        encoded = json.dumps(snapshot)
        decoded = json.loads(encoded)

        self.assertEqual(len(decoded), 1)
        self.assertTrue(decoded[0]["stale"])
        self.assertEqual(len(decoded[0]["readings"]), 6)

    def test_invalid_coordinates_are_rejected(self):
        with self.assertRaises(ValueError):
            self.grid.update(
                1, 91, ORIGIN_LON, SENSORS_A, timestamp=100
            )

    def test_invalid_sensor_count_is_rejected(self):
        with self.assertRaises(ValueError):
            self.grid.update(
                1, ORIGIN_LAT, ORIGIN_LON, (1, 2, 3), timestamp=100
            )

    def test_invalid_sensor_value_is_rejected(self):
        with self.assertRaises(ValueError):
            self.grid.update(
                1, ORIGIN_LAT, ORIGIN_LON,
                (1, 2, 3, 4, 5, 65536), timestamp=100
            )

    def test_invalid_half_life_is_rejected(self):
        with self.assertRaises(ValueError):
            StreamingHazardGrid(
                ORIGIN_LAT,
                ORIGIN_LON,
                confidence_half_life_seconds=0,
            )

    def test_frame_adapter_uses_frame_timestamp_and_values(self):
        frame = TelemetryFrame(
            drone_id=7,
            sequence=123,
            timestamp=500,
            latitude=ORIGIN_LAT,
            longitude=ORIGIN_LON,
            sensors=SENSORS_A,
        )

        cell = self.grid.update_frame(frame)
        self.assertEqual(cell.readings[(7, 0)].value, 100)
        self.assertEqual(cell.readings[(7, 0)].timestamp, 500)


if __name__ == "__main__":
    unittest.main()
