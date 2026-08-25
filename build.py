#!/usr/bin/env python3
"""build.py — CLI wrapper: scans a folder of markdown notes and writes
viewer/graph-data.js for the JARVIS brain visualization.

The actual scanning/graph logic lives in app/graph.py (shared with the
running server, which regenerates the graph on every /chat and /remember
call) — this script just drives it from the command line.

Standard library only. No pip installs.

Usage:
    python3 build.py [notes_dir]

If notes_dir is omitted, defaults to ./notes (and its captures/ subfolder,
if present, is picked up automatically — so notes grown live via /remember
are included too).
"""
import os
import sys

from app.graph import regenerate_graph

DEFAULT_NOTES_DIR = "notes"
VIEWER_DIR = "viewer"


def main():
    notes_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NOTES_DIR

    if not os.path.isdir(notes_dir):
        print(f"Notes directory not found: {notes_dir}", file=sys.stderr)
        sys.exit(1)

    graph = regenerate_graph(notes_dir, VIEWER_DIR)
    print(f"Scanned {notes_dir} ({len(graph['nodes'])} notes, {len(graph['links'])} links)")
    print(f"Wrote {os.path.join(VIEWER_DIR, 'graph-data.js')}")


if __name__ == "__main__":
    main()
