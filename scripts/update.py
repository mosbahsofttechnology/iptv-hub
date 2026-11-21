#!/usr/bin/env python3
"""
IPTV Hub — keep a database of Iranian & Afghan TV streams fresh.

Pipeline:
  1. load the previous state (JSON snapshot committed to git)
  2. refresh channel/stream metadata from iptv-org country playlists
  3. apply manual overrides (data/overrides.json)
  4. health-check every stream (master playlist -> media playlist -> segment)
  5. update SQLite, export playlists + status.json + dashboard

The SQLite file is a local working database and is not committed.
data/state.json is the durable, git-friendly snapshot.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import socket
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
PLAYLISTS_DIR = ROOT / "playlists"
DB_PATH = DATA_DIR / "iptv.db"
STATE_PATH = DATA_DIR / "state.json"
OVERRIDES_PATH = DATA_DIR / "overrides.json"
TEMPLATE_PATH = ROOT / "scripts" / "dashboard_template.html"

SOURCES = [
    {
        "id": "iptv-org-ir",
        "url": "https://iptv-org.github.io/iptv/countries/ir.m3u",
        "country": "IR",
        "label": "Iran (iptv-org)",
    },
    {
        "id": "iptv-org-af",
        "url": "https://iptv-org.github.io/iptv/countries/af.m3u",
        "country": "AF",
        "label": "Afghanistan (iptv-org)",
    },
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
}

# Origin used when testing whether a stream may be played inside a browser.
# hls.js fetches every playlist/segment with XHR, so each response needs a
# matching Access-Control-Allow-Origin header.
BROWSER_ORIGIN = os.environ.get(
    "IPTV_BROWSER_ORIGIN",
    f"https://{os.environ['GITHUB_REPOSITORY_OWNER'].lower()}.github.io"
    if os.environ.get("GITHUB_REPOSITORY_OWNER")
    else "https://mosbahsofttechnology.github.io",
)

COUNTRY_FA = {"IR": "ایران", "AF": "افغانستان"}
COUNTRY_ORDER = {"IR": 0, "AF": 1}

CATEGORY_FA = {
    "General": "عمومی",
    "News": "خبری",
    "Movies": "فیلم",
    "Movie": "فیلم",
    "Entertainment": "سرگرمی",
    "Music": "موسیقی",
    "Religious": "مذهبی",
    "Kids": "کودکان",
    "Sports": "ورزش",
    "Series": "سریال",
    "Documentary": "مستند",
    "Classic": "کلاسیک",
    "Family": "خانوادگی",
    "Culture": "فرهنگ",
    "Education": "آموزش",
    "Lifestyle": "سبک زندگی",
    "Travel": "سفر",
    "Food": "غذا",
    "Cooking": "آشپزی",
    "Shop": "فروش",
    "Business": "اقتصاد",
    "Weather": "هوا",
    "Science": "علم",
    "Auto": "خودرو",
    "Comedy": "کمدی",
    "Drama": "درام",
    "Action": "اکشن",
    "Horror": "ترسناک",
    "Animation": "انیمیشن",
    "Anime": "انیمه",
    "Cartoon": "کارتون",
    "Reality": "ریالیتی",
    "Talk": "گفتگو",
    "Politics": "سیاست",
    "Public": "عمومی",
    "Local": "محلی",
    "Regional": "منطقه‌ای",
    "National": "ملی",
    "International": "بین‌المللی",
    "Undefined": "عمومی",
}

STATUS_RANK = {"ok": 0, "geo": 1, "unstable": 2, "unknown": 3, "dead": 4}
STATUS_FA = {
    "ok": "سالم",
    "geo": "محدودیت جغرافیایی",
    "dead": "قطع",
    "unstable": "ناپایدار",
    "unknown": "نامشخص",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    category TEXT,
    logo TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS streams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    url TEXT NOT NULL UNIQUE,
    quality TEXT,
    referrer TEXT,
    source TEXT,
    priority INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1,
    status TEXT DEFAULT 'unknown',
    http_code INTEGER,
    latency_ms INTEGER,
    last_checked_at TEXT,
    last_ok_at TEXT,
    fail_streak INTEGER DEFAULT 0,
    browser_ok INTEGER,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id INTEGER NOT NULL REFERENCES streams(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    status TEXT,
    http_code INTEGER,
    latency_ms INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_streams_channel ON streams(channel_id);
CREATE INDEX IF NOT EXISTS idx_checks_stream ON checks(stream_id);
CREATE INDEX IF NOT EXISTS idx_checks_time ON checks(checked_at);
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    url TEXT,
    last_fetch_at TEXT,
    ok INTEGER,
    note TEXT
);
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def log(message: str) -> None:
    stamp = dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value or "").strip("-").lower()
    if value:
        return value
    return "ch-" + hashlib.md5((value or "x").encode("utf-8")).hexdigest()[:8]


def quality_rank(quality: str | None) -> int:
    if not quality:
        return 0
    match = re.search(r"(\d{3,4})", quality)
    if not match:
        return 0
    height = int(match.group(1))
    if height >= 2160:
        return 4
    if height >= 1080:
        return 3
    if height >= 720:
        return 2
    if height >= 480:
        return 1
    return 0


def clean_name(name: str) -> str:
    name = re.sub(r"\s*\((?:2160|1080|720|576|540|480|360|240)p\)", "", name, flags=re.I)
    name = re.sub(r"\s*\[(?:Not 24/7|Geo-blocked|Offline)\]", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


def category_fa(category: str | None) -> str:
    if not category:
        return "عمومی"
    return CATEGORY_FA.get(category, category)


def extinf_name(line: str) -> str:
    """Return the display name after the first comma that is outside quotes."""
    in_quotes = False
    for index, char in enumerate(line):
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            return line[index + 1 :].strip()
    return ""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _open(req: urllib.request.Request, timeout: float):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
            # Plenty of public IPTV endpoints have broken certificate chains.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            return urllib.request.urlopen(req, timeout=timeout, context=context)
        raise


def fetch(
    url: str,
    *,
    referrer: str | None = None,
    timeout: float = 10.0,
    max_bytes: int = 65_536,
    use_range: bool = True,
    origin: str | None = None,
):
    headers = dict(BASE_HEADERS)
    if use_range:
        headers["Range"] = "bytes=0-65535"
    if referrer:
        headers["Referer"] = referrer
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, headers=headers)
    response = _open(req, timeout)
    try:
        data = response.read(max_bytes)
        return response.status, response.headers, data, response.geturl()
    finally:
        response.close()


# --------------------------------------------------------------------------- #
# M3U parsing
# --------------------------------------------------------------------------- #

ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


def parse_m3u(text: str, country: str, source_id: str) -> list[dict]:
    lines = text.replace("\ufeff", "").splitlines()
    entries: list[dict] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line.startswith("#EXTINF"):
            index += 1
            continue

        attrs = dict(ATTR_RE.findall(line))
        raw_name = extinf_name(line)
        name = clean_name(raw_name)
        referrer = attrs.get("http-referrer", "").strip()
        index += 1
        while index < len(lines) and lines[index].lstrip().startswith("#"):
            option = lines[index].strip()
            upper = option.upper()
            if upper.startswith("#EXTVLCOPT:HTTP-REFERRER="):
                referrer = option.split("=", 1)[1].strip()
            elif upper.startswith("#KODIPROP:INPUTSTREAM.ADAPTIVE.STREAM_HEADERS="):
                for part in option.split("=", 1)[1].split("&"):
                    if part.lower().startswith("referer="):
                        referrer = part.split("=", 1)[1].strip()
            index += 1

        url = lines[index].strip() if index < len(lines) else ""
        index += 1
        if not url.startswith(("http://", "https://")):
            continue

        tvg_id = attrs.get("tvg-id", "").strip()
        channel_id = tvg_id.split("@")[0] if tvg_id else f"{country.lower()}.{slugify(name)}"
        group = attrs.get("group-title", "").strip()
        group = re.sub(r"^(Iran|Afghanistan)\s*[•|]\s*", "", group)
        # Some playlists use "Classic;Music" for multiple groups; keep the first.
        group = re.split(r"[;,]", group)[0].strip()
        if group in ("", "Undefined"):
            group = "General"

        quality_match = re.search(r"\((2160|1080|720|576|540|480|360|240)p\)", raw_name, flags=re.I)
        quality = f"{quality_match.group(1)}p" if quality_match else ""

        entries.append(
            {
                "channel_id": channel_id,
                "name": name or channel_id,
                "country": country,
                "category": group,
                "logo": attrs.get("tvg-logo", "").strip(),
                "url": url,
                "quality": quality,
                "referrer": referrer,
                "source": source_id,
            }
        )
    return entries


def parse_variants(text: str, base_url: str) -> list[tuple[int, int, str]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        bandwidth = re.search(r"BANDWIDTH=(\d+)", line)
        resolution = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        for candidate in lines[index + 1 :]:
            if not candidate.startswith("#"):
                variants.append(
                    (
                        int(bandwidth.group(1)) if bandwidth else 0,
                        int(resolution.group(2)) if resolution else 0,
                        urllib.parse.urljoin(base_url, candidate),
                    )
                )
                break
    variants.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return variants


def first_segment(text: str, base_url: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return urllib.parse.urljoin(base_url, line)
    return None


def is_media(headers, data: bytes) -> bool:
    if not data:
        return False
    content_type = (headers.get("Content-Type") or "").lower()
    if any(token in content_type for token in ("video/", "mp2t", "octet-stream", "mp4")):
        return True
    sample = data[:1024]
    lowered = sample.lower()
    if b"<html" in lowered or lowered.lstrip().startswith(b"{"):
        return False
    return b"\x00" in sample


# --------------------------------------------------------------------------- #
# database
# --------------------------------------------------------------------------- #

def init_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE streams ADD COLUMN browser_ok INTEGER")
    except sqlite3.OperationalError:
        pass
    return conn


def upsert_channel(
    conn: sqlite3.Connection,
    channel_id: str,
    name: str,
    country: str,
    category: str,
    logo: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO channels(id, name, country, category, logo, updated_at)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            country = COALESCE(NULLIF(excluded.country, ''), channels.country),
            category = COALESCE(NULLIF(excluded.category, ''), channels.category),
            logo = COALESCE(NULLIF(excluded.logo, ''), channels.logo),
            updated_at = excluded.updated_at
        """,
        (channel_id, name, country, category, logo, now_iso()),
    )


def upsert_stream(
    conn: sqlite3.Connection,
    channel_id: str,
    url: str,
    quality: str = "",
    referrer: str = "",
    source: str = "",
    priority: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO streams(channel_id, url, quality, referrer, source, priority, added_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            channel_id = excluded.channel_id,
            quality = COALESCE(NULLIF(excluded.quality, ''), streams.quality),
            referrer = COALESCE(NULLIF(excluded.referrer, ''), streams.referrer),
            source = CASE WHEN excluded.source = 'manual' THEN 'manual' ELSE streams.source END,
            priority = MAX(streams.priority, excluded.priority)
        """,
        (channel_id, url, quality, referrer, source, priority, now_iso()),
    )


# --------------------------------------------------------------------------- #
# state + sources + overrides
# --------------------------------------------------------------------------- #

def import_state(conn: sqlite3.Connection) -> None:
    if not STATE_PATH.exists():
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"state: cannot read {STATE_PATH.name}: {exc}")
        return

    for channel in state.get("channels", []):
        channel_id = channel.get("id")
        if not channel_id:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(id, name, country, category, logo, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                channel.get("name") or channel_id,
                channel.get("country") or "",
                channel.get("category") or "General",
                channel.get("logo") or "",
                now_iso(),
            ),
        )

    restored = 0
    for stream in state.get("streams", []):
        channel_id = stream.get("channel_id")
        url = stream.get("url")
        if not channel_id or not url:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO channels(id, name, country, category, logo, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (channel_id, channel_id, "", "General", "", now_iso()),
        )
        upsert_stream(
            conn,
            channel_id,
            url,
            quality=stream.get("quality") or "",
            referrer=stream.get("referrer") or "",
            source=stream.get("source") or "",
            priority=int(stream.get("priority") or 0),
        )
        conn.execute(
            """
            UPDATE streams SET
                active = ?,
                status = ?,
                http_code = ?,
                latency_ms = ?,
                last_checked_at = ?,
                last_ok_at = ?,
                fail_streak = ?,
                browser_ok = ?
            WHERE url = ?
            """,
            (
                1 if stream.get("active", 1) else 0,
                stream.get("status") or "unknown",
                stream.get("http_code"),
                stream.get("latency_ms"),
                stream.get("last_checked_at"),
                stream.get("last_ok_at"),
                int(stream.get("fail_streak") or 0),
                stream.get("browser_ok"),
                url,
            ),
        )
        restored += 1
    conn.commit()
    log(f"state: restored {restored} streams from snapshot")


def refresh_source(conn: sqlite3.Connection, source: dict) -> None:
    log(f"source: {source['label']} ...")
    ok = 0
    note = ""
    try:
        status, _headers, data, _final = fetch(
            source["url"], timeout=25, max_bytes=5_000_000, use_range=False
        )
        if status not in (200, 206):
            raise ValueError(f"HTTP {status}")
        text = data.decode("utf-8-sig", "ignore")
        if "#EXTM3U" not in text:
            raise ValueError("response is not an M3U playlist")
        entries = parse_m3u(text, source["country"], source["id"])
        for entry in entries:
            upsert_channel(
                conn,
                entry["channel_id"],
                entry["name"],
                entry["country"],
                entry["category"],
                entry["logo"],
            )
            upsert_stream(
                conn,
                entry["channel_id"],
                entry["url"],
                quality=entry["quality"],
                referrer=entry["referrer"],
                source=entry["source"],
            )
        conn.commit()
        ok = 1
        note = f"{len(entries)} streams"
        log(f"source: {source['label']}: {len(entries)} streams")
    except Exception as exc:  # noqa: BLE001 - a broken source must not stop the run
        note = f"{type(exc).__name__}: {exc}"[:300]
        log(f"source: FAILED {source['label']}: {note}")
    conn.execute(
        """
        INSERT INTO sources(id, url, last_fetch_at, ok, note)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            url = excluded.url,
            last_fetch_at = excluded.last_fetch_at,
            ok = excluded.ok,
            note = excluded.note
        """,
        (source["id"], source["url"], now_iso(), ok, note),
    )
    conn.commit()


def apply_overrides(conn: sqlite3.Connection) -> None:
    if not OVERRIDES_PATH.exists():
        return
    try:
        items = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("overrides file must contain a list")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log(f"overrides: cannot read: {exc}")
        return

    count = 0
    for item in items:
        url = (item.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        channel_id = item.get("channel_id") or f"manual.{slugify(item.get('name', 'channel'))}"
        name = item.get("name") or channel_id
        country = (item.get("country") or "").upper()
        category = item.get("category") or "General"
        upsert_channel(conn, channel_id, name, country, category, item.get("logo") or "")
        # A changed manual URL replaces the old manual one for that channel.
        conn.execute(
            "UPDATE streams SET active = 0 WHERE channel_id = ? AND source = 'manual' AND url <> ?",
            (channel_id, url),
        )
        upsert_stream(
            conn,
            channel_id,
            url,
            quality=item.get("quality") or "",
            referrer=item.get("referrer") or "",
            source="manual",
            priority=int(item.get("priority") or 50),
        )
        count += 1
    conn.commit()
    log(f"overrides: applied {count} manual stream(s)")


# --------------------------------------------------------------------------- #
# health checks
# --------------------------------------------------------------------------- #

def cors_allowed(value: str | None) -> bool:
    value = (value or "").strip().rstrip("/")
    return value == "*" or value == BROWSER_ORIGIN


def browser_probe(url: str, referrer: str | None, timeout: float) -> int:
    """Return 1 when hls.js could play this stream from a browser page.

    It follows the same master -> media -> segment chain as the player and
    checks Access-Control-Allow-Origin on every response. Any network error
    means the browser would fail too, so it counts as not playable.
    """
    try:
        code, headers, data, final_url = fetch(
            url,
            referrer=referrer,
            timeout=timeout,
            max_bytes=200_000,
            use_range=False,
            origin=BROWSER_ORIGIN,
        )
        text = data.decode("utf-8", "ignore")
        if "#EXTM3U" not in text:
            return 0
        ok = cors_allowed(headers.get("Access-Control-Allow-Origin"))
        if "#EXT-X-STREAM-INF" in text:
            variants = parse_variants(text, final_url)
            if not variants:
                return 0
            media_url = variants[0][2]
            code, headers, data, final_url = fetch(
                media_url,
                referrer=referrer,
                timeout=timeout,
                max_bytes=200_000,
                use_range=False,
                origin=BROWSER_ORIGIN,
            )
            text = data.decode("utf-8", "ignore")
            if "#EXTM3U" not in text:
                return 0
            ok = ok and cors_allowed(headers.get("Access-Control-Allow-Origin"))

        segment_url = first_segment(text, final_url)
        if not segment_url:
            return 0
        seg_code, seg_headers, _seg_data, _seg_final = fetch(
            segment_url,
            referrer=referrer,
            timeout=timeout,
            origin=BROWSER_ORIGIN,
        )
        if seg_code not in (200, 206):
            return 0
        return 1 if ok and cors_allowed(seg_headers.get("Access-Control-Allow-Origin")) else 0
    except Exception:  # noqa: BLE001
        return 0


def check_stream(row: sqlite3.Row, timeout: float) -> dict:
    url = row["url"]
    referrer = row["referrer"] or None
    started = time.monotonic()

    def result(status: str, code: int | None = None, error: str = "") -> dict:
        return {
            "id": row["id"],
            "url": url,
            "status": status,
            "http_code": code,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": error[:300],
            "checked_at": now_iso(),
            "browser_ok": None,
        }

    def ok_result(code: int | None) -> dict:
        item = result("ok", code)
        item["browser_ok"] = browser_probe(url, referrer, timeout)
        return item

    try:
        code, headers, data, final_url = fetch(url, referrer=referrer, timeout=timeout)
        text = data.decode("utf-8", "ignore")

        if "#EXTM3U" in text:
            if "#EXT-X-STREAM-INF" in text:
                variants = parse_variants(text, final_url)
                if not variants:
                    return result("unstable", code, "master playlist without variants")
                _bandwidth, _height, variant_url = variants[0]
                code, headers, data, final_url = fetch(
                    variant_url, referrer=referrer, timeout=timeout
                )
                text = data.decode("utf-8", "ignore")
                if "#EXTM3U" not in text:
                    return result("unstable", code, "variant is not a playlist")

            segment_url = first_segment(text, final_url)
            if not segment_url:
                return result("unstable", code, "media playlist has no segments")
            seg_code, seg_headers, seg_data, _seg_final = fetch(
                segment_url, referrer=referrer, timeout=timeout
            )
            if seg_code in (200, 206) and is_media(seg_headers, seg_data):
                return ok_result(seg_code)
            return result("unstable", seg_code, "media segment failed")

        if is_media(headers, data):
            return ok_result(code)
        return result("unstable", code, "unknown content (not HLS/TS)")

    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in (403, 451):
            return result("geo", exc.code, f"HTTP {exc.code}")
        if exc.code in (404, 410):
            return result("dead", exc.code, f"HTTP {exc.code}")
        return result("unstable", exc.code, f"HTTP {exc.code}")
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, socket.gaierror):
            return result("dead", None, "DNS error")
        if isinstance(reason, ConnectionRefusedError):
            return result("dead", None, "connection refused")
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return result("unstable", None, "timeout")
        return result("unstable", None, f"{type(reason).__name__}: {reason}")
    except (socket.timeout, TimeoutError):
        return result("unstable", None, "timeout")
    except ssl.SSLError as exc:
        return result("unstable", None, f"TLS: {exc}")
    except Exception as exc:  # noqa: BLE001
        return result("unstable", None, f"{type(exc).__name__}: {exc}")


def apply_results(conn: sqlite3.Connection, results: list[dict]) -> None:
    for item in results:
        row = conn.execute(
            "SELECT active, fail_streak FROM streams WHERE id = ?", (item["id"],)
        ).fetchone()
        if row is None:
            continue
        active = int(row["active"] or 0)
        streak = int(row["fail_streak"] or 0)
        status = item["status"]

        last_ok = None
        if status == "ok":
            active = 1
            streak = 0
            last_ok = item["checked_at"]
        elif status == "dead":
            streak += 1
            if streak >= 3:
                active = 0
        # geo / unstable: keep the stream enabled; the failure may be
        # location-dependent or temporary.

        conn.execute(
            """
            UPDATE streams SET
                status = ?,
                http_code = ?,
                latency_ms = ?,
                last_checked_at = ?,
                last_ok_at = COALESCE(?, last_ok_at),
                fail_streak = ?,
                active = ?,
                browser_ok = ?
            WHERE id = ?
            """,
            (
                status,
                item["http_code"],
                item["latency_ms"],
                item["checked_at"],
                last_ok,
                streak,
                active,
                item.get("browser_ok"),
                item["id"],
            ),
        )
        conn.execute(
            """
            INSERT INTO checks(stream_id, checked_at, status, http_code, latency_ms, error)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                item["checked_at"],
                status,
                item["http_code"],
                item["latency_ms"],
                item["error"],
            ),
        )
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    conn.execute("DELETE FROM checks WHERE checked_at < ?", (cutoff,))
    conn.commit()


def check_all(conn: sqlite3.Connection, workers: int, timeout: float, limit: int) -> list[dict]:
    rows = conn.execute("SELECT id, url, referrer FROM streams ORDER BY id").fetchall()
    if limit:
        rows = rows[:limit]
    total = len(rows)
    log(f"check: {total} streams with {workers} workers (timeout {timeout:g}s)")
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(check_stream, row, timeout) for row in rows]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001
                log(f"check: worker crashed: {exc}")
            if index % 25 == 0 or index == total:
                log(f"check: {index}/{total}")
    return results


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #

def stream_key(stream: dict) -> tuple:
    return (
        STATUS_RANK.get(stream.get("status") or "unknown", 9),
        -(int(stream.get("priority") or 0)),
        stream.get("latency_ms") if stream.get("latency_ms") is not None else 10**9,
        -quality_rank(stream.get("quality")),
        stream.get("url") or "",
    )


def choose_stream(streams: list[dict], verified: bool = False) -> dict | None:
    if verified:
        pool = [s for s in streams if s.get("active") and s.get("status") == "ok"]
    else:
        pool = [s for s in streams if s.get("active") and s.get("status") != "dead"]
    if not pool:
        return None
    return min(pool, key=stream_key)


def export_state(conn: sqlite3.Connection) -> None:
    channels = [
        dict(row)
        for row in conn.execute(
            "SELECT id, name, country, category, logo FROM channels ORDER BY id"
        )
    ]
    streams = []
    for row in conn.execute(
        """
        SELECT channel_id, url, quality, referrer, source, priority, active, status,
               http_code, latency_ms, last_checked_at, last_ok_at, fail_streak,
               browser_ok, added_at
        FROM streams ORDER BY channel_id, url
        """
    ):
        item = dict(row)
        item["active"] = int(item.get("active") or 0)
        streams.append(item)
    state = {
        "version": 1,
        "updated_at": now_iso(),
        "channels": channels,
        "streams": streams,
    }
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    log(f"state: wrote {len(channels)} channels / {len(streams)} streams")


def load_channels_with_streams(conn: sqlite3.Connection) -> list[dict]:
    channels = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT id, name, country, category, logo FROM channels"
        )
    }
    grouped: dict[str, list[dict]] = {}
    for row in conn.execute(
        """
        SELECT id, channel_id, url, quality, referrer, source, priority, active,
               status, http_code, latency_ms, last_checked_at, last_ok_at,
               fail_streak, browser_ok
        FROM streams ORDER BY id
        """
    ):
        stream = dict(row)
        stream["active"] = int(stream.get("active") or 0)
        grouped.setdefault(stream["channel_id"], []).append(stream)
    result = []
    for channel_id, channel in channels.items():
        streams = grouped.get(channel_id, [])
        if not streams:
            continue
        channel["streams"] = streams
        result.append(channel)
    return result


def sanitize_attr(value: str) -> str:
    return (value or "").replace('"', "'").replace("\n", " ")


def build_playlist(
    channels: list[dict], country: str | None = None, verified: bool = False
) -> str:
    lines = ["#EXTM3U"]
    ordered = sorted(
        channels,
        key=lambda ch: (
            COUNTRY_ORDER.get((ch.get("country") or "").upper(), 9),
            category_fa(ch.get("category")),
            (ch.get("name") or "").lower(),
        ),
    )
    for channel in ordered:
        if country and (channel.get("country") or "").upper() != country:
            continue
        best = choose_stream(channel["streams"], verified=verified)
        if not best:
            continue
        country_code = (channel.get("country") or "").upper()
        group = f"{COUNTRY_FA.get(country_code, country_code)} • {category_fa(channel.get('category'))}"
        extinf = (
            f'#EXTINF:-1 tvg-id="{sanitize_attr(channel["id"])}" '
            f'group-title="{sanitize_attr(group)}",{channel["name"]}'
        )
        logo = channel.get("logo") or ""
        if logo:
            extinf = extinf.replace(
                ' tvg-id=', f' tvg-logo="{sanitize_attr(logo)}" tvg-id=', 1
            )
        lines.append(extinf)
        if best.get("referrer"):
            lines.append(f'#EXTVLCOPT:http-referrer={best["referrer"]}')
        lines.append(best["url"])
    return "\n".join(lines) + "\n"


def write_playlists(channels: list[dict]) -> None:
    (ROOT / "playlist.m3u").write_text(
        build_playlist(channels), encoding="utf-8"
    )
    (ROOT / "playlist-verified.m3u").write_text(
        build_playlist(channels, verified=True), encoding="utf-8"
    )
    (PLAYLISTS_DIR / "iran.m3u").write_text(
        build_playlist(channels, country="IR"), encoding="utf-8"
    )
    (PLAYLISTS_DIR / "afghanistan.m3u").write_text(
        build_playlist(channels, country="AF"), encoding="utf-8"
    )
    log("playlists: wrote playlist.m3u, playlist-verified.m3u, iran.m3u, afghanistan.m3u")


def build_status(conn: sqlite3.Connection, channels: list[dict]) -> dict:
    stream_counts = {"ok": 0, "geo": 0, "dead": 0, "unstable": 0, "unknown": 0}
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM streams GROUP BY status"):
        key = row["status"] or "unknown"
        stream_counts[key] = stream_counts.get(key, 0) + int(row["n"])

    data = []
    playable = 0
    browser_playable = 0
    for channel in channels:
        streams = channel["streams"]
        best = choose_stream(streams)
        status = best["status"] if best else "dead"
        browser_ok = bool(best.get("browser_ok")) if best else False
        if best:
            playable += 1
            if browser_ok:
                browser_playable += 1
        alternatives = sorted(streams, key=stream_key)
        data.append(
            {
                "id": channel["id"],
                "name": channel["name"],
                "country": (channel.get("country") or "").upper(),
                "country_fa": COUNTRY_FA.get(
                    (channel.get("country") or "").upper(),
                    channel.get("country") or "",
                ),
                "category": channel.get("category") or "General",
                "category_fa": category_fa(channel.get("category")),
                "logo": channel.get("logo") or "",
                "status": status,
                "status_fa": STATUS_FA.get(status, status),
                "quality": best.get("quality") if best else "",
                "latency_ms": best.get("latency_ms") if best else None,
                "url": best.get("url") if best else None,
                "last_checked_at": best.get("last_checked_at") if best else None,
                "streams_total": len(streams),
                "streams_ok": sum(1 for s in streams if s.get("status") == "ok"),
                "browser_ok": browser_ok,
                "alternatives": [
                    {
                        "url": stream["url"],
                        "status": stream.get("status") or "unknown",
                        "quality": stream.get("quality") or "",
                        "latency_ms": stream.get("latency_ms"),
                        "priority": stream.get("priority") or 0,
                    }
                    for stream in alternatives
                ],
            }
        )

    data.sort(
        key=lambda item: (
            COUNTRY_ORDER.get(item["country"], 9),
            item["category_fa"],
            item["name"].lower(),
        )
    )
    sources = [
        dict(row)
        for row in conn.execute(
            "SELECT id, url, last_fetch_at, ok, note FROM sources ORDER BY id"
        )
    ]
    return {
        "generated_at": now_iso(),
        "counts": {
            "channels": len(data),
            "playable_channels": playable,
            "browser_playable_channels": browser_playable,
            "streams": sum(stream_counts.values()),
            "ok": stream_counts.get("ok", 0),
            "geo": stream_counts.get("geo", 0),
            "dead": stream_counts.get("dead", 0),
            "unstable": stream_counts.get("unstable", 0),
            "unknown": stream_counts.get("unknown", 0),
        },
        "sources": sources,
        "channels": data,
    }


def render_dashboard(status: dict) -> None:
    if not TEMPLATE_PATH.exists():
        log("dashboard: template missing, skipped")
        return
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = json.dumps(status, ensure_ascii=False).replace("</", "<\\/")
    html = template.replace("/*__DATA__*/", payload)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    (ROOT / "status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    log("dashboard: wrote docs/index.html and status.json")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update the IPTV link database")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=0, help="check only N streams")
    parser.add_argument("--no-check", action="store_true", help="skip health checks")
    parser.add_argument("--no-sources", action="store_true", help="skip playlist refresh")
    args = parser.parse_args(argv)

    started = time.monotonic()
    conn = init_db()
    import_state(conn)

    if not args.no_sources:
        for source in SOURCES:
            refresh_source(conn, source)
    apply_overrides(conn)

    if not args.no_check:
        results = check_all(conn, args.workers, args.timeout, args.limit)
        apply_results(conn, results)
        summary: dict[str, int] = {}
        for item in results:
            summary[item["status"]] = summary.get(item["status"], 0) + 1
        log("check summary: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.items())))
    else:
        log("check: skipped (--no-check)")

    export_state(conn)
    channels = load_channels_with_streams(conn)
    write_playlists(channels)
    status = build_status(conn, channels)
    render_dashboard(status)

    counts = status["counts"]
    log(
        "done: "
        f"{counts['playable_channels']}/{counts['channels']} playable channels, "
        f"{counts['ok']} ok / {counts['geo']} geo / {counts['dead']} dead / "
        f"{counts['unstable']} unstable streams "
        f"in {time.monotonic() - started:.1f}s"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
