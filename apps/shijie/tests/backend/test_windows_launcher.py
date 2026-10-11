"""Exercise the actual Windows batch entry point, including paths with spaces."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


class LauncherFormatTests(unittest.TestCase):
    def test_batch_file_is_ascii_with_windows_line_endings(self):
        source = (ROOT / "START.cmd").read_bytes()
        source.decode("ascii")
        self.assertGreater(source.count(b"\r\n"), 10)
        self.assertNotIn(b"\n", source.replace(b"\r\n", b""))


@unittest.skipUnless(os.name == "nt", "Requires the real Windows cmd parser")
class WindowsLauncherTests(unittest.TestCase):
    def test_cmd_entry_serves_isolated_database_without_opening_browser(self):
        with tempfile.TemporaryDirectory(prefix="shijie-cmd-test-") as directory:
            temporary = Path(directory).resolve()
            self.assertEqual(temporary.parent, Path(tempfile.gettempdir()).resolve())
            self.assertTrue(temporary.name.startswith("shijie-cmd-test-"))
            data = temporary / "isolated data"
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            command = f'cmd.exe /d /s /c ""{ROOT / "START.cmd"}" --port {port} --data-dir "{data}" --no-open"'
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env={**os.environ, "STUDY_NO_PAUSE": "1", "PYTHONIOENCODING": "utf-8"},
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            received = None
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        output, error = process.communicate(timeout=2)
                        self.fail("Batch launcher exited before HTTP readiness: " +
                                  (output + error).decode("utf-8", errors="replace"))
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/bootstrap", timeout=.5) as response:
                            received = json.load(response)
                        break
                    except OSError:
                        time.sleep(.05)
                self.assertIsNotNone(received, "Actual cmd launcher did not start the app")
                self.assertEqual(received["projects"], [])
                self.assertTrue((data / "study.sqlite3").is_file())
            finally:
                if process.poll() is None:
                    # Stop only this test-owned cmd process and its descendants.
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                   capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    output, error = process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    output, error = process.communicate(timeout=5)
                self.assertNotIn(b"not recognized", error.lower())


if __name__ == "__main__":
    unittest.main()
