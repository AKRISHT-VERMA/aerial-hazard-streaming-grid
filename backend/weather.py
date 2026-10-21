"""Weather data providers. Live weather is cached; demo values are labeled."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from collections import OrderedDict
import threading
import time
from typing import Protocol
from urllib.parse import urlencode
from urllib.request import urlopen


@dataclass(frozen=True)
class WeatherObservation:
    temperature: float
    wind_speed: float
    wind_direction: float
    observed_at: float
    source: str
    synthetic: bool = False


class WeatherProvider(Protocol):
    def get(self, latitude: float, longitude: float) -> WeatherObservation: ...


class DemoWeatherProvider:
    """Fixed synthetic weather for repeatable local demonstrations."""

    def __init__(self, temperature: float = 25.0, wind_speed: float = 2.0,
                 wind_direction: float = 180.0) -> None:
        values = (temperature, wind_speed, wind_direction)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("demo weather values must be finite")
        self.values = values

    def get(self, latitude: float, longitude: float) -> WeatherObservation:
        return WeatherObservation(*self.values, time.time(), "synthetic-demo", True)


class OpenMeteoWeatherProvider:
    """Fetch current conditions from Open-Meteo without an API key."""

    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, cache_seconds: float = 60.0, timeout_seconds: float = 3.0,
                 cache_max_entries: int = 1024) -> None:
        if cache_seconds <= 0 or timeout_seconds <= 0:
            raise ValueError("cache and timeout values must be positive")
        if not isinstance(cache_max_entries, int) or cache_max_entries < 1:
            raise ValueError("cache_max_entries must be a positive integer")
        self.cache_seconds = cache_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_max_entries = cache_max_entries
        self._cache: OrderedDict[tuple[float, float], tuple[float, WeatherObservation]] = OrderedDict()
        self._cache_lock = threading.RLock()
        self.expired_evictions = 0
        self.capacity_evictions = 0
        self.cache_hits = 0

    def get(self, latitude: float, longitude: float) -> WeatherObservation:
        if not math.isfinite(latitude) or not -90 <= latitude <= 90:
            raise ValueError("latitude must be finite and between -90 and 90")
        if not math.isfinite(longitude) or not -180 <= longitude <= 180:
            raise ValueError("longitude must be finite and between -180 and 180")
        key = (round(latitude, 3), round(longitude, 3))
        now = time.monotonic()
        with self._cache_lock:
            for old_key, (inserted, _) in list(self._cache.items()):
                if now - inserted >= self.cache_seconds:
                    del self._cache[old_key]
                    self.expired_evictions += 1
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                self.cache_hits += 1
                return cached[1]
        query = urlencode({"latitude": key[0], "longitude": key[1],
                           "current": "temperature_2m,wind_speed_10m,wind_direction_10m"})
        with urlopen(f"{self.endpoint}?{query}", timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read())
        current = payload["current"]
        observation = WeatherObservation(
            float(current["temperature_2m"]),
            float(current["wind_speed_10m"]),
            float(current["wind_direction_10m"]),
            time.time(), "open-meteo", False,
        )
        if not all(math.isfinite(value) for value in (
            observation.temperature, observation.wind_speed, observation.wind_direction
        )):
            raise ValueError("weather provider returned non-finite values")
        with self._cache_lock:
            # Another caller may have filled the key during the network request.
            self._cache[key] = (time.monotonic(), observation)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max_entries:
                self._cache.popitem(last=False)
                self.capacity_evictions += 1
        return observation

    def health(self) -> dict[str, int]:
        now = time.monotonic()
        with self._cache_lock:
            for key, (inserted, _) in list(self._cache.items()):
                if now - inserted >= self.cache_seconds:
                    del self._cache[key]
                    self.expired_evictions += 1
            return {
                "cache_size": len(self._cache),
                "cache_capacity": self.cache_max_entries,
                "cache_expired_evictions": self.expired_evictions,
                "cache_capacity_evictions": self.capacity_evictions,
                "cache_hits": self.cache_hits,
            }

