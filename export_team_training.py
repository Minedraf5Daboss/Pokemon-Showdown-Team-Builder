"""Export reproducible, model-independent chat JSONL for later local training.

Default: only Gen 9 Doubles Ubers. Other formats require --formats explicitly.
No model is downloaded or trained by importing data or running the builder.
Prompt text is synthetic; the six-member teams and spreads are sourced.
"""

import argparse
from collections import Counter
import json
from pathlib import Path

from models import FORMAT_ID
from team_corpus import load_corpus
from team_styles import STYLES

SYSTEM_INTENT = ("Extract the requested team styles from a Pokemon team-building prompt. "
                 "Return JSON with format, requested, and excluded. Do not invent Pokemon or sets.")
SYSTEM_TEAM = ("Explain a sourced doubles team for the requested style and format. Return JSON. "
               "Treat strategy labels as heuristic annotations and do not claim measured win rates.")


def examples(team: dict, task: str) -> list[dict]:
    """Request a subset of a team's labels; do not teach unrequested labels."""
    records = []
    for label in team["annotations"]["labels"]:
        style = STYLES[label]
        prompts = [f"Build me a {style.aliases[0]} team from scratch.",
                   f"I'd like {style.aliases[-1]} in {team['format']}."]
        for prompt in prompts:
            if task == "intent":
                target = {"format": team["format"], "requested": [label], "excluded": []}
                system = SYSTEM_INTENT
            else:
                target = {"format": team["format"], "team_id": team["team_id"],
                          "requested_styles": [label], "all_team_labels": team["annotations"]["labels"],
                          "members": [member["pokemon_sets"][0] for member in team["members"]],
                          "excels_at": style.excels_at, "watch_out_for": style.watch_out_for,
                          "label_method": team["annotations"]["label_method"],
                          "sources": [source["smogon_url"] for source in team["sources"]]}
                system = SYSTEM_TEAM
            records.append({"messages": [
                {"role": "system", "content": system + " Format: " + team["format"]},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": json.dumps(target, ensure_ascii=False)}],
                "metadata": {"task": task, "team_id": team["team_id"], "format": team["format"],
                             "split_group": team["split_group"], "prompt_origin": "synthetic_template",
                             "label_method": team["annotations"]["label_method"],
                             "review_status": team["annotations"]["review_status"]}})
    return records


def export(output: Path, formats: set[str], task: str) -> dict:
    teams = [team for team in load_corpus(format_id=None) if team["format"] in formats]
    if not teams:
        raise ValueError("No corpus teams match the requested formats")
    output.mkdir(parents=True, exist_ok=True)
    counts, seen_groups = {}, {}
    for split in ("train", "validation", "test"):
        rows = []
        for team in teams:
            if team["split"] == split:
                previous = seen_groups.setdefault(team["split_group"], split)
                if previous != split:
                    raise ValueError("Team family leaks across data splits")
                rows.extend(examples(team, task))
        # Template duplicates are not extra evidence. In intent mode two teams
        # can produce identical messages, so collapse them within each split.
        unique = {json.dumps(row["messages"], sort_keys=True): row for row in rows}
        rows = list(unique.values())
        (output / f"{split}.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
        counts[split] = len(rows)
    report = {"task": task, "formats": sorted(formats), "source_teams": len(teams), "examples": counts,
              "team_splits": dict(Counter(team["split"] for team in teams)),
              "warning": "Weak labels and template prompts require expert review and real-prompt evaluation before model deployment."}
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--formats", nargs="+", default=[FORMAT_ID])
    parser.add_argument("--task", choices=["intent", "team"], default="team")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "data" / "team_corpus" / "training")
    args = parser.parse_args(argv)
    print(json.dumps(export(args.output, set(args.formats), args.task), indent=2))


if __name__ == "__main__":
    main()
