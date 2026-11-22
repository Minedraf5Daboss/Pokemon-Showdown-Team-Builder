"""Explainable prompt interpretation and weak labels for doubles team mechanics.

This is a deterministic baseline, not a trained language model. The same feature
definitions annotate the corpus and guide search, making mistakes reviewable.
Training examples MUST retain label_method/review_status: heuristic labels are
not Smogon endorsements, matchup measurements, or calibrated probabilities.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Candidate


@dataclass(frozen=True)
class Style:
    aliases: tuple[str, ...]
    requirements: tuple[tuple[str, int], ...]
    excels_at: str
    watch_out_for: str
    kind: str = "legacy"
    sources: tuple[str, ...] = ()
    benefits: tuple[tuple[str, str, str], ...] = ()


# Requirements describe the mechanics that can be verified from selected sets.
def load_styles() -> dict[str, Style]:
    path = Path(__file__).parent / "data" / "team_archetypes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        name: Style(tuple(row["aliases"]), tuple(map(tuple, row["requirements"])),
                    row["excels_at"], row["watch_out_for"], row.get("kind", "legacy"),
                    tuple(row.get("sources", ())), tuple(map(tuple, row.get("benefits", ()))))
        for name, row in data.items()
    }


STYLES = load_styles()
LABEL_METHOD = "mechanics_rules_v2"

SETUP = {"Swords Dance", "Nasty Plot", "Calm Mind", "Dragon Dance", "Quiver Dance", "Shell Smash",
         "Bulk Up", "Iron Defense", "Coil", "Belly Drum", "Geomancy", "Shift Gear", "Cosmic Power",
         "Victory Dance", "No Retreat", "Clangorous Soul", "Acupressure", "Torch Song", "Tidy Up"}
RECOVERY = {"Recover", "Roost", "Slack Off", "Soft-Boiled", "Milk Drink", "Moonlight", "Morning Sun",
            "Shore Up", "Synthesis", "Rest", "Wish", "Life Dew", "Heal Pulse", "Pollen Puff", "Jungle Healing"}
SUPPORT = {"Fake Out", "Follow Me", "Rage Powder", "Helping Hand", "Wide Guard", "Quick Guard",
           "Taunt", "Encore", "Spore", "Sleep Powder", "Will-O-Wisp", "Snarl", "Parting Shot",
           "Coaching", "Haze", "Clear Smog", "Heal Pulse", "Pollen Puff", "Trick Room", "Tailwind",
           "Reflect", "Light Screen", "Aurora Veil", "Thunder Wave", "Icy Wind", "Electroweb"}
FIRE = {"Heat Wave", "Flare Blitz", "Fire Punch", "Flamethrower", "Fire Blast", "Eruption",
        "Overheat", "Sacred Fire", "Lava Plume", "Torch Song", "Fiery Dance", "V-create"}
WATER = {"Water Spout", "Origin Pulse", "Surf", "Hydro Pump", "Muddy Water", "Wave Crash",
         "Waterfall", "Liquidation", "Surging Strikes", "Scald", "Jet Punch", "Aqua Jet"}
SPEED = {"Trick Room", "Tailwind", "Icy Wind", "Electroweb", "Thunder Wave", "Nuzzle", "Glare", "Scary Face"}
RESIDUAL = {"Toxic", "Toxic Spikes", "Salt Cure", "Leech Seed", "Will-O-Wisp", "Infestation"}
PIVOT = {"U-turn", "Volt Switch", "Flip Turn", "Parting Shot", "Chilly Reception", "Teleport"}
DAMAGE_CONTROL = {"Fake Out", "Parting Shot", "Snarl", "Will-O-Wisp", "Wide Guard"}
REDIRECTION = {"Follow Me", "Rage Powder"}
PRIORITY_ATTACKS = {"Extreme Speed", "Sucker Punch", "Ice Shard", "Aqua Jet", "Jet Punch", "Bullet Punch",
                    "Mach Punch", "Vacuum Wave", "Thunderclap", "Shadow Sneak", "First Impression",
                    "Accelerock", "Quick Attack", "Water Shuriken"}
SPECIAL_SPREAD = {"Astral Barrage", "Heat Wave", "Water Spout", "Origin Pulse", "Muddy Water", "Surf",
                  "Blizzard", "Dazzling Gleam", "Eruption", "Hyper Voice", "Electroweb", "Icy Wind",
                  "Sludge Wave"}
GRASS_ATTACKS = {"Grassy Glide", "Wood Hammer", "Horn Leech", "Power Whip", "Seed Bomb", "Leaf Blade",
                 "Bullet Seed", "Leaf Storm", "Giga Drain", "Energy Ball", "Grass Knot"}
DANCE_MOVES = {"Quiver Dance", "Fiery Dance", "Dragon Dance", "Swords Dance", "Victory Dance",
               "Revelation Dance", "Aqua Step", "Petal Dance"}
GRAVITY_SLEEP_MOVES = {"Hypnosis", "Sleep Powder", "Sing", "Dark Void"}
WEATHER_SETTERS = {"sun_setter", "rain_setter", "sand_setter", "snow_setter"}


@lru_cache(maxsize=1)
def dex_snapshot() -> dict:
    path = Path(__file__).parent / "data" / "team_corpus" / "dex.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"species": {}}


@lru_cache(maxsize=1)
def physical_moves() -> frozenset[str]:
    path = Path(__file__).parent / "data" / "smogon" / "battle_dex.json"
    dex = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(dex["moves"][move] for move, category in dex["move_categories"].items()
                     if category == "Physical")


def set_features(pokemon: dict) -> Counter:
    """One count per member per property; no inference from species rarity."""
    moves = set(pokemon["moves"])
    ability, item = pokemon.get("ability", ""), pokemon.get("item", "")
    evs = pokemon.get("evs", {})
    species = pokemon["species"]
    species_info = dex_snapshot()["species"].get(species, {})
    base = species_info.get("baseStats", {})
    offensive = max(evs.get("atk", 0), evs.get("spa", 0)) >= 160
    bulky = evs.get("hp", 0) >= 180 or evs.get("def", 0) + evs.get("spd", 0) >= 200
    slow = offensive and base.get("spe", 999) <= 90 and evs.get("spe", 0) <= 32
    fast = offensive and (evs.get("spe", 0) >= 180 or item == "Choice Scarf")
    screens = {"Reflect", "Light Screen"} <= moves or "Aurora Veil" in moves
    support = bool(moves & SUPPORT) or ability in {"Intimidate", "Friend Guard"}
    physical_attack = evs.get("atk", 0) >= 160 and bool(moves & physical_moves())
    grassy_attack = bool(moves & GRASS_ATTACKS) or ("Ivy Cudgel" in moves and species == "Ogerpon")
    grounded = "Flying" not in species_info.get("types", ()) and ability != "Levitate" and item != "Air Balloon"
    sun_payoff = ability in {"Chlorophyll", "Solar Power", "Protosynthesis", "Orichalcum Pulse"} or bool(moves & FIRE)
    rain_payoff = ability in {"Swift Swim", "Rain Dish", "Dry Skin"} or bool(moves & (WATER | {"Thunder", "Hurricane"}))
    f = {
        "attacker": offensive, "bulky_attacker": offensive and bulky,
        "slow_attacker": slow, "fast_attacker": fast, "support": support,
        "defensive": bulky and (support or bool(moves & RECOVERY)),
        "sustain": bool(moves & RECOVERY) or ability == "Regenerator",
        "residual": bool(moves & RESIDUAL) or ability == "Toxic Debris",
        "setup": bool(moves & SETUP), "screens": screens,
        "speed_control": bool(moves & SPEED), "trick_room": "Trick Room" in moves,
        "tailwind": "Tailwind" in moves,
        "sun_setter": ability in {"Drought", "Orichalcum Pulse", "Desolate Land"} or "Sunny Day" in moves,
        "rain_setter": ability in {"Drizzle", "Primordial Sea"} or "Rain Dance" in moves,
        "sand_setter": ability == "Sand Stream" or "Sandstorm" in moves,
        "snow_setter": ability == "Snow Warning" or bool(moves & {"Snowscape", "Hail"}),
        "sun_payoff": sun_payoff, "rain_payoff": rain_payoff,
        "sand_payoff": ability in {"Sand Rush", "Sand Force"},
        "snow_payoff": ability == "Slush Rush" or bool(moves & {"Blizzard", "Aurora Veil"}),
        "perish_song": "Perish Song" in moves,
        "trapping": ability in {"Shadow Tag", "Arena Trap", "Magnet Pull"} or bool(moves & {"Mean Look", "Block", "Spider Web"}),
        "commander": ability == "Commander", "dondozo": species == "Dondozo",
        "psychic_terrain": ability == "Psychic Surge" or "Psychic Terrain" in moves,
        "psychic_payoff": bool(moves & {"Expanding Force", "Psyblade", "Psychic", "Psyshock"}),
        "electric_terrain": ability in {"Electric Surge", "Hadron Engine"} or "Electric Terrain" in moves,
        "electric_payoff": ability == "Quark Drive" or bool(moves & {"Electro Drift", "Rising Voltage", "Thunderbolt", "Wild Charge"}),
        "gravity": "Gravity" in moves,
        "gravity_payoff": bool(moves & {"Hypnosis", "Sleep Powder", "Precipice Blades", "Earthquake"}),
        "partner_manipulation": bool(moves & {"Instruct", "Decorate", "Skill Swap", "Entrainment"}),
        "beat_up": "Beat Up" in moves, "justified": ability == "Justified",
        "dancer": ability == "Dancer", "quiver_dance": "Quiver Dance" in moves,
        "slow_sun_attacker": slow and sun_payoff and ability != "Chlorophyll",
        "slow_rain_attacker": slow and bool(moves & (WATER | {"Thunder", "Hurricane"})) and ability != "Swift Swim",
        "pivot": bool(moves & PIVOT),
        "damage_control": bool(moves & DAMAGE_CONTROL) or ability in {"Intimidate", "Friend Guard"} or screens,
        "redirection": bool(moves & REDIRECTION),
        "protected_setup": offensive and bool(moves & SETUP) and not moves & REDIRECTION,
        "priority_attacker": bool(moves & PRIORITY_ATTACKS),
        "sword_of_ruin": ability == "Sword of Ruin",
        "extreme_speed_dragonite": species == "Dragonite" and "Extreme Speed" in moves,
        "physical_ruin_payoff": ability != "Sword of Ruin" and (physical_attack or "Body Press" in moves),
        "beads_of_ruin": ability == "Beads of Ruin",
        "special_ruin_payoff": ability != "Beads of Ruin" and evs.get("spa", 0) >= 160 and bool(moves & SPECIAL_SPREAD),
        "grassy_terrain": ability == "Grassy Surge" or "Grassy Terrain" in moves,
        "grassy_glide": grounded and "Grassy Glide" in moves,
        "grassy_payoff": grounded and grassy_attack,
        "coaching": "Coaching" in moves,
        "coaching_payoff": "Coaching" not in moves and (physical_attack or "Body Press" in moves),
        "beat_up_payoff": "Beat Up" not in moves and (ability == "Justified" or "Rage Fist" in moves),
        "costar": ability == "Costar",
        "ally_dance": ability != "Dancer" and bool(moves & DANCE_MOVES),
        "gravity_sleep_payoff": bool(moves & GRAVITY_SLEEP_MOVES),
        "blizzard": "Blizzard" in moves,
        "aurora_veil": "Aurora Veil" in moves,
    }
    return Counter({key: 1 for key, value in f.items() if value})


def speed_mode(pokemon: dict, features: Counter) -> str:
    if features["trick_room"]:
        return "trick_room_setter"
    base_speed = dex_snapshot()["species"].get(pokemon["species"], {}).get("baseStats", {}).get("spe", 999)
    if (base_speed <= 90 and pokemon["evs"]["spe"] <= 32
            and pokemon["nature"] in {"Quiet", "Brave", "Sassy", "Relaxed"}
            and pokemon["ivs"]["spe"] <= 1 and pokemon.get("item") != "Choice Scarf"
            and pokemon.get("ability") not in {"Swift Swim", "Chlorophyll", "Sand Rush", "Slush Rush"}):
        if features["slow_attacker"]:
            return "trick_room_attacker"
        if features["support"] or features["defensive"] or features["sustain"]:
            return "min_speed_support"
    return "standard"


UNUSUAL_PAIRS = (
    ("commander", "dondozo"), ("perish_song", "trapping"), ("partner_manipulation", "attacker"),
    ("beat_up", "beat_up_payoff"), ("dancer", "ally_dance"),
)


# Generic attack slots and team-level combinations do not identify set variants.
SET_ROLES = {feature for style in STYLES.values() for feature, _ in style.requirements}
SET_ROLES -= {"attacker", "unusual_engine"}
SET_ROLES.add("partner_manipulation")
SLOW_ROLES = {"slow_attacker", "slow_sun_attacker", "slow_rain_attacker"}


def archetype_set_roles(features: Counter, mode: str) -> set[str]:
    roles = features.keys() & SET_ROLES
    if mode == "standard":
        roles -= SLOW_ROLES
    return roles


def engine_features(counts: Counter) -> Counter:
    """Require paired mechanics before calling a team quirky."""
    result = counts.copy()
    result["unusual_engine"] = int(any(counts[first] and counts[second] for first, second in UNUSUAL_PAIRS))
    return result


def team_features(sets: list[dict]) -> Counter:
    return engine_features(sum((set_features(pokemon) for pokemon in sets), Counter()))


def candidate_features(candidate: Candidate) -> Counter:
    from export_sets import showdown_set

    # Search currently chooses species with their FIRST set, not a mix of moves
    # from every alternate set. Corpus entries preserve each observed set.
    return set_features(showdown_set(candidate.pokemon_sets[0]))


def matches(style: str, counts: Counter) -> bool:
    counts = engine_features(counts)
    return all(counts[feature] >= minimum for feature, minimum in STYLES[style].requirements)


def annotate_team(sets: list[dict]) -> dict:
    counts = team_features(sets)
    labels = [label for label in STYLES if matches(label, counts)]
    evidence = {label: {feature: counts[feature] for feature, _ in STYLES[label].requirements}
                for label in labels}
    # A team can have several engines. Broad playstyles are deliberately marked
    # medium confidence and require review, even when the rule is satisfied.
    broad = {"balance", "bulky_offense", "defensive", "stall", "hyper_offense", "quirky"}
    return {"labels": labels, "label_method": LABEL_METHOD,
            "review_status": "needs_human_review", "evidence": evidence,
            "confidence": {label: "medium" if label in broad else "high" for label in labels},
            "excels_at": [STYLES[label].excels_at for label in labels],
            "watch_out_for": [STYLES[label].watch_out_for for label in labels],
            "features": dict(sorted(counts.items())),
            "notes": ["Strengths describe intended mechanics, not measured matchup win rates."]}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_-]", " ", text.casefold())).strip()


@dataclass(frozen=True)
class TeamIntent:
    requested: tuple[str, ...] = ()
    excluded: tuple[str, ...] = ()
    original_prompt: str = ""


def parse_team_prompt(prompt: str | None) -> TeamIntent:
    """Parse known phrases, including scoped negation; refuse unknown-only input.

    A future classifier can produce this object, but its output must still be
    checked against STYLES and the candidate mechanics before search.
    """
    if prompt is None or not prompt.strip():
        return TeamIntent()
    if len(prompt) > 2000:
        raise ValueError("Team style prompt must be at most 2000 characters")
    text = normalize_text(prompt)
    phrases = sorted(((alias, label) for label, style in STYLES.items()
                      for alias in {normalize_text(label), *(normalize_text(a) for a in style.aliases)}),
                     key=lambda pair: (-len(pair[0]), pair[0]))
    found, occupied = [], set()
    for alias, label in phrases:
        for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", text):
            span = set(range(match.start(), match.end()))
            if occupied & span:
                continue
            occupied |= span
            found.append((match.start(), match.end(), label))
    found.sort()
    requested, excluded = [], []
    negative = False
    last_end = 0
    for start, end, label in found:
        between = text[last_end:start]
        # Commas/conjunctions continue a negative list. Contrast or a fresh
        # request starts a positive clause: "no rain or sun, but trick room".
        if re.search(r"\b(?:but|instead|want|with|prefer|using)\b|[.;]", between):
            negative = False
        if re.search(r"\b(?:no|not|without|avoid|exclude|excluding|don't|do not)\b", between):
            negative = True
        (excluded if negative else requested).append(label)
        last_end = end
    if not found:
        raise ValueError("No recognized team style. Try sun, rain, hyper offense, defensive, "
                         "Trick Room, setup, quirky, or Perish Trap; use --list-styles for more.")
    if set(requested) & set(excluded):
        raise ValueError("A team style cannot be both requested and excluded")
    if "hyper_offense" in requested and set(requested) & {"defensive", "stall", "hard_trick_room"}:
        raise ValueError("Hyper offense conflicts with defensive, stall, or hard Trick Room; choose a main style")
    return TeamIntent(tuple(dict.fromkeys(requested)), tuple(dict.fromkeys(excluded)), prompt)


class StylePlan:
    """A bounded team-level potential used as a telescoping search contribution."""

    def __init__(self, intent: TeamIntent):
        self.intent = intent
        self.required_counts = Counter()
        for label in intent.requested:
            self.required_counts |= Counter(dict(STYLES[label].requirements))
        self.relevant = {key for label in (*intent.requested, *intent.excluded)
                         for key, _ in STYLES[label].requirements}
        if "unusual_engine" in self.relevant:
            self.relevant.update(key for pair in UNUSUAL_PAIRS for key in pair)
        self.requested_weather = WEATHER_SETTERS & {
            key for label in intent.requested for key, _ in STYLES[label].requirements
        }
        self.forbidden_weather = WEATHER_SETTERS & {label + "_setter" for label in intent.excluded}
        if self.requested_weather:
            self.forbidden_weather |= WEATHER_SETTERS - self.requested_weather

    def allowed_candidate(self, features: Counter) -> bool:
        # A single-weather request cannot accidentally acquire another setter.
        return not any(features[setter] for setter in self.forbidden_weather)

    def relevant_features(self, features: Counter) -> Counter:
        return Counter({key: value for key, value in features.items() if key in self.relevant})

    def can_complete(self, counts: Counter, remaining: int) -> bool:
        if remaining == 0:
            return self.complete(counts)
        return all(counts[key] + remaining >= minimum for key, minimum in self.required_counts.items())

    def potential(self, counts: Counter) -> float:
        counts = engine_features(counts)
        total = 0.0
        for label in self.intent.requested:
            needs = STYLES[label].requirements
            total += sum(min(counts[key] / n, 1) for key, n in needs) / len(needs)
        total -= sum(matches(label, counts) for label in self.intent.excluded)
        return total

    def complete(self, counts: Counter) -> bool:
        return (all(matches(label, counts) for label in self.intent.requested)
                and not any(matches(label, counts) for label in self.intent.excluded))

    def check_pool(self, features: list[Counter]) -> None:
        available = engine_features(sum(features, Counter()))
        missing = []
        for label in self.intent.requested:
            unmet = [f"{key.replace('_', ' ')} ({available[key]}/{n})"
                     for key, n in STYLES[label].requirements if available[key] < n]
            if unmet:
                missing.append(label.replace("_", " ") + ": " + ", ".join(unmet))
        if missing:
            raise ValueError("Current eligible pool cannot support " + "; ".join(missing)
                             + ". Add appropriate sets to the candidate pool or change the style.")
