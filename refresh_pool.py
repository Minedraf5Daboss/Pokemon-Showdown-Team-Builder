"""Build the offline production catalog from reviewed recommendations.

Species usage and pair frequencies retain their common all-rating denominator.
The 1500/1630 reports select coverage and are stored separately per species.
"""

import argparse
import json
from pathlib import Path

from recommend_sets import write_json
from verify_teammates import audit, usage_rows

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "smogon"


def refresh_sets_only(recommendations, data=DATA):
    path = data / "candidate_pool.json"
    pool = json.loads(path.read_text(encoding="utf-8"))
    species = recommendations["species"]
    if (pool["schema_version"], pool["format"], pool["months"]) != (
            recommendations["schema_version"], recommendations["format"], recommendations["months"]):
        raise ValueError("Set-only refresh requires the same catalog format and evidence window")
    if set(species) != {candidate["name"] for candidate in pool["candidates"]}:
        raise ValueError("Set-only refresh requires the same species; use the full refresh")
    for candidate in pool["candidates"]:
        selected = species[candidate["name"]]
        if any(candidate[key] != selected[key] for key in ("base_species", "selection", "usage_reports")):
            raise ValueError(f"Species evidence changed for {candidate['name']}; use the full refresh")
        candidate["sets"] = selected["sets"]
    if "archetype_coverage" in recommendations:
        pool["archetype_coverage"] = recommendations["archetype_coverage"]
    write_json(path, pool)
    print(f"Updated {sum(len(c['sets']) for c in pool['candidates'])} sets across {len(species)} species.")


def refresh(data, *, sets_only=False):
    recommendations = json.loads((data / "set_recommendations.json").read_text(encoding="utf-8"))
    if recommendations["schema_version"] != 2 or recommendations["format"] != "gen9doublesubers":
        raise ValueError("Unsupported recommendations schema/format")
    if sets_only:
        refresh_sets_only(recommendations, data)
        return
    names = list(recommendations["species"])
    report = audit(data, names, expected_months=recommendations["months"])
    previous = json.loads((data / "previous_recommendations.json").read_text(encoding="utf-8"))
    names = [name for name in previous if name in names] + [name for name in names if name not in previous]
    tables = [usage_rows((data / month / "gen9doublesubers-0.txt").read_text(encoding="utf-8"))
              for month in recommendations["months"]]
    appearances = {name: sum(rows.get(name, {}).get("raw", 0) for rows in tables) for name in names}
    application = {"format": recommendations["format"], "cutoff": 0,
                   "months": recommendations["months"], "battles": report["battles"],
                   "verified_values": {}, "preserved_manual": {}, "unfilled": {}}
    candidates = []
    for name in names:
        selected = recommendations["species"][name]
        pairs = report["pairs"][name]
        # Leave unverified pair counts absent. Scoring distinguishes missing
        # evidence from an observed zero, and the audit records both.
        teammates = {other: pair["value"] for other, pair in pairs.items() if pair["status"] == "verified"}
        application["verified_values"][name] = teammates
        application["preserved_manual"][name] = {}
        application["unfilled"][name] = [other for other in pairs if other not in teammates]
        candidates.append({"name": name, **selected, "usage": round(appearances[name] / (2 * report["battles"]), 10),
                           "usage_appearances": appearances[name], "teammate_values": teammates})
    catalog = {"schema_version": 2, "format": recommendations["format"], "usage_cutoff": 0,
               "months": recommendations["months"], "battles": report["battles"], "candidates": candidates}
    if "archetype_coverage" in recommendations:
        catalog["archetype_coverage"] = recommendations["archetype_coverage"]
    for filename, document in [("candidate_pool.json", catalog), ("applied_teammate_values.json", application),
                           ("teammate_audit.json", report)]:
        path = data / filename
        temporary = path.with_suffix(".json.tmp")
        options = {"separators": (",", ":")} if filename == "teammate_audit.json" else {"indent": 2}
        temporary.write_text(json.dumps(document, **options) + "\n", encoding="utf-8")
        temporary.replace(path)
    print(f"Updated {len(names)} species, {sum(len(c['sets']) for c in candidates)} sets and "
          f"{sum(map(len, application['verified_values'].values()))} verified teammate directions.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sets-only", action="store_true", help="Refresh variants using the existing species and teammate evidence")
    args = parser.parse_args(argv)
    refresh(DATA, sets_only=args.sets_only)


if __name__ == "__main__":
    main()
