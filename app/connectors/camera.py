"""app/connectors/camera.py — grabbing a frame from the server machine's
webcam (Model layer: hardware I/O).

Talks to Video4Linux2 directly through ioctl and mmap. That is a lot more code
than shelling out to ffmpeg, and it is here because ffmpeg, fswebcam, v4l2-ctl
and OpenCV are all absent on this machine and the project installs nothing —
the whole server is standard library only. A UVC webcam offers MJPG, which is
already JPEG on the wire, so the frame needs no decoding, no colour-space
conversion and no image library: dequeue one buffer, write the bytes.

If a future machine's camera offers no MJPG, capture reports that plainly
rather than returning a broken file — YUYV would need a JPEG encoder, which is
exactly the dependency this module exists to avoid.
"""
import errno
import fcntl
import mmap
import os
import struct
import time

# ---- V4L2 ABI --------------------------------------------------------------
# Layouts are the 64-bit Linux ones. They are asserted against the driver at
# runtime (see _capture_once): a struct-size mistake shows up as EINVAL, which
# is reported rather than guessed at.

def _IOC(direction, type_char, nr, size):
    return (direction << 30) | (size << 16) | (ord(type_char) << 8) | nr


_READ, _WRITE = 2, 1

_SZ_CAPABILITY = 104
_SZ_FORMAT = 208
_SZ_REQUESTBUFFERS = 20
_SZ_BUFFER = 88

VIDIOC_QUERYCAP = _IOC(_READ, "V", 0, _SZ_CAPABILITY)
VIDIOC_S_FMT = _IOC(_READ | _WRITE, "V", 5, _SZ_FORMAT)
VIDIOC_REQBUFS = _IOC(_READ | _WRITE, "V", 8, _SZ_REQUESTBUFFERS)
VIDIOC_QUERYBUF = _IOC(_READ | _WRITE, "V", 9, _SZ_BUFFER)
VIDIOC_QBUF = _IOC(_READ | _WRITE, "V", 15, _SZ_BUFFER)
VIDIOC_DQBUF = _IOC(_READ | _WRITE, "V", 17, _SZ_BUFFER)
VIDIOC_STREAMON = _IOC(_WRITE, "V", 18, 4)
VIDIOC_STREAMOFF = _IOC(_WRITE, "V", 19, 4)

BUF_TYPE_VIDEO_CAPTURE = 1
MEMORY_MMAP = 1
CAP_VIDEO_CAPTURE = 0x00000001

#: 'MJPG' as the driver spells it: a little-endian four-character code.
PIXFMT_MJPEG = int.from_bytes(b"MJPG", "little")

DEFAULT_DEVICE = "/dev/video0"
DEFAULT_WIDTH, DEFAULT_HEIGHT = 1280, 720

#: A webcam's first frames are black or badly exposed while the sensor's
#: auto-exposure settles. Grabbing frame one gives a dark, useless picture and
#: the model then describes a dark room — a wrong answer that looks like a
#: right one. These are dequeued and thrown away.
WARMUP_FRAMES = 6

#: A camera held open by another application never delivers a buffer, and
#: without a bound the request thread would hang until the browser gave up.
CAPTURE_TIMEOUT_S = 5.0


def list_devices():
    """Capture-capable video devices, newest-numbered last."""
    found = []
    for name in sorted(os.listdir("/dev")):
        if not name.startswith("video"):
            continue
        path = "/dev/" + name
        info = _describe(path)
        if info:
            found.append(info)
    return found


def _describe(path):
    """Name and capabilities, or None when it is not a usable capture node.

    A UVC webcam publishes several /dev/video* nodes and only one of them
    captures; the others carry metadata and would fail confusingly later.
    """
    try:
        fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        cap = bytearray(_SZ_CAPABILITY)
        fcntl.ioctl(fd, VIDIOC_QUERYCAP, cap, True)
        # Offset 84 is `capabilities` (everything the whole device can do),
        # 88 is `device_caps` (what THIS node does). A UVC webcam publishes a
        # metadata node alongside the real one and they differ only here, so
        # reading 84 lists a device that cannot capture and fails later.
        device_caps = struct.unpack_from("<I", cap, 88)[0]
        if not device_caps & CAP_VIDEO_CAPTURE:
            return None
        return {
            "device": path,
            "name": bytes(cap[16:48]).split(b"\0")[0].decode("utf-8", "replace"),
            "driver": bytes(cap[0:16]).split(b"\0")[0].decode("utf-8", "replace"),
        }
    except OSError:
        return None
    finally:
        os.close(fd)


def _capture_once(device, width, height):
    """One MJPEG frame as JPEG bytes. Raises OSError with a readable message."""
    fd = os.open(device, os.O_RDWR)
    mapped = None
    streaming = False
    try:
        fmt = bytearray(_SZ_FORMAT)
        struct.pack_into("<I", fmt, 0, BUF_TYPE_VIDEO_CAPTURE)
        # v4l2_pix_format sits at offset 8 (the union is pointer-aligned).
        struct.pack_into("<IIII", fmt, 8, width, height, PIXFMT_MJPEG, 0)
        fcntl.ioctl(fd, VIDIOC_S_FMT, fmt, True)

        got = struct.unpack_from("<I", fmt, 16)[0]
        if got != PIXFMT_MJPEG:
            raise OSError(
                f"{device} will not provide MJPEG (the driver chose "
                f"{got.to_bytes(4, 'little').decode('ascii', 'replace')!r}). "
                "Only MJPEG can be saved without an image library.")

        req = bytearray(_SZ_REQUESTBUFFERS)
        struct.pack_into("<III", req, 0, 1, BUF_TYPE_VIDEO_CAPTURE, MEMORY_MMAP)
        fcntl.ioctl(fd, VIDIOC_REQBUFS, req, True)
        if struct.unpack_from("<I", req, 0)[0] < 1:
            raise OSError(f"{device} granted no capture buffers")

        buf = bytearray(_SZ_BUFFER)
        struct.pack_into("<I", buf, 0, 0)                       # index
        struct.pack_into("<I", buf, 4, BUF_TYPE_VIDEO_CAPTURE)
        struct.pack_into("<I", buf, 60, MEMORY_MMAP)
        fcntl.ioctl(fd, VIDIOC_QUERYBUF, buf, True)
        offset = struct.unpack_from("<I", buf, 64)[0]
        length = struct.unpack_from("<I", buf, 72)[0]
        mapped = mmap.mmap(fd, length, mmap.MAP_SHARED,
                           mmap.PROT_READ | mmap.PROT_WRITE, offset=offset)

        fcntl.ioctl(fd, VIDIOC_STREAMON,
                    struct.pack("<I", BUF_TYPE_VIDEO_CAPTURE))
        streaming = True

        frame = b""
        deadline = time.monotonic() + CAPTURE_TIMEOUT_S
        for _ in range(WARMUP_FRAMES + 1):
            fcntl.ioctl(fd, VIDIOC_QBUF, buf, True)
            while True:
                if time.monotonic() > deadline:
                    raise OSError(
                        f"{device} delivered no frame within "
                        f"{CAPTURE_TIMEOUT_S:.0f}s — it may be in use by another "
                        "application.")
                try:
                    fcntl.ioctl(fd, VIDIOC_DQBUF, buf, True)
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EINTR):
                        raise
                    time.sleep(0.02)
            used = struct.unpack_from("<I", buf, 8)[0]
            frame = mapped[:used]
        return bytes(frame)
    finally:
        if streaming:
            try:
                fcntl.ioctl(fd, VIDIOC_STREAMOFF,
                            struct.pack("<I", BUF_TYPE_VIDEO_CAPTURE))
            except OSError:
                pass
        if mapped is not None:
            mapped.close()
        os.close(fd)


def capture_frame(device=None, width=DEFAULT_WIDTH, height=DEFAULT_HEIGHT):
    """Returns {"ok": True, "jpeg": bytes, ...} or {"ok": False, "error": str}.

    Never raises: this is reached from a tool call inside a chat turn, and the
    turn should be able to say "the camera is busy" rather than die.
    """
    if device is None:
        devices = list_devices()
        if not devices:
            return {"ok": False,
                    "error": "No camera found on this machine (no capture-capable "
                             "/dev/video* device)."}
        device = devices[0]["device"]

    try:
        jpeg = _capture_once(device, width, height)
    except PermissionError:
        return {"ok": False,
                "error": f"Not allowed to read {device}. Add this user to the "
                         f"'video' group (sudo usermod -aG video $USER) and log in again."}
    except FileNotFoundError:
        return {"ok": False, "error": f"{device} does not exist."}
    except OSError as e:
        return {"ok": False, "error": f"Could not capture from {device}: {e}"}

    # A truncated or non-JPEG buffer would reach the model as an unreadable
    # image and come back as a confusing refusal; catch it here instead.
    if not jpeg.startswith(b"\xff\xd8") or len(jpeg) < 1024:
        return {"ok": False,
                "error": f"{device} returned {len(jpeg)} bytes that are not a JPEG frame."}

    return {"ok": True, "jpeg": jpeg, "device": device, "bytes": len(jpeg)}
