#!/usr/bin/env python3
"""Convenience entry point: python server.py [--port 8765]."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from handoff_service.server import serve

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    parser.add_argument("--state", default=os.environ.get("HANDOFF_STATE"))
    args = parser.parse_args()
    serve(args.host, args.port, args.state)
