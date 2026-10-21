
import socket
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.telemetry.protocol import decode_frame
from backend.telemetry.reorder import ReorderBuffer


HOST = "127.0.0.1"
PORT = 9999

# Match the current simulator's operating area.
GRID_ORIGIN_LATITUDE = 18.5204
GRID_ORIGIN_LONGITUDE = 73.8567


def process_ready_frames(frames, grid, processed_count):
    """Update the grid only with frames released by the reorder buffer."""
    for frame in frames:
        cell = grid.update_frame(frame)
        processed_count += 1

        print(
            f"Processed={processed_count} "
            f"Drone={frame.drone_id} "
            f"Seq={frame.sequence} "
            f"Lat={frame.latitude:.6f} "
            f"Lon={frame.longitude:.6f} "
            f"Sensors={frame.sensors} "
            f"Cell={cell.cell_id} "
            f"GridCells={len(grid.cells)}"
        )

    return processed_count


def main():
    reorder = ReorderBuffer(window_seconds=2.0)
    grid = StreamingHazardGrid(
        origin_latitude=GRID_ORIGIN_LATITUDE,
        origin_longitude=GRID_ORIGIN_LONGITUDE,
        cell_size_meters=10.0,
    )

    received = rejected = processed = 0

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))
        sock.settimeout(0.1)

        print(f"Listening on {HOST}:{PORT}")
        print("Binary protocol and CRC16 validation enabled.")
        print("Reorder buffer and 10-metre streaming grid enabled.")
        print("Press Ctrl+C to stop.")

        try:
            while True:
                try:
                    data, address = sock.recvfrom(2048)
                except socket.timeout:
                    ready = reorder.flush_expired()
                    processed = process_ready_frames(
                        ready, grid, processed
                    )
                    continue

                received += 1

                try:
                    frame = decode_frame(data)
                except ValueError as exc:
                    rejected += 1
                    print(f"Rejected datagram from {address}: {exc}")
                    continue

                ready = reorder.push(frame)
                processed = process_ready_frames(ready, grid, processed)

        except KeyboardInterrupt:
            # Give buffered frames enough time to expire, as before.
            deadline = time.monotonic() + reorder.window_seconds

            while time.monotonic() < deadline:
                ready = reorder.flush_expired()
                processed = process_ready_frames(
                    ready, grid, processed
                )
                time.sleep(0.05)

        finally:
            print("\n--- Receiver statistics ---")
            print(f"Datagrams received:        {received}")
            print(f"Invalid frames rejected:   {rejected}")
            print(f"Frames processed:          {processed}")
            print(f"Grid cells created:        {len(grid.cells)}")
            print(f"Duplicates suppressed:     {reorder.duplicates}")
            print(f"Late frames rejected:      {reorder.late_frames}")
            print(f"Out-of-order arrivals:     {reorder.reordered_frames}")


if __name__ == "__main__":
    main()
