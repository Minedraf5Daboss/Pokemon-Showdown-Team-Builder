"""Verify cached Smogon reports and audit directional pool relationships.

Run from this directory with: .venv/Scripts/python.exe verify_teammates.py
This writes an audit report; it never edits models.py.
"""

import hashlib
import json
import math
from pathlib import Path
import re

from catalog_paths import active_catalog_dir


def require(condition, message):
    if not condition:
        raise ValueError(message)


def usage_rows(text):
    rows = {}
    for line in text.splitlines():
        columns = [column.strip() for column in line.split("|")]
        if len(columns) == 9 and columns[1].isdigit():
            rows[columns[2]] = {
                "usage": float(columns[3].rstrip("%")) / 100,
                "raw": int(columns[4]),
            }
    return rows


def audit(root, names=None, *, expected_months=None):
    manifests = json.loads((root / "download_manifest.json").read_text(encoding="utf-8"))
    if names is None:
        recommendations = json.loads((root / "set_recommendations.json").read_text(encoding="utf-8"))
        if expected_months is None:
            expected_months = recommendations["months"]
        names = list(recommendations["species"])
    manifest_months = [item["month"] for item in manifests]
    require(manifest_months and manifest_months == sorted(set(manifest_months)),
            "Cutoff-0 source months must be unique and chronological")
    if expected_months is not None:
        require(manifest_months == list(expected_months), "Cutoff-0 sources do not match the recommendation months")
    months = []
    text_checks = 0
    for manifest in manifests:
        month = manifest["month"]
        folder = root / month
        checked = set()
        for source in manifest["sources"]:
            filename = source.get("path")
            if filename is None:
                filename = source["url"].rsplit("/", 1)[1]
                if "/moveset/" in source["url"]:
                    filename = "moveset-" + filename
            else:
                filename = Path(filename).name
            require(filename in {"gen9doublesubers-0.json", "gen9doublesubers-0.txt",
                                 "moveset-gen9doublesubers-0.txt"} and filename not in checked,
                    f"Unexpected cutoff-0 source: {month}/{filename}")
            content = (folder / filename).read_bytes()
            require(hashlib.sha256(content).hexdigest() == source["sha256"],
                    f"Source checksum mismatch: {month}/{filename}")
            checked.add(filename)
        require(len(checked) == 3, f"Incomplete cutoff-0 sources: {month}")
        document = json.loads((folder / "gen9doublesubers-0.json").read_text())
        info = document["info"]
        require(info["metagame"] == "gen9doublesubers" and info["cutoff"] == 0
                and info["team type"] is None, f"Wrong ladder/cutoff: {month}")
        table = (folder / "gen9doublesubers-0.txt").read_text()
        total = re.search(r"Total battles: (\d+)", table)
        require(total and int(total[1]) > 0 and int(total[1]) == info["number of battles"],
                f"Missing or inconsistent battle totals: {month}")
        battles = int(total[1])
        rows = usage_rows(table)
        data = document["data"]
        moveset = (folder / "moveset-gen9doublesubers-0.txt").read_text()
        for name in names:
            if name not in rows:
                require(name not in data, f"Detail without usage row: {name}")
                continue
            require(abs(rows[name]["raw"] / (2 * battles) - rows[name]["usage"])
                    <= 5.1e-8, f"Raw usage mismatch: {month}/{name}")
            if name not in data:
                continue
            entry = data[name]
            require(math.isclose(rows[name]["usage"], entry["usage"], abs_tol=1e-12),
                    f"JSON/text usage mismatch: {month}/{name}")
            count = sum(entry["Abilities"].values())
            require(count == entry["Raw count"] and count >= rows[name]["raw"],
                    f"Unexpected unweighted moveset count: {month}/{name}")
            pattern = (r"(?m)^\| " + re.escape(name) +
                       r"\s+\|\n\+-+\+\n\| Raw count: (\d+)\s+\|")
            match = re.search(pattern, moveset)
            require(match is not None and int(match[1]) == entry["Raw count"],
                    f"Moveset raw count mismatch: {month}/{name}")
            block = moveset[match.end():].split("| Checks and Counters", 1)[0]
            teammate_section = re.search(r"\| Teammates\s+\|\n(.*?)\+-+\+",
                                         block, re.S)
            require(teammate_section is not None, f"Missing teammate section: {name}")
            for teammate, percentage in re.findall(
                    r"\| (.*?) (\d+\.\d+)%\s+\|", teammate_section[1]):
                expected = 100 * entry["Teammates"][teammate] / count
                require(abs(float(percentage) - expected) <= 0.00051,
                        f"Published teammate percentage mismatch: {month}/{name}/{teammate}")
                text_checks += 1
        months.append({"month": month, "battles": battles, "rows": rows, "data": data})

    pairs = {}
    for candidate in names:
        pairs[candidate] = {}
        for teammate in names:
            if candidate == teammate:
                continue
            observations = []
            for month in months:
                a = month["data"].get(candidate)
                b = month["data"].get(teammate)
                candidate_count = month["rows"].get(candidate, {}).get("raw")
                teammate_count = month["rows"].get(teammate, {}).get("raw")
                pair_count = None
                if candidate_count is not None and teammate_count is not None:
                    if a is not None:
                        pair_count = a["Teammates"].get(teammate, 0)
                    if b is not None:
                        reverse = b["Teammates"].get(candidate, 0)
                        require(pair_count is None or pair_count == reverse,
                                f"Asymmetric raw pair counts: {month['month']}/{candidate}/{teammate}")
                        pair_count = reverse
                    if pair_count is not None:
                        require(0 <= pair_count <= min(candidate_count, teammate_count),
                                "Pair count exceeds species appearances")
                observations.append({"month": month["month"],
                                     "candidate_count": candidate_count,
                                     "pair_count": pair_count})
            complete = all(row["pair_count"] is not None and row["candidate_count"]
                           for row in observations)
            numerator = sum(row["pair_count"] for row in observations) if complete else None
            denominator = sum(row["candidate_count"] for row in observations) if complete else None
            pairs[candidate][teammate] = {
                "status": "verified" if complete else "unavailable",
                "pair_count": numerator,
                "candidate_count": denominator,
                "value": round(numerator / denominator, 10) if complete else None,
                "months": observations,
            }
    return {
        "format": "gen9doublesubers", "cutoff": 0,
        "months": [month["month"] for month in months],
        "battles": sum(month["battles"] for month in months),
        "formula": "sum(qualifying team pair counts) / sum(candidate raw usage-table appearances)",
        "published_moveset_percentage_checks": text_checks,
        "pairs": pairs,
    }


if __name__ == "__main__":
    root = active_catalog_dir()
    report = audit(root)
    (root / "teammate_audit.json").write_text(json.dumps(report, indent=2) + "\n")
    counts = {status: sum(pair["status"] == status for row in report["pairs"].values()
                          for pair in row.values()) for status in ["verified", "unavailable"]}
    print(json.dumps({"battles": report["battles"], "pair_status": counts,
                      "published_percentage_checks": report["published_moveset_percentage_checks"]}))
