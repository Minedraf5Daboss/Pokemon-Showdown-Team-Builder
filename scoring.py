"""Pure helpers for scoring additions to a team.

Compute pool_usage_bounds(pool) once, then reuse the bounds for every score.

These are ranking heuristics: teammate frequency is not strategic synergy,
and role overlap alone is not evidence of a bad pairing. This module does
not model damage, speed matchups, weather dependencies or threat coverage.
"""

from collections.abc import Iterable, Mapping, Sequence
from math import isfinite
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import Candidate


def normalize(value: float, minimum: float, maximum: float) -> float:
    """Map a value to [0, 1]; equal bounds return 0.0."""
    if minimum > maximum:
        raise ValueError("minimum must not exceed maximum")
    if minimum == maximum:
        return 0.0
    return float(max(0.0, min(1.0, (value - minimum) / (maximum - minimum))))


def pool_usage_bounds(pool: Iterable["Candidate"]) -> tuple[float, float]:
    """Compute usage bounds once across the entire nonempty candidate pool."""
    usages = [candidate.usage for candidate in pool]
    if not usages:
        raise ValueError("cannot compute usage bounds for an empty pool")
    return min(usages), max(usages)


def usage_component(
    candidate: "Candidate", usage_min: float, usage_max: float
) -> float:
    """Normalize usage using the precomputed pool bounds."""
    return normalize(candidate.usage, usage_min, usage_max)


def synergy_component(
    candidate: "Candidate", team_so_far: Sequence["Candidate"],
    *, required_pokemon: str | None = None, seed_weight: float = 1.0,
) -> float:
    """Average usage-weighted pair scores; empty teams score 0.0.

    Each direction is weighted by its source Pokemon's raw usage, giving the
    more-used Pokemon's teammate score greater influence. See pair_synergy
    for missing-data and equal-zero-usage behavior.
    The required Pokemon's pair gets seed_weight votes in the team average;
    each other member gets one. With seed_weight=1, this is the ordinary mean.
    """
    if (isinstance(seed_weight, bool) or not isinstance(seed_weight, (int, float))
            or not isfinite(seed_weight) or seed_weight < 1):
        raise ValueError("seed_weight must be a finite number greater than or equal to 1")
    if not team_so_far:
        return 0.0
    required_name = required_pokemon.strip().casefold() if required_pokemon else None
    weighted_sum = 0.0
    total_weight = 0.0
    for member in team_so_far:
        member_name = member.pokemon_sets[0].name.casefold() if member.pokemon_sets else None
        weight = seed_weight if required_name is not None and member_name == required_name else 1.0
        weighted_sum += weight * pair_synergy(candidate, member)
        total_weight += weight
    return float(weighted_sum / total_weight)


def pair_synergy(candidate: "Candidate", teammate: "Candidate") -> float:
    """Blend both teammate directions, weighted by their source usage.

    For A and B: (usage_A * score_A_to_B + usage_B * score_B_to_A)
                / (usage_A + usage_B).

    Uses raw usage, not pool-normalized usage or the overall usage weight.
    If both usages are zero, take the arithmetic mean. A missing direction
    uses the available direction alone; two missing directions score zero.
    An explicit zero is observed data and keeps its weight. Names come from
    each candidate's first set. Negative teammate scores keep their sign.
    """
    candidate_name = candidate.pokemon_sets[0].name if candidate.pokemon_sets else None
    teammate_name = teammate.pokemon_sets[0].name if teammate.pokemon_sets else None
    forward = candidate.teammate_values.get(teammate_name)
    reverse = teammate.teammate_values.get(candidate_name)
    if forward is None:
        return float(reverse) if reverse is not None else 0.0
    if reverse is None:
        return float(forward)
    total_usage = candidate.usage + teammate.usage
    if total_usage == 0:
        return float((forward + reverse) / 2)
    return float((candidate.usage * forward + teammate.usage * reverse) / total_usage)


def role_component(
    candidate: "Candidate",
    team_so_far: Sequence["Candidate"],
    target_roles: Iterable[str],
) -> float:
    """Reward each distinct target role the candidate adds to the team."""
    covered_roles = {role for member in team_so_far for role in member.role_tags}
    new_roles = (set(candidate.role_tags) & set(target_roles)) - covered_roles
    return float(len(new_roles))


def penalty_component(
    candidate: "Candidate", team_so_far: Sequence["Candidate"]
) -> float:
    """Subtract one per shared role per member, counting repeated tags once."""
    candidate_roles = set(candidate.role_tags)
    return -float(sum(
        len(candidate_roles & set(member.role_tags)) for member in team_so_far
    ))


def score_addition(
    candidate: "Candidate",
    team_so_far: Sequence["Candidate"],
    weights: Mapping[str, float],
    usage_min: float,
    usage_max: float,
    target_roles: Iterable[str],
    *,
    required_pokemon: str | None = None,
    seed_weight: float = 1.0,
) -> dict[str, float]:
    """Return weighted components and total without changing the inputs.

    Weights must contain usage, synergy, role, and penalty. Use a positive
    penalty weight to preserve the penalty component's negative sign.
    Optional seed weighting applies only to the synergy average.
    """
    components = {
        "usage": float(weights["usage"] * usage_component(candidate, usage_min, usage_max)),
        "synergy": float(weights["synergy"] * synergy_component(
            candidate, team_so_far, required_pokemon=required_pokemon, seed_weight=seed_weight,
        )),
        "role": float(weights["role"] * role_component(candidate, team_so_far, target_roles)),
        "penalty": float(weights["penalty"] * penalty_component(candidate, team_so_far)),
    }
    return {**components, "total": sum(components.values())}
