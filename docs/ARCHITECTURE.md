# Aerial Hazard Streaming Grid

## Runtime data flow

1. `scripts/udp_drone_simulator.py` emits fixed-size, deterministic-seed
   synthetic frames over UDP. It reports loss, duplication, reordering and
   explicitly labels its telemetry synthetic.
2. `backend.telemetry.protocol` validates the 29-byte format and CRC16.
3. `backend.telemetry.reorder` tracks sequence state per drone, suppresses
   duplicates, handles 16-bit wraparound, and advances after a two-second gap.
4. `backend.grid.spatial_grid` stores the latest non-older sensor sample per
   drone and sensor type in each local tangent-plane grid cell. Confidence
   decays exponentially; cells older than the configured threshold are stale.
5. `backend.weather` supplies cached Open-Meteo observations or explicitly
   synthetic fixed demo weather. Provider failures yield missing weather
   features and do not stop telemetry processing.
6. `backend.risk` builds the stable 30-column feature schema, including
   validity flags for absent sensor and weather values. A configured,
   trusted scikit-learn SVM pipeline can score features. The included default
   threshold scorer is synthetic demonstration output, not a trained model.
7. `backend.alerts` applies activation, warning/critical escalation, clear
   hysteresis and acknowledgement. Escalating a warning to critical resets
   its acknowledgement.
8. `backend.persistence` writes readings asynchronously in bounded batches
   with bounded retries; exhausted batches increment failure counters rather
   than blocking shutdown forever. Acknowledgements are written synchronously
   before the API confirms them.
9. `backend.live_api` serves a loopback dashboard, JSON cell/alert endpoints,
   component health and full-state WebSocket updates. Live map reads use only
   in-memory grid state. Health treats intentionally disabled persistence as
   disabled, reports UDP drops/errors and weather outages as degradation, and
   marks exhausted or unavailable persistence accordingly.

## Feature schema

`backend.risk.FEATURE_COLUMNS` defines the model contract. The columns are
center sensor values (6), center validity flags (6), neighbour sensor values
(6), neighbour validity flags (6), then temperature, wind speed, wind
direction, and three matching weather validity flags. Values are finite
floats; flags are encoded as 0 or 1. A missing value is represented by a zero
with its validity flag set to zero. There is no trained artifact or training
dataset in this repository.

## Persistence and compatibility

The PostgreSQL writer is optional and isolated from live map queries. Sensor
history uses `(drone_id, sequence, observed_at)` as an idempotency key so a
16-bit sequence wrap does not collide with an earlier frame. Alert
acknowledgements are stored separately. The receiver's existing
`process_ready_frames` helper and protocol/grid APIs remain usable without
FastAPI or PostgreSQL dependencies.

The repository contains no separate legacy store-then-predict application;
the streaming additions preserve the original UDP simulator, receiver helper,
wire protocol, and grid API that are present here.

## Development and deployment boundaries

- Use Python 3.10+ and `python -m unittest discover -v`.
- FastAPI/Uvicorn and its WebSocket protocol implementation, asyncpg and
  scikit-learn/joblib are optional dependencies.
- `.github/workflows/ci.yml` runs unit and loopback integration checks, a real
  PostgreSQL 16 integration test, and a headless Chromium dashboard smoke test.
- Use a trusted model artifact only; loading joblib can execute code.
- The default scorer and simulator data are clearly marked synthetic. The
  repository is not safety-validated for industrial use.
- The development HTTP server listens on loopback by default and has no
  built-in authentication; the supplied runner rejects non-loopback HTTP
  binds. Use an authenticated TLS gateway before sharing it. UDP has no
  authentication and must be restricted to trusted senders.
- Keep database credentials outside Git in environment variables or a secret
  manager; do not print connection strings or telemetry payloads to logs.
