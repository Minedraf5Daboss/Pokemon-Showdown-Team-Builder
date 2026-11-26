"""Stage or activate a monthly Gen 9 Doubles Ubers candidate catalog."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import partial
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import urlsplit
from uuid import uuid4

from candidate_evidence import verify_sets
from catalog_paths import ACTIVE_CATALOG, CATALOG_DATA, active_catalog_dir, catalog_dir
from fetch_candidate_sources import CUTOFFS, fetch
from recommend_sets import recommend, write_json
from refresh_pool import refresh

ROOT = Path(__file__).parent
FORMAT = "gen9doublesubers"
REPORT_URL = re.compile(
    rf"/stats/(\d{{4}}-\d{{2}})(?:/?|/(?:{FORMAT}-\d+\.txt|"
    rf"chaos/{FORMAT}-\d+\.json|moveset/{FORMAT}-\d+\.txt))"
)


def monthly_urls(urls):
    """Accept a month directory or one of its Doubles Ubers reports."""
    supplied = {}
    for url in urls:
        parts = urlsplit(url)
        match = REPORT_URL.fullmatch(parts.path)
        if (parts.scheme != "https" or parts.netloc not in {"www.smogon.com", "smogon.com"}
                or parts.query or parts.fragment or not match):
            raise ValueError(f"Expected a Smogon monthly stats or Doubles Ubers report URL: {url}")
        month = date.fromisoformat(f"{match[1]}-01")
        supplied[month] = f"https://{parts.netloc}/stats/{match[1]}/"
    month = max(supplied)
    base = supplied[month].rsplit("/", 2)[0]
    window = {}
    for _ in range(3):
        key = month.isoformat()[:7]
        window[key] = supplied.pop(month, f"{base}/{key}/")
        if len(window) < 3:
            month = (month - timedelta(days=1)).replace(day=1)
    if supplied:
        raise ValueError("All source URLs must fall within the latest three-month window")
    return dict(sorted(window.items()))


def monthly_reports(month, url):
    for cutoff in (0, *CUTOFFS):
        stem = f"{FORMAT}-{cutoff}"
        yield url + f"{stem}.txt", f"{month}/{stem}.txt"
        yield url + f"chaos/{stem}.json", f"{month}/{stem}.json"
        if cutoff == 0:
            yield url + f"moveset/{stem}.txt", f"{month}/moveset-{stem}.txt"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cached_reports(root):
    cache = {}
    for data in dict.fromkeys((root, active_catalog_dir(root))):
        records = read_json(data / "candidate_sources.json")
        for month in read_json(data / "download_manifest.json"):
            for source in month["sources"]:
                prefix = "moveset-" if "/moveset/" in source["url"] else ""
                name = prefix + source["url"].rsplit("/", 1)[1]
                records.append({**source, "path": f"{month['month']}/{name}"})
        for source in records:
            cache[source["url"], source["path"]] = data, source
    return cache


def copy_source(source_root, destination, record):
    source = source_root / record["path"]
    target = destination / record["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if sha256(target) != record["sha256"]:
        raise ValueError(f"Cached source checksum mismatch: {source}")


def collect_reports(root, destination, months, *, cached_only):
    cache, records, pending = cached_reports(root), {}, []
    sources = [source for month, url in months.items() for source in monthly_reports(month, url)]
    for source in sources:
        if source in cache:
            data, record = cache[source]
            copy_source(data, destination, record)
            records[source[1]] = record
        else:
            pending.append(source)
    if pending and cached_only:
        raise ValueError("Missing cached reports: " + ", ".join(url for url, _ in pending))
    print(f"Reusing {len(records)} checked reports and downloading {len(pending)} reports.", flush=True)
    with ThreadPoolExecutor(max_workers=3) as executor:
        for record in executor.map(partial(fetch, root=destination), pending):
            records[record["path"]] = record

    all_rating = []
    for month in months:
        paths = (f"{month}/{name}" for name in (f"{FORMAT}-0.json", f"{FORMAT}-0.txt", f"moveset-{FORMAT}-0.txt"))
        all_rating.append({"month": month, "sources": [records[path] for path in paths]})
    write_json(destination / "download_manifest.json", all_rating)
    return [records[path] for _, path in sources if not path.endswith(("-0.json", "-0.txt"))]


def copy_reviewed_inputs(root, destination, records):
    for filename in ("legacy_sets.json", "previous_recommendations.json", "set_blueprints.tsv"):
        shutil.copyfile(root / filename, destination / filename)
    for filename in ("sets_manifest.json", "candidate_sources.json"):
        dex_sources = [source for source in read_json(root / filename) if source["path"].startswith("sets/")]
        for source in dex_sources:
            copy_source(root, destination, source)
        if filename == "sets_manifest.json":
            write_json(destination / filename, dex_sources)
        else:
            write_json(destination / filename, records + dex_sources)


def stage(root, months, *, cached_only=False):
    if not shutil.which("node"):
        raise ValueError("Install Node.js to regenerate and validate the catalog")
    if not (ROOT / ".tools/pokemon-showdown/package/dist/sim/team-validator.js").is_file():
        raise ValueError("Run python fetch_validator.py once to install the pinned Showdown validator")
    staging = root / "staged"
    staging.mkdir(parents=True, exist_ok=True)
    snapshot = f"{max(months)}-{uuid4().hex[:8]}"
    destination = catalog_dir(root, snapshot)
    with TemporaryDirectory(prefix=".building-", dir=staging) as temporary:
        # Keep the temporary parent in place when the finished snapshot is moved.
        data = Path(temporary) / snapshot
        data.mkdir()
        records = collect_reports(root, data, months, cached_only=cached_only)
        copy_reviewed_inputs(root, data, records)
        subprocess.run(["node", str(ROOT / "export_battle_dex.cjs"), str(data / "battle_dex.json")], check=True)
        print(f"Building the pool for {', '.join(months)}.", flush=True)
        recommend(data, list(months))
        evidence = verify_sets(data, ROOT)
        write_json(data / "set_evidence.json", evidence)
        del evidence
        refresh(data)

        from export_sets import export_pool
        from models import load_pool

        pool = load_pool(data / "candidate_pool.json")
        if not pool:
            raise ValueError("The refreshed catalog has no candidates")
        export_pool(data, pool)
        subprocess.run(["node", str(ROOT / "verify_showdown.cjs"), str(data)], check=True)
        write_json(data / "stage.json", {
            "snapshot": snapshot, "months": list(months), "species": len(pool),
            "sets": sum(len(candidate.pokemon_sets) for candidate in pool),
            "candidate_pool_sha256": sha256(data / "candidate_pool.json"),
        })
        data.rename(destination)
    return snapshot, destination


def activate(root, snapshot):
    data = catalog_dir(root, snapshot)
    pool = data / "candidate_pool.json"
    if snapshot == "original":
        if not pool.is_file():
            raise ValueError("The original candidate pool is missing")
    else:
        metadata = read_json(data / "stage.json")
        if metadata.get("snapshot") != snapshot or metadata.get("candidate_pool_sha256") != sha256(pool):
            raise ValueError(f"Candidate pool does not match the validated snapshot: {snapshot}")
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as marker:
        temporary = Path(marker.name)
        marker.write(snapshot + "\n")
    try:
        temporary.replace(root / ACTIVE_CATALOG)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage", help="Build and validate a separate snapshot")
    staging.add_argument("urls", nargs="+", metavar="SMOGON_URL")
    staging.add_argument("--cached-only", action="store_true", help="Require every report to be available locally")
    activation = commands.add_parser("activate", help="Select a validated snapshot for new builder processes")
    activation.add_argument("snapshot", help="Staged snapshot ID or 'original'")
    args = parser.parse_args(argv)
    try:
        if args.command == "stage":
            snapshot, directory = stage(CATALOG_DATA, monthly_urls(args.urls), cached_only=args.cached_only)
            print(f"Staged catalog in {directory}")
            print(f"To use it, run: python stage_pool.py activate {snapshot}")
        else:
            activate(CATALOG_DATA, args.snapshot)
            print(f"Activated {args.snapshot}. Restart any running builder to load it.")
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Catalog refresh failed: {exc}\n")


if __name__ == "__main__":
    main()
