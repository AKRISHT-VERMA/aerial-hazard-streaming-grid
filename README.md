# Aerial Hazard Streaming Grid

Streaming grid for fixed-size drone telemetry. It validates CRC16 frames,
reorders UDP packets, aggregates sensor readings into spatial cells, scores
the local cell and its neighbours with weather features, manages alert
hysteresis, and provides live HTTP/WebSocket views. Historical readings and
alert acknowledgements can be written asynchronously to PostgreSQL.

> **Safety status:** this project is a software demonstration, not an
> operational safety system. No trained or safety-validated model is
> included. The default threshold scorer is a synthetic demo and the UI labels
> its predictions. Calibrate, independently validate, and certify any model
> and deployment before using it in a real industrial setting.

## Run the local demonstration

Python 3.10 or later is required.

```powershell
python -m pip install -r requirements.txt
$env:HAZARD_TELEMETRY_SOURCE = "demo"
$env:HAZARD_WEATHER_SOURCE = "demo"
python scripts/run_server.py
```

Open <http://127.0.0.1:8000>. In another terminal, send labeled synthetic
telemetry to the local UDP receiver:

```powershell
python scripts/udp_drone_simulator.py --count 100 --loss 0.30 --duplicate 0.10 --seed 42
```

The simulator prints that its sensor frames are synthetic. Set
`HAZARD_TELEMETRY_SOURCE=demo` in the server process when running it so each
live cell is also labeled as synthetic in the dashboard. Demo weather is
selected explicitly with `HAZARD_WEATHER_SOURCE=demo`. The default weather
provider fetches current conditions from Open-Meteo, caches them briefly, and
continues with missing weather features if the service is unavailable.

## SVM and feature schema

The repository does not include a training dataset or model artifact. To load
a trusted scikit-learn SVM pipeline, install `requirements-model.txt` and set
`HAZARD_MODEL_PATH` to the local joblib artifact. The pipeline must accept 30
finite features in the order declared by `backend.risk.FEATURE_COLUMNS` and
return `low`, `medium`, or `high` labels. If it supports `predict_proba`, the
`high` probability becomes the risk score. Only load artifacts from a trusted
training process; joblib files can execute code when loaded. A configured
artifact is not assumed to be safety-validated.

## PostgreSQL

Install `requirements-postgres.txt`, set `DATABASE_URL` in the process
environment, and start the server. The service creates its two tables when it
connects. Sensor writes use a bounded asynchronous batch queue with retries;
after five retries a failed batch is abandoned with explicit error counters in
`/health` (the live in-memory grid continues). Queue saturation is surfaced as
an ingest error instead of silently dropping history. Alert acknowledgements
are written synchronously before the API confirms them. Keep credentials in
environment variables or a secret manager; never commit a populated `.env`
file. `backend/migrations/001_initial.sql` contains the schema for managed
deployments. Shutdown drains the queue, so it can take time while the database
is retrying; retry limits bound that delay.

## Endpoints

- `GET /health`: service status and live-state revision.
- `GET /api/cells`: current in-memory cells, readings, confidence and stale flags.
- `GET /api/alerts`: active alerts.
- `POST /api/alerts/{alert_id}/acknowledge`: acknowledge an alert with
  `{"user":"operator name"}`.
- `WS /ws`: full-state snapshots after each live update.
- `/`: live dashboard.

The map API reads only in-memory state; historical database queries are not
used to render live cells. The development server binds to loopback by
  default. `/health` includes UDP queue capacity/drop/error counters and
  PostgreSQL queue/failure counters when enabled. HTTP has no built-in user
  authentication, and the supplied runner refuses non-loopback HTTP binds.
  For remote access, put it behind an authenticated, TLS-terminating gateway
  and keep the app port inaccessible from untrusted networks. The UDP ingest
  protocol itself has no authentication; restrict its network access to
  trusted senders. Never expose either port directly to the public internet.

## Tests and checks

Run the dependency-free suite with:

```powershell
python -m unittest discover -v
```

The tests cover the wire protocol, sequence reordering, grid aggregation and
staleness, feature validation, alert transitions, bounded queue behavior,
writer shutdown after a database outage, and loopback UDP-to-live-state
integration. PostgreSQL service integration and browser rendering checks
require their optional runtime dependencies and services. Run the demo with
Python 3.10+ and `python -m pip install -r requirements.txt`; optional SVM and
PostgreSQL dependencies are listed separately in `requirements-model.txt` and
`requirements-postgres.txt`.
