#!/usr/bin/env python3
"""
Neocities stats collector.

Takes a snapshot of your Neocities site stats and appends it to data/stats.json.
Uses ONLY the Python standard library, so it runs in GitHub Actions with no pip install.

Data collected:
  - Public info (no key needed): views, hits, created_at, last_updated, tags, domain
  - Private list (needs API key): file count, total size, per-extension breakdown,
    largest files, last file update

Deltas (views/day, hits/day, etc.) are NOT stored here — the dashboard computes them
from consecutive snapshots, so the raw file stays a clean append-only log.

Env vars:
  NEOCITIES_SITENAME   required, e.g. "encrize"
  NEOCITIES_API_KEY    optional; if set, also pulls /api/list (files + size + types)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://neocities.org/api"
OUT = Path(__file__).parent / "data" / "stats.json"
USER_AGENT = "neocities-stats-dashboard/1.1 (+github actions)"


def _get(url: str, api_key: str | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_info(sitename: str, api_key: str | None) -> dict:
    # With a key, /api/info (no sitename) returns YOUR site. Public fallback uses sitename.
    if api_key:
        try:
            data = _get(f"{API}/info", api_key=api_key)
            if data.get("result") == "success":
                return data["info"]
        except urllib.error.HTTPError as e:
            print(f"[warn] authed /info failed ({e.code}), falling back to public", file=sys.stderr)
    data = _get(f"{API}/info?sitename={sitename}")
    if data.get("result") != "success":
        raise RuntimeError(f"/api/info error: {data}")
    return data["info"]


def _ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." in name and not name.startswith("."):
        return name.rsplit(".", 1)[-1].lower()
    return "(none)"


def fetch_files(api_key: str) -> dict:
    """Returns file stats + per-extension breakdown + largest files, or {} on failure."""
    try:
        data = _get(f"{API}/list", api_key=api_key)
    except urllib.error.HTTPError as e:
        print(f"[warn] /api/list failed ({e.code}); skipping file stats", file=sys.stderr)
        return {}
    if data.get("result") != "success":
        print(f"[warn] /api/list error: {data}", file=sys.stderr)
        return {}

    files = [f for f in data.get("files", []) if not f.get("is_directory")]
    total = sum(int(f.get("size", 0)) for f in files)
    updates = [f.get("updated_at") for f in files if f.get("updated_at")]

    by_ext: dict[str, dict] = {}
    for f in files:
        ext = _ext_of(f.get("path", ""))
        bucket = by_ext.setdefault(ext, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += int(f.get("size", 0))

    largest = sorted(
        ({"path": f.get("path", ""), "bytes": int(f.get("size", 0))} for f in files),
        key=lambda x: x["bytes"],
        reverse=True,
    )[:10]

    return {
        "file_count": len(files),
        "total_bytes": total,
        "last_file_update": max(updates) if updates else None,
        "files_by_ext": by_ext,
        "largest_files": largest,
    }


def main() -> int:
    sitename = os.environ.get("NEOCITIES_SITENAME", "").strip()
    api_key = os.environ.get("NEOCITIES_API_KEY", "").strip() or None
    if not sitename:
        print("ERROR: set NEOCITIES_SITENAME", file=sys.stderr)
        return 1
    if not api_key:
        print("[warn] NEOCITIES_API_KEY not set — file size / count / types will be empty", file=sys.stderr)

    info = fetch_info(sitename, api_key)
    snapshot = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sitename": info.get("sitename", sitename),
        "views": info.get("views"),
        "hits": info.get("hits"),
        "domain": info.get("domain"),
        "tags": info.get("tags", []),
        "created_at": info.get("created_at"),
        "last_updated": info.get("last_updated"),
    }
    if api_key:
        snapshot.update(fetch_files(api_key))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if OUT.exists():
        try:
            history = json.loads(OUT.read_text("utf-8"))
        except json.JSONDecodeError:
            print("[warn] stats.json was corrupt; starting fresh", file=sys.stderr)

    # De-dupe: skip if the same calendar day already has an identical views/hits reading.
    today = snapshot["ts"][:10]
    if history and history[-1]["ts"][:10] == today \
            and history[-1].get("views") == snapshot["views"] \
            and history[-1].get("hits") == snapshot["hits"]:
        history[-1] = snapshot  # refresh timestamp/details, don't grow the log
    else:
        history.append(snapshot)

    OUT.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"OK: {len(history)} snapshots -> {OUT}")
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
