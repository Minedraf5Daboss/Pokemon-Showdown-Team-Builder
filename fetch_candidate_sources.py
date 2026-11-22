"""Cache the requested SV Doubles Ubers ladder and primary Smogon set sources.

Network is explicit; normal imports and refresh_pool.py use only frozen files.
The inclusive cutoff is a union across reports, never a rounded average.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import urllib.request

from verify_teammates import usage_rows

ROOT = Path(__file__).parent / "data" / "smogon"
MONTHS = ("2026-08", "2026-09", "2026-10")
CUTOFFS = (1500, 1630)
MIN_USAGE = 0.0001  # 0.01%, expressed as a fraction.


def fetch(source):
    url, relative = source
    path = ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "showdown-builder/2.0"})
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read()
            if relative.endswith(".json"):
                json.loads(content)
            elif relative.endswith(".txt") and not usage_rows(content.decode()):
                raise ValueError(f"No usage rows: {url}")
            elif relative.endswith(".html") and b"dexSettings = " not in content:
                raise ValueError(f"Not a Smogon dex page: {url}")
            path.write_bytes(content)
            return {"url": url, "path": relative, "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content), "retrieved_at": datetime.now(timezone.utc).isoformat()}
        except (OSError, ValueError) as exc:
            error = exc
            time.sleep(attempt + 1)
    raise RuntimeError(f"Could not retrieve {url}: {error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dex", action="store_true", help="Also retrieve a dex page for every qualifying species")
    args = parser.parse_args()
    sources = []
    for month in MONTHS:
        for cutoff in CUTOFFS:
            stem = f"gen9doublesubers-{cutoff}"
            sources.extend([(f"https://www.smogon.com/stats/{month}/{stem}.txt", f"{month}/{stem}.txt"),
                            (f"https://www.smogon.com/stats/{month}/chaos/{stem}.json", f"{month}/{stem}.json")])
    manifest_path = ROOT / "candidate_sources.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    indexed = {record["path"]: record for record in existing}
    for record in existing:
        if hashlib.sha256((ROOT / record["path"]).read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"Cached source checksum mismatch: {record['path']}")
    pending = [source for source in sources if source[1] not in indexed]
    with ThreadPoolExecutor(max_workers=3) as executor:
        for record in executor.map(fetch, pending):
            indexed[record["path"]] = record
            print(record["path"], flush=True)
    manifest_path.write_text(json.dumps(list(indexed.values()), indent=2) + "\n")
    names = set()
    for month in MONTHS:
        for cutoff in CUTOFFS:
            rows = usage_rows((ROOT / month / f"gen9doublesubers-{cutoff}.txt").read_text())
            names.update(name for name, row in rows.items() if row["usage"] >= MIN_USAGE)
    print(f"{len(names)} species reach {MIN_USAGE:.2%} in at least one requested report.", flush=True)
    if args.dex:
        # Retain existing primary source snapshots; their hashes remain auditable.
        previous = json.loads((ROOT / "sets_manifest.json").read_text())
        for record in previous:
            if record["path"].startswith("sets/"):
                indexed.setdefault(record["path"], record)
        pending = [(f"https://www.smogon.com/dex/sv/pokemon/{name.lower().replace(' ', '-')}/doubles-ubers/",
                    f"sets/{name.lower()}-dex.html") for name in sorted(names)
                   if f"sets/{name.lower()}-dex.html" not in indexed]
        with ThreadPoolExecutor(max_workers=3) as executor:
            for record in executor.map(fetch, pending):
                indexed[record["path"]] = record
                manifest_path.write_text(json.dumps(list(indexed.values()), indent=2) + "\n")
                print(record["path"], flush=True)
    manifest_path.write_text(json.dumps(list(indexed.values()), indent=2) + "\n")


if __name__ == "__main__":
    main()
