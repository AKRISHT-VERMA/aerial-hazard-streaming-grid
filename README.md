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
python -m venv .venv
.\.venv\Scripts\Activate.ps1
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

## Configuration

Settings are environment variables; the runner does not automatically load
`.env` files. `.env.example` lists the available variables and defaults.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAZARD_HTTP_HOST` | `127.0.0.1` | HTTP bind address; the runner only permits loopback. |
| `HAZARD_HTTP_PORT` | `8000` | Dashboard and API port. |
| `HAZARD_UDP_HOST` | `127.0.0.1` | UDP telemetry bind address. |
| `HAZARD_UDP_PORT` | `9999` | UDP telemetry port. |
| `HAZARD_GRID_MAX_CELLS` | `1000` | Maximum live grid cells. At capacity, oldest stale unalerted cells are evicted first; otherwise a new frame is dropped and counted. |
| `HAZARD_CELL_RETENTION_SECONDS` | `300` | Retain cells/readings for up to five minutes without newer telemetry; active-alert cells remain pinned. |
| `HAZARD_GRID_MAX_DRONES_PER_CELL` | `32` | Maximum distinct drone readings retained per cell (protocol maximum is 256). Expired readings are removed first; otherwise updates are dropped and counted. |
| `HAZARD_PREDICTION_MAX_ENTRIES` | `1000` | Maximum retained predictions; default is the grid capacity. |
| `HAZARD_PREDICTION_RETENTION_SECONDS` | `300` | Expire predictions after five minutes without a newer score; predictions associated with active alerts are retained. |
| `HAZARD_WEATHER_CACHE_SECONDS` | `60` | Weather cache entry lifetime. |
| `HAZARD_WEATHER_CACHE_MAX_ENTRIES` | `1024` | Maximum live weather cache entries; least-recently-used entries are evicted when full. |
| `HAZARD_TELEMETRY_SOURCE` | live | Set to `demo` to label simulator input synthetic. |
| `HAZARD_WEATHER_SOURCE` | live | Set to `demo` for fixed synthetic weather. |
| `HAZARD_MODEL_PATH` | unset | Trusted joblib pipeline; unset selects the synthetic demo scorer. |
| `DATABASE_URL` | unset | Optional PostgreSQL DSN; keep credentials out of source control. |

CLI flags `--host`, `--port`, `--udp-host`, and `--udp-port` override the
matching bind variables.

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

- `GET /health`: overall `healthy`, `degraded`, or `unavailable` status plus
  component details. Intentionally disabled PostgreSQL reports `disabled` and
  does not make an otherwise healthy demo degraded. UDP drops/errors, weather
  outages, queue saturation, abandoned database batches, grid/prediction
  capacity, evictions, and dropped updates are reported. An unexpectedly lost
  UDP transport makes health unavailable and returns HTTP 503.
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

The live grid, per-cell drone readings, prediction history, and weather cache
have explicit limits. At grid capacity, stale cells without active alerts are
evicted before fresh or alert-pinned cells; if no safe cell can be evicted, the
incoming frame is dropped and `/health` increments `state_capacity.dropped_updates`.
Active alert state is retained and pins its grid cell. Predictions age out after
their configured retention interval unless tied to an active alert; prediction
capacity pressure is counted separately. Weather entries expire by TTL and the
least-recently-used entry is evicted at capacity. These defaults bound memory
for the demo; they are not a production workload sizing or load-test guarantee.

## Troubleshooting

- **No cells appear:** confirm the server and simulator use the same UDP port;
  run the simulator on the same host as the default loopback listener, and
  check `GET /health` for received, rejected, dropped, and processed counts.
- **Frames are rejected:** packets must be exactly 29 bytes, pass CRC16, and
  contain latitude/longitude within geographic bounds. Use the supplied
  simulator to send correctly encoded synthetic frames.
- **Weather shows unavailable:** live weather requires outbound HTTPS to
  Open-Meteo. Check network access; telemetry continues using missing weather
  features. Set `HAZARD_WEATHER_SOURCE=demo` for labeled synthetic weather.
- **Persistence reports failures:** verify the optional dependency, DSN,
  database reachability, and permission to create tables/indexes. Inspect
  `/health`; after five retries, failed batches are counted but not retained.
- **Model will not load:** install `requirements-model.txt`, check the local
  artifact path and trusted provenance, and ensure the pipeline accepts the
  documented 30-feature schema and `low`/`medium`/`high` labels.

## Tests and checks

Install the test dependencies and run the complete suite with:

```powershell
python -m pip install -r requirements-test.txt
python -m unittest discover -v
```

The tests cover the wire protocol, sequence reordering, grid aggregation and
staleness, feature validation, alert transitions, bounded queue behavior,
writer shutdown after a database outage, and loopback UDP-to-live-state
integration. PostgreSQL service integration and browser rendering checks
are skipped locally unless configured. Run the PostgreSQL integration test
with `requirements-postgres.txt` and `TEST_DATABASE_URL` pointing to a
disposable database. Run the browser smoke test with
`requirements-browser-test.txt`, Chromium installed using
`python -m playwright install chromium`, and `RUN_BROWSER_TESTS=1`. GitHub
Actions runs both checks with a PostgreSQL 16 service and headless Chromium.
The server uses
`requirements.txt`; test-only HTTP client dependencies are in
`requirements-test.txt`. Optional SVM and PostgreSQL dependencies are listed
separately in `requirements-model.txt` and `requirements-postgres.txt`.

