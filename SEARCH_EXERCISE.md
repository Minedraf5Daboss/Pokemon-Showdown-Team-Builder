# Beam search walkthrough

`search.py` now implements the search using your `partialTeam` structure.
`scoring.py` supports extra compatibility weight for the required Pokemon.
The memory trick for the search is
**Seed, Grow, Merge, Trim, Repeat**: start a team, try additions, combine duplicate
teams, keep a shortlist, then fill the next slot.

## What each file does

| File | Job |
| --- | --- |
| `models.py` | Stores Candidates and their sets/stats; parses restricted names. |
| `scoring.py` | Scores one proposed addition against the current members. |
| `search.py` | Builds and ranks partial teams until they have six members. |
| `exceptions.py` | Defines the shared errors imported by `search.py`. |
| `team_output.py` | Stores finished results and prints their component totals. |
| `scratch_runner.py` | Reads command-line options or restrictions and runs the search. |
| `test_search.py` | Checks the completed implementation, including a hand trace. |

## Remember the team as a folder and a receipt

Your `partialTeam` stores three things:

- `members`: the folder of Candidate objects. Every Candidate already contains
  its Pokemon sets, EVs/IVs, moves, nature, item, ability, roles, usage and teammate
  values. Keeping the Candidate keeps all that information together.
- `score`: the running total on the receipt.
- `breakdowns`: the receipt's individual lines. Entry `i` belongs to member `i`
  and contains weighted usage, synergy, role, penalty and total values.

`name_of(candidate)` reads `candidate.pokemon_sets[0].name`. The `[0]` selects
the first PokemonSet from the Candidate's list; `candidate.PokemonSet` was not
an attribute on your Candidate class.

## Seed, Grow, Merge, Trim, Repeat

1. **Check and filter.** Validate sizes and scoring settings. Resolve names,
   reject a missing required Pokemon or a required/excluded conflict, and
   remove excluded candidates. Fail if too few distinct candidates remain.
   Usage bounds are computed once from the original supplied pool and reused.
2. **Seed.** With no required Pokemon, `beam = [partialTeam()]`: one empty team
   with score zero. With a required Pokemon, that Pokemon is the only member,
   still at score zero. Its breakdown is all zeros. It affects future synergy,
   role coverage and penalties, but receives no contribution of its own.
3. **Grow.** The `while` loop runs until the teams reach `team_size` (default 6).
   The outer `for` visits each partial team; the inner `for` tries every eligible
   candidate. `continue` skips a Pokemon already on that team or a held item
   already used by another member, including the required seed. These checks
   use each candidate's first set and ignore capitalization and surrounding
   spaces. Multiple Pokemon may hold no item (an empty item field). If no valid
   expansion survives, the search raises `ValueError` explaining the constraints.
   `score_addition(candidate, team.members, ...)` returns the proposed gain.
   A new `partialTeam` gets `team.score + addition["total"]` and a new member
   list. Using `team.members + [candidate]` leaves the parent list unchanged.
4. **Merge.** `frozenset` turns the names into an order-independent key:
   A then B and B then A both have the same membership. `best_by_members`
   stores the highest-scoring route to each membership. Ties use alphabetical
   addition order. Merge happens before trimming so duplicates do not waste slots.
5. **Trim.** `sorted(..., key=_rank_key)[:beam_width]` keeps only the best partial
   teams. `_rank_key` starts with negative score, so normal ascending sorting puts
   the largest score first. The default beam width is 100, as approved.
6. **Repeat and finish.** Each round fills one more slot. Return up to `top_k`
   finished teams (default 3), best first. `FinishedTeam.breakdown` contains
   component totals; `addition_breakdowns` keeps the individual contributions.

**Width means how many possibilities you remember, not how many Pokemon go on
one team.** Width 1 is greedy: keep only the best next choice. Width 100 remembers
up to 100 partial teams, allowing a weaker early choice to survive and improve
later. It still discards paths and does not guarantee the global optimum.

Because each addition averages synergy against existing members, the running
score can depend on addition order. The best order survives when memberships
merge. Later additions depend on the existing members, so retaining the better
running score is sufficient for those paths. The printed member order is the
construction order, not a recommendation of which two Pokemon to lead in battle.
Scores from runs with different required seeds are not directly comparable:
each required seed contributes zero instead of being scored as an addition.

## Give the required Pokemon two votes

The required Pokemon is the team's base. By default, its pair compatibility
gets **two votes**, while each other member gets one vote in the synergy average.
This makes later choices favor the base more strongly while still considering
the rest of the team. This uses the existing usage-weighted bidirectional pair
scores; the extra weight applies when combining those pairs across the team.

For a candidate facing a seed and two other members:

```text
synergy = (2 * pair_with_seed + pair_with_member_2 + pair_with_member_3) / 4
```

For pair scores 0.8, 0.2 and 0.2, equal weighting gives 0.4. Seed weight 2
gives 0.5. The usual synergy weight (default 0.7) is then applied, giving 0.35.
This changes only the synergy component. Usage, roles, penalties and team-style
scoring retain their own weights, so seed compatibility is a preference rather
than a guarantee that the strongest seed pairing always wins.

The average is normalized by total votes. With only the seed present, its pair
is already the whole average, so multiplying its votes does not change that first
addition. Searches without a required Pokemon use the ordinary average. The
seed still starts at score zero. Increasing its weight does not mean adding a
second copy of it to the team.

Use `beam_search(..., seed_weight=3)` or runner option `--seed-weight 3` to
give the base three votes. Use 1 to restore equal weighting. The setting must
be finite and at least 1; higher values are not guaranteed to produce a better
competitive team.

## A hand trace

For this small teaching example, usage weight and synergy weight are both 1;
role and penalty weights are 0. A/B/C have raw usages 0.8/0.6/0.2, giving
normalized usage values 1, 2/3, 0. Both directions of their pair scores agree:
A-B = 0.1, A-C = 0.9, B-C = 0.2. Beam width is 2, with no required Pokemon.

| Round | Expanded paths and running scores | Kept |
| --- | --- | --- |
| Slot 1 | A: 1; B: 0.666667; C: 0 | A, B |
| Slot 2 | AB: 1.766667; AC: 1.9; BA: 1.766667; BC: 0.866667 | AC, AB (BA duplicates AB) |
| Slot 3 | ACB: 2.716667; ABC: 2.316667 | ACB (same three members, better route) |

For ACB, the final addition is B: usage `2/3` plus average synergy
`(0.1 + 0.2) / 2 = 0.15`. Add that to AC's 1.9 for 2.716667.
This is checked in `test_search.py` using `pytest.approx` for float comparisons.

## Production pool

`models.build_pool()` returns independent copies of all 18 production
candidates. `models.teampool` holds the complete inline definitions, including
abilities, items, Tera types, moves, natures, EVs and IVs. Usage and all 306
directional teammate values come from the cached Smogon Doubles Ubers reports.
Usage varies from 0.0043975736 (Lucario) to 0.2508367748 (Incineroar).
See `data/smogon/README.md` for set evidence and limitations.

The runner, exports and search fixture now use `models.py`. `toy_data.py`
contains no candidate data or application dependencies. Search code should use:

```python
from models import DEFAULT_TARGET_ROLES, build_pool

pool = build_pool()
```

The role vocabulary is `Support`, `Speed Control`, `Physical Sweeper`,
`Special Sweeper`, `Setup`, and `Weather Setter`. The default target roles
omit Weather Setter, so a search is not required to choose weather support.
`models.validate_pool(pool)` raises `ValueError` for unknown teammate names,
missing or duplicate candidate names, invalid roles or frequencies, and
incomplete sets. It runs when the model data is loaded and on each `build_pool()`
call. Full format legality is checked separately by the Showdown verifier.

## Exceptions to use in your search

The search imports these shared definitions and re-exports them. A caller can
import them from `search` or `exceptions`:

```python
RequiredNotInPoolError(["Missing name"])
RequiredAlsoExcludedError(["Amoonguss"])
PoolTooSmallError(available=5, required=6)
```

All three derive from `SearchError`. The earlier names `TeamBuilderError`,
`RequiredPokemonNotInPoolError` and `RequiredPokemonExcludedError` remain aliases,
so existing exercise imports catch the same classes. Invalid settings or unknown
restricted names raise `ValueError`. The runner displays these errors without a
traceback and exits with status 2.

## Runner interface

The call in `scratch_runner.py` uses:

```python
beam_search(
    pool,
    *,
    required_pokemon,   # one species name, or None
    excluded_pokemon,   # a collection of species names
    team_size,
    beam_width,
    top_k,
    weights,
    target_roles,
    seed_weight,        # defaults to 2; 1 restores equal compatibility weighting
)
```

The search returns a list of `team_output.FinishedTeam` objects, best first. Each stores
`members` (a list of Candidates) and `breakdown` (a dictionary with `usage`,
`synergy`, `role`, `penalty`, and `total`). These should be the weighted
component totals for the finished team. `addition_breakdowns` stores the individual
contributions in member order. The printer displays the summed values without
rescoring, sorting, or modifying results. Fewer than three teams can be returned
if the beam is narrower than three or fewer distinct teams are possible.

From any PowerShell location:

```powershell
& "C:\dev\showdown-builder\.venv\Scripts\python.exe" "C:\dev\showdown-builder\scratch_runner.py"
```

The runner defaults request six-member teams, required Amoonguss, no exclusions,
beam width 100, and up to three results. The function itself defaults to no
required Pokemon. To start empty and restrict two species:

```powershell
& "C:\dev\showdown-builder\.venv\Scripts\python.exe" "C:\dev\showdown-builder\scratch_runner.py" --required none --exclude "Miraidon,Urshifu"
```

Use `--interactive` to enter restrictions at a prompt, `--required "Gengar"`
to change the seed, or `--beam-width 1` for greedy selection. Restriction parsing
lives in `models.parse_restricted_mons`; it trims spaces, ignores capitalization
and duplicate/blank entries, and rejects unknown names. No prompt runs on import.

The default scoring weights are usage 0.3, synergy 0.7, role 0.5 and penalty 0.2.
The penalty helper returns negative values, so its weight stays positive.
Usage/teammate data and role coverage are heuristics, not a battle simulator:
the search does not calculate damage, type matchups or win probability.

## Pytest scaffolding

`test_search.py` contains working tests for the completed search and runner.
`test_search_scaffold.py` retains imports, a fresh-pool fixture, and empty
test functions corresponding to the exercise. There are no assertions in
those functions. The module is marked skipped so unfinished tests do not
appear to pass. Uncomment the search import, write your tests, and remove
the module's `pytestmark` when ready (or mark individual unfinished tests).

```powershell
& "C:\dev\showdown-builder\.venv\Scripts\python.exe" -m pytest "C:\dev\showdown-builder" -q
```
