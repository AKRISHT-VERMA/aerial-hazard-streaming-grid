"""Async UDP ingest adapter with CRC validation and sequence reordering."""

from __future__ import annotations

import asyncio
import logging
import socket

from backend.service import HazardService
from backend.telemetry.protocol import decode_frame
from backend.telemetry.reorder import ReorderBuffer

logger = logging.getLogger(__name__)


class UDPTransportLost(RuntimeError):
    """Raised when the UDP listener closes outside its requested shutdown."""


class HazardDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: HazardService, loop: asyncio.AbstractEventLoop,
                 reorder_window_seconds: float = 2.0,
                 processing_queue_size: int = 1024) -> None:
        if processing_queue_size < 1:
            raise ValueError("processing_queue_size must be positive")
        self.service = service
        self.loop = loop
        self.reorder = ReorderBuffer(window_seconds=reorder_window_seconds)
        self.transport: asyncio.DatagramTransport | None = None
        self.received = 0
        self.rejected = 0
        self.processed = 0
        self._flush_handle: asyncio.TimerHandle | None = None
        self.processing_queue: asyncio.Queue = asyncio.Queue(maxsize=processing_queue_size)
        self.queue_dropped = 0
        self.processing_errors = 0
        self._worker: asyncio.Task | None = None
        self.transport_alive = False
        self.shutting_down = False
        self.transport_loss: str | None = None
        self._closed = loop.create_future()

    def connection_made(self, transport) -> None:
        self.transport = transport
        self.transport_alive = True
        self._worker = self.loop.create_task(self._run(), name="hazard-frame-worker")
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
        self.transport_alive = False
        self.transport_loss = type(exc).__name__ if exc is not None else "closed"
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if not self._closed.done():
            self._closed.set_result(exc)

    def _schedule_flush(self) -> None:
        if self.transport_alive and not self.shutting_down:
            self._flush_handle = self.loop.call_later(0.1, self._flush)

    def _flush(self) -> None:
        if not self.transport_alive or self.shutting_down:
            return
        self._process(self.reorder.flush_expired())
        self._schedule_flush()

    def begin_shutdown(self) -> None:
        self.shutting_down = True
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None

    async def wait_closed(self):
        return await self._closed

    def _process(self, frames) -> None:
        for frame in frames:
            try:
                self.processing_queue.put_nowait(frame)
            except asyncio.QueueFull:
                self.queue_dropped += 1
                logger.error("UDP processing queue full; frame dropped (total=%d)", self.queue_dropped)

    async def _run(self) -> None:
        while True:
            frame = await self.processing_queue.get()
            try:
                if frame is None:
                    return
                await self._process_one(frame)
            finally:
                self.processing_queue.task_done()

    async def _process_one(self, frame) -> None:
        try:
            result = await self.service.process_frame(frame)
            if result is not None:
                self.processed += 1
        except Exception:
            self.processing_errors += 1
            logger.exception("Failed to process a valid telemetry frame")

    async def drain(self) -> None:
        await self.processing_queue.join()
        if self._worker is not None and not self._worker.done():
            await self.processing_queue.put(None)
            await self._worker


async def receive_udp(service: HazardService, host: str = "127.0.0.1",
                      port: int = 9999, protocol_ready: asyncio.Future | None = None) -> None:
    if not 0 <= port <= 65535:
        raise ValueError("UDP port must be between 0 and 65535")
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: HazardDatagramProtocol(service, loop),
        local_addr=(host, port),
        family=socket.AF_INET6 if ":" in host else socket.AF_INET,
    )
    if protocol_ready is not None and not protocol_ready.done():
        protocol_ready.set_result(protocol)
    try:
        await protocol.wait_closed()
        if not protocol.shutting_down:
            raise UDPTransportLost(
                f"UDP transport closed unexpectedly ({protocol.transport_loss})"
            )
    finally:
        protocol.begin_shutdown()
        transport.close()
        await protocol.drain()

