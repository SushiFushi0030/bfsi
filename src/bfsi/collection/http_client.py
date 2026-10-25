"""Public HTTP transport and WBI signing used by the paginated collector."""

from __future__ import annotations

import hashlib
import json
import ssl
import time
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen


NAV_URL = "https://api.bilibili.com/x/web-interface/nav"

# Bilibili web client uses this fixed permutation to turn two live WBI keys
# into the 32-character key used for signing.
MIXIN_KEY_ORDER = (
    46, 47, 18, 2, 53, 8, 23, 32,
    15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19,
    29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63,
    57, 62, 11, 36, 20, 34, 44, 52,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/140.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
}


def get_bytes(url: str) -> bytes:
    """Send one GET request and return the response body unchanged."""
    request = Request(url, headers=HEADERS)
    ssl_context = ssl.create_default_context()
    default_ca = ssl.get_default_verify_paths().cafile
    macos_ca = Path("/etc/ssl/cert.pem")
    if default_ca is None and macos_ca.is_file():
        # The python.org macOS build can miss its own CA bundle. Reuse the
        # operating system's bundle while keeping certificate checks enabled.
        ssl_context.load_verify_locations(cafile=macos_ca)
    with urlopen(request, timeout=20, context=ssl_context) as response:
        return response.read()


def filename_stem(url: str) -> str:
    """Extract the filename without its extension from a URL."""
    return Path(urlparse(url).path).stem


def get_mixin_key() -> str:
    """Fetch today's live WBI keys and combine them as Bilibili expects."""
    from .collector import _decode_transport
    nav = json.loads(_decode_transport(get_bytes(NAV_URL)))
    wbi_img = nav["data"]["wbi_img"]
    raw_key = filename_stem(wbi_img["img_url"]) + filename_stem(wbi_img["sub_url"])
    return "".join(raw_key[index] for index in MIXIN_KEY_ORDER)[:32]


def sign(params: dict[str, str | int], mixin_key: str) -> dict[str, str | int]:
    """Add a fresh timestamp and calculate WBI's w_rid signature."""
    signed = dict(params)
    signed["wts"] = int(time.time())

    # WBI removes these five characters before URL encoding and hashing.
    forbidden = "!'()*"
    cleaned = {
        key: "".join(char for char in str(value) if char not in forbidden)
        for key, value in signed.items()
    }
    canonical_query = urlencode(
        sorted(cleaned.items()),
        quote_via=quote,
        safe="~",
    )
    signed["w_rid"] = hashlib.md5(
        (canonical_query + mixin_key).encode("utf-8")
    ).hexdigest()
    return signed
