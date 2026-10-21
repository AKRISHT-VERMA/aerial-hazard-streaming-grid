"""Async UDP ingest adapter with CRC validation and sequence reordering."""

from __future__ import annotations

import asyncio
import logging
import socket

from backend.service import HazardService
from backend.telemetry.protocol import decode_frame
from backend.telemetry.reorder import ReorderBuffer

logger = logging.getLogger(__name__)


class HazardDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: HazardService, loop: asyncio.AbstractEventLoop,
                 reorder_window_seconds: float = 2.0) -> None:
        self.service = service
        self.loop = loop
        self.reorder = ReorderBuffer(window_seconds=reorder_window_seconds)
        self.transport: asyncio.DatagramTransport | None = None
        self.received = 0
        self.rejected = 0
        self.processed = 0
        self._flush_handle: asyncio.TimerHandle | None = None
        self._processing_tasks: set[asyncio.Task] = set()

    def connection_made(self, transport) -> None:
        self.transport = transport
        self._schedule_flush()

    def datagram_received(self, data: bytes, addr) -> None:
        self.received += 1
        try:
            frame = decode_frame(data)
        except ValueError:
            self.rejected += 1
            logger.debug("Rejected invalid telemetry datagram")
            return
        self._process(self.reorder.push(frame))

    def error_received(self, exc: Exception) -> None:
        logger.warning("UDP socket reported an error: %s", type(exc).__name__)

    def connection_lost(self, exc) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()

    def _schedule_flush(self) -> None:
        self._flush_handle = self.loop.call_later(0.1, self._flush)

    def _flush(self) -> None:
        self._process(self.reorder.flush_expired())
        self._schedule_flush()

    def _process(self, frames) -> None:
        for frame in frames:
            task = self.loop.create_task(self._process_one(frame))
            self._processing_tasks.add(task)
            task.add_done_callback(self._processing_tasks.discard)

    async def _process_one(self, frame) -> None:
        try:
            await self.service.process_frame(frame)
            self.processed += 1
        except Exception:
            logger.exception("Failed to process a valid telemetry frame")

    async def drain(self) -> None:
        if self._processing_tasks:
            await asyncio.gather(*tuple(self._processing_tasks), return_exceptions=True)


async def receive_udp(service: HazardService, host: str = "127.0.0.1",
                      port: int = 9999) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("UDP port must be between 0 and 65535")
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: HazardDatagramProtocol(service, loop),
        local_addr=(host, port),
        family=socket.AF_INET6 if ":" in host else socket.AF_INET,
    )
    try:
        await asyncio.Future()
    finally:
        transport.close()
        await protocol.drain()
