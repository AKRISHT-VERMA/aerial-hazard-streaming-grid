"""Chromium dashboard smoke test, enabled in CI after Playwright install."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from urllib.request import urlopen

from backend.telemetry.protocol import TelemetryFrame, encode_frame


RUN_BROWSER = os.environ.get("RUN_BROWSER_TESTS") == "1" and importlib.util.find_spec("playwright")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@unittest.skipUnless(RUN_BROWSER, "install requirements-browser-test.txt and set RUN_BROWSER_TESTS=1")
class TestDashboardBrowser(unittest.TestCase):
    def test_live_render_synthetic_labels_empty_state_and_disconnect(self):
        from playwright.sync_api import sync_playwright

        root = Path(__file__).resolve().parents[1]
        http_port, udp_port = free_port(), free_port()
        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
        env.update({"HAZARD_TELEMETRY_SOURCE": "demo", "HAZARD_WEATHER_SOURCE": "demo"})
        server = subprocess.Popen(
            [sys.executable, "scripts/run_server.py", "--port", str(http_port),
             "--udp-port", str(udp_port)],
            cwd=root, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        browser = None
        try:
            base_url = f"http://127.0.0.1:{http_port}"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    with urlopen(f"{base_url}/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                self.fail("dashboard server did not become healthy")

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(base_url, wait_until="domcontentloaded")
                self.assertIn("Aerial Hazard Streaming Grid", page.title())
                self.assertIn("Waiting for telemetry", page.locator("#map").inner_text())

                frame = TelemetryFrame(9, 0, int(time.time()), 18.5204, 73.8567,
                                       (2000, 1900, 1800, 1700, 1600, 1500))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(encode_frame(frame), ("127.0.0.1", udp_port))

                page.locator("#cells tr").first.wait_for(timeout=10_000)
                self.assertIn("SYNTHETIC INPUT", page.locator("#cells").inner_text())
                self.assertIn("SYNTHETIC DEMO", page.locator("#cells").inner_text())
                page.locator("#alerts tr").first.wait_for(timeout=10_000)
                self.assertIn("SYNTHETIC DEMO", page.locator("#alerts").inner_text())

                server.terminate()
                server.wait(timeout=10)
                page.wait_for_function(
                    "document.querySelector('#connection').textContent === 'Reconnecting…'",
                    timeout=8_000,
                )
                browser.close()
                browser = None
        finally:
            if browser is not None:
                browser.close()
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
