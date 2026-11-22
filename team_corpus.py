"""Read training data and explicitly adapt a published team to Candidate objects.

The corpus does not modify models.teampool. A record from another format is
never silently promoted into the Doubles Ubers search pool.
"""

import json
from pathlib import Path

from models import Candidate, FORMAT_ID, PokemonSet

DEFAULT_CORPUS = Path(__file__).parent / "data" / "team_corpus" / "teams.jsonl"


def load_corpus(path: Path = DEFAULT_CORPUS, *, format_id: str | None = FORMAT_ID) -> list[dict]:
    records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(record.get("schema_version") != 1 for record in records):
        raise ValueError("Unsupported team corpus schema")
    return [record for record in records if format_id is None or record["format"] == format_id]


def team_candidates(record: dict, *, format_id: str = FORMAT_ID) -> list[Candidate]:
    if record["format"] != format_id:
        raise ValueError(f"Source format {record['format']} does not match requested {format_id}")
    if record["validation"]["status"] != "passed":
        raise ValueError("Only validated teams can be adapted")
    candidates = []
    for member in record["members"]:
        sets = []
        for pokemon in member["pokemon_sets"]:
            data = dict(pokemon)
            data["evs"] = PokemonSet.EV(**data["evs"])
            data["ivs"] = PokemonSet.IV(**data["ivs"])
            sets.append(PokemonSet(**data))
        # Numeric search scores cannot represent unknown frequencies. This
        # explicit adapter uses a neutral zero while preserving the missingness
        # warning. The corpus and training examples retain JSON null.
        candidates.append(Candidate(
            pokemon_sets=sets, usage=member["usage"] if member["usage"] is not None else 0.0,
            usage_appearances=member["usage_appearances"] or 0,
            role_tags=list(member["role_tags"]), teammate_values=dict(member["teammate_values"]),
            evidence_status=member["evidence_status"], team_notes=list(member["team_notes"]),
        ))
    return candidates
