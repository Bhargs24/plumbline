#!/usr/bin/env python
"""
Launch the Plumbline console.

    python serve.py [--port 8912] [--db plumbline.db]

Resolves paths relative to the repository rather than the caller's working
directory, so the console finds its store wherever it is started from.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> int:
    ap = argparse.ArgumentParser(description="Plumbline console and API")
    ap.add_argument("--port", type=int, default=8912)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default="plumbline.db")
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    os.environ["PLUMBLINE_DB"] = args.db

    import uvicorn
    print(f"  console  http://{args.host}:{args.port}/")
    print(f"  api      http://{args.host}:{args.port}/api/runs")
    print(f"  store    {Path(args.db).resolve()}")
    uvicorn.run("plumbline.server.app:app", host=args.host, port=args.port,
                reload=args.reload, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
