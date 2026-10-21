
import socket
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.telemetry.protocol import decode_frame
from backend.telemetry.reorder import ReorderBuffer

HOST = "127.0.0.1"
PORT = 9999


def display_frame(frame, processed_count):
    print(
        f"Processed={processed_count} "
        f"Drone={frame.drone_id} "
        f"Seq={frame.sequence} "
        f"Lat={frame.latitude:.6f} "
        f"Lon={frame.longitude:.6f} "
        f"Sensors={frame.sensors}"
    )


def main():
    reorder = ReorderBuffer(window_seconds=2.0)
    received = rejected = processed = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))
        sock.settimeout(0.1)

        print(f"Listening on {HOST}:{PORT}")
        print("Binary protocol, CRC16 validation and reorder buffer enabled.")
        print("Press Ctrl+C to stop.")

        try:
            while True:
                try:
                    data, address = sock.recvfrom(2048)
                except socket.timeout:
                    ready = reorder.flush_expired()
                    for frame in ready:
                        processed += 1
                        display_frame(frame, processed)
                    continue

                received += 1

                try:
                    frame = decode_frame(data)
                except ValueError as exc:
                    rejected += 1
                    print(f"Rejected datagram from {address}: {exc}")
                    continue

                for ready in reorder.push(frame):
                    processed += 1
                    display_frame(ready, processed)

        except KeyboardInterrupt:
            deadline = time.monotonic() + reorder.window_seconds

            while time.monotonic() < deadline:
                for frame in reorder.flush_expired():
                    processed += 1
                    display_frame(frame, processed)

                time.sleep(0.05)

        finally:
            print("\n--- Receiver statistics ---")
            print(f"Datagrams received:        {received}")
            print(f"Invalid frames rejected:   {rejected}")
            print(f"Frames processed:          {processed}")
            print(f"Duplicates suppressed:     {reorder.duplicates}")
            print(f"Late frames rejected:      {reorder.late_frames}")
            print(f"Out-of-order arrivals:     {reorder.reordered_frames}")


if __name__ == "__main__":
    main()
