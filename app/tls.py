"""app/tls.py — TLS material for the HTTPS listener (self-signed, local).

Jarvis is a LAN server, so there is no CA to ask: this module keeps a
self-signed certificate under certs/ and regenerates it when it is missing,
expired, or no longer covers the machine's current LAN address (a laptop's
IP moves, and a certificate without the address you typed in the bar is a
much louder browser error than the plain self-signed warning).

Standard library only — the key/cert are produced by shelling out to the
`openssl` binary, which is present on every machine this runs on. If it is
missing, HTTPS is skipped and the HTTP listener carries on alone.
"""
import datetime
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(ROOT, "certs")
CERT_PATH = os.path.join(CERT_DIR, "jarvis.crt")
KEY_PATH = os.path.join(CERT_DIR, "jarvis.key")

VALID_DAYS = 825  # the longest a self-signed leaf may live before browsers balk
RENEW_WITHIN_DAYS = 7


def local_ip_addresses():
    """Every IPv4/IPv6 address this host answers on, best effort.

    The UDP-connect trick is what finds the address a phone on the same
    Wi-Fi will actually dial; getaddrinfo alone often reports only
    127.0.1.1 on Linux."""
    addresses = {"127.0.0.1", "::1"}
    for family, dest in ((socket.AF_INET, ("8.8.8.8", 80)),
                         (socket.AF_INET6, ("2001:4860:4860::8888", 80))):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.connect(dest)          # no packet is sent; this only picks a route
            addresses.add(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addresses.add(info[4][0])
    except OSError:
        pass
    return {a.split("%")[0] for a in addresses if a}


def _canonical_ip(addr):
    """One spelling per address, so a stored certificate can be compared
    against the live ones — openssl prints ::1 back as 0:0:0:0:0:0:0:1, and a
    mismatch there would otherwise regenerate the certificate on every boot."""
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            return socket.inet_ntop(family, socket.inet_pton(family, addr))
        except (OSError, ValueError):
            continue
    return addr


def _san_value():
    names = ["DNS:localhost"]
    hostname = socket.gethostname()
    if hostname and hostname != "localhost":
        names.append(f"DNS:{hostname}")
        names.append(f"DNS:{hostname}.local")
    names += [f"IP:{addr}" for addr in sorted(local_ip_addresses())]
    return ",".join(names)


def _cert_is_usable(cert_path, wanted_ips):
    """True when the existing certificate is still valid and still names the
    addresses we are about to serve on."""
    try:
        out = subprocess.run(
            ["openssl", "x509", "-in", cert_path, "-noout", "-enddate", "-ext", "subjectAltName"],
            capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return False

    match = re.search(r"notAfter=(.+)", out)
    if not match:
        return False
    try:
        expiry = datetime.datetime.strptime(match.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
    except ValueError:
        return False
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    if expiry - now < datetime.timedelta(days=RENEW_WITHIN_DAYS):
        return False

    covered = {_canonical_ip(a) for a in re.findall(r"IP Address:([0-9A-Fa-f.:]+)", out)}
    return {_canonical_ip(a) for a in wanted_ips} <= covered


def ensure_certificate(cert_path=CERT_PATH, key_path=KEY_PATH):
    """Returns (cert_path, key_path), generating a self-signed pair if the
    stored one is missing, stale, or short an address. Raises RuntimeError if
    openssl cannot produce one."""
    wanted_ips = local_ip_addresses()
    if os.path.isfile(cert_path) and os.path.isfile(key_path) \
            and _cert_is_usable(cert_path, wanted_ips):
        return cert_path, key_path

    if not shutil.which("openssl"):
        raise RuntimeError("the `openssl` command is not installed")

    os.makedirs(CERT_DIR, exist_ok=True)
    subject = f"/CN={socket.gethostname() or 'jarvis'}/O=Jarvis (self-signed)"
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
           "-days", str(VALID_DAYS), "-subj", subject,
           "-addext", "subjectAltName=" + _san_value(),
           "-keyout", key_path, "-out", cert_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        # -addext arrived in OpenSSL 1.1.1; without it the certificate is
        # still usable, it just names no addresses (every browser will warn).
        result = subprocess.run([c for c in cmd if not c.startswith("subjectAltName=")
                                 and c != "-addext"], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "openssl failed").strip().splitlines()[-1])

    os.chmod(key_path, 0o600)
    sys.stderr.write(f"[jarvis] tls: generated a self-signed certificate at {cert_path}\n")
    return cert_path, key_path


def make_ssl_context(cert_path, key_path):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context
