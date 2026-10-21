"""Real PostgreSQL contract test; enabled by TEST_DATABASE_URL in CI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
import os
import random
import time
import unittest
from types import SimpleNamespace
from uuid import uuid4

from backend.persistence import AsyncPostgresStore
from backend.telemetry.protocol import TelemetryFrame


HAS_DATABASE = bool(os.environ.get("TEST_DATABASE_URL")) and importlib.util.find_spec("asyncpg")


@unittest.skipUnless(HAS_DATABASE, "set TEST_DATABASE_URL and install asyncpg for real PostgreSQL integration")
class TestRealPostgresIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_schema_duplicate_writes_acknowledgement_and_shutdown_drain(self):
        import asyncpg

        drone_id = 254
        sequence = random.randrange(0, 65536)
        timestamp = int(time.time())
        frame = TelemetryFrame(drone_id, sequence, timestamp, 18.5204, 73.8567,
                               (101, 202, 303, 404, 505, 606))
        result = {"cell_id": [1, 2], "risk": 0.7, "risk_label": "high",
                  "prediction_source": "integration-test", "synthetic_prediction": True,
                  "weather": {"source": "test", "synthetic": True}}
        alert_id = uuid4()
        alert = SimpleNamespace(alert_id=str(alert_id), cell_id=(1, 2),
                                 acknowledged_by="ci-test", acknowledged_at=time.time())
        store = None
        connection = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
        try:
            store = await AsyncPostgresStore.connect(
                os.environ["TEST_DATABASE_URL"], queue_size=4, batch_size=2,
                flush_interval=0.01,
            )
            self.assertTrue(await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_name='hazard_readings')"
            ))
            store.enqueue_reading(frame, result)
            store.enqueue_reading(frame, result)  # Same primary key must be idempotent.
            await store.acknowledge_alert(alert)
            await asyncio.wait_for(store.close(), timeout=10)
            store = None

            observed_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            rows = await connection.fetch(
                "SELECT drone_id, sequence, risk, risk_label, sensors, synthetic_prediction "
                "FROM hazard_readings WHERE drone_id=$1 AND sequence=$2 AND observed_at=$3",
                drone_id, sequence, observed_at,
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["risk_label"], "high")
            self.assertEqual(rows[0]["risk"], 0.7)
            self.assertTrue(rows[0]["synthetic_prediction"])
            sensors = rows[0]["sensors"]
            if isinstance(sensors, str):
                sensors = json.loads(sensors)
            self.assertEqual(sensors, [101, 202, 303, 404, 505, 606])
            self.assertEqual(await connection.fetchval(
                "SELECT count(*) FROM alert_acknowledgements WHERE alert_id=$1", alert_id
            ), 1)
        finally:
            if store is not None:
                await store.close()
            await connection.execute(
                "DELETE FROM hazard_readings WHERE drone_id=$1 AND sequence=$2 AND observed_at=$3",
                drone_id, sequence, datetime.fromtimestamp(timestamp, tz=timezone.utc),
            )
            await connection.execute("DELETE FROM alert_acknowledgements WHERE alert_id=$1", alert_id)
            await connection.close()


if __name__ == "__main__":
    unittest.main()
