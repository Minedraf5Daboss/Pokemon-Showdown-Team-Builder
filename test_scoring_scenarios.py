"""Worked scoring examples and behavior checks requested in step 14."""

from copy import deepcopy

import pytest

from models import Candidate, teampool
from scoring import normalize, pair_synergy, pool_usage_bounds, score_addition, synergy_component


@pytest.fixture
def pool():
    # Each test may adjust its own candidates without changing the shared data.
    return {candidate.pokemon_sets[0].name: deepcopy(candidate) for candidate in teampool}


def test_dragonite_addition_matches_hand_calculation(pool):
    team_so_far = [pool["Amoonguss"], pool["Gengar"]]
    weights = {"usage": 0.3, "synergy": 0.7, "role": 0.5, "penalty": 0.2}
    targets = ["Support", "Special Sweeper", "Physical Sweeper"]

    result = score_addition(
        pool["Dragonite"], team_so_far, weights, 0.0043975736, 0.2508367748, targets
    )

    # Fixed historical bounds keep this worked example independent of pool growth.
    # Independently calculated with usage-weighted bidirectional pair scores:
    # usage: (0.0268823572 - 0.0043975736) / (0.2508367748 - 0.0043975736) * 0.3
    # Dragonite/Amoonguss: (0.0268823572 * 0.1151230725
    #                     + 0.1288607504 * 0.0240164639) / 0.1557431076
    #                     = 0.039742106166983
    # Dragonite/Gengar:   (0.0268823572 * 0.0144622163
    #                     + 0.0104918691 * 0.0370552147) / 0.0373742263
    #                     = 0.020804629381319
    # synergy: mean of those two pairs * 0.7 = 0.021191357441906
    # roles: one new Physical Sweeper * 0.5; no overlapping roles to penalize.
    expected = {
        "usage": 0.027371599352514,
        "synergy": 0.021191357441906,
        "role": 0.5,
        "penalty": 0.0,
        "total": 0.548562956794420,
    }
    # pytest.approx supports dictionaries and tolerates floating-point rounding.
    # https://docs.pytest.org/en/stable/how-to/assert.html#assertions-about-approximate-equality
    assert result == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_normalize_equal_minimum_and_maximum_returns_zero():
    assert normalize(0.2, 0.2, 0.2) == pytest.approx(0.0)


@pytest.mark.parametrize("candidate_usage,teammate_usage,expected", [
    (0.8, 0.2, 0.74),  # More-used candidate's 0.9 direction dominates.
    (0.2, 0.8, 0.26),  # More-used teammate's 0.1 direction dominates.
    (0.2, 0.2, 0.5),
    (0.0, 0.0, 0.5),  # Explicit fallback avoids division by zero.
    (0.8, 0.0, 0.9),
    (0.0, 0.8, 0.1),
])
def test_lopsided_pair_prioritizes_more_used_pokemon(pool, candidate_usage, teammate_usage, expected):
    # The pair score has the same meaning regardless of which Pokemon is added.
    garchomp, tyranitar = pool["Garchomp"], pool["Tyranitar"]
    garchomp.usage, tyranitar.usage = candidate_usage, teammate_usage
    garchomp.teammate_values["Tyranitar"] = 0.9
    tyranitar.teammate_values["Garchomp"] = 0.1
    weights = {"usage": 0.0, "synergy": 1.0, "role": 0.0, "penalty": 0.0}
    bounds = pool_usage_bounds(teampool)

    forward = score_addition(garchomp, [tyranitar], weights, *bounds, [])
    reverse = score_addition(tyranitar, [garchomp], weights, *bounds, [])

    assert synergy_component(garchomp, [tyranitar]) == pytest.approx(expected)
    assert synergy_component(tyranitar, [garchomp]) == pytest.approx(expected)
    assert forward["total"] == pytest.approx(expected)
    assert reverse["total"] == pytest.approx(expected)


def test_pair_synergy_distinguishes_missing_direction_from_observed_zero(pool):
    garchomp, tyranitar = pool["Garchomp"], pool["Tyranitar"]
    garchomp.usage, tyranitar.usage = 0.8, 0.2
    garchomp.teammate_values = {}
    tyranitar.teammate_values = {"Garchomp": 0.8}
    assert pair_synergy(garchomp, tyranitar) == pytest.approx(0.8)
    assert pair_synergy(tyranitar, garchomp) == pytest.approx(0.8)

    garchomp.teammate_values["Tyranitar"] = 0.0
    assert pair_synergy(garchomp, tyranitar) == pytest.approx(0.16)

    garchomp.teammate_values.clear()
    tyranitar.teammate_values.clear()
    assert pair_synergy(garchomp, tyranitar) == pytest.approx(0.0)


def test_raising_usage_weight_changes_highest_scoring_candidate(pool):
    team_so_far = [pool["Gengar"]]
    candidates = {
        "low_usage": Candidate(usage=0.1, teammate_values={"Gengar": 0.9}),
        "high_usage": Candidate(usage=0.9, teammate_values={"Gengar": 0.1}),
    }
    bounds = pool_usage_bounds(candidates.values())
    weights = {"usage": 0.2, "synergy": 1.0, "role": 0.0, "penalty": 0.0}
    higher_usage_weight = {**weights, "usage": 1.0}

    def totals(chosen_weights):
        return {name: score_addition(candidate, team_so_far, chosen_weights, *bounds, [])["total"]
                for name, candidate in candidates.items()}

    original = totals(weights)
    increased = totals(higher_usage_weight)

    # Normalized usage is 0 versus 1. Only its weight changes; synergy stays fixed.
    assert original == pytest.approx({"low_usage": 0.9, "high_usage": 0.3})
    assert increased == pytest.approx({"low_usage": 0.9, "high_usage": 1.1})
    assert max(original, key=original.get) == "low_usage"
    assert max(increased, key=increased.get) == "high_usage"


def test_score_addition_leaves_team_so_far_unchanged(pool):
    team_so_far = [pool["Amoonguss"], pool["Gengar"]]
    before = deepcopy(team_so_far)
    original_members = tuple(team_so_far)

    score_addition(
        pool["Dragonite"], team_so_far,
        {"usage": 0.3, "synergy": 0.7, "role": 0.5, "penalty": 0.2},
        *pool_usage_bounds(teampool), ["Support", "Special Sweeper", "Physical Sweeper"]
    )

    # Deep equality catches changes to nested sets, stats, roles or teammate maps;
    # identity checks also catch replacement or reordering of equal members.
    assert team_so_far == before
    assert all(current is original for current, original in zip(team_so_far, original_members))
