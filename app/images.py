"""app/images.py — parses the model's trailing 'IMAGE: <url>', 'VIDEO: <url>'
and 'SOURCE: <url>' lines into structured URLs (Model layer: parsing) — images/
videos feed the client's reference-window gallery, sources are kept only in
conversation history so later "send that to Discord" turns can cite them.
Kept in sync with the IMAGE/VIDEO/SOURCE-line contract described in
app/persona.py's system prompt.
"""
import re

IMAGE_LINE_RE = re.compile(r"^[ \t]*IMAGE:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
VIDEO_LINE_RE = re.compile(r"^[ \t]*VIDEO:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
SOURCE_LINE_RE = re.compile(r"^[ \t]*SOURCE:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)


def _clean_urls(matches, max_urls):
    urls, seen = [], set()
    for m in matches:
        url = m.group(1).strip().strip("<>()[]\"'").rstrip(".,;")
        if not re.match(r"^https?://", url, re.IGNORECASE) or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls


def extract_media_references(answer, max_media=6):
    """Pulls trailing 'IMAGE: <url>', 'VIDEO: <url>' and 'SOURCE: <url>' lines
    out of reply. Returns (clean_answer, image_urls, video_urls, source_urls)."""
    img_matches = list(IMAGE_LINE_RE.finditer(answer))
    vid_matches = list(VIDEO_LINE_RE.finditer(answer))
    src_matches = list(SOURCE_LINE_RE.finditer(answer))

    clean = IMAGE_LINE_RE.sub("", answer)
    clean = VIDEO_LINE_RE.sub("", clean)
    clean = SOURCE_LINE_RE.sub("", clean).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    image_urls = _clean_urls(img_matches, max_media)
    video_urls = _clean_urls(vid_matches, max_media)
    source_urls = _clean_urls(src_matches, 3)

    return clean, image_urls, video_urls, source_urls


def extract_image_references(answer, max_images=6):
    clean, images, _, _ = extract_media_references(answer, max_images)
    return clean, images

