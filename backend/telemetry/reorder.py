
from dataclasses import dataclass, field
import time

from backend.telemetry.protocol import TelemetryFrame


@dataclass
class ReorderBuffer:
    """Per-drone sequence reorder buffer with a bounded time window."""

    window_seconds: float = 2.0
    expected_sequence: dict[int, int] = field(default_factory=dict)
    pending: dict[int, dict[int, tuple[float, TelemetryFrame]]] = field(
        default_factory=dict
    )
    seen: dict[int, set[int]] = field(default_factory=dict)
    last_activity: dict[int, float] = field(default_factory=dict)
    duplicates: int = 0
    late_frames: int = 0

    @staticmethod
    def _distance(sequence: int, expected: int) -> int:
        """Forward distance on the 16-bit sequence-number ring."""
        return (sequence - expected) & 0xFFFF

    def push(
        self,
        frame: TelemetryFrame,
        now: float | None = None,
    ) -> list[TelemetryFrame]:
        """Buffer a frame and return any frames now ready for processing."""
        if now is None:
            now = time.monotonic()

        drone = frame.drone_id
        sequence = frame.sequence

        if drone not in self.expected_sequence:
            self.expected_sequence[drone] = sequence
            self.pending[drone] = {}
            self.seen[drone] = set()

        expected = self.expected_sequence[drone]
        pending = self.pending[drone]
        seen = self.seen[drone]

        if sequence in seen or sequence in pending:
            self.duplicates += 1
            return []

        distance = self._distance(sequence, expected)

        # Half the sequence space distinguishes forward from old packets.
        if distance >= 0x8000:
            self.late_frames += 1
            return []

        pending[sequence] = (now, frame)
        seen.add(sequence)
        self.last_activity[drone] = now

        ready: list[TelemetryFrame] = []

        # Emit consecutive frames in order.
        while expected in pending:
            _, next_frame = pending.pop(expected)
            ready.append(next_frame)
            expected = (expected + 1) & 0xFFFF

        # If a gap persists, wait up to the reorder window, then advance
        # to the nearest buffered sequence. This bounds waiting time.
        while pending:
            oldest_arrival = min(arrival for arrival, _ in pending.values())

            if now - oldest_arrival < self.window_seconds:
                break

            nearest = min(
                pending,
                key=lambda seq: self._distance(seq, expected),
            )
            expected = nearest

            while expected in pending:
                _, next_frame = pending.pop(expected)
                ready.append(next_frame)
                expected = (expected + 1) & 0xFFFF

        self.expected_sequence[drone] = expected

        # Bound duplicate history. Keep recent sequence values near expected.
        if len(seen) > 4096:
            self.seen[drone] = {
                seq for seq in seen
                if self._distance(seq, expected) < 2048
            }

        return ready



    def flush_expired(
        self,
        now: float | None = None,
    ) -> list[TelemetryFrame]:
        """Release buffered frames once their reorder window expires."""
        if now is None:
            now = time.monotonic()

        ready: list[TelemetryFrame] = []

        for drone, pending in self.pending.items():
            expected = self.expected_sequence[drone]

            while pending:
                oldest_arrival = min(
                    arrival for arrival, _ in pending.values()
                )

                if now - oldest_arrival < self.window_seconds:
                    break

                nearest = min(
                    pending,
                    key=lambda seq: self._distance(seq, expected),
                )

                expected = nearest

                while expected in pending:
                    _, frame = pending.pop(expected)
                    ready.append(frame)
                    expected = (expected + 1) & 0xFFFF

            self.expected_sequence[drone] = expected

        return ready
