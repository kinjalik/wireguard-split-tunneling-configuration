#!/usr/bin/env python3
"""
Reporting for CIDR updates via Telegram.

Compare rich-list.csv (current) with rich-list.csv.old (previous snapshot) and send a
Telegram message listing CIDRs that appeared or disappeared, with ASN and organization from
the respective CSV row.

Required env:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID

rich-list.csv format (from get-iana-cidrs.py):
  CIDR,ASN,organization

Data under SPLIT_TUNNELING_DIR (hardcoded below).

Requires: requests (pip install requests)
"""

from __future__ import annotations

import csv
import html
import os
import sys
from pathlib import Path

SPLIT_TUNNELING_DIR = Path("/etc/split-tunneling")

try:
    import requests
except ImportError as e:
    print("error: install requests (pip install requests)", file=sys.stderr)
    raise SystemExit(1) from e


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def load_rich_map(path: Path) -> dict[str, tuple[str, str]]:
    """Map CIDR -> (ASN, organization). Last row wins if duplicates."""
    m: dict[str, tuple[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if not row:
                continue
            cidr = row[0].strip()
            if not cidr or cidr.upper() == "CIDR":
                continue
            asn = row[1].strip() if len(row) > 1 else ""
            org = row[2].strip() if len(row) > 2 else ""
            m[cidr] = (asn, org)
    return m


def diff_cidrs(
    old: dict[str, tuple[str, str]], new: dict[str, tuple[str, str]]
) -> tuple[list[str], list[str]]:
    old_keys, new_keys = set(old), set(new)
    appeared = sorted(new_keys - old_keys)
    disappeared = sorted(old_keys - new_keys)
    return appeared, disappeared


def _one_line(text: str) -> str:
    return " ".join(text.replace("\r", " ").split())


def format_row_html(sign: str, cidr: str, asn: str, org: str) -> str:
    a = _one_line(asn) if asn else "unknown"
    o = _one_line(org) if org else "unknown"
    return (
        f"{sign} <code>{html.escape(cidr)}</code> - "
        f"{html.escape(a)} - {html.escape(o)}"
    )


def build_message_html(
    appeared: list[str],
    disappeared: list[str],
    old_map: dict[str, tuple[str, str]],
    new_map: dict[str, tuple[str, str]],
) -> str:
    lines: list[str] = [
        "<b>Russian CIDRs updated</b>",
        "",
    ]
    for c in appeared:
        asn, org = new_map.get(c, ("", ""))
        lines.append(format_row_html("+", c, asn, org))
    lines.append('\r')
    for c in disappeared:
        asn, org = old_map.get(c, ("", ""))
        lines.append(format_row_html("-", c, asn, org))
    return "\n".join(lines)


def chunk_text(text: str, max_len: int = 3900) -> list[str]:
    """Split on newlines; stay under Telegram's ~4096 byte limit."""
    chunks: list[str] = []
    buf: list[str] = []
    cur = 0
    for line in text.split("\n"):
        seg = line + "\n"
        if len(seg) > max_len:
            if buf:
                chunks.append("".join(buf).rstrip("\n"))
                buf = []
                cur = 0
            for i in range(0, len(line), max_len):
                chunks.append(line[i : i + max_len])
            continue
        if cur + len(seg) > max_len and buf:
            chunks.append("".join(buf).rstrip("\n"))
            buf = [seg]
            cur = len(seg)
        else:
            buf.append(seg)
            cur += len(seg)
    if buf:
        chunks.append("".join(buf).rstrip("\n"))
    return chunks


def telegram_send(token: str, chat_id: str, text: str) -> None:
    """Send with HTML parse mode so bold and inline code render in Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in chunk_text(text):
        if not chunk:
            continue
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=120,
        )
        try:
            data = r.json()
        except ValueError:
            die(f"telegram: invalid JSON response: {r.text[:300]}", 1)
        if not data.get("ok"):
            die(f"telegram API error: {data.get('description', r.text[:300])}", 1)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        die("error: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")

    rich_new = SPLIT_TUNNELING_DIR / "rich-list.csv"
    rich_old = SPLIT_TUNNELING_DIR / "rich-list.csv.old"

    if not rich_old.is_file():
        print(
            f"notice: no {rich_old}; nothing to compare (run get-iana-cidrs.py at least twice).",
            file=sys.stderr,
        )
        raise SystemExit(0)

    if not rich_new.is_file():
        die(f"error: missing {rich_new}")

    old_map = load_rich_map(rich_old)
    new_map = load_rich_map(rich_new)
    appeared, disappeared = diff_cidrs(old_map, new_map)

    if not appeared and not disappeared:
        print(
            "no CIDR changes vs rich-list.csv.old; not sending Telegram",
            file=sys.stderr,
        )
        raise SystemExit(0)

    msg = build_message_html(appeared, disappeared, old_map, new_map)
    telegram_send(token, chat_id, msg)
    print(f"telegram: sent update ({len(msg)} bytes)", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("interrupted", 130)
