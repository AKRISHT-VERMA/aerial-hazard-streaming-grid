
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
DEFAULT_CELL_RETENTION_SECONDS = 300.0
DEFAULT_MAX_CELLS = 1_000
DEFAULT_MAX_DRONES_PER_CELL = 32
SENSOR_COUNT = 6


class GridCapacityError(RuntimeError):
    """A new cell or drone could not be retained within configured limits."""


@dataclass(frozen=True)
class SensorReading:
    drone_id: int
    sensor_index: int
    value: int
    timestamp: float
    received_at: float


@dataclass
class HazardCell:
    cell_id: tuple[int, int]
    center_latitude: float
    center_longitude: float
    # Multiple drones can report the same sensor type in one cell.
    readings: dict[tuple[int, int], SensorReading] = field(
        default_factory=dict
    )
    last_received_at: float = 0.0

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
        cell_retention_seconds: float = DEFAULT_CELL_RETENTION_SECONDS,
        max_cells: int = DEFAULT_MAX_CELLS,
        max_drones_per_cell: int = DEFAULT_MAX_DRONES_PER_CELL,
        clock=time.time,
    ) -> None:
        self._validate_coordinates(origin_latitude, origin_longitude)

        if cell_size_meters <= 0:
            raise ValueError("cell_size_meters must be positive")
        if confidence_half_life_seconds <= 0:
            raise ValueError("confidence half-life must be positive")
        if stale_after_seconds <= 0:
            raise ValueError("stale threshold must be positive")
        if cell_retention_seconds <= 0:
            raise ValueError("cell retention must be positive")
        if not isinstance(max_cells, int) or max_cells < 1:
            raise ValueError("max_cells must be a positive integer")
        if (not isinstance(max_drones_per_cell, int)
                or not 1 <= max_drones_per_cell <= 256):
            raise ValueError("max_drones_per_cell must be between 1 and 256")

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
        self.cell_retention_seconds = float(cell_retention_seconds)
        self.max_cells = max_cells
        self.max_drones_per_cell = max_drones_per_cell
        self.clock = clock
        self._origin_cosine = cosine

        self.cells: dict[tuple[int, int], HazardCell] = {}
        self.cell_evictions = 0
        self.reading_evictions = 0
        self.dropped_updates = 0
        self._evicted_cell_ids: list[tuple[int, int]] = []

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
        *,
        protected_cell_ids: set[tuple[int, int]] | None = None,
    ) -> HazardCell:
        """Consume a decoded frame without changing the UDP protocol."""
        return self.update(
            drone_id=frame.drone_id,
            latitude=frame.latitude,
            longitude=frame.longitude,
            sensor_values=frame.sensors,
            timestamp=float(frame.timestamp),
            protected_cell_ids=protected_cell_ids,
        )

    def update(
        self,
        drone_id: int,
        latitude: float,
        longitude: float,
        sensor_values: tuple[int, ...],
        timestamp: float | None = None,
        *,
        protected_cell_ids: set[tuple[int, int]] | None = None,
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
        now = self.clock()
        protected = protected_cell_ids or set()

        self._evict_expired_cells(now, protected)
        cell = self.cells.get(cell_id)

        if cell is None:
            if len(self.cells) >= self.max_cells:
                eligible = [item for item in self.cells.values()
                            if item.cell_id not in protected
                            and self.is_stale(item, now)]
                if eligible:
                    self._remove_cell(min(eligible, key=lambda item: item.last_updated or 0).cell_id)
                else:
                    self.dropped_updates += 1
                    raise GridCapacityError("grid capacity is full; no stale unalerted cell can be evicted")
            center_lat, center_lon = self._cell_center(cell_id)
            cell = HazardCell(
                cell_id=cell_id,
                center_latitude=center_lat,
                center_longitude=center_lon,
            )
            self.cells[cell_id] = cell

        self._prune_cell_readings(cell, now)
        drones = {drone for drone, _ in cell.readings}
        if drone_id not in drones and len(drones) >= self.max_drones_per_cell:
            stale_drones = []
            for existing_drone in drones:
                readings = [r for (d, _), r in cell.readings.items() if d == existing_drone]
                newest = max((r.timestamp for r in readings), default=0)
                if now - newest > self.stale_after_seconds:
                    stale_drones.append((newest, existing_drone))
            if stale_drones:
                _, expired_drone = min(stale_drones)
                for key in [key for key in cell.readings if key[0] == expired_drone]:
                    del cell.readings[key]
                    self.reading_evictions += 1
            else:
                self.dropped_updates += 1
                raise GridCapacityError("cell reading capacity is full; no stale drone reading can be evicted")

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
                    received_at=now,
                )

        cell.last_received_at = now

        return cell

    def _remove_cell(self, cell_id: tuple[int, int]) -> None:
        self.cells.pop(cell_id, None)
        self.cell_evictions += 1
        self._evicted_cell_ids.append(cell_id)

    def _evict_expired_cells(self, now: float, protected: set[tuple[int, int]]) -> None:
        for cell_id, cell in list(self.cells.items()):
            self._prune_cell_readings(cell, now)
            last_updated = cell.last_updated
            if (cell_id not in protected
                    and now - cell.last_received_at > self.cell_retention_seconds):
                self._remove_cell(cell_id)

    def _prune_cell_readings(self, cell: HazardCell, now: float) -> None:
        for key, reading in list(cell.readings.items()):
            if now - reading.received_at > self.cell_retention_seconds:
                del cell.readings[key]
                self.reading_evictions += 1

    def take_evicted_cell_ids(self) -> list[tuple[int, int]]:
        evicted, self._evicted_cell_ids = self._evicted_cell_ids, []
        return evicted

    def health(self) -> dict[str, int | float]:
        return {
            "cell_count": len(self.cells), "cell_capacity": self.max_cells,
            "cell_evictions": self.cell_evictions,
            "reading_count": sum(len(cell.readings) for cell in self.cells.values()),
            "reading_capacity_per_cell": self.max_drones_per_cell * SENSOR_COUNT,
            "reading_evictions": self.reading_evictions,
            "dropped_updates": self.dropped_updates,
        }

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

    def _sensor_validity(self, cells: list[HazardCell], now: float) -> list[bool]:
        """Mark a sensor type valid when at least one weighted sample remains."""
        weights = [0.0] * SENSOR_COUNT
        for cell in cells:
            for reading in cell.readings.values():
                weights[reading.sensor_index] += self.confidence(reading, now)
        return [weight > 0.0 for weight in weights]

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

        weather_values = [
            weather_temperature,
            weather_wind_speed,
            weather_wind_direction,
        ]
        return {
            "cell_id": list(cell_id),
            "center_sensor_values": self._aggregate_sensors([cell], now),
            "center_sensor_validity": self._sensor_validity([cell], now),
            "neighbour_sensor_values": self._aggregate_sensors(
                adjacent, now
            ),
            "neighbour_sensor_validity": self._sensor_validity(adjacent, now),
            "neighbour_cell_count": len(adjacent),
            "weather_temperature": weather_temperature,
            "weather_wind_speed": weather_wind_speed,
            "weather_wind_direction": weather_wind_direction,
            "weather_validity": [value is not None for value in weather_values],
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

