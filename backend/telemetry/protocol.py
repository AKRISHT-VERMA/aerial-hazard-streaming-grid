
import struct
from dataclasses import dataclass

FRAME_SIZE = 29
SENSOR_COUNT = 6
SENSOR_SCALE = 100  # Example scale: 1234 encodes as 12.34 units

# CRC-16/CCITT-FALSE parameters:
# Polynomial 0x1021, initial value 0xFFFF, no reflection, XOR-out 0.
CRC16_POLY = 0x1021
CRC16_INIT = 0xFFFF


@dataclass(frozen=True)
class TelemetryFrame:
    drone_id: int
    sequence: int
    timestamp: int
    latitude: float
    longitude: float
    sensors: tuple[int, int, int, int, int, int]


def crc16_ccitt_false(data: bytes) -> int:
    crc = CRC16_INIT

    for byte in data:
        crc ^= byte << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


def encode_frame(frame: TelemetryFrame) -> bytes:
    if not 0 <= frame.drone_id <= 255:
        raise ValueError("drone_id must fit in 1 byte")

    if not 0 <= frame.sequence <= 65535:
        raise ValueError("sequence must fit in 16 bits")

    if not 0 <= frame.timestamp <= 0xFFFFFFFF:
        raise ValueError("timestamp must fit in 32 bits")

    if len(frame.sensors) != SENSOR_COUNT:
        raise ValueError("Exactly six sensor values are required")

    lat = round(frame.latitude * 1_000_000)
    lon = round(frame.longitude * 1_000_000)

    if not -90_000_000 <= lat <= 90_000_000:
        raise ValueError("Latitude out of range")

    if not -180_000_000 <= lon <= 180_000_000:
        raise ValueError("Longitude out of range")

    for value in frame.sensors:
        if not 0 <= value <= 65535:
            raise ValueError("Sensor values must fit in unsigned 16 bits")

    # 1 + 2 + 4 + 4 + 4 + 12 = 27 bytes before CRC.
    payload = struct.pack(
        ">BHIii6H",
        frame.drone_id,
        frame.sequence,
        frame.timestamp,
        lat,
        lon,
        *frame.sensors,
    )

    checksum = crc16_ccitt_false(payload)
    encoded = payload + struct.pack(">H", checksum)

    assert len(encoded) == FRAME_SIZE
    return encoded


def decode_frame(data: bytes) -> TelemetryFrame:
    if len(data) != FRAME_SIZE:
        raise ValueError(
            f"Invalid frame length: expected {FRAME_SIZE}, got {len(data)}"
        )

    payload = data[:-2]
    received_crc = struct.unpack(">H", data[-2:])[0]
    expected_crc = crc16_ccitt_false(payload)

    if received_crc != expected_crc:
        raise ValueError("CRC16 checksum mismatch")

    fields = struct.unpack(">BHIii6H", payload)
    if not -90_000_000 <= fields[3] <= 90_000_000:
        raise ValueError("Latitude out of range")
    if not -180_000_000 <= fields[4] <= 180_000_000:
        raise ValueError("Longitude out of range")

    return TelemetryFrame(
        drone_id=fields[0],
        sequence=fields[1],
        timestamp=fields[2],
        latitude=fields[3] / 1_000_000,
        longitude=fields[4] / 1_000_000,
        sensors=tuple(fields[5:11]),
    )
