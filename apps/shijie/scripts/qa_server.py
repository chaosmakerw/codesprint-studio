"""Owned, disposable loopback server for UI checks; no user data or model calls."""
from pathlib import Path
import argparse
import json
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from projectstudy.server import make_server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    server = make_server(port=0, data_dir=args.data_dir)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    print(json.dumps({"port": server.server_address[1]}), flush=True)
    try:
        for line in sys.stdin:
            if line.strip() == "stop":
                break
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


if __name__ == "__main__":
    main()
