# AI Prompt and Validation Log

Record the prompts actually used to develop this project.
Do not record an implementation as complete until it is verified.

## P01 - Architecture inspection

Objective:
Establish the system architecture and implementation order.

Prompt:
Inspect the project requirements for the Aerial Hazard Streaming Grid.
Identify the required components, dependencies, failure cases and
acceptance tests. Preserve the original store-then-predict pipeline.
Do not claim functionality exists until it has been implemented and tested.

Tool:
ChatGPT

Result:
The architecture was mapped across the fixed-size telemetry protocol,
reorder buffer, spatial grid, simulator, tests and planned live components.
The repository contains no separate legacy store-then-predict application;
its existing simulator, receiver helper, wire protocol and grid APIs remain
available alongside the streaming pipeline.

Validation:
Inspected the root and source tree and verified the existing tests before
implementation; the final suite result is recorded under P02.

## Subsequent prompts

For each prompt, record:
- Prompt ID and objective
- Exact prompt used
- Files created or changed
- Tests executed and actual results
- Follow-up fixes and remaining limitations

## P03 - End-to-end engineering audit

Objective:
Audit the complete PR pipeline, fix verified reliability/security/validation
issues, test actual component integrations, update operator documentation, and
leave the PR as a draft.

Prompt:
Perform a rigorous end-to-end engineering audit of this repository and draft
PR #1, “Complete the aerial hazard streaming pipeline.” Inspect the
implementation, tests, dependencies, configuration, and documentation before
changing code. Trace UDP reception through CRC, sequence ordering, spatial
updates, features, prediction, alerts, persistence, API, WebSocket, and
dashboard. Fix integration and lifecycle bugs; test malformed and stale data,
bounded background work, alert transitions, recovery and synthetic labeling.
Review security and deployment defaults, update setup/troubleshooting docs,
run all configured checks, record exact results and remaining external
verification needs, commit to the PR branch, and keep it in draft without
merging.

Files changed:
Bounded and instrumented UDP processing queue; bounded PostgreSQL retries and
failure health counters; partial-startup cleanup; reject CRC-valid packets
with out-of-range coordinates; validate SVM feature metadata/probabilities and
acknowledgement identities; restrict the supplied HTTP runner to loopback;
document finite retry behavior and unauthenticated UDP; add integration and
failure-path tests; move the HTTP test client to test-only requirements.

Validation:
- `python -m unittest discover -v`: 55 tests passed after audit changes.
- `python -m compileall -q .`: passed.
- `python scripts/run_server.py --help`: passed.
- `python scripts/run_server.py --host 0.0.0.0 --port 8000`: rejected as
  expected; the supplied runner limits HTTP to loopback.
- No configured lint or static type checker was found. PostgreSQL, live
  Open-Meteo, browser rendering, and a trusted trained SVM were not available
  for production integration validation.

Remaining limitations:
No trained data or safety-validated artifact is included. PostgreSQL retry
exhaustion reports and abandons the affected in-memory batch; an operator must
monitor `/health` and restore DB connectivity. UDP is unauthenticated and must
be firewalled to trusted sources. HTTP has no app authentication; remote use
requires an authenticated TLS gateway. No production safety certification is
claimed.

## P02 - Complete the streaming pipeline

Objective:
Inspect the repository, implement the remaining planned features, fix defects,
add tests and documentation, and prepare the changes for review.

Prompt:
Inspect the entire repository and finish the aerial hazard streaming grid project end-to-end. First understand the existing code and architecture. Implement all missing planned features, fix bugs, add appropriate tests, and update documentation. Preserve working functionality and avoid unnecessary rewrites. Run the available test suite and relevant integration checks, fix failures, and report exactly what passed and what remains incomplete. Clearly label synthetic data and demo predictions. Do not expose secrets. Make the changes directly in the connected repository and prepare them for review.

Files created or changed:
Risk and weather adapters; alert state management; asynchronous PostgreSQL
writer and schema; UDP-to-service pipeline; live HTTP/WebSocket API and
dashboard; server and simulator entry points; feature, safety, setup and
validation documentation; unit and integration tests.

Validation:
- `python -m unittest discover -v`: 50 tests passed.
- `python -m compileall -q .`: passed.
- `python scripts/run_server.py --help`: passed.
- Credential-like assignment scan: no matches.
- HTTP/WebSocket lifecycle and loopback UDP ingest integration tests passed.

Follow-up fixes and remaining limitations:
The existing repository did not contain a separate legacy store-then-predict
application, so the streaming implementation preserves the existing simulator,
receiver helper, protocol and grid APIs. PostgreSQL and live Open-Meteo access
were not available for external integration testing. No trained model or
training dataset is present; demo scoring is synthetic and not safety
validated. The dashboard has no built-in authentication and is loopback-only
by default.
