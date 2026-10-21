import asyncio
import socket
import unittest

from backend.grid.spatial_grid import StreamingHazardGrid
from backend.service import HazardService
from backend.telemetry.protocol import TelemetryFrame, encode_frame
from backend.udp import HazardDatagramProtocol
from backend.weather import DemoWeatherProvider


class TestLiveUDPIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_udp_datagrams_reach_live_state_and_invalid_frames_are_counted(self):
        service = HazardService(
            StreamingHazardGrid(18.5204, 73.8567),
            weather=DemoWeatherProvider(),
            synthetic_input=True,
        )
        loop = asyncio.get_running_loop()
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: HazardDatagramProtocol(service, loop),
            local_addr=("127.0.0.1", 0),
        )
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            port = transport.get_extra_info("sockname")[1]
            sender.sendto(b"bad frame", ("127.0.0.1", port))
            frame = TelemetryFrame(9, 1, 1_800_000_000, 18.5204, 73.8567,
                                   (1900, 1800, 1700, 1600, 1550, 1500))
            sender.sendto(encode_frame(frame), ("127.0.0.1", port))
            for _ in range(100):
                if protocol.processed:
                    break
                await asyncio.sleep(.01)
            self.assertEqual(protocol.rejected, 1)
            self.assertEqual(protocol.processed, 1)
            state = service.snapshot()
            self.assertTrue(state["synthetic_input"])
            self.assertTrue(state["predictions"]["0,0"]["synthetic_input"])
        finally:
            sender.close()
            transport.close()
            await protocol.drain()


if __name__ == "__main__":
    unittest.main()
