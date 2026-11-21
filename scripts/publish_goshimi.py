#!/usr/bin/env python3
"""Publish the IPTV Hub catalog into the Goshimi (Aseman TV) app fields.

The app stores its catalog in `apps.extra` as five JSON arrays. The admin API
now exposes:

  action=fields_get   -> { fields: { ... } }
  action=fields_set   -> merge the allowed catalog fields

This script only updates the TV fields, preserving the app's curated metadata
(titles, logos, categories, radio lists, health-probe settings) and adding our
fresh source URLs / clean egress links to the matched channels.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = os.environ.get(
    "GOSHIMI_API_URL", "https://apps.goshimi.ir/admin/api.php"
).strip()
API_KEY = os.environ.get("GOSHIMI_API_KEY", "").strip()
STATUS_PATH = Path(os.environ.get("GOSHIMI_STATUS_PATH", "status.json"))
TIMEOUT = float(os.environ.get("GOSHIMI_TIMEOUT", "30"))
TV_FIELDS = ("tvs_afghanistan", "tvs_iran_internal", "tvs_iran_satellite")
MAX_SOURCES = 8

# Goshimi catalog item id -> IPTV Hub channel id. Items missing from our
# catalogue (or dead) are left untouched, keeping the app's existing sources.
ITEM_CHANNEL = {
    "af_tolo": "ToloTV.af",
    "af_ariana": "ArianaTV.af",
    "af_shamshad": "ShamshadTV.af",
    "af_khurshid": "KhurshidTV.af",
    "af_lemar": "LemarTV.af",
    "af_rta": "RTA.af",
    "af_tolonews": "TOLOnews.af",
    "af_kayhan": "KayhanTV.af",
    "af_tamadon": "TamadonTV.af",
    "af_bahar": "BaharTV.af",
    "af_eslah": "EslahTV.af",
    "af_amc": "AMC.af",
    "af_dunya": "DunyaNawTV.af",
    "af_shams": "ShamsTV.af",
    "af_rtaedu": "RTAEducation.af",
    "ir_1": "IRIB1.ir",
    "ir_2": "IRIB2.ir",
    "ir_3": "IRIB3.ir",
    "ir_4": "IRIB4.ir",
    "ir_ifilm": "iFilmPersian.ir",
    "ir_nasim": "Nasim.ir",
    "ir_varzesh": "VarzeshTV.ir",
    "ir_pooya": "PooyaTV.ir",
    "ir_presstv": "PressTV.ir",
    "ir_hispan": "HispanTV.ir",
    "ir_ifilm2": "iFilm2.ir",
    "sat_manoto": "Manoto.uk",
    "sat_gem": "GEMTV.tr",
    "sat_iranintl": "IranInternational.uk",
    "sat_pmc": None,
    "sat_bbc": None,
    "sat_voa": None,
}


def log(message: str) -> None:
    print(message, flush=True)


def api_call(action: str, fields: dict | None = None) -> dict:
    body: dict = {"action": action}
    if fields is not None:
        body["fields"] = fields
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8", "ignore"))
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "goshimi api error"))
    return payload.get("data") or {}


def source(url: str, label: str, referrer: str | None = None) -> dict:
    item = {"url": url, "label": label}
    if referrer:
        item["headers"] = {"Referer": referrer}
    return item


def is_private_source(channel: dict) -> bool:
    try:
        host = (urllib.parse.urlparse(channel.get("url") or "").hostname or "").lower()
    except ValueError:
        return False
    return host.endswith("onetv.app") or host.endswith("hls2.xyz")


def playable(channel: dict) -> bool:
    status = channel.get("status")
    if status == "dead":
        return False
    if status == "geo":
        reach = channel.get("reach") or {}
        if reach.get("ir") or reach.get("intl"):
            return True
        # OneTV / Aparatchii block datacentres but may work on the user's
        # own network/app. Keep the direct source for the native app.
        return is_private_source(channel)
    return status in ("ok", "unstable")


def channel_sources(channel: dict, group: str) -> list[dict]:
    links = channel.get("links") or {}
    direct = channel.get("url") or links.get("direct")
    referrer = channel.get("referrer")
    if is_private_source(channel):
        return [source(direct, "منبع خصوصی (OneTV)")] if direct else []
    candidates: list[dict] = []

    if group == "iranInternalTv":
        if links.get("ir"):
            candidates.append(source(links["ir"], "مسیر ایران (پروکسی)"))
        if direct:
            candidates.append(source(direct, "منبع اصلی", referrer))
        if links.get("intl"):
            candidates.append(source(links["intl"], "مسیر خارج (پروکسی)"))
    elif group == "iranSatelliteTv":
        if links.get("intl"):
            candidates.append(source(links["intl"], "مسیر خارج (پروکسی)"))
        if direct:
            candidates.append(source(direct, "منبع اصلی", referrer))
        if links.get("ir"):
            candidates.append(source(links["ir"], "مسیر ایران (پروکسی)"))
    else:
        if direct:
            candidates.append(source(direct, "منبع اصلی", referrer))
        if links.get("intl"):
            candidates.append(source(links["intl"], "مسیر خارج (پروکسی)"))
        if links.get("ir"):
            candidates.append(source(links["ir"], "مسیر ایران (پروکسی)"))
    return candidates


def merge_item(item: dict, channel: dict) -> bool:
    if not playable(channel):
        return False
    group = str(item.get("group") or "")
    candidates = channel_sources(channel, group)
    if not candidates:
        return False

    existing = item.get("sources")
    if not isinstance(existing, list):
        existing = []
    existing_by_url = {
        str(src.get("url")): src
        for src in existing
        if isinstance(src, dict) and src.get("url")
    }

    merged: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        url = candidate.get("url")
        if not url or url in seen:
            continue
        # If the app already has this URL, keep its curated label/headers.
        merged.append(existing_by_url.get(url, candidate))
        seen.add(url)
    for src in existing:
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "")
        if not url or url in seen:
            continue
        merged.append(src)
        seen.add(url)
    merged = merged[:MAX_SOURCES]

    if merged == existing:
        return False
    item["sources"] = merged
    if not item.get("tvgId") and channel.get("id"):
        item["tvgId"] = channel["id"]
    return True


def main() -> int:
    if not API_KEY:
        log("goshimi: GOSHIMI_API_KEY not set; skipped")
        return 0
    try:
        status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"goshimi: cannot read {STATUS_PATH}: {exc}")
        return 1

    by_id = {str(channel.get("id")): channel for channel in status.get("channels", [])}
    current = api_call("fields_get")
    fields = current.get("fields") or {}
    if not isinstance(fields, dict):
        log("goshimi: fields_get returned no fields")
        return 1

    changed_fields: dict[str, list] = {}
    matched = updated = 0
    for field in TV_FIELDS:
        items = fields.get(field)
        if not isinstance(items, list):
            continue
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            channel_id = ITEM_CHANNEL.get(str(item.get("id")))
            if not channel_id:
                channel_id = str(item.get("tvgId") or "")
            channel = by_id.get(channel_id)
            if not channel:
                continue
            matched += 1
            if merge_item(item, channel):
                updated += 1
                changed = True
        if changed:
            changed_fields[field] = items

    log(
        f"goshimi: matched={matched}, updated={updated}, "
        f"changed_fields={','.join(changed_fields) or '-'}"
    )
    if not changed_fields:
        log("goshimi: no catalog changes")
        return 0

    try:
        old_version = int(fields.get("catalog_version") or 0)
    except (TypeError, ValueError):
        old_version = 0
    changed_fields["catalog_version"] = old_version + 1
    changed_fields["catalog_updated_at"] = dt.datetime.now(
        dt.timezone.utc
    ).isoformat(timespec="seconds")

    result = api_call("fields_set", changed_fields)
    log(
        "goshimi: published catalog_version="
        f"{changed_fields['catalog_version']} "
        f"updated={','.join(result.get('updated') or [])}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (urllib.error.URLError, RuntimeError, OSError) as exc:
        log(f"goshimi: publish failed: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
