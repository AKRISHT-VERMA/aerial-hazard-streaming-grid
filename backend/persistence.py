"""Optional asynchronous PostgreSQL persistence for readings and alert acks."""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID
from typing import Any

logger = logging.getLogger(__name__)


class PersistenceQueueFull(RuntimeError):
    pass


class AsyncPostgresStore:
    """Bounded asyncpg writer. Install the optional ``postgres`` extra to use."""

    def __init__(self, pool: Any, *, queue_size: int = 1000,
                 batch_size: int = 100, flush_interval: float = 0.25) -> None:
        if queue_size < 1 or batch_size < 1 or flush_interval <= 0:
            raise ValueError("queue, batch, and flush interval must be positive")
        self.pool = pool
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.worker: asyncio.Task | None = None
        self._closed = False

    @classmethod
    async def connect(cls, dsn: str, **options) -> "AsyncPostgresStore":
        try:
            import asyncpg
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the 'postgres' extra to use PostgreSQL") from exc
        pool = await asyncpg.create_pool(dsn=dsn)
        store = cls(pool, **options)
        async with pool.acquire() as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS hazard_readings (
                    drone_id SMALLINT NOT NULL, sequence INTEGER NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL, latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL, sensors JSONB NOT NULL,
                    cell_x INTEGER NOT NULL, cell_y INTEGER NOT NULL,
                    risk DOUBLE PRECISION NOT NULL, risk_label TEXT NOT NULL,
                    prediction_source TEXT NOT NULL, synthetic_prediction BOOLEAN NOT NULL,
                    weather JSONB NOT NULL,
                    PRIMARY KEY (drone_id, sequence, observed_at)
                )"""
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS hazard_readings_observed_at_idx "
                "ON hazard_readings (observed_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS hazard_readings_cell_idx "
                "ON hazard_readings (cell_x, cell_y, observed_at DESC)"
            )
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS alert_acknowledgements (
                    alert_id UUID PRIMARY KEY, cell_x INTEGER NOT NULL, cell_y INTEGER NOT NULL,
                    acknowledged_by TEXT NOT NULL, acknowledged_at TIMESTAMPTZ NOT NULL
                )"""
            )
        store.worker = asyncio.create_task(store._run(), name="hazard-postgres-writer")
        return store

    def enqueue_reading(self, frame, result: dict) -> None:
        if self._closed:
            raise RuntimeError("PostgreSQL store is closed")
        try:
            self.queue.put_nowait((frame, result))
        except asyncio.QueueFull as exc:
            raise PersistenceQueueFull("PostgreSQL write queue is full") from exc

    async def acknowledge_alert(self, alert) -> None:
        if self._closed:
            raise RuntimeError("PostgreSQL store is closed")
        if alert.acknowledged_by is None or alert.acknowledged_at is None:
            raise ValueError("alert must be acknowledged before persistence")
        async with self.pool.acquire() as connection:
            await connection.execute(
                """INSERT INTO alert_acknowledgements
                   (alert_id, cell_x, cell_y, acknowledged_by, acknowledged_at)
                   VALUES ($1, $2, $3, $4, to_timestamp($5))
                   ON CONFLICT (alert_id) DO UPDATE SET
                     acknowledged_by=EXCLUDED.acknowledged_by,
                     acknowledged_at=EXCLUDED.acknowledged_at""",
                UUID(alert.alert_id), alert.cell_id[0], alert.cell_id[1],
                alert.acknowledged_by, alert.acknowledged_at,
            )

    async def _run(self) -> None:
        while True:
            first = await self.queue.get()
            if first is None:
                self.queue.task_done()
                break
            batch = [first]
            stopping = False
            deadline = asyncio.get_running_loop().time() + self.flush_interval
            while len(batch) < self.batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self.queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if item is None:
                    self.queue.task_done()
                    stopping = True
                    break
                batch.append(item)
            rows = [self._reading_row(frame, result) for frame, result in batch]
            delay = 0.25
            while True:
                try:
                    async with self.pool.acquire() as connection:
                        await connection.executemany(
                            """INSERT INTO hazard_readings
                               (drone_id, sequence, observed_at, latitude, longitude,
                                sensors, cell_x, cell_y, risk, risk_label,
                                prediction_source, synthetic_prediction, weather)
                               VALUES ($1, $2, to_timestamp($3), $4, $5, $6::jsonb,
                                       $7, $8, $9, $10, $11, $12, $13::jsonb)
                           ON CONFLICT (drone_id, sequence, observed_at) DO NOTHING""", rows,
                        )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Keep the batch accounted for and retry; never silently
                    # discard readings during a temporary database outage.
                    logger.warning("PostgreSQL batch write failed (%s); retrying", type(exc).__name__)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 10.0)
            for _ in batch:
                self.queue.task_done()
            if stopping:
                break

    @staticmethod
    def _reading_row(frame, result) -> tuple:
        x, y = result["cell_id"]
        return (
            frame.drone_id, frame.sequence, frame.timestamp, frame.latitude,
            frame.longitude, json.dumps(frame.sensors), x, y, result["risk"],
            result["risk_label"], result["prediction_source"],
            result["synthetic_prediction"], json.dumps(result["weather"]),
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.worker is not None:
            await self.queue.join()
            await self.queue.put(None)
            await self.worker
        await self.pool.close()
