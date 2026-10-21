"""Optional FastAPI HTTP and WebSocket interface for live in-memory state."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from backend.service import HazardService

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
        app.state.freshness_task = None
        app.state.persistence = None
        dsn = database_url if database_url is not None else os.environ.get("DATABASE_URL")
        if dsn:
            from backend.persistence import AsyncPostgresStore
            store = await AsyncPostgresStore.connect(dsn)
            app.state.persistence = store
            service.persistence = store
        from backend.udp import receive_udp
        app.state.udp_task = asyncio.create_task(
            receive_udp(service, udp_host, udp_port), name="hazard-udp-listener"
        )

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
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            freshness = app.state.freshness_task
            if freshness is not None:
                freshness.cancel()
                try:
                    await freshness
                except asyncio.CancelledError:
                    pass
            if app.state.persistence is not None:
                await app.state.persistence.close()

    app = FastAPI(title="Aerial Hazard Streaming Grid", version="1.0.0",
                  lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "revision": service.revision,
                "synthetic_input": service.synthetic_input}

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
