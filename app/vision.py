"""app/vision.py — carrying pictures into a model call (Model layer).

Every provider here spoke text only: an internal turn was
``{"role": ..., "content": str}`` and each provider stringified it. Showing the
model a camera frame means a turn can now also carry images, and each backend
wants that in a different shape — so the shapes live here, once, instead of
being reinvented in three provider modules.

An internal turn gains one optional key::

    {"role": "user", "content": "what do you see?",
     "images": [{"media_type": "image/jpeg", "data": "<base64>"}]}

Absent or empty, everything behaves exactly as before — text in, text out.

Standard library only, like the rest of the provider layer.
"""
import base64
import binascii

#: More than a handful of frames is never a better answer, just a slower and
#: costlier one — and on a metered key, a way to burn a quota in one turn.
MAX_IMAGES = 4

#: Per image, after decoding. Comfortably above a webcam JPEG and far below
#: what any provider will accept, so an oversized frame is rejected here with
#: a clear reason rather than as an opaque HTTP 400 from someone's API.
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp", "image/gif")


def normalize_images(raw):
    """Returns a clean list of {"media_type", "data"} — dropping anything
    malformed rather than raising.

    Frames arrive from a browser canvas and from a webcam driver, so this is
    the boundary where untrusted, possibly enormous input becomes something a
    provider payload can be built from. A bad frame must never take the whole
    turn down with it: the user asked a question, and answering it without the
    picture beats erroring out.

    Accepts either a bare base64 string, a data: URL, or the dict form.
    """
    if not raw:
        return []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []

    out = []
    for item in raw[:MAX_IMAGES]:
        media_type, data = "image/jpeg", None

        if isinstance(item, str):
            data = item
        elif isinstance(item, dict):
            data = item.get("data") or item.get("base64") or item.get("url")
            media_type = (item.get("media_type") or item.get("mime_type")
                          or media_type)
        if not isinstance(data, str) or not data.strip():
            continue

        # "data:image/jpeg;base64,AAAA…" — what a browser canvas hands you.
        if data.startswith("data:"):
            header, _, payload = data.partition(",")
            if not payload:
                continue
            if ";" in header and header[5:].split(";")[0]:
                media_type = header[5:].split(";")[0]
            data = payload

        data = "".join(data.split())          # strip newlines from wrapped b64
        if media_type not in _ALLOWED_TYPES:
            continue
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            continue
        if not decoded or len(decoded) > MAX_IMAGE_BYTES:
            continue

        out.append({"media_type": media_type, "data": data})
    return out


def images_of(message):
    """The normalized images on one internal turn."""
    return normalize_images(message.get("images")) if isinstance(message, dict) else []


def has_images(messages):
    return any(images_of(m) for m in messages or ())


# ---- per-provider shapes ---------------------------------------------------
# The image goes BEFORE the text in all three. Models attend better to a
# question asked about a picture they have already been shown than to one they
# are told to hold in mind while the picture arrives.

def to_anthropic_content(text, images):
    """Anthropic content blocks. Returns a plain string when there is no
    image, so ordinary turns keep exactly the payload they had before."""
    if not images:
        return text
    blocks = [{"type": "image",
               "source": {"type": "base64", "media_type": im["media_type"],
                          "data": im["data"]}}
              for im in images]
    blocks.append({"type": "text", "text": text})
    return blocks


def to_gemini_parts(text, images):
    """Gemini parts, which always come as a list."""
    parts = [{"inline_data": {"mime_type": im["media_type"], "data": im["data"]}}
             for im in images]
    parts.append({"text": text})
    return parts


def to_openai_content(text, images):
    """OpenAI-compatible content (LM Studio). Images ride as data: URLs.

    Whether they are UNDERSTOOD depends on the model loaded in LM Studio: a
    text-only model given an image typically ignores it or errors, so callers
    should prefer a provider known to see. Sending it correctly is this
    module's job; choosing the backend is not.
    """
    if not images:
        return text
    content = [{"type": "image_url",
                "image_url": {"url": f"data:{im['media_type']};base64,{im['data']}"}}
               for im in images]
    content.append({"type": "text", "text": text})
    return content
