"""Optional FastAPI HTTP and WebSocket interface for live in-memory state."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from backend.service import HazardService

logger = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
except ImportError:  # FastAPI remains an optional runtime dependency.
    FastAPI = None


def create_app(service: HazardService, *, udp_host: str = "127.0.0.1",
               udp_port: int = 9999, database_url: str | None = None):
    """Create the UI API; historical database queries are not on map routes."""
    if FastAPI is None:  # pragma: no cover - optional dependency
        raise RuntimeError("Install requirements.txt to run the live dashboard API")

    @asynccontextmanager
    async def lifespan(app):
        app.state.udp_task = None
        app.state.udp_protocol = None
        app.state.freshness_task = None
        app.state.persistence = None
        dsn = database_url if database_url is not None else os.environ.get("DATABASE_URL")
        if dsn:
            from backend.persistence import AsyncPostgresStore
            store = await AsyncPostgresStore.connect(dsn)
            app.state.persistence = store
            service.persistence = store
        from backend.udp import receive_udp
        protocol_ready = asyncio.get_running_loop().create_future()
        app.state.udp_task = asyncio.create_task(
            receive_udp(service, udp_host, udp_port, protocol_ready), name="hazard-udp-listener"
        )
        app.state.udp_failure = None

        def observe_listener(task):
            if task.cancelled():
                return
            failure = task.exception()
            if failure is not None:
                app.state.udp_failure = type(failure).__name__
                logger.error("UDP listener stopped unexpectedly (%s)", type(failure).__name__)

        app.state.udp_task.add_done_callback(observe_listener)
        done, _ = await asyncio.wait((protocol_ready, app.state.udp_task),
                                      return_when=asyncio.FIRST_COMPLETED)
        if app.state.udp_task in done:
            try:
                await app.state.udp_task
            finally:
                if app.state.persistence is not None:
                    await app.state.persistence.close()
                    service.persistence = None
        try:
            app.state.udp_protocol = protocol_ready.result()
        except BaseException:
            app.state.udp_task.cancel()
            await asyncio.gather(app.state.udp_task, return_exceptions=True)
            raise

        async def publish_freshness():
            while True:
                await asyncio.sleep(1.0)
                service.refresh()

        app.state.freshness_task = asyncio.create_task(
            publish_freshness(), name="hazard-freshness-updates"
        )
        try:
            yield
        finally:
            task = app.state.udp_task
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            freshness = app.state.freshness_task
            if freshness is not None:
                freshness.cancel()
                try:
                    await freshness
                except asyncio.CancelledError:
                    pass
            if app.state.persistence is not None:
                await app.state.persistence.close()
                service.persistence = None

    app = FastAPI(title="Aerial Hazard Streaming Grid", version="1.0.0",
                  lifespan=lifespan)

    @app.get("/health")
    async def health():
        from fastapi.responses import JSONResponse

        udp_task = getattr(app.state, "udp_task", None)
        protocol = getattr(app.state, "udp_protocol", None) if udp_task else None
        persistence = getattr(app.state, "persistence", None)
        udp_running = (protocol is not None and protocol.transport_alive
                       and udp_task is not None and not udp_task.done())
        udp_status = "unavailable" if not udp_running else (
            "degraded" if protocol.queue_dropped or protocol.processing_errors else "healthy"
        )
        udp_health = ({"status": udp_status, "received": protocol.received,
                       "rejected_frames": protocol.rejected, "processed": protocol.processed,
                       "queue_depth": protocol.processing_queue.qsize(),
                       "queue_capacity": protocol.processing_queue.maxsize,
                       "dropped_frames": protocol.queue_dropped,
                       "processing_errors": protocol.processing_errors,
                       "transport_alive": protocol.transport_alive,
                       "listener_failure": getattr(app.state, "udp_failure", None),
                       "transport_loss": protocol.transport_loss} if protocol else
                      {"status": "unavailable", "received": 0, "rejected_frames": 0,
                       "processed": 0, "queue_depth": 0, "queue_capacity": 0,
                       "dropped_frames": 0, "processing_errors": 0,
                       "transport_alive": False, "transport_loss": None,
                       "listener_failure": getattr(app.state, "udp_failure", None)})
        persistence_health = persistence.health() if persistence else {"status": "disabled"}
        weather_status = ("unknown" if service.weather_available is None else
                          "healthy" if service.weather_available else "degraded")
        component_statuses = [udp_status]
        if persistence_health["status"] != "disabled":
            component_statuses.append(persistence_health["status"])
        if weather_status in {"healthy", "degraded", "unavailable"}:
            component_statuses.append(weather_status)
        overall = ("unavailable" if "unavailable" in component_statuses else
                   "degraded" if "degraded" in component_statuses else "healthy")
        payload = {
            "status": overall,
            "revision": service.revision,
            "synthetic_input": service.synthetic_input,
            "synthetic_prediction": getattr(service.model, "source", "unknown").startswith("synthetic"),
            "udp": udp_health,
            "state_capacity": service.grid.health() | service.state_health(),
            "persistence": persistence_health,
            "weather": {"status": weather_status, "failures": service.weather_failures,
                        **(service.weather.health() if hasattr(service.weather, "health") else {})},
        }
        return JSONResponse(status_code=503 if overall == "unavailable" else 200, content=payload)

    @app.get("/", include_in_schema=False)
    async def dashboard():
        from fastapi.responses import FileResponse
        from pathlib import Path
        return FileResponse(Path(__file__).parent / "static" / "dashboard.html")

    @app.get("/api/cells")
    async def cells():
        return service.snapshot()["cells"]

    @app.get("/api/alerts")
    async def alerts():
        return service.snapshot()["alerts"]

    @app.post("/api/alerts/{alert_id}/acknowledge")
    async def acknowledge(alert_id: str, body: dict):
        user = body.get("user") if isinstance(body, dict) else None
        try:
            return await service.acknowledge(alert_id, user)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404 if isinstance(exc, KeyError) else 422,
                                detail=str(exc)) from exc

    @app.websocket("/ws")
    async def updates(websocket: WebSocket):
        await websocket.accept()
        queue = service.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            service.unsubscribe(queue)

    return app

