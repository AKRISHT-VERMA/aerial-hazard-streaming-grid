"""Run UDP ingest, hazard scoring, optional PostgreSQL and live dashboard."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.live_api import create_app
from backend.risk import DemoThresholdModel, SVMRiskModel
from backend.service import HazardService
from backend.weather import DemoWeatherProvider, OpenMeteoWeatherProvider


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("HAZARD_HTTP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HAZARD_HTTP_PORT", "8000")))
    parser.add_argument("--udp-host", default=os.getenv("HAZARD_UDP_HOST", "127.0.0.1"))
    parser.add_argument("--udp-port", type=int, default=int(os.getenv("HAZARD_UDP_PORT", "9999")))
    args = parser.parse_args()

    http_host = args.host.strip().lower()
    try:
        is_loopback = ipaddress.ip_address(http_host).is_loopback
    except ValueError:
        is_loopback = http_host == "localhost"
    if not is_loopback:
        parser.error("HTTP must bind to loopback; use an authenticated TLS gateway for remote access")

    model_path = os.getenv("HAZARD_MODEL_PATH")
    model = SVMRiskModel(model_path) if model_path else DemoThresholdModel()
    weather = (DemoWeatherProvider() if os.getenv("HAZARD_WEATHER_SOURCE") == "demo"
               else OpenMeteoWeatherProvider(
                   cache_seconds=float(os.getenv("HAZARD_WEATHER_CACHE_SECONDS", "60")),
                   cache_max_entries=int(os.getenv("HAZARD_WEATHER_CACHE_MAX_ENTRIES", "1024")),
               ))
    grid_max_cells = int(os.getenv("HAZARD_GRID_MAX_CELLS", "10000"))
    grid = StreamingHazardGrid(
        18.5204, 73.8567,
        max_cells=grid_max_cells,
        cell_retention_seconds=float(os.getenv("HAZARD_CELL_RETENTION_SECONDS", "300")),
        max_drones_per_cell=int(os.getenv("HAZARD_GRID_MAX_DRONES_PER_CELL", "256")),
    )
    service = HazardService(
        grid, model=model, weather=weather,
        synthetic_input=os.getenv("HAZARD_TELEMETRY_SOURCE") == "demo",
        prediction_retention_seconds=float(os.getenv("HAZARD_PREDICTION_RETENTION_SECONDS", "300")),
        prediction_capacity=int(os.getenv("HAZARD_PREDICTION_MAX_ENTRIES", str(grid_max_cells))),
    )
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install requirements.txt to run the dashboard server") from exc
    uvicorn.run(create_app(service, udp_host=args.udp_host, udp_port=args.udp_port),
                host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

