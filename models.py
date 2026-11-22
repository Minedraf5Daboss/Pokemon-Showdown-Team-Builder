from copy import deepcopy
from dataclasses import dataclass, field, replace
from math import isfinite
import json
from pathlib import Path

FORMAT_ID = "gen9doublesubers"
ROLE_VOCABULARY = frozenset({
    "Support", "Speed Control", "Physical Sweeper", "Special Sweeper",
    "Setup", "Weather Setter",
})
DEFAULT_TARGET_ROLES = (
    "Support", "Speed Control", "Physical Sweeper", "Special Sweeper", "Setup",
)


def _validate_stats(values, maximum, label, total=None):
    if any(type(value) is not int or not 0 <= value <= maximum for value in values):
        raise ValueError(f"{label} must be integers between 0 and {maximum}")
    if total is not None and sum(values) > total:
        raise ValueError(f"Total {label} must not exceed {total}")


@dataclass
class PokemonSet:
    name: str
    ability: str
    tera_Type: str
    held_Item: str
    move1: str
    move2: str
    move3: str
    move4: str
    nature: str
    @dataclass
    class EV:
        hp_EV: int
        atk_EV: int
        def_EV: int
        spa_EV: int
        spd_EV: int
        spe_EV: int

        def __post_init__(self):
            _validate_stats(vars(self).values(), 252, "EVs", 510)

    @dataclass
    class IV:
        hp_IV: int = 31
        atk_IV: int = 31
        def_IV: int = 31
        spa_IV: int = 31
        spd_IV: int = 31
        spe_IV: int = 31

        def __post_init__(self):
            _validate_stats(vars(self).values(), 31, "IVs")

    evs: EV | None = None
    ivs: IV | None = None
    level: int = 100
    set_id: str = ""
    label: str = ""
    scenario_tags: list[str] = field(default_factory=list)
    role_tags: list[str] = field(default_factory=list)
    evidence_status: str = "unreviewed"
    source_urls: list[str] = field(default_factory=list)
    team_notes: list[str] = field(default_factory=list)

    @property
    def moves(self) -> tuple[str, ...]:
        """One to four moves; Ditto legitimately has only Transform."""
        return tuple(move for move in (self.move1, self.move2, self.move3, self.move4) if move)

@dataclass
class Candidate:
    pokemon_sets: list[PokemonSet] = field(default_factory=list)
    usage: float = 0.0
    # All-rating pooled team-side frequency, on a 0.0-1.0 scale.
    role_tags: list[str] = field(default_factory=list)
    teammate_values: dict[str, float] = field(default_factory=dict)
    usage_appearances: int = 0
    evidence_status: str = "unreviewed"
    team_notes: list[str] = field(default_factory=list)
    base_species: str = ""
    usage_reports: list[dict] = field(default_factory=list)
    selection: str = ""


def set_key(candidate: Candidate) -> tuple[str, str]:
    """Identity for a selected build, separate from the species-clause key."""
    pokemon = candidate.pokemon_sets[0]
    return pokemon.name.strip().casefold(), pokemon.set_id


def set_variants(pool: list[Candidate]) -> list[Candidate]:
    """Expand species records into search choices with set-specific roles/notes.

    Species usage/co-occurrence is shared evidence, never an estimated set rate.
    The returned choices own independent sets so callers cannot mutate the pool.
    """
    choices = []
    for candidate in pool:
        ids = [pokemon.set_id for pokemon in candidate.pokemon_sets]
        if len(ids) > 1 and (not all(ids) or len(set(ids)) != len(ids)):
            raise ValueError("Multiple sets require distinct nonempty set IDs")
        for pokemon in candidate.pokemon_sets:
            choices.append(replace(
                candidate, pokemon_sets=[deepcopy(pokemon)],
                role_tags=list(pokemon.role_tags or candidate.role_tags),
                evidence_status=pokemon.evidence_status if pokemon.set_id else candidate.evidence_status,
                team_notes=list(pokemon.team_notes if pokemon.set_id else candidate.team_notes),
            ))
    return choices


def validate_pool(pool: list[Candidate]) -> None:
    """Reject incomplete production sets, invalid references, roles or frequencies."""
    if any(not candidate.pokemon_sets or not candidate.pokemon_sets[0].name for candidate in pool):
        raise ValueError("Every candidate must have a named Pokemon set")
    names = [candidate.pokemon_sets[0].name for candidate in pool]
    known_names = set(names)
    if len(known_names) != len(names):
        raise ValueError("Pool contains duplicate Pokemon names")
    for candidate, name in zip(pool, names):
        invalid_names = set(candidate.teammate_values) - known_names
        if invalid_names:
            raise ValueError(f"{name}: unknown teammate names: {', '.join(sorted(invalid_names))}")
        if name in candidate.teammate_values:
            raise ValueError(f"{name}: a candidate cannot be its own teammate")
        invalid_roles = set(candidate.role_tags) - ROLE_VOCABULARY
        if invalid_roles:
            raise ValueError(f"{name}: unknown role tags: {', '.join(sorted(invalid_roles))}")
        if not isfinite(candidate.usage) or not 0 <= candidate.usage <= 1:
            raise ValueError(f"{name}: usage must be between 0 and 1")
        if any(not isfinite(value) or not 0 <= value <= 1
               for value in candidate.teammate_values.values()):
            raise ValueError(f"{name}: teammate frequencies must be between 0 and 1")
        set_ids = [pokemon.set_id for pokemon in candidate.pokemon_sets]
        if len(set_ids) != len(set(set_ids)):
            raise ValueError(f"{name}: duplicate set IDs")
        for pokemon in candidate.pokemon_sets:
            if pokemon.name != name:
                raise ValueError(f"{name}: all sets must describe the same species")
            if not all(getattr(pokemon, field) for field in
                       ("ability", "tera_Type", "nature", "move1")) or not isinstance(pokemon.held_Item, str):
                raise ValueError(f"{name}: production sets must include all battle fields")
            if len(pokemon.moves) != len(set(pokemon.moves)):
                raise ValueError(f"{name}: duplicate moves")
            if set(pokemon.role_tags) - ROLE_VOCABULARY:
                raise ValueError(f"{name}: unknown set role tags")
            if type(pokemon.level) is not int or not 1 <= pokemon.level <= 100:
                raise ValueError(f"{name}: invalid level")
            if pokemon.evs is None or pokemon.ivs is None:
                raise ValueError(f"{name}: production sets must include explicit EVs and IVs")
            _validate_stats(vars(pokemon.evs).values(), 252, "EVs", 510)
            _validate_stats(vars(pokemon.ivs).values(), 31, "IVs")


def build_pool() -> list[Candidate]:
    """Return a validated independent copy of the production pool for a search."""
    pool = deepcopy(teampool)
    validate_pool(pool)
    return pool


def parse_restricted_mons(restricted_mons: str, pool: list[Candidate]) -> set[str]:
    """Turn comma-separated exclusions into pool names; reject misspellings.

    Restricted means user-excluded here, not a format's legendary limit.
    Input stays in the runner, so importing this module never prompts.
    """
    known_names = {
        candidate.pokemon_sets[0].name.casefold(): candidate.pokemon_sets[0].name
        for candidate in pool
    }
    requested = {name.strip().casefold() for name in restricted_mons.split(",")
                 if name.strip()}
    unknown = requested - known_names.keys()
    if unknown:
        raise ValueError(f"Restricted Pokemon not in pool: {', '.join(sorted(unknown))}")
    return {known_names[name] for name in requested}


def load_pool(path: Path | None = None) -> list[Candidate]:
    """Load the frozen production catalog without a network or toy-data fallback."""
    path = path or Path(__file__).parent / "data" / "smogon" / "candidate_pool.json"
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if document.get("schema_version") != 2 or document.get("format") != FORMAT_ID:
        raise ValueError("Unsupported candidate catalog schema/format")
    pool = []
    for record in document["candidates"]:
        if not record["sets"]:
            raise ValueError(f"{record['name']}: every candidate needs a set")
        sets = []
        for selected in record["sets"]:
            selected_moves = selected["moves"]
            if (not isinstance(selected_moves, list)
                    or not 1 <= len(selected_moves) <= 4
                    or any(not isinstance(move, str) or not move for move in selected_moves)):
                raise ValueError("A Pokemon set must have one to four moves")
            moves = selected_moves + [""] * (4 - len(selected_moves))
            sets.append(PokemonSet(
                record["name"], selected["ability"], selected["tera_Type"], selected["held_Item"],
                moves[0], moves[1], moves[2], moves[3], selected["nature"],
                evs=PokemonSet.EV(**{key + "_EV": value for key, value in selected["evs"].items()}),
                ivs=PokemonSet.IV(**{key + "_IV": value for key, value in selected["ivs"].items()}),
                level=selected["level"], set_id=selected["set_id"], label=selected["label"],
                scenario_tags=selected["scenario_tags"], role_tags=selected["role_tags"],
                evidence_status=selected["evidence_status"], source_urls=selected["source_urls"],
                team_notes=selected["team_notes"],
            ))
        pool.append(Candidate(
            pokemon_sets=sets, usage=record["usage"], role_tags=list(sets[0].role_tags),
            teammate_values=record["teammate_values"], usage_appearances=record["usage_appearances"],
            evidence_status=sets[0].evidence_status, team_notes=list(sets[0].team_notes),
            base_species=record["base_species"], usage_reports=record["usage_reports"], selection=record["selection"],
        ))
    validate_pool(pool)
    return pool


# Frozen, sourced production data; regenerate with refresh_pool.py.
teampool = load_pool()
