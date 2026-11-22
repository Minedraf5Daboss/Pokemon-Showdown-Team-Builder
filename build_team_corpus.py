"""Build labeled candidate-shaped records from validated, cached real teams.

Offline: python build_team_corpus.py
No candidate-pool membership filter is applied. No new EV spreads, movesets,
teammate frequencies, or team compositions are generated here.
"""

from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from models import FORMAT_ID
from team_styles import LABEL_METHOD, STYLES, annotate_team, set_features
from verify_teammates import audit, usage_rows

ROOT = Path(__file__).parent / "data" / "team_corpus"
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def fingerprint(sets: list[dict]) -> str:
    """Ignore member/move order, nicknames and shininess, retaining battle choices."""
    fields = ("species", "ability", "item", "nature", "level", "evs", "ivs",
              "teraType", "gender", "happiness", "gigantamax", "hpType")
    members = [{**{key: pokemon[key] for key in fields if key in pokemon},
                "moves": sorted(pokemon["moves"])} for pokemon in sets]
    canonical = sorted(json.dumps(pokemon, sort_keys=True, separators=(",", ":")) for pokemon in members)
    return hashlib.sha256(json.dumps(canonical).encode()).hexdigest()


def roles_for(member: dict) -> list[str]:
    features = set_features(member["set"])
    roles = []
    if features["support"] or features["sustain"]:
        roles.append("Support")
    if features["speed_control"]:
        roles.append("Speed Control")
    for category, role in (("Physical", "Physical Sweeper"), ("Special", "Special Sweeper")):
        if sum(move["category"] == category for move in member["move_details"]) >= 2:
            roles.append(role)
    if features["setup"]:
        roles.append("Setup")
    if any(features[weather + "_setter"] for weather in ("sun", "rain", "sand", "snow")):
        roles.append("Weather Setter")
    return roles


def candidate_record(member: dict, frequency: dict | None, teammates: dict) -> dict:
    pokemon = member["set"]
    # Preserve PokemonSet's existing naming, including tera_Type/held_Item and
    # the six *_EV and *_IV keys. Extra simulator fields live in set_metadata.
    moves = pokemon["moves"] + [""] * (4 - len(pokemon["moves"]))
    notes = ["Published set; role and strategy tags are inferred and need review."]
    if member["defaulted_fields"]:
        notes.append("Unspecified source fields use simulator defaults; see defaulted_fields.")
    if frequency is None:
        notes.append("Usage is unknown in this format; null does not mean zero usage.")
    return {
        "pokemon_sets": [{"name": pokemon["species"], "ability": pokemon["ability"],
                          "tera_Type": pokemon.get("teraType", ""), "held_Item": pokemon["item"],
                          **{f"move{i + 1}": move for i, move in enumerate(moves)},
                          "nature": pokemon["nature"], "level": pokemon["level"],
                          "evs": {f"{stat}_EV": pokemon["evs"][stat] for stat in STATS},
                          "ivs": {f"{stat}_IV": pokemon["ivs"][stat] for stat in STATS}}],
        "usage": frequency["value"] if frequency else None,
        "usage_appearances": frequency["appearances"] if frequency else None,
        "role_tags": roles_for(member), "teammate_values": teammates,
        "evidence_status": "published_set_with_inferred_roles", "team_notes": notes,
        "set_metadata": {**{key: value for key, value in member.items() if key != "set"},
                         "showdown_set": pokemon, "mechanic_tags": dict(set_features(pokemon)),
                         "usage_evidence": frequency},
    }


def load_usage(teams: list[dict]) -> tuple[dict, dict]:
    """Use the existing verified 0-cutoff source counts, only for Doubles Ubers.

    These are recent species-level ladder frequencies. They do not measure the
    popularity, win rate or historical performance of an archived composition.
    """
    root = Path(__file__).parent / "data" / "smogon"
    names = sorted({member["set"]["species"] for team in teams if team["format"] == FORMAT_ID
                    for member in team["members"]})
    evidence = audit(root, names=names)
    months = evidence["months"]
    rows = [usage_rows((root / month / f"{FORMAT_ID}-0.txt").read_text()) for month in months]
    usage = {}
    for name in names:
        if all(name in month for month in rows):
            appearances = sum(month[name]["raw"] for month in rows)
            usage[name] = {"value": round(appearances / (2 * evidence["battles"]), 10),
                           "appearances": appearances, "format": FORMAT_ID, "cutoff": 0,
                           "months": months, "team_sides": 2 * evidence["battles"],
                           "source_urls": [f"https://www.smogon.com/stats/{month}/{FORMAT_ID}-0.txt"
                                           for month in months]}
    return usage, evidence["pairs"]


def assign_splits(teams: list[dict]) -> None:
    """Keep connected five-of-six roster variants together across formats.

    Split BEFORE generating prompts. Otherwise the same team with a changed
    EV spread or a rephrased prompt can leak into validation/test.
    """
    parents = list(range(len(teams)))

    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    rosters = [{member["set_metadata"]["base_species"] for member in team["members"]} for team in teams]
    for i, team in enumerate(teams):
        for j in range(i):
            if team["generation"] == teams[j]["generation"] and len(rosters[i] & rosters[j]) >= 5:
                parents[find(i)] = find(j)
    groups = {}
    for i, team in enumerate(teams):
        roster = f"{team['generation']}:" + ",".join(sorted(rosters[i]))
        groups.setdefault(find(i), []).append(roster)
    group_ids = {key: hashlib.sha256("|".join(sorted(set(values))).encode()).hexdigest()
                 for key, values in groups.items()}
    for i, team in enumerate(teams):
        group_id = group_ids[find(i)]
        bucket = int(group_id[:8], 16) % 10
        team["split_group"] = group_id
        team["split"] = "test" if bucket == 0 else "validation" if bucket == 1 else "train"


def build(root: Path = ROOT) -> dict:
    normalized = json.loads((root / "normalized.json").read_text(encoding="utf-8"))
    usage, pair_evidence = load_usage(normalized["teams"])
    by_id = {}
    for source_team in normalized["teams"]:
        sets = [member["set"] for member in source_team["members"]]
        digest = fingerprint(sets)
        team_id = source_team["format"] + "-" + digest[:16]
        if team_id in by_id:
            by_id[team_id]["sources"].append(source_team["source"])
            continue
        annotations = annotate_team(sets)
        names = [pokemon["species"] for pokemon in sets]
        members = []
        for member in source_team["members"]:
            name = member["set"]["species"]
            focus = source_team["format"] == FORMAT_ID
            pairs = {other: pair_evidence[name][other]["value"] for other in names
                     if focus and other != name and pair_evidence[name][other]["status"] == "verified"}
            members.append(candidate_record(member, usage.get(name) if focus else None, pairs))
        by_id[team_id] = {
            "schema_version": 1, "team_id": team_id, "set_fingerprint": digest,
            "format": source_team["format"], "generation": source_team["generation"],
            "game_type": "doubles", "focus_format": source_team["format"] == FORMAT_ID,
            "title": source_team["title"], "author": source_team["author"],
            "paste_author": source_team["paste_author"], "sources": [source_team["source"]],
            "validation": source_team["validation"], "annotations": annotations,
            "stats_note": source_team["stats_note"], "members": members,
        }
    teams = sorted(by_id.values(), key=lambda team: (not team["focus_format"], team["format"], team["team_id"]))
    assign_splits(teams)
    (root / "teams.jsonl").write_text("".join(json.dumps(team, ensure_ascii=False) + "\n" for team in teams), encoding="utf-8")
    focus = [team for team in teams if team["focus_format"]]
    formats = Counter(team["format"] for team in teams)
    label_counts = Counter(label for team in teams for label in team["annotations"]["labels"])
    focus_counts = Counter(label for team in focus for label in team["annotations"]["labels"])
    report = {"schema_version": 1, "target_teams": 250, "unique_teams": len(teams),
              "pokemon_sets": sum(len(team["members"]) for team in teams),
              "focus_format": FORMAT_ID, "focus_teams": len(focus),
              "by_format": dict(formats), "labels": dict(label_counts), "focus_labels": dict(focus_counts),
              "missing_focus_labels": sorted(set(STYLES) - focus_counts.keys()),
              "splits": dict(Counter(team["split"] for team in teams)),
              "focus_splits": dict(Counter(team["split"] for team in focus)),
              "unique_species": len({p["pokemon_sets"][0]["name"] for t in teams for p in t["members"]}),
              "duplicate_records_merged": len(normalized["teams"]) - len(teams),
              "label_method": LABEL_METHOD, "all_labels_need_review": True,
              "limitations": ["Collection is a sourced starter corpus, not a comprehensive census.",
                              "Not all community submissions are endorsed or currently common.",
                              "Supplemental formats must be explicitly selected for training.",
                              "Target count is aspirational: no generated teams fill shortfalls."]}
    (root / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (root / "taxonomy.json").write_text(json.dumps({label: asdict(style) for label, style in STYLES.items()}, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
