"""Fill set-role coverage from separate Smogon Doubles Ubers component reports."""

from collections import defaultdict
import hashlib
from itertools import product
import json

from team_styles import archetype_set_roles, set_features, speed_mode
from usage_variants import compatible_moves, parse_spread, pooled_components, ranked, validate_options


MIN_COMPONENT_SHARE = .05
MIN_SPREAD_SHARE = .025
MIN_WEIGHT = .01
ROLE_TARGET = 2
OPTIONS_PER_ROLE = 6
FIELD_ITEMS = {
    "damprock": ({"raindance"}, {"drizzle"}),
    "heatrock": ({"sunnyday"}, {"drought", "orichalcumpulse"}),
    "smoothrock": ({"sandstorm"}, {"sandstream"}),
    "icyrock": ({"snowscape"}, {"snowwarning"}),
    "lightclay": ({"reflect", "lightscreen", "auroraveil"}, set()),
    "terrainextender": ({"electricterrain", "grassyterrain", "psychicterrain", "mistyterrain"},
                        {"electricsurge", "grassysurge", "psychicsurge", "mistysurge", "hadronengine"}),
}
UTILITY_ATTACKS = {"fakeout", "uturn", "voltswitch", "flipturn", "electroweb", "icywind", "snarl"}


def thresholds(pooled):
    weight = sum(pooled["Abilities"].values())
    return max(weight * MIN_COMPONENT_SHARE, MIN_WEIGHT), max(weight * MIN_SPREAD_SHARE, MIN_WEIGHT)


def showdown_data(name, build):
    return {"species": name, "name": name, "ability": build["ability"], "item": build["held_Item"],
            "teraType": build["tera_Type"], "moves": build["moves"], "nature": build["nature"],
            "evs": build["evs"], "ivs": build["ivs"], "level": build["level"]}


def full_key(pokemon):
    return (pokemon["ability"], pokemon["item"], pokemon["teraType"], tuple(sorted(pokemon["moves"])),
            pokemon["nature"], tuple(sorted(pokemon["evs"].items())), tuple(sorted(pokemon["ivs"].items())),
            pokemon["level"])


def role_keys(pokemon):
    features = set_features(pokemon)
    mode = speed_mode(pokemon, features)
    return mode, {"baseline", *archetype_set_roles(features, mode)}


def distinct_role_set(pokemon):
    # A different item, ability or moveset is a real option. For otherwise
    # identical sets, only a substantial change in investment counts.
    evs = pokemon["evs"]
    return (pokemon["ability"], pokemon["item"], frozenset(pokemon["moves"]),
            tuple(sorted(stat for stat, value in evs.items() if value >= 160)))


def field_item_works(ability, item, moves):
    if item not in FIELD_ITEMS:
        return True
    field_moves, field_abilities = FIELD_ITEMS[item]
    return ability in field_abilities or bool(field_moves.intersection(moves))


def supported(build, pooled, to_id):
    floor, spread_floor = thresholds(pooled)
    keys = {
        "Abilities": [to_id(build["ability"])],
        "Items": [to_id(build["held_Item"]) or "nothing"],
        "Moves": ["ironhead" if move == "Behemoth Blade" else to_id(move) for move in build["moves"]],
    }
    spread = build["nature"] + ":" + "/".join(str(value) for value in build["evs"].values())
    return (field_item_works(keys["Abilities"][0], keys["Items"][0], keys["Moves"])
            and pooled["Tera Types"][to_id(build["tera_Type"])] > 0
            and pooled["Spreads"][spread] >= spread_floor
            and all(pooled[section][key] >= floor for section, values in keys.items() for key in values))


def observed_options(name, dex, pooled, infer_ivs):
    floor, spread_floor = thresholds(pooled)
    abilities = ranked(pooled["Abilities"], dex["abilities"], floor)
    item_names = {**dex["items"], "nothing": ""}
    items = ranked(pooled["Items"], item_names, floor)
    teras = ranked(pooled["Tera Types"], dex["types"])
    moves = ranked(pooled["Moves"], dex["moves"], floor)
    if not (teras and moves):
        return
    spreads = []
    for spread, weight in pooled["Spreads"].most_common():
        if weight < spread_floor:
            break
        nature, evs = parse_spread(spread)
        if sum(evs.values()) <= 510 and all(0 <= ev <= 252 for ev in evs.values()):
            spreads.append((spread, nature, evs))

    categories = dex["move_categories"]
    for ability, item, (spread, nature, evs) in product(abilities, items, spreads):
        usable, _ = compatible_moves(moves, categories, item, evs)
        invested = {category for stat, category in (("atk", "Physical"), ("spa", "Special")) if evs[stat] >= 160}
        attacks = {move for move in usable if move not in UTILITY_ATTACKS and categories[move] in invested}
        # Keep a second move list without Trick Room so the most popular room
        # move cannot crowd out a supported standard-speed build.
        move_lists = [usable]
        if "trickroom" in usable:
            move_lists.append([move for move in usable if move != "trickroom"])
        seen = set()
        for available in move_lists:
            anchors = [(), *((move,) for move in available)]
            if {"reflect", "lightscreen"} <= set(available):
                anchors.append(("reflect", "lightscreen"))
            for forced in anchors:
                move_ids = tuple(forced) + tuple(move for move in available if move not in forced)[:4 - len(forced)]
                key = frozenset(move_ids)
                if not key or key in seen:
                    continue
                seen.add(key)
                if len(move_ids) < min(4, len(moves)):
                    # Smogon's stats may expose only Transform or a short set.
                    # Keep short movesets only when the entire observed list is short.
                    continue
                if invested and not attacks & key:
                    continue
                if not field_item_works(ability, item, key):
                    continue
                display = [dex["moves"][move] for move in move_ids]
                ivs = infer_ivs(name, display, nature, evs, categories)
                pokemon = {"species": name, "name": name, "ability": dex["abilities"][ability],
                           "item": item_names[item], "teraType": dex["types"][teras[0]],
                           "moves": display, "nature": nature, "evs": evs, "ivs": ivs, "level": 100}
                yield pokemon, spread, (ability, item, teras[0], move_ids)
                if ivs["atk"] == 0:
                    yield {**pokemon, "ivs": {**ivs, "atk": 31}}, spread, (ability, item, teras[0], move_ids)


def shortlisted(name, dex, pools, builds, infer_ivs, to_id, targets=None):
    coverage = defaultdict(set)
    existing = set()
    for build in builds:
        pokemon = showdown_data(name, build)
        existing.add(full_key(pokemon))
        if (build["evidence_status"] == "published_smogon_dubers_sample"
                or any(supported(build, pool, to_id) for pool in pools.values())):
            mode, roles = role_keys(pokemon)
            for role in roles:
                coverage[mode, role].add(distinct_role_set(pokemon))

    viable = {}
    shortlisted_shapes = defaultdict(set)
    cutoffs = (1630, 1500) if sum(pools[1630]["Abilities"].values()) >= 8 else (1500, 1630)
    for cutoff in cutoffs:
        for pokemon, spread, keys in observed_options(name, dex, pools[cutoff], infer_ivs):
            key = full_key(pokemon)
            if key in existing or key in viable:
                continue
            mode, roles = role_keys(pokemon)
            roles = {(mode, role) for role in roles}
            distinct = distinct_role_set(pokemon)
            # Include the same shape with a legalizing Attack IV fallback, but
            # do not let tiny spread edits consume the entire shortlist.
            shape = (distinct, pokemon["ivs"]["atk"])
            needed = [role for role in roles if (targets is None or role in targets) and len(coverage[role]) < ROLE_TARGET
                      and distinct not in coverage[role] and shape not in shortlisted_shapes[role]
                      and len(shortlisted_shapes[role]) < OPTIONS_PER_ROLE]
            if needed:
                viable[key] = {"set": pokemon, "cutoff": cutoff, "spread": spread, "keys": keys, "roles": roles}
                for role in needed:
                    shortlisted_shapes[role].add(shape)
    return coverage, list(viable.values())


def make_build(option, primary, pools, reports, months, urls, to_id, roles_for, notes_for):
    pokemon = option["set"]
    name, moves, ability, item = pokemon["species"], pokemon["moves"], pokemon["ability"], pokemon["item"]
    evs, tera, cutoff = pokemon["evs"], pokemon["teraType"], option["cutoff"]
    mode, role = primary
    mode_label = {"standard": "Standard", "trick_room_setter": "Trick Room setter",
                  "trick_room_attacker": "Trick Room attacker", "min_speed_support": "Minimum-Speed support"}[mode]
    role_label = role.replace("_", " ")
    label = mode_label if role in {"baseline", "trick_room", "slow_attacker"} else f"{mode_label} - {role_label}"
    tags = ["archetype_variant", mode]
    if mode == "trick_room_attacker":
        tags.append("external_room_setter")
    elif mode == "trick_room_setter":
        tags.append("trick_room")
    if role in {"beat_up", "beat_up_payoff"}:
        tags.append("beat_up")
    if role in {"commander", "dondozo", "costar"}:
        tags.append("commander")

    a, i, t, move_ids = option["keys"]
    pooled = pools[cutoff]
    digest = hashlib.sha256(json.dumps(full_key(pokemon)).encode()).hexdigest()[:12]
    return {"set_id": f"{to_id(name)}:archetype-{mode.replace('_', '-')}-{digest}",
            "label": label, "scenario_tags": tags, "role_tags": roles_for(moves, evs, ability),
            "ability": ability, "held_Item": item, "tera_Type": tera, "moves": moves, "nature": pokemon["nature"],
            "evs": evs, "ivs": pokemon["ivs"], "level": pokemon["level"],
            "evidence_status": "dubers_usage_derived", "team_notes": notes_for(name, moves, item, ability, tags),
            "source_urls": [urls[f"{month}/gen9doublesubers-{cutoff}.json"] for month in months
                            if name in reports[month, cutoff]],
            "evidence": {"cutoff": cutoff, "weighted_appearances": sum(pooled["Abilities"].values()),
                         "selected_components": {
                             "Abilities": {ability: pooled["Abilities"][a]}, "Items": {item: pooled["Items"][i]},
                             "Tera Types": {tera: pooled["Tera Types"][t]},
                             "Moves": {move: pooled["Moves"][key] for move, key in zip(moves, move_ids)},
                         },
                         "selected_spread": option["spread"], "selected_spread_weight": pooled["Spreads"][option["spread"]],
                         "archetype_role": role, "speed_mode": mode},
            "iv_basis": "Role-inferred IVs checked for Showdown legality.",
            "optimality": "Archetype alternative assembled from observed components and an observed spread. "
                          "The reports do not give this complete set's usage."}


def fill_roles(evaluations, species, reports, months, urls, to_id, roles_for, notes_for):
    if not evaluations:
        return
    # Send only battle data to the pinned validator. Keep evidence in Python.
    validated = validate_options([[{"set": option["set"]} for option in options]
                                  for _, _, options in evaluations.values()], all_legal=True)
    for (name, (pools, coverage, options)), legal in zip(evaluations.items(), validated, strict=True):
        available = [options[index] for index in legal]
        builds = species[name]["sets"]

        def gaps(option):
            distinct = distinct_role_set(option["set"])
            return {role for role in option["roles"] if len(coverage[role]) < ROLE_TARGET
                    and distinct not in coverage[role]}

        while available:
            index = max(range(len(available)), key=lambda i: len(gaps(available[i])))
            option = available.pop(index)
            remaining = gaps(option)
            if not remaining:
                break
            primary = min(remaining, key=lambda pair: (pair[1] == "baseline", len(coverage[pair]), pair))
            builds.append(make_build(option, primary, pools, reports, months, urls, to_id, roles_for, notes_for))
            for role in option["roles"]:
                coverage[role].add(distinct_role_set(option["set"]))


def add_archetype_variants(species, dex, reports, months, urls, infer_ivs, to_id, roles_for, notes_for):
    evaluations = {}
    initial_counts = {}
    for name, record in species.items():
        if record["selection"] != "above_threshold":
            continue
        pools = {cutoff: pooled_components(reports, months, cutoff, name) for cutoff in (1630, 1500)}
        coverage, options = shortlisted(name, dex, pools, record["sets"], infer_ivs, to_id)
        evaluations[name] = pools, coverage, options
        initial_counts[name] = len(record["sets"])
    fill_roles(evaluations, species, reports, months, urls, to_id, roles_for, notes_for)

    # Refill the shortlist for remaining singletons. Once a set is selected,
    # neighboring candidates with the same role shape no longer take its slots.
    fallback = {}
    for name, (pools, coverage, _) in evaluations.items():
        singles = {key for key, builds in coverage.items() if key[1] != "baseline" and len(builds) == 1}
        if singles:
            reviewed, options = shortlisted(name, dex, pools, species[name]["sets"], infer_ivs, to_id, singles)
            fallback[name] = pools, reviewed, options
    fill_roles(fallback, species, reports, months, urls, to_id, roles_for, notes_for)
    evaluations.update(fallback)

    additions = {name: len(species[name]["sets"]) - initial for name, initial in initial_counts.items()}
    single_roles, double_roles = {}, 0
    for name, (_, coverage, _) in evaluations.items():
        singles = [{"speed_mode": mode, "role": role} for (mode, role), sets in sorted(coverage.items())
                   if role != "baseline" and len(sets) == 1]
        if singles:
            single_roles[name] = singles
        double_roles += sum(len(sets) >= ROLE_TARGET for (_, role), sets in coverage.items() if role != "baseline")
    return {"sets_added": sum(additions.values()), "species_with_additions": sum(count > 0 for count in additions.values()),
            "species_reviewed": len(evaluations),
            "role_modes_with_alternatives": double_roles,
            "role_modes_with_one_supported_set": sum(map(len, single_roles.values())),
            "single_set_roles": single_roles,
            "component_share_floor": MIN_COMPONENT_SHARE, "spread_share_floor": MIN_SPREAD_SHARE,
            "weighted_count_floor": MIN_WEIGHT}
