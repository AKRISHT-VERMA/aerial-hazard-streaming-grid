"""Connect decoded telemetry to scoring, alerts, persistence and live state."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
import time
from typing import Any

from backend.alerts import AlertManager
from backend.grid.spatial_grid import StreamingHazardGrid
from backend.risk import DemoThresholdModel, RiskModel
from backend.weather import DemoWeatherProvider, WeatherProvider

logger = logging.getLogger(__name__)


class HazardService:
    def __init__(self, grid: StreamingHazardGrid, *,
                 model: RiskModel | None = None,
                 weather: WeatherProvider | None = None,
                 alerts: AlertManager | None = None,
                 persistence: Any | None = None,
                 synthetic_input: bool = False) -> None:
        self.grid = grid
        self.model = model or DemoThresholdModel()
        self.weather = weather or DemoWeatherProvider()
        self.alerts = alerts or AlertManager()
        self.persistence = persistence
        self.synthetic_input = synthetic_input
        self.revision = 0
        self.last_prediction: dict[tuple[int, int], dict[str, Any]] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._processing_lock = asyncio.Lock()

    async def process_frame(self, frame) -> dict[str, Any]:
        # Keep frames serialized so slow weather responses cannot reorder model
        # updates after the UDP reorder buffer has already released them.
        async with self._processing_lock:
            cell = self.grid.update_frame(frame)
            try:
                weather = await asyncio.to_thread(
                    self.weather.get, cell.center_latitude, cell.center_longitude
                )
                weather_values = (weather.temperature, weather.wind_speed,
                                  weather.wind_direction)
                weather_payload = asdict(weather)
            except Exception as exc:
                logger.warning("Weather lookup unavailable (%s)", type(exc).__name__)
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
            self.last_prediction[cell.cell_id] = result
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
        predictions = {f"{key[0]},{key[1]}": value
                       for key, value in self.last_prediction.items()}
        return {
            "revision": self.revision,
            "cells": self.grid.snapshot(),
            "alerts": self.alerts.snapshot(),
            "predictions": predictions,
            "synthetic_input": self.synthetic_input,
            "prediction_notice": (
                "This demonstration has no safety-validated model. Synthetic sensor data, weather, and scores are labeled where used."
            ),
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
