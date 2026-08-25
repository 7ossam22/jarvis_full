"""app/images.py — parses the model's trailing 'IMAGE: <url>' lines into
structured URLs for the client's reference-window gallery (Model layer:
parsing). Kept in sync with the IMAGE-line contract described in
app/persona.py's system prompt — changing the wire format means updating
both.
"""
import re

IMAGE_LINE_RE = re.compile(r"^[ \t]*IMAGE:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)


def extract_image_references(answer, max_images=6):
    """Pulls every trailing 'IMAGE: <url>' line out of the model's reply (there
    may be several, for a requested gallery). Returns (clean_answer, [urls]).
    URLs are never spoken/shown as text — only handed to the client as
    structured data for the reference windows."""
    matches = list(IMAGE_LINE_RE.finditer(answer))
    if not matches:
        return answer, []
    clean = IMAGE_LINE_RE.sub("", answer).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    urls = []
    seen = set()
    for m in matches:
        url = m.group(1).strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            continue  # "none", or garbage — drop it rather than pass it along
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_images:
            break
    return clean, urls
