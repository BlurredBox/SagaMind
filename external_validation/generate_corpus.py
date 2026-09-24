"""Generate the deterministic frozen trial corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from external_validation.common import generate_cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--output", type=Path, default=Path("external_validation/trial_corpus.jsonl"))
    args = parser.parse_args()
    cases = generate_cases(args.count, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(case.to_dict(), sort_keys=True) + "\n" for case in cases),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
