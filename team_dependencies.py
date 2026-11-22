"""Set-specific team prerequisites, enforced independently of style prompts.

These checks establish that partners exist in a team, not that weather, terrain
or boosts will survive an opponent's actions. Optional damage/accuracy benefits
(such as rain for Urshifu or Landorus) are not treated as mandatory partners.
"""

from dataclasses import dataclass
from functools import lru_cache

from models import Candidate, PokemonSet, set_key


SELF_BOOSTS = frozenset({
    "Swords Dance", "Nasty Plot", "Calm Mind", "Dragon Dance", "Quiver Dance",
    "Bulk Up", "Iron Defense", "Belly Drum", "Cosmic Power", "Shell Smash",
    "Coil", "Agility", "Shift Gear", "Geomancy", "Meteor Beam", "Scale Shot",
})
SEEDS = {"Electric Seed": "Electric Terrain", "Psychic Seed": "Psychic Terrain",
         "Grassy Seed": "Grassy Terrain", "Misty Seed": "Misty Terrain"}
STAGE_ABILITIES = frozenset({
    "Speed Boost", "Soul-Heart", "Moody", "Intrepid Sword", "Dauntless Shield",
    "Defiant", "Competitive", "Stamina", "Moxie", "Grim Neigh", "Chilling Neigh",
    "As One (Spectrier)", "As One (Glastrier)",
})


@dataclass(frozen=True)
class SetDependencies:
    requires: frozenset[str]
    provides: frozenset[str]


def set_dependencies(pokemon: PokemonSet) -> SetDependencies:
    moves = set(pokemon.moves)
    ability, item = pokemon.ability, pokemon.held_Item
    tags = set(pokemon.scenario_tags)
    required, provided = set(), set()
    for capability, abilities, move in (
        ("sun", {"Drought", "Orichalcum Pulse"}, "Sunny Day"),
        ("rain", {"Drizzle"}, "Rain Dance"),
        ("snow", {"Snow Warning"}, "Snowscape"),
        ("Electric Terrain", {"Electric Surge", "Hadron Engine"}, "Electric Terrain"),
        ("Psychic Terrain", {"Psychic Surge"}, "Psychic Terrain"),
        ("Grassy Terrain", {"Grassy Surge"}, "Grassy Terrain"),
        ("Misty Terrain", {"Misty Surge"}, "Misty Terrain"),
    ):
        if ability in abilities or move in moves:
            provided.add(capability)
    if "Trick Room" in moves:
        provided.add("Trick Room")
    if item in SEEDS:
        required.add(SEEDS[item])
    if "Expanding Force" in moves:
        required.add("Psychic Terrain")
    if ability in {"Chlorophyll", "Solar Power"} or (moves & {"Solar Beam", "Solar Blade"} and item != "Power Herb"):
        required.add("sun")
    if ability == "Swift Swim" or ("Electro Shot" in moves and item != "Power Herb"):
        required.add("rain")
    if "Aurora Veil" in moves or ("Blizzard" in moves and "snow" in tags):
        required.add("snow")
    # Published samples inherit whole-team style tags, not per-member needs.
    if "external_room_setter" in tags or ("trick_room" in tags and "published_sample" not in tags):
        required.add("Trick Room")
    if pokemon.name == "Dondozo":
        provided.add("Dondozo")
        if "commander" in tags or "Order Up" in moves:
            required.add("Tatsugiri with Commander")
            provided.add("boosted ally")
    if pokemon.name.startswith("Tatsugiri") and ability == "Commander":
        provided.add("Tatsugiri with Commander")
        required.add("Dondozo")
    if ability == "Costar":
        required.add("boosted ally")
        if "commander" in tags:
            required.update({"Dondozo", "Tatsugiri with Commander"})
    if ability != "Costar" and (moves & SELF_BOOSTS or ability in STAGE_ABILITIES
                               or item in {*SEEDS, "Weakness Policy"}):
        provided.add("boosted ally")
    if "Beat Up" in moves:
        provided.add("Beat Up")
        if "beat_up" in tags:
            required.add("Rage Fist or Justified partner")
    if "Rage Fist" in moves or ability == "Justified":
        provided.add("Rage Fist or Justified partner")
    if "Rage Fist" in moves and "beat_up" in tags:
        required.add("Beat Up")
    return SetDependencies(frozenset(required), frozenset(provided))


@dataclass(frozen=True)
class DependencyState:
    provided: int = 0
    required: int = 0
    species: int = 0
    items: int = 0


class DependencyPlan:
    """Cache exact prerequisite completion searches over the remaining slots.

    Only providers of a missing prerequisite need exploring. Adding a provider
    also adds its own prerequisites, so chains and mutually dependent pairs are
    checked without assuming that several variants of one species can coexist.
    """

    def __init__(self, candidates: list[Candidate], *, unique_items: bool = False):
        profiles = [(candidate, set_dependencies(candidate.pokemon_sets[0])) for candidate in candidates]
        labels = sorted({label for _, profile in profiles for label in profile.requires | profile.provides})
        self.bits = {label: 1 << index for index, label in enumerate(labels)}
        bases = sorted({(c.base_species or c.pokemon_sets[0].name).strip().casefold() for c in candidates})
        base_bits = {name: 1 << index for index, name in enumerate(bases)}
        items = sorted({c.pokemon_sets[0].held_Item.strip().casefold() for c in candidates} - {""})
        item_bits = {name: 1 << index for index, name in enumerate(items)}
        self.options = {}
        for candidate, profile in profiles:
            base = (candidate.base_species or candidate.pokemon_sets[0].name).strip().casefold()
            item = candidate.pokemon_sets[0].held_Item.strip().casefold()
            self.options[set_key(candidate)] = DependencyState(
                sum(self.bits[label] for label in profile.provides),
                sum(self.bits[label] for label in profile.requires), base_bits[base],
                item_bits.get(item, 0) if unique_items else 0,
            )
        # Variants with identical prerequisite behavior are interchangeable here.
        options = set(self.options.values())
        self.providers = {bit: tuple(sorted((o for o in options if o.provided & bit),
                                           key=lambda o: (o.species, o.required, o.provided, o.items)))
                          for bit in self.bits.values()}
        self.can_complete = lru_cache(maxsize=32768)(self._can_complete)

    def add(self, state: DependencyState, candidate: Candidate) -> DependencyState:
        return self._merge(state, self.options[set_key(candidate)])

    @staticmethod
    def _merge(state: DependencyState, option: DependencyState) -> DependencyState:
        return DependencyState(state.provided | option.provided, state.required | option.required,
                               state.species | option.species, state.items | option.items)

    def missing(self, state: DependencyState) -> list[str]:
        return [label for label, bit in self.bits.items() if state.required & ~state.provided & bit]

    def _can_complete(self, state: DependencyState, slots: int) -> bool:
        missing = state.required & ~state.provided
        if not missing:
            return True
        if slots <= 0:
            return False
        choices = [tuple(option for option in self.providers[bit]
                         if not option.species & state.species and not option.items & state.items)
                   for bit in self.bits.values() if missing & bit]
        # Every completion must provide this missing capability; try the rarest.
        return any(self.can_complete(self._merge(state, option), slots - 1)
                   for option in min(choices, key=len))


def unmet_dependencies(team: list[Candidate]) -> list[str]:
    """Describe prerequisites missing from an already selected team."""
    plan = DependencyPlan(team)
    state = DependencyState()
    for member in team:
        state = plan.add(state, member)
    return plan.missing(state)
