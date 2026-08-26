"""app/images.py — parses the model's trailing 'IMAGE: <url>' and 'VIDEO: <url>'
lines into structured URLs for the client's reference-window gallery (Model layer:
parsing). Kept in sync with the IMAGE/VIDEO-line contract described in
app/persona.py's system prompt.
"""
import re

IMAGE_LINE_RE = re.compile(r"^[ \t]*IMAGE:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)
VIDEO_LINE_RE = re.compile(r"^[ \t]*VIDEO:[ \t]*(\S+)[ \t]*$", re.IGNORECASE | re.MULTILINE)


def extract_media_references(answer, max_media=6):
    """Pulls trailing 'IMAGE: <url>' and 'VIDEO: <url>' lines out of reply.
    Returns (clean_answer, image_urls, video_urls)."""
    img_matches = list(IMAGE_LINE_RE.finditer(answer))
    vid_matches = list(VIDEO_LINE_RE.finditer(answer))

    clean = IMAGE_LINE_RE.sub("", answer)
    clean = VIDEO_LINE_RE.sub("", clean).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()

    image_urls = []
    seen_img = set()
    for m in img_matches:
        url = m.group(1).strip().strip("<>()[]\"'").rstrip(".,;")
        if not re.match(r"^https?://", url, re.IGNORECASE) or url in seen_img:
            continue
        seen_img.add(url)
        image_urls.append(url)
        if len(image_urls) >= max_media:
            break

    video_urls = []
    seen_vid = set()
    for m in vid_matches:
        url = m.group(1).strip().strip("<>()[]\"'").rstrip(".,;")
        if not re.match(r"^https?://", url, re.IGNORECASE) or url in seen_vid:
            continue
        seen_vid.add(url)
        video_urls.append(url)
        if len(video_urls) >= max_media:
            break


    return clean, image_urls, video_urls


def extract_image_references(answer, max_images=6):
    clean, images, _ = extract_media_references(answer, max_images)
    return clean, images

