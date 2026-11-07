# -*- coding: utf-8 -*-
"""Repair missing/mangled media pointers under lifecycle protection.

The import is inert. Run after stopping writers; --dry-run does not change rows.
An applied repair creates a registered committed backup before changing roots.
"""
import argparse
import json
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    for name in ("db", "media", "reports", "checkpoints"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args(argv)
    from reno import files_json, storage_admin, storage_lifecycle, tasks
    try:
        profile = storage_admin.profile(database=args.db, media=args.media,
                                        reports=args.reports, checkpoints=args.checkpoints)
        result = files_json.repair(dry_run=args.dry_run, profile=profile)
    except (storage_lifecycle.StorageLifecycleError, tasks.TaskError):
        print(json.dumps({"code": "FILES_REPAIR_UNAVAILABLE", "detail": "路径修复未完成，请核对实例路径、文件保护设施及写入者状态。"}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"code": "FILES_REPAIR_FAILED", "detail": "路径修复未确认，请核对本机记录和备份。"}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
