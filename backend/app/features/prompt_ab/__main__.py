"""Prompt A/B: the old prompts of a git ref and today's on the same inputs of a database copy.

Usage: python -m app.features.prompt_ab --db PATH --old-ref REF [--calls N] [--subject ID]
       [--dry-run] [--out DIR]
"""

import argparse
import sys
from pathlib import Path

from app.features.prompt_ab.inputs import Refused

SQLITE_HEADER = b"SQLite format 3\x00"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.features.prompt_ab", description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="a copy of an app database")
    parser.add_argument("--old-ref", required=True, help="the git ref of the old prompts")
    parser.add_argument("--calls", type=int, default=30, help="the most calls allowed (1-30)")
    parser.add_argument("--subject", type=int, default=None, help="a Polish subject id")
    parser.add_argument("--dry-run", action="store_true", help="build the page, call nothing")
    parser.add_argument("--out", type=Path, default=None, help="the run folder")
    return parser.parse_args(argv)


def _precheck(path: Path) -> None:
    """Before the settings load (which can touch the data folder): the file must be SQLite."""
    if not path.is_file():
        raise Refused(f"No such database file: {path}")
    with path.open("rb") as file:
        if file.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise Refused(f"Not a SQLite database: {path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _precheck(args.db)
        from app.config import get_settings
        from app.features.prompt_ab.loader import LoaderError
        from app.features.prompt_ab.run import RunOptions, run

        options = RunOptions(
            db=args.db,
            old_ref=args.old_ref,
            calls=args.calls,
            subject_id=args.subject,
            dry_run=args.dry_run,
            out_dir=args.out,
        )
        try:
            page = run(get_settings(), options)
        except LoaderError as error:
            print(f"Refused: {error}", file=sys.stderr)
            return 2
    except Refused as error:
        print(f"Refused: {error}", file=sys.stderr)
        return 2
    print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
