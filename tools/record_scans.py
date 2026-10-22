#!/usr/bin/env python3
"""Record LiDAR revolutions from the Pi backend to a JSON-lines file (one scan per line).

    python tools/record_scans.py scans.jsonl --seconds 10

Useful to replay real rooms in tests (pi/tests/data/) or to study the data offline.
"""

import argparse
import json
import time
import urllib.request


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--host", default="robert.local:8000")
    ap.add_argument("--seconds", type=float, default=10)
    args = ap.parse_args()
    last, n = None, 0
    end = time.monotonic() + args.seconds
    with open(args.out, "w") as f:
        while time.monotonic() < end:
            with urllib.request.urlopen(f"http://{args.host}/api/lidar/scan", timeout=2) as r:
                bins = json.load(r)["bins"]
            if bins and bins != last:
                f.write(json.dumps(bins) + "\n")
                last, n = bins, n + 1
            time.sleep(0.05)
    print(f"{n} scans -> {args.out}")


if __name__ == "__main__":
    main()
