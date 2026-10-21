
import unittest
import struct

from backend.telemetry.protocol import (
    FRAME_SIZE,
    TelemetryFrame,
    crc16_ccitt_false,
    decode_frame,
    encode_frame,
)


class TestTelemetryProtocol(unittest.TestCase):
    def setUp(self):
        self.frame = TelemetryFrame(
            drone_id=7,
            sequence=1234,
            timestamp=1792600000,
            latitude=18.5204,
            longitude=73.8567,
            sensors=(100, 200, 300, 400, 500, 600),
        )

    def test_crc_reference_vector(self):
        self.assertEqual(
            crc16_ccitt_false(b"123456789"),
            0x29B1,
        )

    def test_frame_is_exactly_29_bytes(self):
        encoded = encode_frame(self.frame)
        self.assertEqual(len(encoded), FRAME_SIZE)

    def test_round_trip(self):
        decoded = decode_frame(encode_frame(self.frame))

        self.assertEqual(decoded.drone_id, self.frame.drone_id)
        self.assertEqual(decoded.sequence, self.frame.sequence)
        self.assertEqual(decoded.timestamp, self.frame.timestamp)
        self.assertAlmostEqual(
            decoded.latitude, self.frame.latitude, places=6
        )
        self.assertAlmostEqual(
            decoded.longitude, self.frame.longitude, places=6
        )
        self.assertEqual(decoded.sensors, self.frame.sensors)

    def test_corrupted_frame_is_rejected(self):
        encoded = bytearray(encode_frame(self.frame))
        encoded[10] ^= 0x01

        with self.assertRaisesRegex(ValueError, "CRC16"):
            decode_frame(bytes(encoded))

    def test_crc_valid_frame_with_invalid_coordinates_is_rejected(self):
        payload = struct.pack(">BHIii6H", 1, 1, 100, 91_000_000, 0, 1, 2, 3, 4, 5, 6)
        packet = payload + struct.pack(">H", crc16_ccitt_false(payload))
        with self.assertRaisesRegex(ValueError, "Latitude"):
            decode_frame(packet)

    def test_truncated_frame_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "length"):
            decode_frame(b"\x00" * 28)

    def test_extra_bytes_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "length"):
            decode_frame(b"\x00" * 30)

    def test_sequence_boundaries(self):
        for sequence in (0, 65535):
            frame = TelemetryFrame(
                drone_id=1,
                sequence=sequence,
                timestamp=100,
                latitude=0.0,
                longitude=0.0,
                sensors=(0, 0, 0, 0, 0, 0),
            )
            self.assertEqual(
                decode_frame(encode_frame(frame)).sequence,
                sequence,
            )


if __name__ == "__main__":
    unittest.main()
