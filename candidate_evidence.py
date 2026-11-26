"""Verify selection coverage, set evidence, and frozen primary source hashes."""

import hashlib
import json

from archetype_variants import role_keys, showdown_data, supported
from recommend_sets import to_id, STATS
from usage_variants import pooled_components
from verify_teammates import require, usage_rows


def species_record(value, name):
    if isinstance(value, dict):
        if to_id(value.get("name", "")) == to_id(name) and isinstance(value.get("abilities"), list):
            return value
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    return next((found for child in children if (found := species_record(child, name)) is not None), None)


def verify_sets(root, source_root=None):
    recommendations = json.loads((root / "set_recommendations.json").read_text(encoding="utf-8"))
    require(recommendations["schema_version"] == 2 and recommendations["format"] == "gen9doublesubers", "Wrong catalog schema/format")
    for filename in ("sets_manifest.json", "candidate_sources.json"):
        for source in json.loads((root / filename).read_text()):
            require(hashlib.sha256((root / source["path"]).read_bytes()).hexdigest() == source["sha256"],
                    f"Source checksum mismatch: {source['path']}")
    tables, documents, qualifying = {}, {}, set()
    for month in recommendations["months"]:
        for cutoff in recommendations["selection_cutoffs"]:
            stem = f"gen9doublesubers-{cutoff}"
            data = json.loads((root / month / f"{stem}.json").read_text())
            require(data["info"]["metagame"] == recommendations["format"] and data["info"]["cutoff"] == cutoff,
                    "Wrong detailed report format/cutoff")
            rows = usage_rows((root / month / f"{stem}.txt").read_text())
            require(all(abs(row["usage"] - data["data"][name]["usage"]) < 1e-7
                        for name, row in rows.items() if name in data["data"]), "Usage JSON/text mismatch")
            tables[month, cutoff], documents[month, cutoff] = rows, data["data"]
            qualifying.update(name for name, row in rows.items() if row["usage"] >= recommendations["threshold"])
    species = recommendations["species"]
    require(qualifying == {name for name, entry in species.items() if entry["selection"] == "above_threshold"},
            "Catalog does not exactly cover the requested inclusive usage union")
    source_root = source_root or root.parent.parent
    for source in recommendations["sample_sources"]:
        require(source["format"] == recommendations["format"] and source["quality"] in {"sample", "sample_variant"}, "Wrong sample scope")
        require(hashlib.sha256((source_root / source["path"]).read_bytes()).hexdigest() == source["sha256"], "Sample hash mismatch")
    corpus = [json.loads(line) for line in (source_root / "data/team_corpus/teams.jsonl").read_text(encoding="utf-8").splitlines()]
    sample_teams = {team["team_id"]: team for team in corpus if team["format"] == recommendations["format"]}
    evidence, all_ids = {}, set()
    for name, record in species.items():
        path = root / "sets" / f"{name.lower()}-dex.html"
        species_info, dex = None, None
        if path.exists():
            settings = json.JSONDecoder().raw_decode(path.read_text(encoding="utf-8").split("dexSettings = ", 1)[1])[0]
            species_info = species_record(settings, name)
            dex = next(value for _, value in settings["injectRpcs"] if isinstance(value, dict) and "strategies" in value)
        pools = {cutoff: pooled_components(documents, recommendations["months"], cutoff, name)
                 for cutoff in recommendations["selection_cutoffs"]}
        for observation in record["usage_reports"]:
            expected = tables[observation["month"], observation["cutoff"]].get(name, {}).get("usage", 0)
            require(observation["usage"] == expected, f"Stale usage observation: {name}")
        variants = []
        for selected in record["sets"]:
            set_id = selected["set_id"]
            require(set_id and set_id not in all_ids, f"Duplicate/empty set ID: {set_id}")
            all_ids.add(set_id)
            generated = selected["evidence_status"] == "dubers_usage_derived"
            if not generated and dex is not None:
                require(species_info is not None and selected["ability"] in species_info["abilities"], f"Invalid ability: {set_id}")
                require(name == "Smeargle" or set(selected["moves"]) <= set(dex["learnset"]), f"Moves not in SV learnset: {set_id}")
            require(1 <= len(selected["moves"]) <= 4 and len(set(selected["moves"])) == len(selected["moves"]), f"Invalid moves: {set_id}")
            require(set(selected["evs"]) == set(STATS) and sum(selected["evs"].values()) <= 510
                    and all(type(v) is int and 0 <= v <= 252 for v in selected["evs"].values()), f"Invalid EVs: {set_id}")
            require(set(selected["ivs"]) == set(STATS) and all(type(v) is int and 0 <= v <= 31 for v in selected["ivs"].values()), f"Invalid IVs: {set_id}")
            require(selected["source_urls"] and selected["scenario_tags"] and selected["iv_basis"], f"Missing provenance/scenario: {set_id}")
            if generated or selected["evidence_status"] == "dubers_usage_informed":
                observed = selected["evidence"]
                pooled = pools[observed["cutoff"]]
                item_key = to_id(selected["held_Item"]) or "nothing"
                expected_components = {
                    "Abilities": {selected["ability"]: pooled["Abilities"][to_id(selected["ability"])]},
                    "Items": {selected["held_Item"]: pooled["Items"][item_key]},
                    "Tera Types": {selected["tera_Type"]: pooled["Tera Types"][to_id(selected["tera_Type"])]},
                    "Moves": {move: pooled["Moves"]["ironhead" if move == "Behemoth Blade" else to_id(move)] for move in selected["moves"]},
                }
                require(observed["selected_components"] == expected_components, f"Evidence does not describe the selected build: {set_id}")
                require(all(weight > 0 for counts in expected_components.values() for weight in counts.values()),
                        f"Unsupported component: {set_id}")
                spread = selected["nature"] + ":" + "/".join(str(selected["evs"][stat]) for stat in STATS)
                require(observed["selected_spread"] == spread and observed["selected_spread_weight"] > 0
                        and observed["selected_spread_weight"] == pooled["Spreads"][spread], f"Unsupported spread: {set_id}")
                require(observed["weighted_appearances"] == sum(pooled["Abilities"].values()), f"Wrong evidence denominator: {set_id}")
                if "archetype_variant" in selected["scenario_tags"]:
                    mode, roles = role_keys(showdown_data(name, selected))
                    require(observed.get("speed_mode") == mode and observed.get("archetype_role") in roles,
                            f"Archetype role does not match the selected set: {set_id}")
                    require(supported(selected, pooled, to_id), f"Archetype evidence below floor: {set_id}")
            elif selected["evidence_status"] == "published_smogon_dubers_sample":
                team = sample_teams[selected["evidence"]["team_id"]]
                pokemon = next(member["pokemon_sets"][0] for member in team["members"] if member["pokemon_sets"][0]["name"] == name)
                require(all(selected[key] == pokemon[key] for key in ("ability", "held_Item", "tera_Type", "nature", "level")), f"Altered sample: {set_id}")
                require(selected["moves"] == [pokemon[f"move{i}"] for i in range(1, 5) if pokemon[f"move{i}"]]
                        and selected["evs"] == {stat: pokemon["evs"][stat + "_EV"] for stat in STATS}
                        and selected["ivs"] == {stat: pokemon["ivs"][stat + "_IV"] for stat in STATS}, f"Altered sample spread/moves: {set_id}")
            else:
                require(selected["evidence_status"] == "experimental" and selected["evidence"].get("legacy") is True
                        and "legacy_below_threshold" in selected["scenario_tags"], "Unexpected unsourced baseline")
            variants.append({"set_id": set_id, "evidence_status": selected["evidence_status"],
                             "learnset_and_ability_verified": not generated and dex is not None,
                             "legality_source": "Showdown export check" if generated or dex is None else "cached Smogon Dex",
                             "evidence": selected["evidence"], "iv_basis": selected["iv_basis"]})
        evidence[name] = {"selection": record["selection"], "sets": variants}
    return evidence
