
import argparse
import random
import socket
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.telemetry.protocol import TelemetryFrame, encode_frame


def build_frame(sequence: int, rng: random.Random) -> bytes:
    """Generate one synthetic sensor frame for demonstration only."""
    frame = TelemetryFrame(
        drone_id=1,
        sequence=sequence % 65536,
        timestamp=int(time.time()),
        latitude=18.5204 + rng.uniform(-0.0001, 0.0001),
        longitude=73.8567 + rng.uniform(-0.0001, 0.0001),
        sensors=tuple(rng.randint(100, 2000) for _ in range(6)),
    )
    return encode_frame(frame)


def main():
    parser = argparse.ArgumentParser(
        description="Simulate a lossy, duplicated and reordered UDP drone link."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--loss", type=float, default=0.30)
    parser.add_argument("--duplicate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--batch-size", type=int, default=5)
    args = parser.parse_args()

    if args.count < 1:
        parser.error("--count must be at least 1")
    if not 0 <= args.loss <= 1 or not 0 <= args.duplicate <= 1:
        parser.error("--loss and --duplicate must be between 0 and 1")
    if args.interval < 0 or args.batch_size < 2:
        parser.error("--interval must be non-negative and batch-size at least 2")

    rng = random.Random(args.seed)
    network_rng = random.Random(args.seed + 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    dropped = duplicated = 0
    sent = 0
    out_of_order_transmissions = 0
    previous_sequence = None
    start = time.monotonic()

    print("DATA: synthetic drone telemetry for demonstration only.")

    try:
        # Send the first frame as a stream-start anchor. The receiver needs
        # an initial sequence before it can distinguish late from future data.
        first = build_frame(0, rng)
        sock.sendto(first, (args.host, args.port))
        sent += 1
        previous_sequence = 0

        # Apply impairment to the remaining frames.
        for batch_start in range(1, args.count, args.batch_size):
            batch = []

            batch_end = min(
                batch_start + args.batch_size,
                args.count,
            )

            for sequence in range(batch_start, batch_end):
                packet = build_frame(sequence, rng)

                if network_rng.random() < args.loss:
                    dropped += 1
                    continue

                batch.append((sequence, packet, False))

                if network_rng.random() < args.duplicate:
                    batch.append((sequence, packet, True))
                    duplicated += 1

            # Shuffle each batch to simulate datagrams arriving out of order.
            network_rng.shuffle(batch)

            for sequence, packet, is_duplicate in batch:
                if (
                    previous_sequence is not None
                    and sequence < previous_sequence
                ):
                    out_of_order_transmissions += 1

                sock.sendto(packet, (args.host, args.port))
                sent += 1
                previous_sequence = sequence

                if args.interval:
                    time.sleep(args.interval)

    finally:
        sock.close()

    impaired_originals = max(0, args.count - 1)

    print(f"Original frames generated:       {args.count}")
    print(f"Datagrams sent:                  {sent}")
    print(f"Original frames dropped:         {dropped}")
    print(f"Duplicate datagrams sent:        {duplicated}")
    print(f"Out-of-order transmissions:      {out_of_order_transmissions}")
    print(f"Configured loss rate:            {args.loss:.0%}")
    print(f"Configured duplication rate:     {args.duplicate:.0%}")
    if impaired_originals:
        print(
            "Observed loss among impaired originals: "
            f"{dropped / impaired_originals:.1%}"
        )
    print(f"Random seed:                     {args.seed}")
    print(f"Elapsed seconds:                 {time.monotonic() - start:.2f}")


if __name__ == "__main__":
    main()
