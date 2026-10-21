
from collections import deque
from dataclasses import dataclass, field
import time

from backend.telemetry.protocol import TelemetryFrame

SEQUENCE_MODULUS = 1 << 16
SEQUENCE_HALF_RANGE = 1 << 15


@dataclass
class ReorderBuffer:
    """Reorder UDP frames with a per-drone timeout window."""

    window_seconds: float = 2.0
    history_limit: int = 4096

    expected_sequence: dict[int, int] = field(default_factory=dict)
    pending: dict[int, dict[int, tuple[float, TelemetryFrame]]] = (
        field(default_factory=dict)
    )
    seen: dict[int, set[int]] = field(default_factory=dict)
    history: dict[int, deque[int]] = field(default_factory=dict)

    duplicates: int = 0
    late_frames: int = 0
    reordered_frames: int = 0

    def __post_init__(self):
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.history_limit < 1:
            raise ValueError("history_limit must be positive")

    @staticmethod
    def _distance(sequence: int, expected: int) -> int:
        """Forward distance on the 16-bit sequence-number ring."""
        return (sequence - expected) % SEQUENCE_MODULUS

    def _remember(self, drone: int, sequence: int) -> None:
        seen = self.seen[drone]
        history = self.history[drone]

        if sequence in seen:
            return

        seen.add(sequence)
        history.append(sequence)

        while len(history) > self.history_limit:
            expired = history.popleft()
            seen.discard(expired)

    def _emit_consecutive(
        self,
        drone: int,
    ) -> list[TelemetryFrame]:
        pending = self.pending[drone]
        expected = self.expected_sequence[drone]
        ready = []

        while expected in pending:
            _, frame = pending.pop(expected)
            ready.append(frame)
            self._remember(drone, expected)
            expected = (expected + 1) % SEQUENCE_MODULUS

        self.expected_sequence[drone] = expected
        return ready

    def push(
        self,
        frame: TelemetryFrame,
        now: float | None = None,
    ) -> list[TelemetryFrame]:
        """Accept a valid decoded frame and emit any ready frames."""
        if now is None:
            now = time.monotonic()

        drone = frame.drone_id
        sequence = frame.sequence

        if drone not in self.expected_sequence:
            self.expected_sequence[drone] = sequence
            self.pending[drone] = {}
            self.seen[drone] = set()
            self.history[drone] = deque()

            self._remember(drone, sequence)
            self.expected_sequence[drone] = (
                sequence + 1
            ) % SEQUENCE_MODULUS
            return [frame]

        pending = self.pending[drone]
        expected = self.expected_sequence[drone]

        if sequence in self.seen[drone] or sequence in pending:
            self.duplicates += 1
            return []

        distance = self._distance(sequence, expected)

        if distance >= SEQUENCE_HALF_RANGE:
            self.late_frames += 1
            return []

        if distance > 0:
            self.reordered_frames += 1

        pending[sequence] = (now, frame)

        ready = self._emit_consecutive(drone)
        ready.extend(self.flush_expired(now=now, drone_id=drone))
        return ready

    def flush_expired(
        self,
        now: float | None = None,
        drone_id: int | None = None,
    ) -> list[TelemetryFrame]:
        """Release buffered frames after their gap has aged two seconds."""
        if now is None:
            now = time.monotonic()

        drones = (
            [drone_id]
            if drone_id is not None
            else list(self.pending)
        )
        ready = []

        for drone in drones:
            pending = self.pending.get(drone)
            if not pending:
                continue

            while pending:
                oldest_arrival = min(
                    arrival for arrival, _ in pending.values()
                )

                if now - oldest_arrival < self.window_seconds:
                    break

                expected = self.expected_sequence[drone]
                nearest = min(
                    pending,
                    key=lambda seq: self._distance(seq, expected),
                )

                # A gap has expired; advance to the nearest buffered frame.
                self.expected_sequence[drone] = nearest
                ready.extend(self._emit_consecutive(drone))

        return ready
