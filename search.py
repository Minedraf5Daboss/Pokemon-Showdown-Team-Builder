"""Build teams by expanding, deduplicating and trimming a beam of partial teams."""

from collections.abc import Iterable, Mapping
from collections import Counter
from dataclasses import dataclass, field
from heapq import nsmallest
from math import isfinite

from exceptions import (
    PoolTooSmallError, RequiredAlsoExcludedError, RequiredNotInPoolError, SearchError,
)
from models import Candidate, DEFAULT_TARGET_ROLES, ROLE_VOCABULARY, parse_restricted_mons, set_key, set_variants
from scoring import pool_usage_bounds, score_addition
from team_output import FinishedTeam
from team_styles import StylePlan, TeamIntent, candidate_features, parse_team_prompt
from team_dependencies import DependencyPlan, DependencyState


DEFAULT_WEIGHTS = {"usage": 0.3, "synergy": 0.7, "role": 0.5, "penalty": 0.2}
DEFAULT_BEAM_WIDTH = 100
DEFAULT_SEED_WEIGHT = 2.0
COMPONENTS = ("usage", "synergy", "role", "penalty", "total")


def name_of(candidate: Candidate) -> str:
    return candidate.pokemon_sets[0].name


def _pokemon_key(candidate: Candidate) -> str:
    return (candidate.base_species or name_of(candidate)).strip().casefold()


def _item_key(candidate: Candidate) -> str:
    # Search uses the first set of each candidate. An empty item means no item.
    return candidate.pokemon_sets[0].held_Item.strip().casefold()


@dataclass
class partialTeam:
    # Each Candidate already contains its sets, EVs/IVs, roles and teammate data.
    # Score is a running sum of additions, not a recomputed whole-team score.
    # breakdowns[i] records the weighted contribution of members[i].
    members: list[Candidate] = field(default_factory=list)
    score: float = 0.0
    breakdowns: list[dict[str, float]] = field(default_factory=list)
    style_counts: Counter = field(default_factory=Counter)
    dependencies: DependencyState = field(default_factory=DependencyState)


def beam_search(
    pool: Iterable[Candidate],
    *,
    required_pokemon: str | None = None,
    required_set_id: str | None = None,
    excluded_pokemon: Iterable[str] | str = (),
    team_size: int = 6,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    top_k: int = 3,
    weights: Mapping[str, float] | None = None,
    target_roles: Iterable[str] = DEFAULT_TARGET_ROLES,
    team_prompt: str | None = None,
    style_intent: TeamIntent | None = None,
    style_weight: float = 3.0,
    seed_weight: float = DEFAULT_SEED_WEIGHT,
    unique_items: bool = False,
    required_members: Mapping[str, str | None] | None = None,
    preference_score=None,
    unusual_species: frozenset[str] = frozenset(),
    novelty_budget: int | None = None,
) -> list[FinishedTeam]:
    """Return up to top_k distinct teams, best first, without changing the pool.

    Each set variant is a separate choice; species usage is shared across them.
    The legacy required_pokemon argument seeds all its eligible variants at
    score zero. Otherwise the search starts with an empty team.
    Explicit required_members are placed before optional members so the beam
    can make room for their partner dependencies from the first step.
    Its pair compatibility receives seed_weight votes in each synergy average;
    other members receive one vote each. Weight 1 restores equal weighting.
    Equal scores use alphabetical addition order as a deterministic tie-break.
    Usage bounds come from the original pool and stay fixed during the search.
    Each team has distinct base species and satisfies its selected sets' partner
    prerequisites. Duplicate held items are legal; unique_items opts into the
    former builder policy, independently of the Doubles Ubers rules.
    required_set_id optionally pins one specific build of required_pokemon.
    This limited-width search does not guarantee the globally best team.
    A team_prompt or a validated style_intent adds a separate style component
    and requires the returned teams to contain the requested mechanics.
    """
    # 1. Check the request before attempting to build anything.
    for label, value in (("team_size", team_size), ("beam_width", beam_width), ("top_k", top_k)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{label} must be a positive integer")
    if team_size > 6:
        raise ValueError("team_size cannot exceed six Pokemon")
    if type(unique_items) is not bool:
        raise ValueError("unique_items must be a boolean")
    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    if (isinstance(seed_weight, bool) or not isinstance(seed_weight, (int, float))
            or not isfinite(seed_weight) or seed_weight < 1):
        raise ValueError("seed_weight must be a finite number greater than or equal to 1")
    if (isinstance(style_weight, bool) or not isinstance(style_weight, (int, float))
            or not isfinite(style_weight) or style_weight < 0):
        raise ValueError("style_weight must be a finite nonnegative number")
    if team_prompt is not None and style_intent is not None:
        raise ValueError("Provide a team prompt or a style intent")
    intent = style_intent if style_intent is not None else parse_team_prompt(team_prompt)
    plan = StylePlan(intent) if intent.requested or intent.excluded else None
    components = COMPONENTS if plan is None else (*COMPONENTS[:-1], "style", "total")
    for component in COMPONENTS[:-1]:
        value = weights.get(component)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ValueError(f"{component} weight must be finite")
        if component != "usage" and value < 0:
            raise ValueError(f"{component} weight must be nonnegative")
    target_roles = tuple(target_roles)
    if set(target_roles) - ROLE_VOCABULARY:
        raise ValueError("target_roles contains an unknown role")
    pool = list(pool)
    if any(not candidate.pokemon_sets or not name_of(candidate).strip() for candidate in pool):
        raise ValueError("Every candidate must have a named Pokemon set")
    by_name = {name_of(candidate).strip().casefold(): candidate for candidate in pool}
    if len(by_name) != len(pool):
        raise ValueError("Pool contains duplicate Pokemon names")
    required = {name.strip().casefold(): pin for name, pin in (required_members or {}).items()}
    if required_pokemon:
        key = required_pokemon.strip().casefold()
        if key in required and required[key] != required_set_id:
            raise ValueError("Conflicting required set pins")
        required[key] = required_set_id
    if len(required) > team_size:
        raise ValueError("Required members exceed team size")
    if set(required) - by_name.keys():
        labels = {n.strip().casefold(): n for n in (required_members or {})}
        if required_pokemon:
            labels[required_pokemon.strip().casefold()] = required_pokemon.strip()
        raise RequiredNotInPoolError([labels[n] for n in sorted(set(required) - by_name.keys())])
    if len({_pokemon_key(by_name[n]) for n in required}) != len(required):
        raise ValueError("Required members violate species clause")
    if novelty_budget is not None and (type(novelty_budget) is not int or not 0 <= novelty_budget <= team_size):
        raise ValueError("Novelty budget must be an integer within team size")

    required_label = required_pokemon.strip() if required_pokemon is not None else ""
    required_name = required_label.casefold()
    seed = None
    if required_name:
        if required_name not in by_name:
            raise RequiredNotInPoolError([required_label])
        seed = by_name[required_name]
    restricted_mons = (excluded_pokemon if isinstance(excluded_pokemon, str)
                       else ",".join(excluded_pokemon))
    excluded = parse_restricted_mons(restricted_mons, pool)
    if set(required) & {n.casefold() for n in excluded}:
        raise RequiredAlsoExcludedError([name_of(by_name[n]) for n in sorted(set(required) & {n.casefold() for n in excluded})])
    if seed is not None and name_of(seed) in excluded:
        raise RequiredAlsoExcludedError([name_of(seed)])
    if required_set_id is not None and seed is None:
        raise ValueError("required_set_id requires required_pokemon")
    eligible = sorted(set_variants([candidate for candidate in pool if name_of(candidate) not in excluded]),
                      key=lambda candidate: (name_of(candidate), candidate.pokemon_sets[0].set_id))
    eligible = [c for c in eligible if name_of(c).casefold() not in required
                or required[name_of(c).casefold()] is None
                or c.pokemon_sets[0].set_id == required[name_of(c).casefold()]]
    seeds = [candidate for candidate in eligible if name_of(candidate).casefold() == required_name]
    if required_set_id is not None:
        seeds = [candidate for candidate in seeds if candidate.pokemon_sets[0].set_id == required_set_id]
        if not seeds:
            raise ValueError("Required set ID not available for the required Pokemon")
    features = {}
    if plan:
        features = {set_key(candidate): candidate_features(candidate) for candidate in eligible}
        eligible = [candidate for candidate in eligible if plan.allowed_candidate(features[set_key(candidate)])]
        seeds = [candidate for candidate in seeds if candidate in eligible]
        if seed and not seeds:
            raise ValueError("Required Pokemon conflicts with the requested weather or excluded style")
        by_species = {}
        for candidate in eligible:
            species = _pokemon_key(candidate)
            by_species.setdefault(species, Counter())
            by_species[species] |= features[set_key(candidate)]
        plan.check_pool(list(by_species.values()))
        features = {key: plan.relevant_features(counts) for key, counts in features.items()}
    available = len({_pokemon_key(candidate) for candidate in eligible})
    for name in required:
        if not any(name_of(c).casefold() == name for c in eligible):
            raise ValueError(f"Required member/set {name} conflicts with engine or available pool")
    if available < team_size:
        raise PoolTooSmallError(available=available, required=team_size)
    dependencies = DependencyPlan(eligible, unique_items=unique_items)
    if seed:
        feasible = [variant for variant in seeds
                    if dependencies.can_complete(dependencies.add(DependencyState(), variant), team_size - 1)]
        if not feasible:
            missing = sorted({label for variant in seeds
                              for label in dependencies.missing(dependencies.add(DependencyState(), variant))})
            raise ValueError("Required set dependencies cannot be satisfied within the team size "
                             "and eligible pool: " + ", ".join(missing))
        seeds = feasible
    usage_min, usage_max = pool_usage_bounds(pool)
    options = []
    for candidate in eligible:
        key = set_key(candidate)
        style_key = tuple(sorted(features.get(key, {}).items()))
        roles_key = tuple(sorted(set(candidate.role_tags)))
        options.append((candidate, key, _pokemon_key(candidate), _item_key(candidate), style_key, roles_key))
    required_options = [option for option in options if option[1][0] in required]
    preferences = {}
    if preference_score is not None:
        components = (*components[:-1], "preference", "total")
        preferences = {set_key(candidate): preference_score(candidate) for candidate in eligible}

    # 2. Seed: one empty team, or the required Pokemon with no score of its own.
    if seed is None:
        beam = [partialTeam()]
    else:
        zero_breakdown = {component: 0.0 for component in components}
        beam = [partialTeam(members=[variant], breakdowns=[zero_breakdown.copy()],
                            style_counts=features.get(set_key(variant), Counter()),
                            dependencies=dependencies.add(DependencyState(), variant)) for variant in seeds]

    # 3. Expand one slot at a time. Every team in a beam has the same size.
    while len(beam[0].members) < team_size:
        best_by_members = {}
        for team in beam:
            member_names = {_pokemon_key(member) for member in team.members}
            member_items = {_item_key(member) for member in team.members} - {""}
            member_keys = frozenset(set_key(member) for member in team.members)
            selected_names = {key[0] for key in member_keys}
            missing = required.keys() - selected_names
            current_novelty = len(selected_names & unusual_species)
            remaining = team_size - len(team.members) - 1
            style_base = plan.potential(team.style_counts) if plan else 0
            style_cache = {}
            score_cache = {}
            for option in required_options if missing else options:
                candidate, candidate_key, species_key, item_key, style_key, roles_key = option
                if species_key in member_names or (unique_items and item_key in member_items):
                    continue
                name_key = candidate_key[0]
                if missing and name_key not in missing:
                    continue
                if novelty_budget is not None:
                    count = current_novelty + (name_key in unusual_species)
                    if count > novelty_budget or count + remaining < novelty_budget:
                        continue
                dependency_state = dependencies.add(team.dependencies, candidate)
                if not dependencies.can_complete(dependency_state, remaining):
                    continue
                counts = team.style_counts
                if plan:
                    # A team-level potential prevents repeated setters from
                    # receiving the same full reward in every slot.
                    if style_key not in style_cache:
                        counts = team.style_counts + features[candidate_key]
                        style_cache[style_key] = (
                            counts, style_weight * (plan.potential(counts) - style_base),
                            plan.can_complete(counts, remaining),
                        )
                    counts, style_score, feasible = style_cache[style_key]
                    if not feasible:
                        continue
                # Set variants share usage and teammate frequencies. Reuse the
                # base score when the variant's roles also match.
                score_key = (name_key, roles_key)
                if score_key not in score_cache:
                    score_cache[score_key] = score_addition(
                        candidate, team.members, weights, usage_min, usage_max, target_roles,
                        required_pokemon=required_label or None, seed_weight=seed_weight,
                    )
                addition = score_cache[score_key].copy()
                if plan:
                    addition["style"] = style_score
                    addition["total"] += addition["style"]
                if preference_score is not None:
                    addition["preference"] = preferences[candidate_key]
                    addition["total"] += addition["preference"]
                expanded = partialTeam(
                    members=team.members + [candidate],
                    score=team.score + addition["total"],
                    breakdowns=team.breakdowns + [addition],
                    style_counts=counts,
                    dependencies=dependency_state,
                )
                # 4. Merge orders of the same team BEFORE they use beam slots.
                # Future additions depend on membership; keep the best score so far.
                key = member_keys | {candidate_key}
                previous = best_by_members.get(key)
                if previous is None or _rank_key(expanded) < _rank_key(previous):
                    best_by_members[key] = expanded

        # 5. Keep only the best beam_width partial teams for the next slot.
        if not best_by_members:
            raise ValueError("No team satisfying the set dependencies, species clause, requested "
                             "styles and item policy survived the beam. "
                             "Increase beam_width, expand the pool, or simplify the style request.")
        beam = nsmallest(beam_width, best_by_members.values(), key=_rank_key)

    # 6. Finish: sum the saved contributions for the printer and explanation layer.
    finished = []
    for team in beam[:top_k]:
        if set(required) - {name_of(c).casefold() for c in team.members}:
            raise ValueError("Required members missing from finished team")
        if novelty_budget is not None and sum(name_of(c).casefold() in unusual_species for c in team.members) != novelty_budget:
            raise ValueError("Required members conflict with the novelty budget")
        if dependencies.missing(team.dependencies):
            raise ValueError("Incomplete set dependencies: " + ", ".join(dependencies.missing(team.dependencies)))
        if plan and not plan.complete(team.style_counts):
            raise ValueError("The requested team size cannot support the requested styles")
        breakdown = {component: sum(step[component] for step in team.breakdowns)
                     for component in components[:-1]}
        breakdown["total"] = team.score
        finished.append(FinishedTeam(
            members=team.members, breakdown=breakdown, addition_breakdowns=team.breakdowns,
            requested_styles=list(intent.requested), excluded_styles=list(intent.excluded),
        ))
    return finished


def _rank_key(team: partialTeam) -> tuple:
    # sorted() puts smaller keys first: a negative score puts the highest score first.
    return -team.score, tuple(set_key(member) for member in team.members)
