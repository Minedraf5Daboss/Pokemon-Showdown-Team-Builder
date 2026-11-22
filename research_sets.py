"""Inspect frozen Smogon usage/set evidence without selecting new builds.

Use fetch_candidate_sources.py --dex for explicit network collection.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

from recommend_sets import dex_page

ROOT = Path(__file__).parent / "data" / "smogon"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*")
    parser.add_argument("--write-summary", action="store_true")
    args = parser.parse_args()
    recommendations = json.loads((ROOT / "set_recommendations.json").read_text())
    names = args.names or list(recommendations["species"])
    unknown = set(names) - recommendations["species"].keys()
    if unknown:
        parser.error("Species not in production: " + ", ".join(sorted(unknown)))
    reports = {(month, cutoff): json.loads((ROOT / month / f"gen9doublesubers-{cutoff}.json").read_text())["data"]
               for month in recommendations["months"] for cutoff in recommendations["selection_cutoffs"]}
    summary = {"schema_version": 2, "format": recommendations["format"], "species": {}}
    for name in names:
        statistics = {}
        for cutoff in recommendations["selection_cutoffs"]:
            pooled = {section: Counter() for section in ("Abilities", "Items", "Moves", "Spreads", "Tera Types")}
            monthly = []
            for month in recommendations["months"]:
                entry = reports[month, cutoff].get(name)
                monthly.append({"month": month, "available": entry is not None,
                                "weighted_appearances": sum(entry["Abilities"].values()) if entry else 0})
                if entry:
                    for section in pooled:
                        pooled[section].update(entry[section])
            statistics[str(cutoff)] = {"monthly": monthly, "top_components": {section: counts.most_common(6) for section, counts in pooled.items()}}
        dex = dex_page(name)
        summary["species"][name] = {"usage_reports": recommendations["species"][name]["usage_reports"],
                                     "set_statistics": statistics,
                                     "published_dubers_analyses": [strategy for strategy in dex["strategies"] if strategy["format"] == "Doubles Ubers"],
                                     "recommended_set_ids": [selected["set_id"] for selected in recommendations["species"][name]["sets"]]}
    if args.write_summary:
        (ROOT / "research_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"Summarized {len(names)} species at separate 1500 and 1630 cutoffs.")
    else:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
