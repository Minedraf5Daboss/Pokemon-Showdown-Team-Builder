"""Audit current production sets against frozen Smogon primary evidence."""
import json
from catalog_paths import CATALOG_DATA, active_catalog_dir
from candidate_evidence import verify_sets, species_record

if __name__ == "__main__":
    root = active_catalog_dir()
    evidence = verify_sets(root, CATALOG_DATA.parent.parent)
    (root / "set_evidence.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(evidence)} species and {sum(len(record['sets']) for record in evidence.values())} sourced variants.")
