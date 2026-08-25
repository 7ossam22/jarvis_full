#!/usr/bin/env python3
"""
build.py — scans a folder of markdown notes and writes viewer/graph-data.js
for the JARVIS knowledge galaxy.

Standard library only. No pip installs.

Usage:
    python3 build.py [notes_dir]

If notes_dir is omitted, defaults to ./notes (and ./captures if it exists,
so notes grown live via /remember are picked up too).
"""
import json
import os
import re
import sys

DEFAULT_NOTES_DIR = "notes"
CAPTURES_DIRNAME = "captures"
OUTPUT_PATH = os.path.join("viewer", "graph-data.js")
EXCERPT_LEN = 700

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]*)?\]\]")
MD_STRIP_RE = re.compile(r"[#*_`>]|!\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\)")


def find_markdown_files(root):
    """Recursively find all .md files under root, skipping hidden dirs."""
    paths = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.lower().endswith(".md"):
                paths.append(os.path.join(dirpath, fn))
    return sorted(paths)


def title_from_filename(path):
    base = os.path.basename(path)
    return base[:-3] if base.lower().endswith(".md") else base


def group_from_path(path, root):
    """First subfolder under root is the group; notes directly in root get 'General'."""
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    return parts[0] if len(parts) > 1 else "General"


def make_excerpt(text):
    # Strip the first-line heading if it just repeats the title, then strip
    # light markdown syntax so the excerpt reads cleanly in the side panel.
    body = text.strip()
    lines = body.split("\n", 1)
    if lines and lines[0].startswith("#"):
        body = lines[1] if len(lines) > 1 else ""
    body = WIKILINK_RE.sub(r"\1", body)  # [[Note Title]] -> Note Title
    cleaned = MD_STRIP_RE.sub("", body)
    cleaned = re.sub(r"\n{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > EXCERPT_LEN:
        cleaned = cleaned[:EXCERPT_LEN].rsplit(" ", 1)[0] + "…"
    return cleaned


def build_graph(notes_dirs):
    """notes_dirs: list of root directories to scan (e.g. ['notes', 'notes/captures'])."""
    files = []
    for root in notes_dirs:
        if os.path.isdir(root):
            files.extend((p, root) for p in find_markdown_files(root))

    # De-dupe in case a captures dir is nested inside the main notes dir.
    seen = set()
    unique_files = []
    for path, root in files:
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)
        unique_files.append((path, root))

    nodes = []
    raw_texts = []
    title_to_id = {}

    for path, root in unique_files:
        title = title_from_filename(path)
        group = group_from_path(path, root)
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            text = ""

        node_id = len(nodes)
        nodes.append({
            "id": node_id,
            "label": title,
            "group": group,
            "excerpt": make_excerpt(text),
            "path": path,
        })
        raw_texts.append(text)
        title_to_id[title.lower()] = node_id

    links = []
    seen_pairs = set()

    def add_link(a, b):
        if a == b:
            return
        pair = (a, b) if a < b else (b, a)
        if pair in seen_pairs:
            return
        seen_pairs.add(pair)
        links.append({"source": pair[0], "target": pair[1]})

    for node_id, text in enumerate(raw_texts):
        # [[wikilinks]]
        for match in WIKILINK_RE.findall(text):
            target_title = match.strip().lower()
            target_id = title_to_id.get(target_title)
            if target_id is not None:
                add_link(node_id, target_id)

        # Plain-text mentions of another note's title.
        lower_text = text.lower()
        for other_title, other_id in title_to_id.items():
            if other_id == node_id:
                continue
            if len(other_title) < 4:
                continue  # skip trivially short titles to avoid noisy links
            if other_title in lower_text:
                add_link(node_id, other_id)

    return {"nodes": nodes, "links": links}


def main():
    notes_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NOTES_DIR

    if not os.path.isdir(notes_dir):
        print(f"Notes directory not found: {notes_dir}", file=sys.stderr)
        sys.exit(1)

    captures_dir = os.path.join(notes_dir, CAPTURES_DIRNAME)
    scan_dirs = [notes_dir]
    # Scan captures separately so its own subfolder doesn't get treated
    # as a nested group name inside "General".
    graph = build_graph(scan_dirs)

    if os.path.isdir(captures_dir):
        # Re-scan with captures merged in properly (captures/<file>.md -> group "captures")
        graph = build_graph([notes_dir])

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by build.py — do not edit by hand.\n")
        f.write("const GRAPH = ")
        f.write(json.dumps(graph, indent=2, ensure_ascii=False))
        f.write(";\n")

    print(f"Scanned {notes_dir} ({len(graph['nodes'])} notes, {len(graph['links'])} links)")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
