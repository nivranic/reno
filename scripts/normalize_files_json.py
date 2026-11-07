# -*- coding: utf-8 -*-
"""One-shot migration: rewrite legacy files_json paths (backslash separators,
absolute Windows paths) to canonical POSIX media-base-relative form.

Idempotent - safe to re-run. Before writing, create a committed SQLite backup
through the protected backup entry described in docs/deploy.md section 5.10.
Copying only reno.db can omit committed WAL data. This normalizer does not
create its own backup; it only rewrites paths resolving to existing files.

Usage:
    .venv/Scripts/python -m scripts.normalize_files_json [--dry-run]
"""
import argparse
import json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()

    from reno import files_json
    stats = files_json.run(dry_run=args.dry_run)
    print(json.dumps({
        "rows": stats["rows"],
        "rewritten": stats["rewritten"],  # would-change count in dry-run
        "missing_count": len(stats["missing"]),
        "missing_sample": stats["missing"][:5],
        "dry_run": args.dry_run,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
