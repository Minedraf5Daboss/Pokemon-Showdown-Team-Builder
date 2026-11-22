"""Search exercise scaffolding: fill in the bodies and remove the skip marker.

No assertions or search implementation are supplied here.
"""

import pytest

from exceptions import (
    PoolTooSmallError, RequiredPokemonExcludedError, RequiredPokemonNotInPoolError,
)
from team_output import FinishedTeam, print_teams
from models import ROLE_VOCABULARY, build_pool, validate_pool

from search import beam_search

# Empty exercises must be reported as unfinished, rather than passing tests.
pytestmark = pytest.mark.skip(reason="Scaffold only: add the test body and assertions")


@pytest.fixture
def pool():
    return build_pool()


def test_pool_has_only_valid_teammate_names(pool):
    pass


def test_pool_uses_only_allowed_role_tags(pool):
    pass


def test_invalid_teammate_name_is_rejected(pool):
    pass


def test_search_returns_three_finished_teams(pool):
    pass


def test_finished_teams_have_six_distinct_pokemon(pool):
    pass


def test_search_includes_required_pokemon(pool):
    pass


def test_search_omits_excluded_pokemon(pool):
    pass


def test_search_works_without_required_pokemon(pool):
    pass


def test_required_pokemon_not_in_pool_raises(pool):
    pass


def test_required_pokemon_also_excluded_raises(pool):
    pass


def test_pool_too_small_raises(pool):
    pass


def test_pool_too_small_after_exclusions_raises(pool):
    pass


def test_finished_teams_are_sorted_by_total_score(pool):
    pass


def test_finished_teams_have_no_duplicate_species_combinations(pool):
    pass


def test_lopsided_pair_prioritizes_more_used_pokemon(pool):
    pass


def test_search_leaves_pool_unchanged(pool):
    pass


def test_identical_runs_give_identical_output(pool):
    pass


def test_beam_width_one_matches_greedy_pick(pool):
    pass


def test_partial_teams_after_slot_two_match_hand_trace(pool):
    pass


def test_printing_helper_shows_names_total_and_components(pool, capsys):
    pass
