# Aerial Hazard Streaming Grid

## Project objective
Extend Aerial Location of Hazardous Atmosphere in Industries with
streaming hazard-grid processing that tolerates unreliable drone telemetry.

## Planned architecture

1. Drone simulator sends fixed-size binary frames over UDP.
2. Telemetry receiver validates frame length and CRC16.
3. Sequence handling detects duplicates and reorders packets within
   a two-second window.
4. Valid readings update an in-memory spatial hazard grid.
5. The grid maintains sensor readings, timestamps and decaying confidence.
6. An SVM evaluates fused features from the cell, its eight neighbours,
   and weather temperature and wind data.
7. An alert state machine manages activation, hysteresis, escalation
   and clearing.
8. A live dashboard displays current cells, alerts and stale data.
9. PostgreSQL stores historical readings asynchronously and persists
   alert acknowledgements.

## Compatibility requirement
The original store-then-predict pipeline must remain available.

## Development principles
- Implement and test one component at a time.
- Do not claim tests passed unless they were actually executed.
- Clearly label synthetic sensor data and demonstration model output.
- Do not commit API keys, passwords or other secrets.
- Keep the live map independent of historical database queries.

## Planned milestones
1. Repository and architecture documentation.
2. Binary UDP frame protocol and CRC16 tests.
3. Loss, duplication and reordering simulator.
4. Spatial grid and confidence decay.
5. SVM and weather integration.
6. Alert state machine and durable acknowledgements.
7. Asynchronous PostgreSQL persistence.
8. Live map and WebSocket updates.
9. End-to-end validation and demonstration.
