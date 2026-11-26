"""Generate distinct, legal Doubles Ubers roles from observed set components."""

from collections import Counter, defaultdict
from itertools import product
import json
from pathlib import Path
import subprocess


WEATHER = {
    "rain": ({"drizzle", "swiftswim", "raindish", "dryskin"},
             {"raindance", "thunder", "hurricane", "waterspout", "originpulse", "hydropump",
              "electroshot", "surgingstrikes", "liquidation", "wavecrash", "muddywater"}),
    "sun": ({"drought", "orichalcumpulse", "chlorophyll", "solarpower", "protosynthesis", "harvest"},
            {"sunnyday", "solarbeam", "solarblade", "eruption", "heatwave",
             "flareblitz", "fireblast", "flamethrower", "armorcannon"}),
    "sand": ({"sandstream", "sandrush", "sandforce"}, {"sandstorm"}),
    "snow": ({"snowwarning", "slushrush", "icebody", "snowcloak"}, {"snowscape", "auroraveil", "blizzard"}),
}
MOVE_ROLES = {
    "trick-room-setter": ("Trick Room setter", "trick_room", {"trickroom"}),
    "tailwind": ("Tailwind setter", "tailwind", {"tailwind"}),
    "redirection": ("Redirection support", "redirection", {"followme", "ragepowder"}),
    "wide-guard": ("Wide Guard support", "wide_guard", {"wideguard"}),
    "pivot-support": ("Doubles pivot and disruption", "pivot",
                      {"fakeout", "partingshot", "uturn", "voltswitch", "flipturn", "chillyreception"}),
    "setup": ("Setup option", "setup",
              {"swordsdance", "nastyplot", "calmmind", "dragondance", "quiverdance", "bulkup",
               "irondefense", "bellydrum", "cosmicpower", "shellsmash", "geomancy"}),
    "screens": ("Screen support", "screens", {"reflect", "lightscreen", "auroraveil"}),
    "psychic-terrain": ("Psychic Terrain option", "psyspam", {"expandingforce", "psychicterrain", "psyblade"}),
    "electric-terrain": ("Electric Terrain option", "electric_terrain", {"electricterrain", "risingvoltage"}),
}
CHOICE_ITEMS = {"choicescarf", "choicespecs", "choiceband"}
CHOICE_STATUS = {"trick", "switcheroo"}
CROWNED_FORMS = {"Zacian-Crowned", "Zamazenta-Crowned"}
STATS = ("hp", "atk", "def", "spa", "spd", "spe")
SECTIONS = ("Abilities", "Items", "Moves", "Spreads", "Tera Types")


def pooled_components(reports, months, cutoff, name):
    pooled = {section: Counter() for section in SECTIONS}
    for month in months:
        entry = reports[month, cutoff].get(name, {})
        for section in SECTIONS:
            pooled[section].update(entry.get(section, {}))
    return pooled


def parse_spread(key):
    nature, values = key.split(":")
    evs = dict(zip(STATS, map(int, values.split("/")), strict=True))
    return nature, evs


def validate_options(groups, *, all_legal=False, allow_normalization=False):
    command = ["node", str(Path(__file__).with_name("validate_set_options.cjs"))]
    if all_legal:
        command.append("--all")
    if allow_normalization:
        command.append("--allow-normalization")
    completed = subprocess.run(command, input=json.dumps(groups, separators=(",", ":")),
                               encoding="utf-8", stdout=subprocess.PIPE, check=True)
    return json.loads(completed.stdout)


def ranked(counts, legal_names, minimum=0):
    return [key for key, weight in counts.most_common() if weight > 0 and weight >= minimum and key in legal_names]


def signature(build):
    return (build["ability"], build["held_Item"], frozenset(build["moves"]))


def compatible_moves(moves, categories, item, evs):
    usable = [move for move in moves if (
        categories[move] != "Status" or
        (item != "assaultvest" and (item not in CHOICE_ITEMS or move in CHOICE_STATUS))
    )]
    if evs["spa"] >= 160 > evs["atk"]:
        off_stat = "Physical"
    elif evs["atk"] >= 160 > evs["spa"]:
        off_stat = "Special"
    else:
        off_stat = None
    utility = {"uturn", "voltswitch", "flipturn", "fakeout", "bulldoze", "icywind",
               "electroweb", "snarl", "bodypress", "foulplay", "endeavor", "ruination"}
    aligned = [move for move in usable if categories[move] != off_stat or move in utility]
    if any(categories[move] != "Status" for move in aligned):
        usable = aligned + [move for move in usable if move not in aligned]
    return usable, off_stat


def option_groups(name, dex, pooled, infer_ivs, *, primary):
    """Yield ordered Showdown candidates for meaningful observed roles."""
    abilities = ranked(pooled["Abilities"], dex["abilities"])
    items = ranked(pooled["Items"], {**dex["items"], "nothing": ""})
    teras = ranked(pooled["Tera Types"], dex["types"])
    spreads = []
    for spread, weight in pooled["Spreads"].most_common():
        nature, evs = parse_spread(spread)
        if weight > 0 and sum(evs.values()) <= 510 and all(0 <= ev <= 252 for ev in evs.values()):
            spreads.append((spread, nature, evs))
    moves = ranked(pooled["Moves"], dex["moves"])
    if not (abilities and items and teras and spreads and moves):
        return

    sample = sum(pooled["Abilities"].values())
    # Avoid dressing up a trace observation as a dedicated strategy.
    common = {section: set(ranked(pooled[section], keys, max(sample * .05, 0.01)))
              for section, keys in (("Abilities", abilities), ("Items", items), ("Moves", moves))}
    plans = {}
    if primary:
        plans["usage-baseline"] = ("Usage baseline", "general", abilities[:3], items[:4], spreads[:4], ())
    for slug, (label, tag, required) in MOVE_ROLES.items():
        seen = tuple(move for move in moves if move in required & common["Moves"])[:2]
        if seen:
            plans[slug] = (label, tag, abilities[:2], items[:4], spreads[:3], seen)
    for weather, (weather_abilities, weather_moves) in WEATHER.items():
        matched = [ability for ability in abilities if ability in weather_abilities & common["Abilities"]]
        seen = tuple(move for move in moves if move in weather_moves & common["Moves"])[:2]
        if matched or seen:
            plans[weather] = (f"{weather.title()} team option", weather, matched or abilities[:2],
                              items[:4], spreads[:3], () if matched else seen)
    slow_spreads = [spread for spread in spreads if spread[1] in {"Quiet", "Brave", "Sassy", "Relaxed"}
                    and spread[2]["spe"] == 0 and max(spread[2]["atk"], spread[2]["spa"]) >= 160
                    and pooled["Spreads"][spread[0]] >= .025 * sample]
    if dex["species"][name]["baseStats"]["spe"] <= 90 and slow_spreads:
        plans["trick-room-attacker"] = ("Slow Trick Room attacker", "trick_room", abilities[:2],
                                       items[:4], slow_spreads[:3], ())
    for item in [key for key in items[:5] if key in common["Items"] and key != "nothing"]:
        plans["item-" + item] = (f"{dex['items'][item]} option", "general", abilities[:2], [item], spreads[:3], ())
    for ability in [key for key in abilities[1:3] if key in common["Abilities"]]:
        plans["ability-" + ability] = (f"{dex['abilities'][ability]} option", "general", [ability],
                                      items[:3], spreads[:3], ())

    for slug, (label, tag, ability_options, item_options, spread_options, required_moves) in plans.items():
        options = []
        for ability, item, (spread, nature, evs), forced in product(
                ability_options, item_options, spread_options, required_moves or (None,)):
            if len(options) >= 40:
                break
            if slug in {"trick-room-attacker", "trick-room-setter"} and item == "choicescarf":
                continue
            if slug in WEATHER and any(ability in family[0] for other, family in WEATHER.items() if other != slug):
                continue
            if item == "choiceband" and evs["spa"] >= 160 > evs["atk"]:
                continue
            if item == "choicespecs" and evs["atk"] >= 160 > evs["spa"]:
                continue
            usable, off_stat = compatible_moves(moves, dex["move_categories"], item, evs)
            if forced in {"dragondance", "swordsdance", "bellydrum", "bulkup"} and off_stat == "Physical":
                continue
            if forced in {"calmmind", "nastyplot", "quiverdance"} and off_stat == "Special":
                continue
            if not usable or (forced and forced not in usable):
                continue
            # Try nearby move combinations if the most common marginals conflict.
            remaining = [move for move in usable if move != forced][:6]
            prefixes = [forced] if forced else []
            count = min(4 - len(prefixes), len(remaining))
            top = remaining[:count]
            groups = [tuple(top)]
            if len(remaining) > count:
                # Replace any one slot so a common form-changing or incompatible
                # move cannot hide an otherwise legal observed moveset.
                groups.extend(tuple(top[:i] + top[i + 1:] + remaining[count:count + 1])
                              for i in reversed(range(count)))
            for group in groups:
                move_ids = [*prefixes, *group]
                if item in CHOICE_ITEMS | {"assaultvest"} and not any(
                        dex["move_categories"][move] != "Status" for move in move_ids):
                    continue
                display = [dex["moves"][move] for move in move_ids]
                ivs = infer_ivs(name, display, nature, evs, dex["move_categories"])
                item_name = "" if item == "nothing" else dex["items"][item]
                actual_ability = dex["abilities"][ability]
                # Start with the most observed Tera type. Role variants use actual
                # observed spreads, with IVs inferred independently of usage.
                set_data = dict(species=name, name=name, ability=actual_ability, item=item_name,
                                teraType=dex["types"][teras[0]], moves=display, nature=nature,
                                evs=evs, ivs=ivs, level=100)
                options.append(dict(set=set_data, spread=spread, label=label, tag=tag, slug=slug,
                                    keys=[ability, item, teras[0], move_ids]))
                # A role-inferred IV can be illegal for a fixed encounter.
                if ivs["atk"] == 0:
                    options.append({**options[-1], "set": {**set_data, "ivs": {**ivs, "atk": 31}}})
        yield slug, options


def generate_variants(species, dex, reports, months, urls, infer_ivs, to_id, roles_for, notes_for):
    groups = {}
    pools = {}
    for name, entry in species.items():
        by_role = defaultdict(list)
        for cutoff in (1630, 1500):
            pooled = pooled_components(reports, months, cutoff, name)
            pools[name, cutoff] = pooled
        # Prefer the higher cutoff when there is enough evidence, and use
        # 1500 separately for roles that aren't legal or supported there.
        cutoffs = (1630, 1500) if sum(pools[name, 1630]["Abilities"].values()) >= 8 else (1500, 1630)
        for cutoff in cutoffs:
            for slug, options in option_groups(name, dex, pools[name, cutoff], infer_ivs, primary=not entry["sets"]):
                by_role[slug].extend({**option, "cutoff": cutoff} for option in options)
        groups.update({(name, slug): options for slug, options in by_role.items()})
    # Node is only needed to regenerate the catalog. Runtime uses frozen JSON.
    entries = list(groups.items())
    validation = validate_options(list(groups.values()))
    legal_species = {name for ((name, _), _), index in zip(entries, validation, strict=True) if index >= 0}
    # Showdown stores crowned forms under their base names with the Rusted
    # items. Permit that normalization if a crowned form needs a new baseline.
    fallback = [i for i, ((name, _), choices) in enumerate(entries)
                if name in CROWNED_FORMS and name not in legal_species and not species[name]["sets"] and choices]
    if fallback:
        normalized = validate_options([entries[i][1] for i in fallback], allow_normalization=True)
        for i, index in zip(fallback, normalized, strict=True):
            validation[i] = index
    for ((name, _), choices), index in zip(entries, validation, strict=True):
        if index < 0:
            continue
        choice = choices[index]
        selected = choice["set"]
        cutoff = choice["cutoff"]
        pooled = pools[name, cutoff]
        tags = [choice["tag"]]
        moves, evs = selected["moves"], selected["evs"]
        ability, item = selected["ability"], selected["item"]
        ability_key, item_key, tera_key, move_keys = choice["keys"]
        build = {"set_id": f"{to_id(name)}:observed-{choice['slug']}", "label": choice["label"],
                 "scenario_tags": tags, "role_tags": roles_for(moves, evs, ability),
                 "ability": ability, "held_Item": item, "tera_Type": selected["teraType"], "moves": moves,
                 "nature": selected["nature"], "evs": evs, "ivs": selected["ivs"], "level": selected["level"],
                 "evidence_status": "dubers_usage_derived", "team_notes": notes_for(name, moves, item, ability, tags),
                 "source_urls": [urls[f"{month}/gen9doublesubers-{cutoff}.json"] for month in months
                                 if name in reports[month, cutoff]],
                 "evidence": {"cutoff": cutoff, "weighted_appearances": sum(pooled["Abilities"].values()),
                              "selected_components": {
                                  "Abilities": {ability: pooled["Abilities"][ability_key]},
                                  "Items": {item: pooled["Items"][item_key]},
                                  "Tera Types": {selected["teraType"]: pooled["Tera Types"][tera_key]},
                                  "Moves": {move: pooled["Moves"][key] for move, key in zip(moves, move_keys)},
                              },
                              "selected_spread": choice["spread"],
                              "selected_spread_weight": pooled["Spreads"][choice["spread"]]},
                 "iv_basis": "Role-inferred IVs checked for Showdown legality.",
                 "optimality": "Generated from observed component marginals. This combination and its performance are not measured."}
        variants = species[name]["sets"]
        # Retain reviewed sets and avoid clones for trivial spread changes.
        duplicates = [other for other in variants if signature(other) == signature(build)]
        if duplicates and (tags == ["general"] or any(
                other["nature"] == build["nature"] and other["evs"] == build["evs"] and
                other["ivs"] == build["ivs"] and other["tera_Type"] == build["tera_Type"]
                for other in duplicates)):
            for other in duplicates:
                if (other["evidence_status"] == "dubers_usage_derived" and
                        other["nature"] == build["nature"] and other["evs"] == build["evs"] and
                        other["ivs"] == build["ivs"] and other["tera_Type"] == build["tera_Type"]):
                    other["scenario_tags"] = list(dict.fromkeys([*other["scenario_tags"], *tags]))
                    other["team_notes"] = notes_for(name, moves, item, ability, other["scenario_tags"])
            continue
        variants.append(build)
    missing = [name for name, record in species.items() if not record["sets"]]
    if missing:
        raise ValueError("No legal observed set found for: " + ", ".join(missing))
