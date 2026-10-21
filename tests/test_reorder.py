
import unittest

from backend.telemetry.protocol import TelemetryFrame
from backend.telemetry.reorder import ReorderBuffer


def frame(sequence: int, drone_id: int = 1) -> TelemetryFrame:
    return TelemetryFrame(
        drone_id=drone_id,
        sequence=sequence,
        timestamp=1000 + sequence,
        latitude=18.5204,
        longitude=73.8567,
        sensors=(100, 200, 300, 400, 500, 600),
    )


class TestReorderBuffer(unittest.TestCase):
    def test_consecutive_frames(self):
        buffer = ReorderBuffer()
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(10), now=0)],
            [10],
        )
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(11), now=0.1)],
            [11],
        )

    def test_duplicate_is_suppressed(self):
        buffer = ReorderBuffer()
        buffer.push(frame(10), now=0)
        self.assertEqual(buffer.push(frame(10), now=0.1), [])
        self.assertEqual(buffer.duplicates, 1)

    def test_out_of_order_frames_are_reordered(self):
        buffer = ReorderBuffer()
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(10), now=0)],
            [10],
        )
        self.assertEqual(buffer.push(frame(12), now=0.1), [])
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(11), now=0.2)],
            [11, 12],
        )

    def test_gap_advances_after_window(self):
        buffer = ReorderBuffer(window_seconds=2.0)
        buffer.push(frame(10), now=0)
        buffer.push(frame(12), now=0.1)
        result = buffer.push(frame(13), now=2.2)
        self.assertEqual([f.sequence for f in result], [12, 13])

    def test_sequence_wraparound(self):
        buffer = ReorderBuffer()
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(65535), now=0)],
            [65535],
        )
        self.assertEqual(
            [f.sequence for f in buffer.push(frame(0), now=0.1)],
            [0],
        )

    def test_old_frame_is_rejected(self):
        buffer = ReorderBuffer()
        buffer.push(frame(20), now=0)
        self.assertEqual(buffer.push(frame(19), now=0.1), [])
        self.assertEqual(buffer.late_frames, 1)

    
    def test_flush_expired_releases_buffered_frames(self):
        buffer = ReorderBuffer(window_seconds=2.0)

        buffer.push(frame(10), now=0)
        buffer.push(frame(12), now=0.1)

        # Before expiry, the missing sequence keeps frame 12 buffered.
        self.assertEqual(buffer.flush_expired(now=1.9), [])

        # Once the two-second window expires, frame 12 is released.
        result = buffer.flush_expired(now=2.2)
        self.assertEqual([f.sequence for f in result], [12])


if __name__ == "__main__":
    unittest.main()
