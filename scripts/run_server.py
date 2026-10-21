"""Run UDP ingest, hazard scoring, optional PostgreSQL and live dashboard."""

from __future__ import annotations

import argparse
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

    model_path = os.getenv("HAZARD_MODEL_PATH")
    model = SVMRiskModel(model_path) if model_path else DemoThresholdModel()
    weather = (DemoWeatherProvider() if os.getenv("HAZARD_WEATHER_SOURCE") == "demo"
               else OpenMeteoWeatherProvider())
    service = HazardService(
        StreamingHazardGrid(18.5204, 73.8567), model=model, weather=weather,
        synthetic_input=os.getenv("HAZARD_TELEMETRY_SOURCE") == "demo",
    )
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Install requirements.txt to run the dashboard server") from exc
    uvicorn.run(create_app(service, udp_host=args.udp_host, udp_port=args.udp_port),
                host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
