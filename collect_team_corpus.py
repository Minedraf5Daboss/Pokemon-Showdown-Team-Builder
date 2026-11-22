"""Download public, attributed team data; never synthesize teams to fill a quota.

Run explicitly (network access): python collect_team_corpus.py
Then: node normalize_team_corpus.cjs; python build_team_corpus.py
Cached files are reused unless --refresh is supplied. Every response is hashed.
Forum discovery is reviewed in sources.json; this does not evade anti-bot pages.
"""

import argparse
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent / "data" / "team_corpus"


def download(url: str, target: Path, refresh: bool = False) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = target.with_suffix(target.suffix + ".meta.json")
    if target.exists() and metadata_path.exists() and not refresh:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata["sha256"] != hashlib.sha256(target.read_bytes()).hexdigest():
            raise ValueError(f"Cached file checksum mismatch: {target}")
        return metadata
    request = urllib.request.Request(url, headers={"User-Agent": "showdown-builder-team-research/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()
        # Reject challenge/error HTML even when the server returns HTTP 200.
        json.loads(content)
        metadata = {"url": url, "resolved_url": response.url,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": hashlib.sha256(content).hexdigest()}
    target.write_bytes(content)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args(argv)
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    manifest, failures = [], []
    for source in sources["sources"]:
        filename = f"raw/{source['id']}.json"
        try:
            metadata = download(source["data_url"], ROOT / filename, args.refresh)
            manifest.append({**source, **metadata, "file": filename})
            print(f"Downloaded/cached {source['id']}", flush=True)
        except (OSError, ValueError) as error:
            failures.append({"id": source["id"], "url": source["data_url"], "error": str(error)})
        # PokePaste asks clients to throttle; cached reads do not contact it.
        time.sleep(0.35)
    report = {"schema_version": 1, "sources": manifest, "failures": failures}
    (ROOT / "download_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(manifest)} sources ready; {len(failures)} failures recorded.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
