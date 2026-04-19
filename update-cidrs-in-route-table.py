#!/usr/bin/env python3
"""
Install policy-routing table routes for traffic that should use EGRESS_DEV (see globals).

Builds the CIDR set with ipaddress:
  (raw-list ∪ include-list) address space \\ exclude-list
  - raw-list: from get-iana-cidrs.py (RIPE RU IPv4)
  - include-list: extra CIDRs to route via this table
  - exclude-list: CIDRs to remove from that union (not installed), even if in raw/include

Optional files: if include-list or exclude-list is missing, it is treated as empty.

Incremental updates: compare planned routes to effective-list on disk (--force ignores that
file and behaves as if the previous effective set were empty). No table flush.

Environment (optional):
  DEBUG                if set to 1/true/yes/on (case-insensitive): print each
                      `ip route` command instead of running it; skips root/ip checks

Requires root on Linux (unless DEBUG). Uses `ip` from iproute2.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------

SPLIT_TUNNELING_DIR = Path("/etc/split-tunneling")

CONFIG = {
    "routing_table": "wireguard2x",
    "egress_dev": "eth0",
}

cfg = Path("/etc/split-tunneling/split-tunneling.ini")
if cfg.is_file():
    for raw in cfg.read_text(encoding="utf-8", errors="replace").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        CONFIG[key] = val

RAW_LIST = SPLIT_TUNNELING_DIR / "raw-list"
INCLUDE_LIST = SPLIT_TUNNELING_DIR / "include-list"
EXCLUDE_LIST = SPLIT_TUNNELING_DIR / "exclude-list"
EFFECTIVE_LIST = SPLIT_TUNNELING_DIR / "effective-list"

ROUTING_TABLE = CONFIG["routing_table"]
EGRESS_DEV = CONFIG["egress_dev"]


def die(msg: str, code: int = 1) -> None:
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def env_debug_enabled() -> bool:
    v = os.environ.get("DEBUG", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def load_ipv4_networks(path: Path) -> list[ipaddress.IPv4Network]:
    """One CIDR per line; optional # comments; missing file => empty."""
    if not path.is_file() or not os.access(path, os.R_OK):
        return []
    out: list[ipaddress.IPv4Network] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tok = line.split(None, 1)[0].strip()
        if not tok:
            continue
        try:
            net = ipaddress.ip_network(tok, strict=False)
        except ValueError:
            continue
        if not isinstance(net, ipaddress.IPv4Network):
            continue
        out.append(net)
    return out


def _subtract_net(
    n: ipaddress.IPv4Network, ex: ipaddress.IPv4Network
) -> list[ipaddress.IPv4Network]:
    """Parts of n that do not overlap exclusion ex (IPv4 only)."""
    if not n.overlaps(ex):
        return [n]
    if n == ex:
        return []
    if n.subnet_of(ex):
        return []
    if ex.subnet_of(n):
        try:
            return list(n.address_exclude(ex))
        except ValueError:
            return [n]
    return [n]


def apply_exclusions(
    merged: list[ipaddress.IPv4Network],
    excludes: list[ipaddress.IPv4Network],
) -> list[ipaddress.IPv4Network]:
    if not excludes:
        return merged
    ex_sorted = sorted(
        ipaddress.collapse_addresses(excludes),
        key=lambda x: (int(x.network_address), x.prefixlen),
    )
    current = list(
        ipaddress.collapse_addresses(
            sorted(merged, key=lambda x: (int(x.network_address), x.prefixlen))
        )
    )
    for ex in ex_sorted:
        nxt: list[ipaddress.IPv4Network] = []
        for n in current:
            nxt.extend(_subtract_net(n, ex))
        current = list(
            ipaddress.collapse_addresses(
                sorted(nxt, key=lambda x: (int(x.network_address), x.prefixlen))
            )
        )
    return current


def build_planned_effective_list(
    raw_nets: list[ipaddress.IPv4Network],
    inc_nets: list[ipaddress.IPv4Network],
    exc_nets: list[ipaddress.IPv4Network],
) -> list[str]:
    """raw ∪ include, collapsed, minus exclude (ipaddress reduction)."""
    combined = raw_nets + inc_nets
    if not combined:
        merged: list[ipaddress.IPv4Network] = []
    else:
        merged = list(
            ipaddress.collapse_addresses(
                sorted(combined, key=lambda n: (int(n.network_address), n.prefixlen))
            )
        )

    final = apply_exclusions(merged, exc_nets)
    return [str(x) for x in final]


def load_effective_list_file(path: Path) -> list[str]:
    if not path.is_file() or not os.access(path, os.R_OK):
        return []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        tok = line.split(None, 1)[0].strip()
        if tok:
            seen.add(tok)
    return sorted(seen)


def ip_route_add(
    cidr: str,
    *,
    table: str,
    dev: str,
    debug: bool,
) -> None:
    cmd = ["ip", "route", "add", cidr, "dev", dev, "table", table]
    if debug:
        print(" ".join(cmd))
        return
    subprocess.run(cmd, check=True)


def ip_route_delete(
    cidr: str,
    *,
    table: str,
    dev: str,
    debug: bool,
) -> None:
    cmd = ["ip", "route", "del", cidr, "dev", dev, "table", table]
    if debug:
        print(" ".join(cmd))
        return
    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Policy routes from raw/include minus exclude (ipaddress).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="ignore effective-list on disk (treat previous state as empty): add all planned routes, delete none",
    )
    args = parser.parse_args()

    debug = env_debug_enabled()

    if not debug:
        if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() != 0:
            die("error: run as root (e.g. sudo %(prog)s)")

        if not shutil.which("ip"):
            die("error: ip (iproute2) not found")

    if not RAW_LIST.is_file() or not os.access(RAW_LIST, os.R_OK):
        die(
            f"error: missing or unreadable RAW_LIST: {RAW_LIST}\n"
            "  run get-iana-cidrs.py first, or edit RAW_LIST in this script."
        )

    # 1. Read lists
    raw_nets = load_ipv4_networks(RAW_LIST)
    include_nets = load_ipv4_networks(INCLUDE_LIST)
    exclude_nets = load_ipv4_networks(EXCLUDE_LIST)

    # 2. Planned effective CIDRs: raw ∪ include − exclude (reduced)
    planned_effective_list = build_planned_effective_list(
        raw_nets, include_nets, exclude_nets
    )
    planned_set = set(planned_effective_list)

    # 3. Last run on disk vs --force (empty prior state)
    if args.force:
        currently_effective_list: list[str] = []
    else:
        currently_effective_list = load_effective_list_file(EFFECTIVE_LIST)

    current_set = set(currently_effective_list)

    # 4. Diff
    routes_to_delete = sorted(current_set - planned_set)
    routes_to_add = sorted(planned_set - current_set)

    if args.force:
        print("--force: ignoring effective-list on disk", file=sys.stderr)
    elif not currently_effective_list:
        print(f"no prior routes in {EFFECTIVE_LIST} (full add)", file=sys.stderr)
    else:
        print(f"incremental update vs {EFFECTIVE_LIST}", file=sys.stderr)

    # 5. Apply deletes then adds
    for cidr in routes_to_delete:
        ip_route_delete(cidr, table=ROUTING_TABLE, dev=EGRESS_DEV, debug=debug)
    for cidr in routes_to_add:
        ip_route_add(cidr, table=ROUTING_TABLE, dev=EGRESS_DEV, debug=debug)

    # 6. Persist what is now installed (planned set)
    EFFECTIVE_LIST.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(planned_set)
    EFFECTIVE_LIST.write_text(
        ("\n".join(ordered) + "\n") if ordered else "",
        encoding="utf-8",
    )

    print(
        f"deleted {len(routes_to_delete)} route(s), added {len(routes_to_add)} route(s) "
        f"in table {ROUTING_TABLE} (dev {EGRESS_DEV}); wrote {EFFECTIVE_LIST}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("interrupted", 130)
