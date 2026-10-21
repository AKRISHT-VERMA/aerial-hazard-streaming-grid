"""Risk model adapters for live hazard scoring.

Production scores require a trusted, trained scikit-learn SVM artifact. The
threshold model in this module is for demonstrations and tests only; its
output is explicitly marked as synthetic and must never be presented as a
validated safety prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Protocol

FEATURE_COLUMNS = (
    tuple(f"center_sensor_{i}" for i in range(6))
    + tuple(f"center_sensor_{i}_valid" for i in range(6))
    + tuple(f"neighbour_sensor_{i}" for i in range(6))
    + tuple(f"neighbour_sensor_{i}_valid" for i in range(6))
    + ("weather_temperature", "weather_wind_speed", "weather_wind_direction")
    + ("weather_temperature_valid", "weather_wind_speed_valid", "weather_wind_direction_valid")
)
FEATURE_COUNT = len(FEATURE_COLUMNS)


@dataclass(frozen=True)
class RiskPrediction:
    risk: float
    label: str
    source: str
    synthetic: bool


class RiskModel(Protocol):
    def predict(self, features: dict[str, Any]) -> RiskPrediction: ...


def feature_vector(features: dict[str, Any]) -> list[float]:
    """Validate and flatten grid features in the model's stable column order."""
    center = features.get("center_sensor_values")
    adjacent = features.get("neighbour_sensor_values")
    if not isinstance(center, (list, tuple)) or len(center) != 6:
        raise ValueError("center_sensor_values must contain six values")
    if not isinstance(adjacent, (list, tuple)) or len(adjacent) != 6:
        raise ValueError("neighbour_sensor_values must contain six values")

    center_valid = features.get("center_sensor_validity", [True] * 6)
    adjacent_valid = features.get("neighbour_sensor_validity", [False] * 6)
    weather_names = ("weather_temperature", "weather_wind_speed", "weather_wind_direction")
    weather = [features.get(name) for name in weather_names]
    weather_valid = features.get("weather_validity", [value is not None for value in weather])
    for name, flags, expected in (
        ("center_sensor_validity", center_valid, 6),
        ("neighbour_sensor_validity", adjacent_valid, 6),
        ("weather_validity", weather_valid, 3),
    ):
        if not isinstance(flags, (list, tuple)) or len(flags) != expected:
            raise ValueError(f"{name} has the wrong number of validity flags")
    values = [*center, *center_valid, *adjacent, *adjacent_valid,
              *(0.0 if v is None else v for v in weather), *weather_valid]
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError("model features must be finite")
    return result


class DemoThresholdModel:
    """Deterministic synthetic scorer for local demos; not safety validated."""

    source = "synthetic-demo-threshold"

    def __init__(self, sensor_threshold: float = 1500.0) -> None:
        if not math.isfinite(sensor_threshold) or sensor_threshold <= 0:
            raise ValueError("sensor_threshold must be positive and finite")
        self.sensor_threshold = sensor_threshold

    def predict(self, features: dict[str, Any]) -> RiskPrediction:
        values = feature_vector(features)
        peak = max((*values[0:6], *values[12:18]), default=0.0)
        risk = min(1.0, max(0.0, peak / (2.0 * self.sensor_threshold)))
        label = "high" if peak >= self.sensor_threshold else "low"
        return RiskPrediction(risk, label, self.source, synthetic=True)


class SVMRiskModel:
    """Load a trusted scikit-learn SVM pipeline from a local joblib artifact."""

    source = "trained-svm"

    def __init__(self, artifact_path: str | Path) -> None:
        try:
            import joblib
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the 'model' extra to load an SVM") from exc
        path = Path(artifact_path)
        if not path.is_file():
            raise FileNotFoundError(f"SVM artifact not found: {path}")
        # joblib artifacts execute Python pickle code. Only load an artifact
        # obtained from a trusted model-training pipeline.
        self.model = joblib.load(path)
        if not callable(getattr(self.model, "predict", None)):
            raise ValueError("SVM artifact must expose predict()")

    def predict(self, features: dict[str, Any]) -> RiskPrediction:
        vector = feature_vector(features)
        label = str(self.model.predict([vector])[0]).lower()
        if label not in {"low", "medium", "high"}:
            raise ValueError(f"Unsupported SVM risk label: {label}")
        probabilities = getattr(self.model, "predict_proba", None)
        if callable(probabilities):
            classes = [str(value).lower() for value in self.model.classes_]
            risk = float(probabilities([vector])[0][classes.index("high")]) if "high" in classes else float(label == "high")
        else:
            risk = {"low": 0.0, "medium": 0.5, "high": 1.0}[label]
        if not math.isfinite(risk) or not 0 <= risk <= 1:
            raise ValueError("SVM risk probability must be between 0 and 1")
        return RiskPrediction(risk, label, self.source, synthetic=False)
