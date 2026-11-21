#!/usr/bin/env python3
"""Small, dependency-free HLS/CORS proxy for the IPTV Hub.

It is meant to run on two machines:

  * an Iranian server, for channels that are only reachable from Iran;
  * an international server, for channels that are blocked/filtered in Iran.

Both instances share TOKEN_SECRET. The updater signs a token per channel URL
and puts the resulting /p/<token> URL into status.json. The proxy verifies the
token, fetches the origin, rewrites every URI inside HLS playlists and streams
segments with permissive CORS headers. It is not an open proxy: without a valid
signed token it refuses to fetch anything.

Environment:
  PORT=18080
  PUBLIC_BASE_URL=https://stream.example.com/hls-proxy
  TOKEN_SECRET=<shared random string, at least 32 chars>
  REGION=ir|intl
  BLOCK_PRIVATE_HOSTS=1
  UPSTREAM_TIMEOUT=25
  MAX_CONCURRENT=96

Routes:
  GET /p/<token>       playback entry (play token)
  GET /s/<token>       playlist/segment/key (sub token, created on the fly)
  GET /probe/<token>   three-stage reachability check (probe token)
  GET /health          liveness/config snapshot
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "18080"))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
TOKEN_SECRET = os.environ.get("TOKEN_SECRET", "").strip()
REGION = os.environ.get("REGION", "unknown").strip() or "unknown"
BLOCK_PRIVATE_HOSTS = os.environ.get("BLOCK_PRIVATE_HOSTS", "1").strip() not in ("0", "false", "no")
UPSTREAM_TIMEOUT = float(os.environ.get("UPSTREAM_TIMEOUT", "25"))
MAX_MANIFEST_BYTES = int(os.environ.get("MAX_MANIFEST_BYTES", str(2 * 1024 * 1024)))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "96"))
SUB_TOKEN_TTL = int(os.environ.get("SUB_TOKEN_TTL", "3600"))
INSECURE_UPSTREAM = os.environ.get("INSECURE_UPSTREAM", "1").strip() not in ("0", "false", "no")

if not PUBLIC_BASE_URL:
    print("PUBLIC_BASE_URL is required (e.g. https://stream.example.com/hls-proxy)", file=sys.stderr)
    raise SystemExit(2)
if len(TOKEN_SECRET) < 16:
    print("TOKEN_SECRET is required and must be at least 16 characters", file=sys.stderr)
    raise SystemExit(2)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
    "Access-Control-Allow-Headers": "Range, Origin, Accept, Content-Type, If-Range",
    "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    "Access-Control-Max-Age": "86400",
}

SEMAPHORE = threading.BoundedSemaphore(max(1, MAX_CONCURRENT))
DNS_CACHE: dict[str, tuple[float, list[tuple[str, int]]]] = {}
DNS_LOCK = threading.Lock()

# Malformed tags such as "EXT-X-VERSION:6" (telewebion) must not be treated
# as URIs, but absolute http(s) URLs must be. The negative lookahead keeps
# "http:"/"https:"/data: out of the tag branch.
TAG_LINE_RE = re.compile(r"^(?!https?:|data:)[A-Za-z0-9-]+:")
URI_ATTR_RE = re.compile(r'URI="([^"]*)"')


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #

def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_token(payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = b64e(body)
    signature = b64e(hmac.new(TOKEN_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def verify_token(token: str | None, kinds: tuple[str, ...] = ("play", "sub")) -> dict | None:
    if not token or token.count(".") != 1 or len(token) > 8192:
        return None
    encoded, signature = token.split(".", 1)
    expected = b64e(hmac.new(TOKEN_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(b64d(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1 or payload.get("k") not in kinds:
        return None
    if not isinstance(payload.get("u"), str) or not payload["u"]:
        return None
    expires = payload.get("x")
    if not isinstance(expires, (int, float)) or expires <= time.time():
        return None
    return payload


def sub_url(absolute_url: str, play_expires: int | None = None) -> str:
    now = int(time.time())
    expires = now + max(60, SUB_TOKEN_TTL)
    if play_expires:
        expires = min(expires, int(play_expires))
    token = sign_token({"v": 1, "k": "sub", "u": absolute_url, "x": expires})
    return f"{PUBLIC_BASE_URL}/s/{token}"


# --------------------------------------------------------------------------- #
# network
# --------------------------------------------------------------------------- #

def resolve_public(host: str, port: int) -> None:
    """Reject hostnames that resolve to private/loopback addresses (basic SSRF guard)."""
    if not BLOCK_PRIVATE_HOSTS:
        return
    now = time.time()
    with DNS_LOCK:
        cached = DNS_CACHE.get(host)
        if cached and cached[0] > now:
            addresses = cached[1]
        else:
            addresses = []
            for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
                addresses.append((info[4][0], info[4][1]))
            DNS_CACHE[host] = (now + 60, addresses)
    for address, _port in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(f"private address refused: {host}")


def open_upstream(url: str, *, method: str = "GET", range_header: str | None = None, if_range: str | None = None):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("invalid upstream url")
    resolve_public(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))

    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "identity",
    }
    if range_header:
        headers["Range"] = range_header
    if if_range:
        headers["If-Range"] = if_range
    # Many IPTV CDNs expect the origin as Referer.
    headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

    request = urllib.request.Request(url, headers=headers, method=method)
    context = None
    if parsed.scheme == "https" and INSECURE_UPSTREAM:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        response = urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT, context=context)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        if "CERTIFICATE_VERIFY_FAILED" in text or isinstance(reason, ssl.SSLCertVerificationError):
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            response = urllib.request.urlopen(request, timeout=UPSTREAM_TIMEOUT, context=context)
        else:
            raise
    return response, response.geturl()


def is_manifest(response_headers, final_url: str, first_bytes: bytes) -> bool:
    content_type = (response_headers.get("Content-Type") or "").lower()
    if "mpegurl" in content_type or "vnd.apple" in content_type:
        return True
    if first_bytes.lstrip().startswith(b"#EXTM3U"):
        return True
    return final_url.lower().split("?")[0].endswith(".m3u8")


def rewrite_manifest(text: str, final_url: str, play_expires: int | None) -> str:
    output: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            output.append(raw_line)
            continue
        if line.startswith("#"):
            def replace(match: re.Match[str]) -> str:
                uri = match.group(1)
                try:
                    absolute = urllib.parse.urljoin(final_url, uri)
                except ValueError:
                    return match.group(0)
                return f'URI="{sub_url(absolute, play_expires)}"'

            output.append(URI_ATTR_RE.sub(replace, raw_line))
            continue
        # A few Iranian CDNs emit malformed tags such as "EXT-X-VERSION:6"
        # without the leading '#'. Treating them as URIs breaks hls.js.
        if TAG_LINE_RE.match(line):
            continue
        try:
            absolute = urllib.parse.urljoin(final_url, line)
        except ValueError:
            output.append(raw_line)
            continue
        output.append(sub_url(absolute, play_expires))
    return "\n".join(output) + "\n"


def first_uri(text: str, base_url: str) -> str | None:
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or TAG_LINE_RE.match(line):
            continue
        try:
            return urllib.parse.urljoin(base_url, line)
        except ValueError:
            continue
    return None


def looks_like_media(headers, first_bytes: bytes) -> bool:
    content_type = (headers.get("Content-Type") or "").lower()
    if any(word in content_type for word in ("mpegurl", "video/", "audio/", "application/octet-stream", "mp2t")):
        return True
    return len(first_bytes) > 188


def probe_url(url: str) -> dict:
    started = time.monotonic()
    result = {"ok": False, "status": "unstable", "http_code": None, "error": ""}
    try:
        response, final_url = open_upstream(url)
        code = response.status
        headers = response.headers
        first = response.read(256 * 1024)
        response.close()
        text = first.decode("utf-8", "ignore")
        if "#EXTM3U" not in text:
            if looks_like_media(headers, first):
                return {"ok": True, "status": "ok", "http_code": code, "latency_ms": int((time.monotonic() - started) * 1000), "error": ""}
            return {"ok": False, "status": "unstable", "http_code": code, "latency_ms": int((time.monotonic() - started) * 1000), "error": "not a playlist"}
        media_url = final_url
        if "#EXT-X-STREAM-INF" in text:
            media_url = first_uri(text, final_url) or ""
            if not media_url:
                return {"ok": False, "status": "unstable", "http_code": code, "latency_ms": int((time.monotonic() - started) * 1000), "error": "no variants"}
            response, final_url = open_upstream(media_url)
            code = response.status
            headers = response.headers
            first = response.read(256 * 1024)
            response.close()
            text = first.decode("utf-8", "ignore")
            if "#EXTM3U" not in text:
                return {"ok": False, "status": "unstable", "http_code": code, "latency_ms": int((time.monotonic() - started) * 1000), "error": "variant is not a playlist"}
        segment_url = first_uri(text, final_url)
        if not segment_url:
            return {"ok": False, "status": "unstable", "http_code": code, "latency_ms": int((time.monotonic() - started) * 1000), "error": "no segments"}
        segment, _final = open_upstream(segment_url, range_header="bytes=0-65535")
        seg_code = segment.status
        seg_headers = segment.headers
        seg_first = segment.read(65536)
        segment.close()
        ok = seg_code in (200, 206) and looks_like_media(seg_headers, seg_first)
        return {
            "ok": ok,
            "status": "ok" if ok else "unstable",
            "http_code": seg_code,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": "" if ok else "segment failed",
        }
    except urllib.error.HTTPError as exc:
        exc.close()
        status = "geo" if exc.code in (403, 451) else ("dead" if exc.code in (404, 410) else "unstable")
        return {"ok": False, "status": status, "http_code": exc.code, "latency_ms": int((time.monotonic() - started) * 1000), "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": "unstable", "http_code": None, "latency_ms": int((time.monotonic() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"[:200]}


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IPTVProxy/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] in ("p", "s", "probe"):
            path = f"/{parts[0]}/<token>"
        sys.stderr.write(f"{self.address_string()} {self.command} {path} -> {args[1] if len(args) > 1 else ''}\n")

    def send_cors(self) -> None:
        for key, value in CORS_HEADERS.items():
            self.send_header(key, value)

    def json_response(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_cors()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self) -> None:
        self.handle_request(head=True)

    def do_GET(self) -> None:
        self.handle_request(head=False)

    def handle_request(self, head: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/health":
            self.json_response(
                {
                    "ok": True,
                    "region": REGION,
                    "public_base_url": PUBLIC_BASE_URL,
                    "version": 1,
                    "max_concurrent": MAX_CONCURRENT,
                }
            )
            return
        if path.startswith("/probe/"):
            payload = verify_token(path[len("/probe/"):], ("probe",))
            if not payload:
                self.json_response({"ok": False, "error": "invalid_token"}, 403)
                return
            self.json_response(probe_url(payload["u"]))
            return
        if not (path.startswith("/p/") or path.startswith("/s/")):
            self.json_response({"ok": False, "error": "not_found"}, 404)
            return
        token = path[3:]
        payload = verify_token(token, ("play", "sub"))
        if not payload:
            self.json_response({"ok": False, "error": "invalid_or_expired_token"}, 403)
            return
        if not SEMAPHORE.acquire(blocking=False):
            self.send_response(503)
            self.send_header("Retry-After", "2")
            self.send_header("Content-Length", "0")
            self.send_cors()
            self.end_headers()
            return
        try:
            self.stream(payload, head=head)
        finally:
            SEMAPHORE.release()

    def stream(self, payload: dict, head: bool) -> None:
        url = payload["u"]
        range_header = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        try:
            response, final_url = open_upstream(
                url,
                method="HEAD" if head else "GET",
                range_header=range_header,
                if_range=if_range,
            )
        except urllib.error.HTTPError as exc:
            exc.close()
            self.json_response({"ok": False, "error": f"upstream_http_{exc.code}"}, exc.code if exc.code >= 400 else 502)
            return
        except Exception as exc:  # noqa: BLE001
            self.json_response({"ok": False, "error": f"upstream_error: {type(exc).__name__}"}, 502)
            return

        try:
            headers = response.headers
            content_type = headers.get("Content-Type") or "application/octet-stream"
            first = response.read(512) if not head else b""
            is_hls = "mpegurl" in content_type.lower() or url.lower().split("?")[0].endswith(".m3u8")
            if not is_hls and first.lstrip().startswith(b"#EXTM3U"):
                is_hls = True
            if is_hls:
                data = first + response.read(max(0, MAX_MANIFEST_BYTES + 1 - len(first)))
                if len(data) > MAX_MANIFEST_BYTES:
                    self.json_response({"ok": False, "error": "manifest_too_large"}, 502)
                    return
                text = data.decode("utf-8", "ignore")
                if "#EXTM3U" not in text:
                    self.send_response(response.status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_cors()
                    self.end_headers()
                    if not head:
                        self.wfile.write(data)
                    return
                rewritten = rewrite_manifest(text, final_url, payload.get("x")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8")
                self.send_header("Content-Length", str(len(rewritten)))
                self.send_header("Cache-Control", "no-store")
                self.send_cors()
                self.end_headers()
                if not head:
                    self.wfile.write(rewritten)
                return

            self.send_response(response.status)
            if headers.get("Content-Type"):
                self.send_header("Content-Type", headers["Content-Type"])
            if headers.get("Content-Length") and not head:
                self.send_header("Content-Length", headers["Content-Length"])
            if headers.get("Content-Range"):
                self.send_header("Content-Range", headers["Content-Range"])
            if headers.get("Accept-Ranges"):
                self.send_header("Accept-Ranges", headers["Accept-Ranges"])
            self.send_header("Cache-Control", "no-store")
            self.send_cors()
            self.close_connection = True
            self.end_headers()
            if head:
                return
            if first:
                self.wfile.write(first)
                self.wfile.flush()
            while True:
                chunk = response.read(128 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            response.close()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    print(f"IPTV proxy listening on 127.0.0.1:{PORT} region={REGION} base={PUBLIC_BASE_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
