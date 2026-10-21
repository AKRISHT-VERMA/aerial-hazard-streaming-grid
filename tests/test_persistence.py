import asyncio
import unittest
from contextlib import asynccontextmanager

from backend.persistence import AsyncPostgresStore, PersistenceQueueFull
from backend.telemetry.protocol import TelemetryFrame


class FakePool:
    def __init__(self):
        self.rows = []
        self.closed = False

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def executemany(self, query, rows):
        self.rows.extend(rows)

    async def close(self):
        self.closed = True


class TestPostgresWriter(unittest.IsolatedAsyncioTestCase):
    async def test_queue_drains_and_pool_closes(self):
        pool = FakePool()
        store = AsyncPostgresStore(pool, queue_size=2, batch_size=2, flush_interval=.01)
        store.worker = asyncio.create_task(store._run())
        frame = TelemetryFrame(1, 7, 100, 18.5, 73.8, (1, 2, 3, 4, 5, 6))
        result = {"cell_id": [0, 1], "risk": .2, "risk_label": "low",
                  "prediction_source": "demo", "synthetic_prediction": True,
                  "weather": {"source": "synthetic-demo"}}
        store.enqueue_reading(frame, result)
        await store.close()
        self.assertEqual(len(pool.rows), 1)
        self.assertEqual(pool.rows[0][0:2], (1, 7))
        self.assertTrue(pool.closed)

    async def test_full_queue_is_reported_without_silent_drop(self):
        store = AsyncPostgresStore(FakePool(), queue_size=1)
        frame = TelemetryFrame(1, 1, 1, 0, 0, (0, 0, 0, 0, 0, 0))
        store.enqueue_reading(frame, {"cell_id": [0, 0]})
        with self.assertRaises(PersistenceQueueFull):
            store.enqueue_reading(frame, {"cell_id": [0, 0]})


if __name__ == "__main__":
    unittest.main()
