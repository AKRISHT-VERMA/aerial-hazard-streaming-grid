
import unittest

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.telemetry.protocol import TelemetryFrame
from scripts.udp_receiver import process_ready_frames


ORIGIN_LAT = 18.5204
ORIGIN_LON = 73.8567


def make_frame(sequence, latitude=ORIGIN_LAT, longitude=ORIGIN_LON):
    return TelemetryFrame(
        drone_id=1,
        sequence=sequence,
        timestamp=1_800_000_000 + sequence,
        latitude=latitude,
        longitude=longitude,
        sensors=(100, 200, 300, 400, 500, 600),
    )


class TestUDPGridIntegration(unittest.TestCase):
    def setUp(self):
        self.grid = StreamingHazardGrid(
            origin_latitude=ORIGIN_LAT,
            origin_longitude=ORIGIN_LON,
            cell_size_meters=10.0,
        )

    def test_processed_frame_updates_grid(self):
        frame = make_frame(1)

        count = process_ready_frames([frame], self.grid, 0)

        self.assertEqual(count, 1)
        self.assertEqual(len(self.grid.cells), 1)

        cell_id = self.grid.cell_for(ORIGIN_LAT, ORIGIN_LON)
        cell = self.grid.cells[cell_id]

        self.assertEqual(cell.readings[(1, 0)].value, 100)
        self.assertEqual(cell.readings[(1, 5)].value, 600)

    def test_multiple_frames_update_processed_count(self):
        frames = [make_frame(1), make_frame(2), make_frame(3)]

        count = process_ready_frames(frames, self.grid, 4)

        self.assertEqual(count, 7)
        self.assertEqual(len(self.grid.cells), 1)

    def test_frames_in_different_cells_create_multiple_cells(self):
        first = make_frame(1)
        second = make_frame(
            2,
            latitude=ORIGIN_LAT,
            longitude=ORIGIN_LON + 0.0002,
        )

        count = process_ready_frames(
            [first, second], self.grid, 0
        )

        self.assertEqual(count, 2)
        self.assertEqual(len(self.grid.cells), 2)

    def test_empty_frame_batch_does_not_change_state(self):
        count = process_ready_frames([], self.grid, 5)

        self.assertEqual(count, 5)
        self.assertEqual(len(self.grid.cells), 0)


if __name__ == "__main__":
    unittest.main()
