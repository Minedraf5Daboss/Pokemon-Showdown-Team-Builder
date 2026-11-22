"""Assemble explicit reviewed blueprints and attributed Smogon sample sets.

No automatic claim of optimality: component marginals do not measure complete
sets, IVs, win rates or EV benchmarks. Run after fetch_candidate_sources.py.
"""

from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

from archetype_variants import add_archetype_variants
from fetch_candidate_sources import MONTHS, CUTOFFS, MIN_USAGE
from usage_variants import generate_variants
from verify_teammates import usage_rows

ROOT = Path(__file__).parent
DATA = ROOT / "data" / "smogon"
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
SECTIONS = ("Abilities", "Items", "Moves", "Spreads", "Tera Types")


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def to_id(value):
    return re.sub(r"[^a-z0-9]", "", value.lower())


def infer_ivs(name, moves, nature, evs, move_categories, speed_iv=None):
    """Use actual move categories rather than EV investment to infer Attack IVs."""
    unknown = [move for move in moves if to_id(move) not in move_categories]
    if unknown:
        raise ValueError(f"Missing move category: {', '.join(unknown)}")
    uses_attack = any(move_categories[to_id(move)] == "Physical" and move not in
                      {"Body Press", "Foul Play", "Endeavor"} for move in moves)
    ivs = dict.fromkeys(STATS, 31)
    if not uses_attack and name not in {"Miraidon", "Koraidon", "Ditto"}:
        ivs["atk"] = 0
    if nature in {"Quiet", "Brave", "Sassy", "Relaxed"} and evs["spe"] == 0:
        ivs["spe"] = 0
    if speed_iv not in (None, ""):
        ivs["spe"] = int(speed_iv)
        if not 0 <= ivs["spe"] <= 31:
            raise ValueError("Speed IV override must be between 0 and 31")
    if name in {"Raging Bolt", "Terapagos"}:
        ivs["atk"] = {"Raging Bolt": 20, "Terapagos": 15}[name]
    return ivs


def dex_page(name):
    path = DATA / "sets" / f"{name.lower()}-dex.html"
    if not path.exists():
        return {"strategies": []}
    html = path.read_text(encoding="utf-8")
    settings = json.JSONDecoder().raw_decode(html.split("dexSettings = ", 1)[1])[0]
    return next(value for _, value in settings["injectRpcs"]
                if isinstance(value, dict) and "strategies" in value)


def roles_for(moves, evs, ability):
    roles = []
    if set(moves) & {"Fake Out", "Follow Me", "Rage Powder", "Helping Hand", "Wide Guard", "Decorate",
                     "Spore", "Parting Shot", "Will-O-Wisp", "Encore", "Disable", "Revival Blessing",
                     "Life Dew", "Lunar Blessing", "Instruct", "Salt Cure", "Ruination", "Transform",
                     "Reflect", "Aurora Veil", "Imprison", "Taunt", "Skill Swap"}:
        roles.append("Support")
    if set(moves) & {"Trick Room", "Tailwind", "Icy Wind", "Electroweb", "Thunder Wave", "Nuzzle", "Quash", "Rock Tomb"}:
        roles.append("Speed Control")
    if evs["atk"] >= 160 or ("Bulk Up" in moves and set(moves) & {"Rage Fist", "Drain Punch"}):
        roles.append("Physical Sweeper")
    if evs["spa"] >= 120:
        roles.append("Special Sweeper")
    if set(moves) & {"Swords Dance", "Nasty Plot", "Calm Mind", "Dragon Dance", "Quiver Dance", "Bulk Up",
                     "Iron Defense", "Belly Drum", "Cosmic Power"}:
        roles.append("Setup")
    if ability in {"Drought", "Drizzle", "Sand Stream", "Snow Warning", "Orichalcum Pulse"}:
        roles.append("Weather Setter")
    return roles or ["Support"]


def notes_for(name, moves, item, ability, scenarios):
    notes = []
    if item.startswith("Choice "):
        notes.append("Choice item locks the selected move until switching.")
    if "trick_room" in scenarios:
        notes.append("Plan Trick Room turns with the team's fast and slow modes.")
    if "Expanding Force" in moves:
        notes.append("Requires Psychic Terrain and a grounded user for terrain-boosted spread Expanding Force.")
    if "Earthquake" in moves:
        notes.append("Earthquake can hit the ally; plan immunities or protection. Grassy Terrain weakens it against grounded targets.")
    if ability in {"Psychic Surge", "Grassy Surge", "Hadron Engine"}:
        notes.append("Terrain setters overwrite one another. Psychic Terrain blocks priority against grounded targets; Electric Terrain blocks grounded sleep.")
    if item == "Focus Sash":
        notes.append("Focus Sash needs full HP; weather and other chip damage can break it.")
    if item in {"Electric Seed", "Psychic Seed", "Grassy Seed", "Misty Seed"}:
        notes.append(f"{item} requires {item.removesuffix(' Seed')} Terrain; search enforces a matching setter.")
    if ability == "Unburden":
        notes.append("Unburden activates only after losing or consuming the held item; Focus Sash does not grant an immediate Speed boost.")
    if name == "Pawmot" and "Double Shock" in moves:
        notes.append("Tera Electric permits repeated Double Shock. Other Tera types make Double Shock fail after Terastallization.")
    if "commander" in scenarios:
        notes.append("Commander requires Dondozo and Tatsugiri together; boosted Dondozo occupies the active battle while Tatsugiri is inside.")
    if ability == "Costar":
        notes.append("Costar copies the ally's stat stages on entry; the partner must establish boosts first.")
    if "beat_up" in scenarios:
        notes.append("Beat Up support targets allied Annihilape to build Rage Fist power; account for the damage taken.")
    if ability == "Beads of Ruin":
        notes.append("Beads of Ruin lowers other active Pokemon's Special Defense, including the ally's.")
    if ability == "Sword of Ruin":
        notes.append("Sword of Ruin lowers other active Pokemon's Defense, including the ally's.")
    if ability == "Vessel of Ruin":
        notes.append("Vessel of Ruin reduces other active Pokemon's Special Attack, including the ally's.")
    if ability == "Neutralizing Gas":
        notes.append("Neutralizing Gas also suppresses eligible allied abilities; evaluate partner dependence before selecting.")
    if ability == "Shadow Tag":
        notes.append("Ghost types can escape trapping; switching moves can also bypass the trap.")
    if "Dark Void" in moves:
        notes.append("Dark Void has 50% base accuracy; this is a high-variance sleep option, not a reliable default.")
    if "Last Respects" in moves:
        notes.append("Last Respects gains power from fainted allies; preserve this set for the intended endgame.")
    if name.startswith("Ogerpon-"):
        notes.append("The mask and Tera type are form-locked; Terastallization changes the ability to Embody Aspect.")
    if name == "Terapagos":
        notes.append("Terapagos requires Stellar Tera; its Stellar form removes weather and terrain with Teraform Zero.")
    if "sun" in scenarios or "rain" in scenarios or "sand" in scenarios or "snow" in scenarios:
        notes.append("Weather can be replaced by an opposing or allied setter; plan the required weather turns.")
    return notes


def main():
    tables, reports = {}, {}
    names = set()
    for month in MONTHS:
        for cutoff in CUTOFFS:
            key = f"{month}/{cutoff}"
            tables[key] = usage_rows((DATA / month / f"gen9doublesubers-{cutoff}.txt").read_text())
            reports[month, cutoff] = json.loads((DATA / month / f"gen9doublesubers-{cutoff}.json").read_text())["data"]
            names.update(name for name, row in tables[key].items() if row["usage"] >= MIN_USAGE)
    dex = json.loads((DATA / "battle_dex.json").read_text(encoding="utf-8"))
    manifest = json.loads((DATA / "candidate_sources.json").read_text())
    urls = {source["path"]: source["url"] for source in manifest}
    legacy = json.loads((DATA / "legacy_sets.json").read_text())
    previous = json.loads((DATA / "previous_recommendations.json").read_text())
    species = {}
    for name in sorted(names | legacy.keys()):
        observations = [{"month": month, "cutoff": cutoff,
                         "usage": tables[f"{month}/{cutoff}"].get(name, {}).get("usage", 0),
                         "url": urls[f"{month}/gen9doublesubers-{cutoff}.txt"]}
                        for month in MONTHS for cutoff in CUTOFFS]
        species[name] = {"base_species": dex["species"][name]["baseSpecies"],
                         "selection": "above_threshold" if name in names else "existing_production_below_threshold",
                         "usage_reports": observations, "sets": []}
    blueprints = list(csv.DictReader((DATA / "set_blueprints.tsv").open(encoding="utf-8"), delimiter="\t"))
    for row in blueprints:
        name = row["species"]
        pooled = {section: Counter() for section in SECTIONS}
        # Cutoffs are nested views of the same battles: select one cutoff for
        # set evidence rather than summing both as independent observations.
        for month in MONTHS:
            entry = reports[month, 1630].get(name, {})
            for section in SECTIONS:
                pooled[section].update(entry.get(section, {}))
        spreads = pooled["Spreads"].most_common()
        spread = spreads[int(row["spread"])][0] if row["spread"].isdigit() else row["spread"]
        nature, values = spread.split(":")
        evs = dict(zip(STATS, map(int, values.split("/"))))
        moves, ability, item = row["moves"].split(","), row["ability"], row["item"]
        scenarios = row["scenario"].split(",")
        # Usage does not contain IVs. Role inferences and benchmark overrides
        # remain separate from the observed nature/EV spread.
        ivs = infer_ivs(name, moves, nature, evs, dex["move_categories"], row.get("speed_iv"))
        tera = row.get("tera") or dex["types"][pooled["Tera Types"].most_common(1)[0][0]]
        # Crowned signature moves are named differently in raw usage reports.
        move_ids = ["ironhead" if move == "Behemoth Blade" else to_id(move) for move in moves]
        component_weights = {"Abilities": {ability: pooled["Abilities"][to_id(ability)]},
                             "Items": {item: pooled["Items"][to_id(item)]},
                             "Tera Types": {tera: pooled["Tera Types"][to_id(tera)]},
                             "Moves": {move: pooled["Moves"][key] for move, key in zip(moves, move_ids)}}
        if any(weight <= 0 for components in component_weights.values() for weight in components.values()):
            raise ValueError(f"Unsupported blueprint component: {name}/{row['label']}")
        selected = {"set_id": to_id(name) + ":" + re.sub(r"[^a-z0-9]+", "-", row["label"].lower()).strip("-"),
                    "label": row["label"], "scenario_tags": scenarios,
                    "ability": ability, "tera_Type": tera, "held_Item": item, "moves": moves,
                    "nature": nature, "evs": evs, "ivs": ivs, "level": 100,
                    "role_tags": roles_for(moves, evs, ability), "evidence_status": "dubers_usage_informed",
                    "team_notes": notes_for(name, moves, item, ability, scenarios),
                    "source_urls": [urls[f"{month}/gen9doublesubers-1630.json"] for month in MONTHS]
                                   + [urls[f"sets/{name.lower()}-dex.html"]],
                    "evidence": {"cutoff": 1630, "weighted_appearances": sum(pooled["Abilities"].values()),
                                 "selected_components": component_weights, "selected_spread": spread,
                                 "selected_spread_weight": pooled["Spreads"][spread],
                                 "curated_dubers_analysis_available": any(s["format"] == "Doubles Ubers" for s in dex_page(name)["strategies"])},
                    "iv_basis": "Inferred for the role; usage statistics do not record IVs. Miraidon/Koraidon retain legal encounter IVs.",
                    "optimality": "Scenario-specific recommendation using observed components and an observed exact nature/EV spread. Marginal usage is not full-set co-occurrence, a win rate or proof of optimality."}
        species[name]["sets"].append(selected)
        if row.get("speed_iv") not in (None, ""):
            selected["iv_basis"] += f" Explicit Speed IV override: {ivs['spe']}; minimum Speed is not assumed for this support set."
        if name in {"Raging Bolt", "Terapagos"}:
            selected["iv_basis"] = f"Attack IV {ivs['atk']} matches the official Showdown SV encounter requirement; other IVs are inferred for the role."
    for name, old in legacy.items():
        selected = {key: old[key] for key in ("ability", "tera_Type", "held_Item", "moves", "nature", "evs", "ivs", "role_tags", "team_notes", "iv_basis", "optimality")}
        selected.update(set_id=to_id(name) + ":legacy", label="Existing production baseline", level=100,
                        scenario_tags=["legacy_below_threshold"], evidence_status="experimental",
                        source_urls=[old["source"]], evidence={"legacy": True})
        species[name]["sets"].append(selected)
    # Exact endorsed sample sets remain intact, with source/default provenance.
    # Only this format and actual sample resources may enter the production pool.
    corpus = ROOT / "data" / "team_corpus"
    sample_sources = []
    for line in (corpus / "teams.jsonl").read_text(encoding="utf-8").splitlines():
        team = json.loads(line)
        approved = [s for s in team["sources"] if s["format"] == "gen9doublesubers" and s["quality"] in {"sample", "sample_variant"}]
        if team["format"] != "gen9doublesubers" or not approved:
            continue
        for source in approved:
            raw = corpus / source["file"]
            if hashlib.sha256(raw.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Sample source checksum mismatch")
            sample_sources.append({**source, "path": str(raw.relative_to(ROOT)).replace("\\", "/")})
        for member in team["members"]:
            pokemon = member["pokemon_sets"][0]
            name = pokemon["name"]
            if name not in species:
                continue
            moves = [pokemon[f"move{i}"] for i in range(1, 5) if pokemon[f"move{i}"]]
            evs = {stat: pokemon["evs"][stat + "_EV"] for stat in STATS}
            ivs = {stat: pokemon["ivs"][stat + "_IV"] for stat in STATS}
            selected = {key: pokemon[key] for key in ("ability", "tera_Type", "held_Item", "nature", "level")}
            selected.update(moves=moves, evs=evs, ivs=ivs,
                            set_id=to_id(name) + ":sample-" + team["team_id"].rsplit("-", 1)[1],
                            label=team["title"] + " sample", scenario_tags=["published_sample", *team["annotations"]["labels"]],
                            role_tags=roles_for(moves, evs, pokemon["ability"]),
                            evidence_status="published_smogon_dubers_sample",
                            source_urls=list(dict.fromkeys(u for s in approved for u in (s["smogon_url"], s["data_url"]))),
                            team_notes=notes_for(name, moves, pokemon["held_Item"], pokemon["ability"], []),
                            evidence={"team_id": team["team_id"], "defaulted_fields": member.get("set_metadata", {}).get("defaulted_fields", []),
                                      "sample_edition": "Smogon samples last edited 2024-11-07; retained as contextual alternatives, not a claim of a 2026 benchmark."},
                            iv_basis="Exact normalized published set; omitted source fields use Showdown defaults.",
                            optimality="Published Doubles Ubers sample; benchmarks belong to the original team and sample edition.")
            signature = lambda s: json.dumps([s[k] for k in ("ability", "tera_Type", "held_Item", "moves", "nature", "evs", "ivs")], sort_keys=True)
            if all(signature(s) != signature(selected) for s in species[name]["sets"]):
                species[name]["sets"].append(selected)
    generate_variants(species, dex, reports, MONTHS, urls, infer_ivs, to_id, roles_for, notes_for)
    archetype_coverage = add_archetype_variants(species, dex, reports, MONTHS, urls,
                                              infer_ivs, to_id, roles_for, notes_for)
    document = {"schema_version": 2, "format": "gen9doublesubers", "months": list(MONTHS),
                "selection_cutoffs": list(CUTOFFS), "threshold": MIN_USAGE, "selection_rule": "usage >= threshold in ANY requested report",
                "archetype_coverage": archetype_coverage, "species": species, "sample_sources": sample_sources}
    write_json(DATA / "set_recommendations.json", document)
    changes = {}
    for name, old in previous.items():
        if name not in species:
            continue
        selected = species[name]["sets"][0]
        changed = [key for key in ("ability", "held_Item", "tera_Type", "moves", "nature", "evs", "ivs") if old[key] != selected[key]]
        changes[name] = {"changed_fields": changed,
                         "previous": {key: old[key] for key in changed},
                         "recommended": {key: selected[key] for key in changed},
                         "recommended_set_id": selected["set_id"],
                         "reason": "Existing below-threshold production baseline retained." if name in legacy else
                                   "Current 1630 usage-informed scenario baseline; additional variants retained where supported."}
    write_json(DATA / "set_changes.json", changes)
    lines = ["# SV Doubles Ubers candidate review", "",
             "Peak usage is the maximum weighted usage across the six requested reports. Sets are separate scenario choices; their usage is not measured.", "",
             "| Species | Peak usage | Sets | First recommendation | Scenarios |", "| --- | ---: | ---: | --- | --- |"]
    for name, entry in species.items():
        selected = entry["sets"][0]
        peak = max(row["usage"] for row in entry["usage_reports"])
        scenarios = sorted({tag for build in entry["sets"] for tag in build["scenario_tags"]})
        lines.append(f"| {name} | {peak:.5%} | {len(entry['sets'])} | {selected['label']}: {selected['held_Item'] or 'No item'} | {', '.join(scenarios)} |")
    (DATA / "candidate_catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Reviewed {len(names)} qualifying species; {len(species)} total species; {sum(len(s['sets']) for s in species.values())} scenario sets.")
    print(json.dumps({key: value for key, value in archetype_coverage.items() if key != "single_set_roles"},
                     sort_keys=True))


if __name__ == "__main__":
    main()
