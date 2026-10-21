
import argparse
import random
import socket
import time
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.telemetry.protocol import TelemetryFrame, encode_frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--loss", type=float, default=0.30)
    parser.add_argument("--duplicate", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--interval", type=float, default=0.02)
    args = parser.parse_args()

    if not 0 <= args.loss <= 1:
        parser.error("--loss must be between 0 and 1")
    if not 0 <= args.duplicate <= 1:
        parser.error("--duplicate must be between 0 and 1")
    if args.count < 1 or args.interval < 0:
        parser.error("count must be positive and interval non-negative")

    rng = random.Random(args.seed)
    rng_network = random.Random(args.seed + 1)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    sent = dropped = duplicated = 0
    started = time.time()

    try:
        for seq in range(args.count):
            frame = TelemetryFrame(
                drone_id=1,
                sequence=seq % 65536,
                timestamp=int(time.time()),
                latitude=18.5204 + rng.uniform(-0.0001, 0.0001),
                longitude=73.8567 + rng.uniform(-0.0001, 0.0001),
                sensors=tuple(rng.randint(100, 2000) for _ in range(6)),
            )
            packet = encode_frame(frame)

            if rng_network.random() < args.loss:
                dropped += 1
                continue

            sock.sendto(packet, (args.host, args.port))
            sent += 1

            if rng_network.random() < args.duplicate:
                sock.sendto(packet, (args.host, args.port))
                duplicated += 1
                sent += 1

            if args.interval:
                time.sleep(args.interval)

    finally:
        sock.close()

    print(f"Original frames generated: {args.count}")
    print(f"Datagrams sent:            {sent}")
    print(f"Original frames dropped:   {dropped}")
    print(f"Duplicate datagrams sent:  {duplicated}")
    print(f"Configured loss rate:      {args.loss:.0%}")
    print(f"Configured duplication:    {args.duplicate:.0%}")
    print(f"Elapsed seconds:            {time.time() - started:.2f}")
    print("Note: This simulator does not yet implement packet reordering.")


if __name__ == "__main__":
    main()
