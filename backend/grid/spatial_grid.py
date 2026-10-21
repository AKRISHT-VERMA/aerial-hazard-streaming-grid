
"""In-memory spatial hazard grid for streaming drone telemetry.

Uses a fixed local tangent-plane approximation for a small industrial site.
All timestamps are Unix epoch seconds, matching TelemetryFrame.timestamp.
No third-party dependencies are required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
from typing import Any

from backend.telemetry.protocol import TelemetryFrame


EARTH_RADIUS_METERS = 6_371_000.0
DEFAULT_CELL_SIZE_METERS = 10.0
DEFAULT_CONFIDENCE_HALF_LIFE_SECONDS = 30.0
DEFAULT_STALE_AFTER_SECONDS = 10.0
SENSOR_COUNT = 6


@dataclass(frozen=True)
class SensorReading:
    drone_id: int
    sensor_index: int
    value: int
    timestamp: float


@dataclass
class HazardCell:
    cell_id: tuple[int, int]
    center_latitude: float
    center_longitude: float
    # Multiple drones can report the same sensor type in one cell.
    readings: dict[tuple[int, int], SensorReading] = field(
        default_factory=dict
    )

    @property
    def last_updated(self) -> float | None:
        if not self.readings:
            return None
        return max(reading.timestamp for reading in self.readings.values())


class StreamingHazardGrid:
    """Maintain live cell state independently of PostgreSQL."""

    def __init__(
        self,
        origin_latitude: float,
        origin_longitude: float,
        cell_size_meters: float = DEFAULT_CELL_SIZE_METERS,
        confidence_half_life_seconds: float = (
            DEFAULT_CONFIDENCE_HALF_LIFE_SECONDS
        ),
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    ) -> None:
        self._validate_coordinates(origin_latitude, origin_longitude)

        if cell_size_meters <= 0:
            raise ValueError("cell_size_meters must be positive")
        if confidence_half_life_seconds <= 0:
            raise ValueError("confidence half-life must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale threshold must be positive")

        cosine = math.cos(math.radians(origin_latitude))
        if abs(cosine) < 1e-6:
            raise ValueError("The local grid origin is too close to a pole")

        self.origin_latitude = float(origin_latitude)
        self.origin_longitude = float(origin_longitude)
        self.cell_size_meters = float(cell_size_meters)
        self.confidence_half_life_seconds = float(
            confidence_half_life_seconds
        )
        self.stale_after_seconds = float(stale_after_seconds)
        self._origin_cosine = cosine

        self.cells: dict[tuple[int, int], HazardCell] = {}

    @staticmethod
    def _validate_coordinates(latitude: float, longitude: float) -> None:
        if not math.isfinite(latitude) or not -90 <= latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")
        if not math.isfinite(longitude) or not -180 <= longitude <= 180:
            raise ValueError(
                "longitude must be finite and between -180 and 180"
            )

    def _to_local_meters(
        self,
        latitude: float,
        longitude: float,
    ) -> tuple[float, float]:
        self._validate_coordinates(latitude, longitude)

        east = (
            EARTH_RADIUS_METERS
            * self._origin_cosine
            * math.radians(longitude - self.origin_longitude)
        )
        north = (
            EARTH_RADIUS_METERS
            * math.radians(latitude - self.origin_latitude)
        )
        return east, north

    def _cell_center(
        self,
        cell_id: tuple[int, int],
    ) -> tuple[float, float]:
        x, y = cell_id
        east = (x + 0.5) * self.cell_size_meters
        north = (y + 0.5) * self.cell_size_meters

        latitude = self.origin_latitude + math.degrees(
            north / EARTH_RADIUS_METERS
        )
        longitude = self.origin_longitude + math.degrees(
            east / (EARTH_RADIUS_METERS * self._origin_cosine)
        )
        return latitude, longitude

    def cell_for(
        self,
        latitude: float,
        longitude: float,
    ) -> tuple[int, int]:
        """Map GPS coordinates to a stable local 10-metre cell ID."""
        east, north = self._to_local_meters(latitude, longitude)
        return (
            math.floor(east / self.cell_size_meters),
            math.floor(north / self.cell_size_meters),
        )

    def update_frame(
        self,
        frame: TelemetryFrame,
    ) -> HazardCell:
        """Consume a decoded frame without changing the UDP protocol."""
        return self.update(
            drone_id=frame.drone_id,
            latitude=frame.latitude,
            longitude=frame.longitude,
            sensor_values=frame.sensors,
            timestamp=float(frame.timestamp),
        )

    def update(
        self,
        drone_id: int,
        latitude: float,
        longitude: float,
        sensor_values: tuple[int, ...],
        timestamp: float | None = None,
    ) -> HazardCell:
        """Update the latest non-older reading for each sensor."""
        if not isinstance(drone_id, int) or not 0 <= drone_id <= 255:
            raise ValueError("drone_id must be an integer from 0 to 255")

        if timestamp is None:
            timestamp = time.time()
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("timestamp must be finite and non-negative")

        self._validate_coordinates(latitude, longitude)

        if len(sensor_values) != SENSOR_COUNT:
            raise ValueError("Exactly six sensor values are required")

        for value in sensor_values:
            if not isinstance(value, int) or not 0 <= value <= 65535:
                raise ValueError(
                    "Sensor values must be unsigned 16-bit integers"
                )

        cell_id = self.cell_for(latitude, longitude)
        cell = self.cells.get(cell_id)

        if cell is None:
            center_lat, center_lon = self._cell_center(cell_id)
            cell = HazardCell(
                cell_id=cell_id,
                center_latitude=center_lat,
                center_longitude=center_lon,
            )
            self.cells[cell_id] = cell

        for sensor_index, value in enumerate(sensor_values):
            key = (drone_id, sensor_index)
            previous = cell.readings.get(key)

            # A delayed/out-of-order frame must not replace newer data.
            if previous is None or timestamp >= previous.timestamp:
                cell.readings[key] = SensorReading(
                    drone_id=drone_id,
                    sensor_index=sensor_index,
                    value=value,
                    timestamp=float(timestamp),
                )

        return cell

    def confidence(
        self,
        reading: SensorReading,
        now: float | None = None,
    ) -> float:
        """Return exponential confidence, halving every configured half-life."""
        if now is None:
            now = time.time()
        if not math.isfinite(now):
            raise ValueError("now must be finite")

        age = max(0.0, now - reading.timestamp)
        return math.exp2(-age / self.confidence_half_life_seconds)

    def is_stale(
        self,
        cell: HazardCell,
        now: float | None = None,
    ) -> bool:
        """Mark a cell stale when its newest reading exceeds the threshold."""
        if now is None:
            now = time.time()
        if not math.isfinite(now):
            raise ValueError("now must be finite")

        last_updated = cell.last_updated
        return (
            last_updated is None
            or now - last_updated > self.stale_after_seconds
        )

    def neighbours(
        self,
        cell_id: tuple[int, int],
    ) -> list[HazardCell]:
        """Return existing cells in the eight immediately adjacent positions."""
        x, y = cell_id
        result: list[HazardCell] = []

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                neighbour = self.cells.get((x + dx, y + dy))
                if neighbour is not None:
                    result.append(neighbour)

        return result

    def _aggregate_sensors(
        self,
        cells: list[HazardCell],
        now: float,
    ) -> list[float]:
        """Confidence-weighted average for each of the six sensor types."""
        totals = [0.0] * SENSOR_COUNT
        weights = [0.0] * SENSOR_COUNT

        for cell in cells:
            for reading in cell.readings.values():
                weight = self.confidence(reading, now)
                index = reading.sensor_index
                totals[index] += reading.value * weight
                weights[index] += weight

        return [
            totals[index] / weights[index] if weights[index] else 0.0
            for index in range(SENSOR_COUNT)
        ]

    def fused_features(
        self,
        cell_id: tuple[int, int],
        *,
        weather_temperature: float | None = None,
        weather_wind_speed: float | None = None,
        weather_wind_direction: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Build a deterministic cell/neighbour feature payload.

        Sensor values are confidence-weighted. Missing sensor types are
        represented by 0.0 and should be accompanied by validity indicators
        when preparing the final trained model's feature vector.
        """
        if now is None:
            now = time.time()

        cell = self.cells.get(cell_id)
        if cell is None:
            raise KeyError(f"Unknown cell: {cell_id}")

        for name, value in (
            ("weather_temperature", weather_temperature),
            ("weather_wind_speed", weather_wind_speed),
            ("weather_wind_direction", weather_wind_direction),
        ):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")

        adjacent = self.neighbours(cell_id)

        return {
            "cell_id": list(cell_id),
            "center_sensor_values": self._aggregate_sensors([cell], now),
            "neighbour_sensor_values": self._aggregate_sensors(
                adjacent, now
            ),
            "neighbour_cell_count": len(adjacent),
            "weather_temperature": weather_temperature,
            "weather_wind_speed": weather_wind_speed,
            "weather_wind_direction": weather_wind_direction,
        }

    def snapshot(
        self,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        """Return a JSON-serializable view for a live-map endpoint."""
        if now is None:
            now = time.time()

        result = []

        for cell in sorted(
            self.cells.values(),
            key=lambda item: item.cell_id,
        ):
            readings = []

            for key in sorted(cell.readings):
                reading = cell.readings[key]
                readings.append({
                    "drone_id": reading.drone_id,
                    "sensor_index": reading.sensor_index,
                    "value": reading.value,
                    "timestamp": reading.timestamp,
                    "confidence": round(self.confidence(reading, now), 6),
                })

            result.append({
                "cell_id": list(cell.cell_id),
                "latitude": cell.center_latitude,
                "longitude": cell.center_longitude,
                "last_updated": cell.last_updated,
                "stale": self.is_stale(cell, now),
                "readings": readings,
            })

        return result
