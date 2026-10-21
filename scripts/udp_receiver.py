
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


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    sock.settimeout(0.1)

    reorder = ReorderBuffer(window_seconds=2.0)

    accepted = rejected = processed = 0

    print(f"Listening on {HOST}:{PORT}")
    print("CRC validation and per-drone reorder buffer enabled.")

    try:
        while True:
            try:
                data, address = sock.recvfrom(2048)
            except socket.timeout:
                for ready in reorder.flush_expired():
                    processed += 1
                    print(
                        f"Processed={processed} "
                        f"Drone={ready.drone_id} "
                        f"Seq={ready.sequence} "
                        f"Lat={ready.latitude:.6f} "
                        f"Lon={ready.longitude:.6f} "
                        f"Sensors={ready.sensors}"
                    )
                continue    

            received_at = time.monotonic()

            try:
                frame = decode_frame(data)
            except ValueError as exc:
                rejected += 1
                print(f"Rejected packet from {address}: {exc}")
                continue

            accepted += 1

            ready_frames = reorder.push(frame, now=received_at)

            for ready in ready_frames:
                processed += 1
                print(
                    f"Processed={processed} "
                    f"Drone={ready.drone_id} "
                    f"Seq={ready.sequence} "
                    f"Lat={ready.latitude:.6f} "
                    f"Lon={ready.longitude:.6f} "
                    f"Sensors={ready.sensors}"
                )

    except KeyboardInterrupt:
        print("\nReceiver stopped.")
        print(f"Valid packets received: {accepted}")
        print(f"Rejected packets:       {rejected}")
        print(f"Frames processed:       {processed}")
        print(f"Duplicates suppressed:  {reorder.duplicates}")
        print(f"Late frames rejected:   {reorder.late_frames}")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
