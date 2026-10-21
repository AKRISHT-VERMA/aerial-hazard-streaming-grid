"""In-memory alert state machine with hysteresis and acknowledgement hooks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import threading
import time
from typing import Callable
from uuid import uuid4

from backend.risk import RiskPrediction


@dataclass
class HazardAlert:
    alert_id: str
    cell_id: tuple[int, int]
    state: str
    severity: str
    risk: float
    prediction_source: str
    synthetic: bool
    first_seen: float
    last_seen: float
    acknowledged_by: str | None = None
    acknowledged_at: float | None = None

    def to_dict(self) -> dict:
        result = asdict(self)
        result["cell_id"] = list(self.cell_id)
        return result


class AlertManager:
    """Activate above ``activate_at``; clear only below ``clear_below``."""

    def __init__(self, activate_at: float = 0.65, clear_below: float = 0.4,
                 escalation_at: float = 0.85,
                 clock: Callable[[], float] = time.time) -> None:
        if not (0 <= clear_below < activate_at <= escalation_at <= 1):
            raise ValueError("thresholds must satisfy 0 <= clear < activate <= escalation <= 1")
        self.activate_at = activate_at
        self.clear_below = clear_below
        self.escalation_at = escalation_at
        self.clock = clock
        self._alerts: dict[tuple[int, int], HazardAlert] = {}
        self._lock = threading.RLock()

    def observe(self, cell_id: tuple[int, int], prediction: RiskPrediction) -> HazardAlert | None:
        if not 0 <= prediction.risk <= 1:
            raise ValueError("risk must be between zero and one")
        now = self.clock()
        with self._lock:
            current = self._alerts.get(cell_id)
            if current is None:
                if prediction.risk < self.activate_at:
                    return None
                current = HazardAlert(
                    str(uuid4()), cell_id, "active", self._severity(prediction.risk),
                    prediction.risk, prediction.source, prediction.synthetic, now, now,
                )
                self._alerts[cell_id] = current
                return current

            current.last_seen = now
            current.risk = prediction.risk
            current.prediction_source = prediction.source
            current.synthetic = prediction.synthetic
            if prediction.risk < self.clear_below:
                current.state = "cleared"
                del self._alerts[cell_id]
                return current
            severity = self._severity(prediction.risk)
            if severity == "critical" and current.severity != "critical":
                current.acknowledged_by = None
                current.acknowledged_at = None
            current.severity = severity
            return current

    def acknowledge(self, alert_id: str, user: str) -> HazardAlert:
        if not user or not user.strip():
            raise ValueError("acknowledging user is required")
        with self._lock:
            for alert in self._alerts.values():
                if alert.alert_id == alert_id:
                    alert.acknowledged_by = user.strip()
                    alert.acknowledged_at = self.clock()
                    return alert
        raise KeyError(f"Active alert not found: {alert_id}")

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [self._alerts[key].to_dict() for key in sorted(self._alerts)]

    def _severity(self, risk: float) -> str:
        return "critical" if risk >= self.escalation_at else "warning"
