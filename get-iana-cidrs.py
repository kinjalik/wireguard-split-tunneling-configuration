#!/usr/bin/env python3
"""
Download Russian IPv4 subnets with corresponding ASN and organization from RIPE and IPinfo.

1) Fetch Russian IPv4 prefixes from RIPE Stat (country-resource-list).
2) Write them in API order to raw-list (no sorting or dedup) for update-cidrs-in-route-table.py.
3) For each raw prefix, resolve IPinfo Lite via the batch endpoint and write rich-list.csv
   (CIDR, ASN, organization). If rich-list.csv already exists, it is copied to rich-list.csv.old
   before being replaced (for diff-and-report-tg.py).

Env:
  IPINFO_TOKEN           required

Data directory is SPLIT_TUNNELING_DIR (hardcoded below).
"""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

RIPE_URL = (
    "https://stat.ripe.net/data/country-resource-list/data.json"
    "?v4_format=prefix&resource=ru"
)

IPINFO_BATCH_URL = "https://api.ipinfo.io/batch/lite"

IPINFO_BATCH_SIZE = 500

SPLIT_TUNNELING_DIR = Path("/etc/split-tunneling")


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def fetch_json(url: str, timeout: int = 120) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "get-iana-cidrs/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_ipv4_prefixes(payload: Any) -> list[str]:
    try:
        items = payload["data"]["resources"]["ipv4"]
    except (KeyError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for x in items:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def cidr_to_lookup_ip(cidr: str) -> str | None:
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return str(net.network_address)
    except ValueError:
        return None


def asn_org_from_lite_obj(obj: Any) -> tuple[str, str]:
    if not isinstance(obj, dict):
        return "", "unexpected batch entry"
    asn = obj.get("asn") or ""
    if not isinstance(asn, str):
        asn = str(asn)
    org = obj.get("as_name") or obj.get("org") or ""
    if not isinstance(org, str):
        org = str(org)
    return asn, org


def map_batch_lite_response(payload: Any, ips: list[str]) -> dict[str, Any]:
    """Return mapping lookup_ip -> lite JSON object."""
    if isinstance(payload, list):
        if len(payload) != len(ips):
            raise ValueError(
                f"batch response list length {len(payload)} != request {len(ips)}"
            )
        return {ip: item for ip, item in zip(ips, payload)}

    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for ip in ips:
            if ip in payload:
                out[ip] = payload[ip]
                continue
            found: Any = None
            for key, val in payload.items():
                if not isinstance(key, str):
                    continue
                if key == ip or key.endswith("/" + ip) or key.endswith(ip):
                    found = val
                    break
            out[ip] = found if isinstance(found, dict) else {}
        return out

    raise ValueError(f"unexpected batch response type: {type(payload).__name__}")


def ipinfo_batch_lite_post(
    batch_url: str, token: str, ips: list[str], timeout: int = 120
) -> dict[str, Any]:
    if not ips:
        return {}
    q = urllib.parse.urlencode({"token": token})
    url = f"{batch_url.rstrip('/')}?{q}"
    body = json.dumps(ips).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "User-Agent": "get-iana-cidrs/1.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except OSError:
            detail = ""
        raise RuntimeError(f"ipinfo batch HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ipinfo batch error: {e.reason}") from e
    except TimeoutError as e:
        raise RuntimeError("ipinfo batch timeout") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("invalid JSON from ipinfo batch") from e

    return map_batch_lite_response(payload, ips)


def enrich_prefixes_ipinfo(
    prefixes: list[str],
    batch_url: str,
    token: str,
    batch_size: int,
) -> dict[str, tuple[str, str]]:
    """cidr -> (asn, organization)"""
    out: dict[str, tuple[str, str]] = {}
    valid: list[tuple[str, str]] = []
    for cidr in prefixes:
        ip = cidr_to_lookup_ip(cidr)
        if ip is None:
            out[cidr] = ("", "invalid CIDR")
        else:
            valid.append((cidr, ip))

    total_batches = (len(valid) + batch_size - 1) // batch_size if valid else 0
    for bi, start in enumerate(range(0, len(valid), batch_size), start=1):
        chunk = valid[start : start + batch_size]
        ips = [ip for _, ip in chunk]
        cidrs = [cidr for cidr, _ in chunk]
        try:
            by_ip = ipinfo_batch_lite_post(batch_url, token, ips)
        except RuntimeError as e:
            for cidr in cidrs:
                out[cidr] = ("", str(e))
        else:
            for cidr, ip in zip(cidrs, ips):
                obj = by_ip.get(ip)
                if obj is None:
                    out[cidr] = ("", "missing in batch response")
                else:
                    out[cidr] = asn_org_from_lite_obj(obj)
        print(f"ipinfo batch: {bi}/{total_batches} ({len(ips)} IPs)", file=sys.stderr)

    return out


def write_rich_csv(path: Path, prefixes: list[str], enriched: dict[str, tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["CIDR", "ASN", "organization"])
        for cidr in prefixes:
            asn, org = enriched.get(cidr, ("", "not resolved"))
            w.writerow([cidr, asn, org])


def main() -> None:
    token = os.environ.get("IPINFO_TOKEN", "").strip()
    if not token:
        die("error: set IPINFO_TOKEN")

    raw_path = SPLIT_TUNNELING_DIR / "raw-list"
    rich_path = SPLIT_TUNNELING_DIR / "rich-list.csv"
    rich_old_path = SPLIT_TUNNELING_DIR / "rich-list.csv.old"

    batch_size = max(1, min(IPINFO_BATCH_SIZE, 1000))

    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0:
        die("error: run as root (writes under /etc by default)")

    print("fetching RIPE country-resource-list …", file=sys.stderr)
    payload = fetch_json(RIPE_URL)
    prefixes = extract_ipv4_prefixes(payload)

    raw_text = "\n".join(prefixes) + ("\n" if prefixes else "")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_text, encoding="utf-8")
    print(f"wrote {len(prefixes)} raw line(s) to {raw_path}", file=sys.stderr)

    print(
        f"calling IPinfo batch/lite ({batch_size} IPs/request) …",
        file=sys.stderr,
    )
    enriched = enrich_prefixes_ipinfo(prefixes, IPINFO_BATCH_URL, token, batch_size)
    if rich_path.exists():
        shutil.copy2(rich_path, rich_old_path)
        os.chmod(rich_old_path, 0o644)
    write_rich_csv(rich_path, prefixes, enriched)
    print(f"wrote {rich_path}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("interrupted", 130)
