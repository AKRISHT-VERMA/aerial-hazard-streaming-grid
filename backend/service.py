"""Connect decoded telemetry to scoring, alerts, persistence and live state."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
import time
from typing import Any

from backend.alerts import AlertManager
from backend.grid.spatial_grid import StreamingHazardGrid
from backend.grid.spatial_grid import GridCapacityError
from backend.risk import DemoThresholdModel, RiskModel
from backend.weather import DemoWeatherProvider, WeatherProvider

logger = logging.getLogger(__name__)


class HazardService:
    def __init__(self, grid: StreamingHazardGrid, *,
                 model: RiskModel | None = None,
                 weather: WeatherProvider | None = None,
                 alerts: AlertManager | None = None,
                 persistence: Any | None = None,
                 synthetic_input: bool = False,
                 prediction_retention_seconds: float = 300.0,
                 prediction_capacity: int | None = None,
                 clock=time.monotonic) -> None:
        if prediction_retention_seconds <= 0:
            raise ValueError("prediction_retention_seconds must be positive")
        prediction_capacity = grid.max_cells if prediction_capacity is None else prediction_capacity
        if not isinstance(prediction_capacity, int) or prediction_capacity < 1:
            raise ValueError("prediction_capacity must be a positive integer")
        self.grid = grid
        self.model = model or DemoThresholdModel()
        self.weather = weather or DemoWeatherProvider()
        self.alerts = alerts or AlertManager()
        self.persistence = persistence
        self.synthetic_input = synthetic_input
        self.revision = 0
        self.last_prediction: dict[tuple[int, int], dict[str, Any]] = {}
        self.prediction_retention_seconds = prediction_retention_seconds
        self.prediction_capacity = prediction_capacity
        self.clock = clock
        self._prediction_times: dict[tuple[int, int], float] = {}
        self.prediction_evictions = 0
        self.dropped_predictions = 0
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._processing_lock = asyncio.Lock()
        self.weather_failures = 0
        self.weather_available: bool | None = None

    async def process_frame(self, frame) -> dict[str, Any]:
        # Keep frames serialized so slow weather responses cannot reorder model
        # updates after the UDP reorder buffer has already released them.
        async with self._processing_lock:
            self._prune_predictions()
            try:
                cell = self.grid.update_frame(
                    frame, protected_cell_ids=self.alerts.active_cell_ids()
                )
            except GridCapacityError:
                # The grid records this controlled drop; do not create a score
                # or alert for telemetry whose state could not be retained.
                self._broadcast()
                return None
            for evicted_id in self.grid.take_evicted_cell_ids():
                if evicted_id in self.last_prediction:
                    del self.last_prediction[evicted_id]
                    self._prediction_times.pop(evicted_id, None)
                    self.prediction_evictions += 1
            try:
                weather = await asyncio.to_thread(
                    self.weather.get, cell.center_latitude, cell.center_longitude
                )
                weather_values = (weather.temperature, weather.wind_speed,
                                  weather.wind_direction)
                weather_payload = asdict(weather)
                self.weather_available = True
            except Exception as exc:
                logger.warning("Weather lookup unavailable (%s)", type(exc).__name__)
                self.weather_failures += 1
                self.weather_available = False
                weather_values = (None, None, None)
                weather_payload = {
                    "temperature": None, "wind_speed": None,
                    "wind_direction": None, "observed_at": time.time(),
                    "source": "unavailable", "synthetic": False,
                }
            features = self.grid.fused_features(
                cell.cell_id,
                weather_temperature=weather_values[0],
                weather_wind_speed=weather_values[1],
                weather_wind_direction=weather_values[2],
                now=self.grid.clock(),
            )
            prediction = self.model.predict(features)
            alert = self.alerts.observe(cell.cell_id, prediction)
            result = {
                "cell_id": list(cell.cell_id),
                "risk": prediction.risk,
                "risk_label": prediction.label,
                "prediction_source": prediction.source,
                "synthetic_prediction": prediction.synthetic,
                "synthetic_input": self.synthetic_input,
                "weather": weather_payload,
                "alert": alert.to_dict() if alert else None,
            }
            if cell.cell_id not in self.last_prediction and len(self.last_prediction) >= self.prediction_capacity:
                candidates = [key for key in self.last_prediction
                              if key not in self.alerts.active_cell_ids()]
                if candidates:
                    oldest = min(candidates, key=lambda key: self._prediction_times.get(key, 0))
                    del self.last_prediction[oldest]
                    self._prediction_times.pop(oldest, None)
                    self.prediction_evictions += 1
                else:
                    self.dropped_predictions += 1
                    self.revision += 1
                    self._broadcast()
                    return result
            self.last_prediction[cell.cell_id] = result
            self._prediction_times[cell.cell_id] = self.clock()
            self.revision += 1
            if self.persistence is not None:
                self.persistence.enqueue_reading(frame, result)
            self._broadcast()
            return result

    async def acknowledge(self, alert_id: str, user: str) -> dict:
        async with self._processing_lock:
            alert = self.alerts.acknowledge(alert_id, user)
            try:
                if self.persistence is not None:
                    await self.persistence.acknowledge_alert(alert)
            except Exception:
                alert.acknowledged_by = None
                alert.acknowledged_at = None
                raise
            self.revision += 1
            self._broadcast()
            return alert.to_dict()

    def snapshot(self) -> dict[str, Any]:
        self._prune_predictions()
        cells = self.grid.snapshot()
        cell_ids = {tuple(cell["cell_id"]) for cell in cells}
        predictions = {f"{key[0]},{key[1]}": value
                       for key, value in self.last_prediction.items()
                       if key in cell_ids}
        return {
            "revision": self.revision,
            "cells": cells,
            "alerts": self.alerts.snapshot(),
            "predictions": predictions,
            "synthetic_input": self.synthetic_input,
            "prediction_notice": (
                "This demonstration has no safety-validated model. Synthetic sensor data, weather, and scores are labeled where used."
            ),
        }

    def _prune_predictions(self) -> None:
        now = self.clock()
        protected = self.alerts.active_cell_ids()
        for key, inserted in list(self._prediction_times.items()):
            if (key not in protected
                    and now - inserted > self.prediction_retention_seconds):
                self._prediction_times.pop(key, None)
                self.last_prediction.pop(key, None)
                self.prediction_evictions += 1

    def state_health(self) -> dict[str, int | float]:
        self._prune_predictions()
        return {
            "prediction_count": len(self.last_prediction),
            "prediction_capacity": self.prediction_capacity,
            "prediction_evictions": self.prediction_evictions,
            "dropped_predictions": self.dropped_predictions,
        }

    def subscribe(self, max_pending: int = 1) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=max_pending)
        self._subscribers.add(queue)
        self._loop = asyncio.get_running_loop()
        queue.put_nowait(self.snapshot())
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def refresh(self) -> None:
        """Publish a clock tick so quiet cells can become visibly stale."""
        self.revision += 1
        self._broadcast()

    def _broadcast(self) -> None:
        if self._loop is None or not self._subscribers:
            return
        snapshot = self.snapshot()

        def publish() -> None:
            for queue in tuple(self._subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()  # Keep the newest full-state snapshot.
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(snapshot)

        self._loop.call_soon_threadsafe(publish)

