# -*- coding: utf-8 -*-
"""reno CLI: init-db / migrate-db / import / run / status / judge / report / serve"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def _membership_input(args):
    """Explicit administrative input; never infer omitted storage roots."""
    from . import storage_admin, storage_lifecycle
    paths = (args.db, args.media, args.reports, args.checkpoints)
    if args.members_file is None:
        return [storage_admin.profile(database=args.db, media=args.media,
                                      reports=args.reports, checkpoints=args.checkpoints)], ()
    if any(path is not None for path in paths):
        raise ValueError("Choose a member document or an explicit profile")

    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Repeated input field")
            value[key] = item
        return value

    with args.members_file.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Member document is too large")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique)
    if type(value) is not dict or set(value) != {"profiles", "declarations"}:
        raise ValueError("Member document requires profiles and declarations")
    if type(value["profiles"]) is not list or not 1 <= len(value["profiles"]) <= 128:
        raise ValueError("Invalid member list")
    profiles = []
    fields = ("database", "media", "reports", "checkpoints")
    for entry in value["profiles"]:
        if type(entry) is not dict or set(entry) != set(fields) or any(type(v) is not str for v in entry.values()):
            raise ValueError("Invalid member profile")
        profiles.append(storage_lifecycle.StorageProfile(*(Path(entry[key]) for key in fields)))
    if type(value["declarations"]) is not list:
        raise ValueError("Invalid completeness declarations")
    return profiles, value["declarations"]


def _storage_roots_error(code):
    """Only machine codes and fixed public text may leave the GC boundary."""
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None:
        code = "ROOTS_PREVIEW_FAILED"
    print(json.dumps({"code": code,
        "detail": "存储引用预览或回收未完成，请核对显式路径、已登记成员、原操作与存储状态。"},
        ensure_ascii=False), file=sys.stderr)
    return 2


def _sender_admin_error(code):
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None:
        code = "SYNC_SENDER_ADMIN_FAILED"
    print(json.dumps({"code": code,
        "detail": "发送端操作未完成，请核对完整路径、原操作编号和绑定证明；已提交停发的请求须沿原编号续跑。"},
        ensure_ascii=False), file=sys.stderr)
    return 2


def _home_checkpoint_error(code):
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None:
        code = "HOME_CHECKPOINT_FAILED"
    print(json.dumps({"code": code,
        "detail": "家庭知识检查点未完成，请核对原编号、完整路径、节点身份和维护锁；原编号重试保留原来源。"},
        ensure_ascii=False), file=sys.stderr)
    return 2


def _home_restore_error(code):
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None:
        code = "HOME_RECOVERY_FAILED"
    print(json.dumps({"code": code,
        "detail": "家庭知识恢复未完成，请核对预览摘要、原操作编号和维护锁；提交结果不确定时沿原编号续跑。"},
        ensure_ascii=False), file=sys.stderr)
    return 2


def _source_continuity_error(code):
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", code) is None:
        code = "SYNC_SOURCE_CONTINUITY_ADMIN_FAILED"
    print(json.dumps({"code": code, "detail": "来源连续性维护未完成，请核对配对身份、预览摘要和原激活编号及时间。"},
                     ensure_ascii=False), file=sys.stderr)
    return 2


def _personal_admin_error(code):
    if type(code) is not str or re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", code) is None:
        code = "PERSONAL_ADMIN_FAILED"
    print(json.dumps({"code": code,
        "detail": "个人数据操作未完成，请核对功能配置、维护锁、原操作编号及请求或预览摘要。"},
        ensure_ascii=False), file=sys.stderr)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reno")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-db", help="explicitly initialize a new database")
    p.add_argument("--db", type=Path, default=None)
    p = sub.add_parser("migrate-db", help="upgrade an existing database; stop workers and write API first")
    p.add_argument("--db", type=Path, default=None)
    p.add_argument("--media", type=Path)
    p.add_argument("--reports", type=Path)
    p.add_argument("--checkpoints", type=Path)
    p.add_argument("--initialize-storage-offline", action="store_true",
                   help="explicitly initialize lifecycle facilities after stopping writers and closing DB connections")
    p = sub.add_parser("frame-cache", help="preview regenerable frame cache eviction; --apply requires the preview digest")
    p.add_argument("--budget-bytes", type=int, help="override the configured managed cache budget for this command")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--expected-digest", help="digest returned by a fresh preview using the same budget")
    personal_parsers = []
    preservation_parser = sub.add_parser("personal-preservation", help="read personal preservation status; create/resume require explicit --apply")
    personal_parsers.append(preservation_parser)
    preservation_action = preservation_parser.add_mutually_exclusive_group()
    for action in ("create", "resume", "verify"):
        preservation_action.add_argument("--" + action, action="store_true")
    preservation_parser.add_argument("--apply", action="store_true")
    preservation_parser.add_argument("--operation-id")
    preservation_parser.add_argument("--expected-request-sha256")
    for name in ("max-files", "max-bytes", "max-seconds"):
        preservation_parser.add_argument("--" + name, type=int)
    for command, description in (
            ("personal-mirror-init", "explicitly initialize an enabled and paired personal mirror sender"),
            ("personal-mirror", "preview one personal snapshot; --apply requires its exact preview SHA"),
            ("personal-mirror-status", "inspect only an original personal mirror operation without network"),
            ("personal-mirror-deliver", "explicitly queue only an original personal mirror UUID and request SHA")):
        p = sub.add_parser(command, help=description)
        personal_parsers.append(p)
        if command != "personal-mirror-status":
            p.add_argument("--apply", action="store_true")
        if command != "personal-mirror-init":
            p.add_argument("--operation-id", required=True)
        if command == "personal-mirror":
            p.add_argument("--snapshot-id", required=True)
            p.add_argument("--expected-head-operation-id")
            p.add_argument("--expected-snapshot-sha256")
        if command in ("personal-mirror-status", "personal-mirror-deliver"):
            p.add_argument("--expected-request-sha256", required=True)
        if command == "personal-mirror-deliver":
            p.add_argument("--request-id", help="task request UUID independent of the mirror operation UUID")
            p.add_argument("--wait", action="store_true", help="run the existing local worker after explicit submission")
    for parser in personal_parsers:
        for name in ("db", "media", "reports", "checkpoints"):
            parser.add_argument("--" + name, type=Path)
    for name, description in (("storage-init", "explicitly initialize lifecycle lock facilities for an existing library"),
                              ("storage-status", "inspect lifecycle lock facilities without creating them"),
                              ("storage-member-init", "explicitly register this library; existing roots remain of unknown completeness"),
                              ("storage-members", "preview root membership; --apply requires a fresh digest and operation UUID")):
        p = sub.add_parser(name, help=description)
        p.add_argument("--db", type=Path)
        p.add_argument("--media", type=Path)
        p.add_argument("--reports", type=Path)
        p.add_argument("--checkpoints", type=Path)
        if name == "storage-members":
            p.add_argument("--members-file", type=Path, help="JSON containing profiles and per-root completeness declarations")
            p.add_argument("--apply", action="store_true")
            p.add_argument("--expected-digest")
            p.add_argument("--operation-id", help="UUID for this enrollment; interrupted writes require a new preview and UUID")
    roots_parser = sub.add_parser("storage-roots", help="preview enrolled storage references; explicit apply/resume use a frozen GC operation")
    for name in ("db", "media", "reports", "checkpoints"):
        roots_parser.add_argument("--" + name, type=Path)
    roots_parser.add_argument("--members-file", type=Path, help="JSON profiles; declarations must be empty")
    roots_parser.add_argument("--keep-publication", type=int, help="publication checkpoints to retain (default 3, at least 1)")
    roots_parser.add_argument("--keep-recovery", type=int, help="recovery checkpoints to retain (default 3, at least 1)")
    roots_parser.add_argument("--namespace", action="append", choices=("sync_media", "local_report", "sync_report", "checkpoint"),
                              help="limit candidate namespaces; repeat to select several (default: all)")
    gc_action = roots_parser.add_mutually_exclusive_group()
    gc_action.add_argument("--apply", action="store_true", help="commit and execute the exact fresh preview; requires digest and UUID")
    gc_action.add_argument("--resume", action="store_true", help="continue only the original operation's frozen selection")
    roots_parser.add_argument("--expected-digest", help="preview_sha256 returned by the same profile and retention policy")
    roots_parser.add_argument("--operation-id", help="canonical UUID binding this single-owner collection operation")
    sender_parsers = []
    for name, description in (("sync-abandon", "preview one publication; explicit CAS apply commits local permanent no-resend"),
                              ("sync-abandon-resume", "deliver only the original committed authorization and peer"),
                              ("sync-retire-local", "preview explicit local references; apply requires digest and operation UUID")):
        p = sub.add_parser(name, help=description)
        sender_parsers.append(p)
        for field in ("db", "media", "reports", "checkpoints"):
            p.add_argument("--" + field, type=Path, required=True)
        if name == "sync-abandon":
            p.add_argument("publication_id")
        if name == "sync-retire-local":
            p.add_argument("--request-file", type=Path, required=True)
        if name == "sync-abandon-resume":
            p.add_argument("--operation-id", required=True)
            p.add_argument("--sender-config", type=Path,
                           help="explicit credential/config file for pending remote delivery; not needed for completed local replay")
        else:
            action = p.add_mutually_exclusive_group()
            action.add_argument("--apply", action="store_true")
            if name == "sync-retire-local":
                action.add_argument("--resume", action="store_true", help="resume the same UUID and original request-file selection")
            p.add_argument("--expected-digest")
            p.add_argument("--operation-id")
    sub.add_parser("sync-identity", help="export the existing node identity without credentials")
    continuity_parser = sub.add_parser("sync-source-continuity", help="read Cloud continuity; explicit --apply activates the exact paired profile and epoch")
    continuity_parser.add_argument("--apply", action="store_true")
    continuity_parser.add_argument("--continuity-id", help="stable canonical UUID; uncertain results reuse the original ID")
    continuity_parser.add_argument("--activated-at", help="original UTC timestamp YYYY-MM-DDTHH:MM:SS.mmmZ; retries keep the same value")
    continuity_parser.add_argument("--expected-digest", help="activation_basis_sha256 from this profile's read-only preview")
    for field in ("db", "media", "reports", "checkpoints"):
        continuity_parser.add_argument("--" + field, type=Path)
    p = sub.add_parser("sync-pair", help="explicitly bind a source identity on a cloud node")
    p.add_argument("--source", type=Path, required=True, help="JSON exported by the source's sync-identity")
    p.add_argument("--refresh-schema", action="store_true", help="refresh only the same idle peer after a schema upgrade")
    p = sub.add_parser("sync-run", help="queue a configured home synchronization cycle")
    p.add_argument("--follow", action="store_true", help="follow the existing worker queue and schedule sync cycles")
    p = sub.add_parser("reconciliation", help="read the review reconciliation report without changing records")
    p.add_argument("--reason", choices=("all", "active", "stale", "missing", "legacy_unbound", "invalid_time"), default="all")
    p.add_argument("--scope", choices=("attention", "history", "all"), default="attention")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--markdown", action="store_true", help="export all matching records as Markdown to stdout")
    p = sub.add_parser("sync-checkpoints", help="list committed receiver checkpoints (read-only)")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("sync-checkpoint-preview", help="verify a receiver checkpoint without restoring it")
    p.add_argument("checkpoint_id")
    home_checkpoint_parser = sub.add_parser("home-checkpoint", help="read an existing Home knowledge checkpoint; --create --apply produces a new one")
    home_checkpoint_parser.add_argument("source_id", help="canonical UUID; retries keep the same original source ID")
    home_checkpoint_parser.add_argument("--create", action="store_true")
    home_checkpoint_parser.add_argument("--apply", action="store_true")
    for name in ("db", "media", "reports", "checkpoints"):
        home_checkpoint_parser.add_argument("--" + name, type=Path)
    home_restore_parser = sub.add_parser("home-restore", help="preview a Home knowledge restore; --apply requires its digest, --resume completes the original operation")
    home_restore_parser.add_argument("source_id", nargs="?", help="Home checkpoint UUID; omit only for --resume")
    restore_action = home_restore_parser.add_mutually_exclusive_group()
    restore_action.add_argument("--apply", action="store_true")
    restore_action.add_argument("--resume", action="store_true")
    home_restore_parser.add_argument("--expected-digest")
    home_restore_parser.add_argument("--operation-id", help="canonical UUID; uncertain results keep the same original operation ID")
    for name in ("db", "media", "reports", "checkpoints"):
        home_restore_parser.add_argument("--" + name, type=Path)
    sub.add_parser("sync-recovery-status", help="read the recovery control and current knowledge version")
    p = sub.add_parser("sync-recovery-checkpoints", help="list protection checkpoints created before recovery")
    p.add_argument("--limit", type=int, default=100)
    for name, description in (("sync-restore", "preview a knowledge restore; --apply executes on this cloud node"),
                              ("sync-resume", "preview resuming knowledge synchronization; --apply requests full republication")):
        p = sub.add_parser(name, help=description)
        if name == "sync-restore":
            p.add_argument("checkpoint_id")
            p.add_argument("--checkpoint-kind", choices=("publication", "recovery"), default="publication")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--operation-id", help="stable UUID; retry uncertain results with the same UUID and arguments")
        p.add_argument("--expected-library-id")
        p.add_argument("--expected-epoch", type=int)
        p.add_argument("--expected-revision", type=int)
        p.add_argument("--expected-control-revision", type=int)

    p = sub.add_parser("import", help="ingest URL or local file")
    p.add_argument("target")
    p.add_argument("--no-process", action="store_true")
    p.add_argument("--request-id", help="stable UUID for retrying an uncertain submission")
    p.add_argument("--wait", action="store_true", help="run the existing worker after submitting")

    p = sub.add_parser("import-batch", help="ingest one URL per line from file (or '-' for stdin)")
    p.add_argument("file")
    p.add_argument("--request-id", help="stable UUID for retrying the same batch")
    p.add_argument("--wait", action="store_true", help="run the existing worker after submitting")

    p = sub.add_parser("import-favlist", help="enumerate a bilibili favorites list and ingest all")
    p.add_argument("url", help="e.g. https://space.bilibili.com/<mid>/favlist?fid=<fid>")
    p.add_argument("--cookies", default=None, help="Netscape cookie file for private lists")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--request-id", help="stable UUID for retrying the same favorites selection")
    p.add_argument("--wait", action="store_true", help="run the existing worker after submitting")

    p = sub.add_parser("run", help="process pending jobs (or one video)")
    p.add_argument("video_id", nargs="?")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("status")
    p = sub.add_parser("judge", help="cross-video dedup+conflict")
    p.add_argument("--threshold", type=float, default=0.22)
    p = sub.add_parser("report")
    p.add_argument("--summary", action="store_true", help="explicitly generate AI summary (may consume model quota)")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)

    submitted_args = sys.argv[1:] if argv is None else argv
    if submitted_args and submitted_args[0] in {
            "personal-preservation", "personal-mirror-init", "personal-mirror",
            "personal-mirror-status", "personal-mirror-deliver"}:
        def personal_argument_error(_message):
            _personal_admin_error("PERSONAL_ADMIN_ARGUMENT_INVALID")
            ap.exit(2)
        ap.error = personal_argument_error
        for parser in personal_parsers:
            parser.error = personal_argument_error
    if submitted_args and submitted_args[0] == "storage-roots":
        def roots_argument_error(_message):
            _storage_roots_error("ROOTS_ARGUMENT_INVALID")
            ap.exit(2)
        # argparse otherwise echoes arbitrary option values, including paths.
        ap.error = roots_argument_error
        roots_parser.error = roots_argument_error
    if submitted_args and submitted_args[0] in {"sync-abandon", "sync-abandon-resume", "sync-retire-local"}:
        def sender_argument_error(_message):
            _sender_admin_error("SYNC_SENDER_ARGUMENT_INVALID")
            ap.exit(2)
        ap.error = sender_argument_error
        for parser in sender_parsers:
            parser.error = sender_argument_error
    if submitted_args and submitted_args[0] == "home-checkpoint":
        def home_checkpoint_argument_error(_message):
            _home_checkpoint_error("HOME_CHECKPOINT_ARGUMENT_INVALID")
            ap.exit(2)
        ap.error = home_checkpoint_argument_error
        home_checkpoint_parser.error = home_checkpoint_argument_error
    if submitted_args and submitted_args[0] == "home-restore":
        def home_restore_argument_error(_message):
            _home_restore_error("HOME_RECOVERY_ARGUMENT_INVALID")
            ap.exit(2)
        ap.error = home_restore_argument_error
        home_restore_parser.error = home_restore_argument_error
    if submitted_args and submitted_args[0] == "sync-source-continuity":
        def source_continuity_argument_error(_message):
            _source_continuity_error("SYNC_SOURCE_CONTINUITY_ARGUMENT_INVALID")
            ap.exit(2)
        ap.error = source_continuity_argument_error
        continuity_parser.error = source_continuity_argument_error
    args = ap.parse_args(argv)
    from . import config, db

    if args.cmd == "personal-preservation":
        from . import personal_preservation_admin, personal_preservation_contract, storage_admin
        try:
            value = storage_admin.profile(database=args.db, media=args.media,
                                          reports=args.reports, checkpoints=args.checkpoints)
            limit_args = {name: getattr(args, name) for name in ("max_files", "max_bytes", "max_seconds")}
            if ((args.create or args.resume) != args.apply
                    or (args.create or args.resume or args.verify) and args.operation_id is None
                    or args.resume and args.expected_request_sha256 is None
                    or args.create and args.expected_request_sha256 is not None
                    or any(item is not None for item in limit_args.values()) and not args.create
                    or not (args.resume or args.verify) and args.expected_request_sha256 is not None):
                return _personal_admin_error("PERSONAL_ADMIN_ARGUMENT_INVALID")
            if args.create:
                limits = {**personal_preservation_contract.DEFAULT_LIMITS,
                          **{name: item for name, item in limit_args.items() if item is not None}}
                result = personal_preservation_admin.create(value, operation_id=args.operation_id, limits=limits)
            elif args.resume:
                result = personal_preservation_admin.resume(value, operation_id=args.operation_id,
                    expected_request_sha256=args.expected_request_sha256)
            elif args.verify:
                result = personal_preservation_admin.verify(value, operation_id=args.operation_id,
                    expected_request_sha256=args.expected_request_sha256)
            else:
                result = personal_preservation_admin.status(value, operation_id=args.operation_id)
        except Exception as exc:
            return _personal_admin_error(getattr(exc, "code", "PERSONAL_PRESERVATION_FAILED"))
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in {"personal-mirror-init", "personal-mirror", "personal-mirror-status", "personal-mirror-deliver"}:
        from . import personal_mirror_admin, storage_admin
        try:
            value = storage_admin.profile(database=args.db, media=args.media,
                                          reports=args.reports, checkpoints=args.checkpoints)
            if args.cmd == "personal-mirror-init":
                if not args.apply:
                    return _personal_admin_error("PERSONAL_ADMIN_EXPLICIT_APPLY_REQUIRED")
                result = personal_mirror_admin.initialize(value)
            elif args.cmd == "personal-mirror":
                if args.apply != (args.expected_snapshot_sha256 is not None):
                    return _personal_admin_error("PERSONAL_ADMIN_PREVIEW_SHA_REQUIRED")
                options = {"operation_id": args.operation_id, "snapshot_id": args.snapshot_id,
                           "expected_head_operation_id": args.expected_head_operation_id}
                result = (personal_mirror_admin.apply(value, **options,
                            expected_snapshot_sha256=args.expected_snapshot_sha256) if args.apply
                          else personal_mirror_admin.preview(value, **options))
            elif args.cmd == "personal-mirror-status":
                result = personal_mirror_admin.status(value, operation_id=args.operation_id,
                    expected_request_sha256=args.expected_request_sha256)
            else:
                if not args.apply:
                    return _personal_admin_error("PERSONAL_ADMIN_EXPLICIT_APPLY_REQUIRED")
                if args.wait and value.identity != storage_admin.profile().identity:
                    return _personal_admin_error("PERSONAL_MIRROR_PROFILE_CHANGED")
                from . import tasks, worker
                result = tasks.submit_personal_mirror(args.operation_id, args.expected_request_sha256,
                                                      request_id=args.request_id, profile=value)
                if args.wait:
                    worker.process_pending()
                    result = tasks.get(result["task_id"], include_internal=True, profile=value)
                    print(json.dumps(result, ensure_ascii=False))
                    return 1 if result["status"] == "failed" else (3 if result["status"] != "done" else 0)
        except Exception as exc:
            return _personal_admin_error(getattr(exc, "code", "PERSONAL_MIRROR_FAILED"))
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "sync-source-continuity":
        from . import sync_source_continuity_admin, storage_admin, storage_lifecycle
        from .sync_protocol import SyncError
        try:
            selection = (args.continuity_id, args.activated_at, args.expected_digest)
            if (args.apply and any(value is None for value in selection)
                    or not args.apply and any(value is not None for value in selection)):
                return _source_continuity_error("SYNC_SOURCE_CONTINUITY_EXPECTATION_REQUIRED")
            profile = storage_admin.profile(database=args.db, media=args.media, reports=args.reports, checkpoints=args.checkpoints)
            if args.apply:
                result = sync_source_continuity_admin.activate(profile=profile, continuity_id=args.continuity_id,
                    activated_at=args.activated_at, expected_basis_sha256=args.expected_digest)
            else:
                result = sync_source_continuity_admin.preview(profile=profile)
        except (SyncError, storage_lifecycle.StorageLifecycleError) as exc:
            return _source_continuity_error(exc.code)
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            return _source_continuity_error("SYNC_SOURCE_CONTINUITY_ADMIN_FAILED")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "home-restore":
        from . import home_recovery_dispatch as home_recovery, storage_admin
        try:
            if args.resume:
                if args.source_id is not None or args.expected_digest is not None or args.operation_id is None:
                    return _home_restore_error("HOME_RECOVERY_EXPECTATION_REQUIRED")
            elif args.apply:
                if args.source_id is None or args.expected_digest is None or args.operation_id is None:
                    return _home_restore_error("HOME_RECOVERY_EXPECTATION_REQUIRED")
            elif args.source_id is None or args.expected_digest is not None or args.operation_id is not None:
                return _home_restore_error("HOME_RECOVERY_EXPECTATION_REQUIRED")
            profile = storage_admin.profile(database=args.db, media=args.media,
                                            reports=args.reports, checkpoints=args.checkpoints)
            if args.resume:
                result = home_recovery.resume(profile=profile, operation_id=args.operation_id)
            elif args.apply:
                result = home_recovery.apply(profile=profile, source_id=args.source_id,
                    expected_digest=args.expected_digest, operation_id=args.operation_id)
            else:
                result = home_recovery.preview(profile=profile, source_id=args.source_id)
        except db.SchemaVersionError:
            return _home_restore_error("SCHEMA_UPGRADE_REQUIRED")
        except Exception as exc:
            return _home_restore_error(getattr(exc, "code", "HOME_RECOVERY_FAILED"))
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "home-checkpoint":
        from . import home_recovery_source_dispatch as home_recovery_source, storage_admin
        try:
            if args.create != args.apply:
                return _home_checkpoint_error("HOME_CHECKPOINT_EXPECTATION_REQUIRED")
            profile = storage_admin.profile(database=args.db, media=args.media,
                                            reports=args.reports, checkpoints=args.checkpoints)
            if args.create:
                result = home_recovery_source.create_checkpoint(profile=profile, source_id=args.source_id)
            else:
                result = home_recovery_source.read_checkpoint(profile=profile, source_id=args.source_id)
        except db.SchemaVersionError:
            return _home_checkpoint_error("SCHEMA_UPGRADE_REQUIRED")
        except Exception as exc:
            return _home_checkpoint_error(getattr(exc, "code", "HOME_CHECKPOINT_FAILED"))
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in {"sync-abandon", "sync-abandon-resume", "sync-retire-local"}:
        from . import sync_sender_admin, storage_admin
        try:
            profile = storage_admin.profile(database=args.db, media=args.media,
                                            reports=args.reports, checkpoints=args.checkpoints)
            if args.cmd == "sync-abandon-resume":
                result = sync_sender_admin.resume_abandon(profile, args.operation_id,
                    sender_config=args.sender_config.absolute() if args.sender_config is not None else None)
            else:
                resuming = args.cmd == "sync-retire-local" and args.resume
                if (args.apply and (args.expected_digest is None or args.operation_id is None)
                        or resuming and (args.operation_id is None or args.expected_digest is not None)
                        or not args.apply and not resuming and (args.expected_digest is not None or args.operation_id is not None)):
                    return _sender_admin_error("SYNC_SENDER_ARGUMENT_INVALID")
                if args.cmd == "sync-abandon":
                    result = (sync_sender_admin.abandon(profile, args.publication_id,
                        expected_digest=args.expected_digest, operation_id=args.operation_id) if args.apply
                        else sync_sender_admin.preview_abandon(profile, args.publication_id))
                else:
                    selected = sync_sender_admin.read_local_selection(args.request_file.absolute())
                    result = (sync_sender_admin.resume_retire_local(profile, selected, operation_id=args.operation_id) if resuming
                        else sync_sender_admin.retire_local(profile, selected, expected_digest=args.expected_digest,
                        operation_id=args.operation_id) if args.apply
                        else sync_sender_admin.preview_retire_local(profile, selected))
            encoded = json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return _sender_admin_error(getattr(exc, "code", "SYNC_SENDER_ADMIN_FAILED"))
        print(encoded)
        return 0

    if args.cmd == "storage-roots":
        from . import storage_roots, storage_gc, storage_gc_contract, storage_lifecycle, storage_members
        from . import storage_readonly, storage_home_roots, storage_checkpoint_roots, storage_backup_roots, storage_source
        try:
            if (args.apply and not (args.expected_digest and args.operation_id)
                    or args.resume and (not args.operation_id or args.expected_digest is not None
                        or args.keep_publication is not None or args.keep_recovery is not None or args.namespace is not None)
                    or not (args.apply or args.resume) and (args.expected_digest is not None or args.operation_id is not None)):
                return _storage_roots_error("ROOTS_ARGUMENT_INVALID")
            if args.members_file is None and not all((args.db, args.media, args.reports, args.checkpoints)):
                return _storage_roots_error("STORAGE_PROFILE_REQUIRED")
            profiles, declarations = _membership_input(args)
            if declarations:
                return _storage_roots_error("ROOTS_DECLARATIONS_NOT_ALLOWED")
            if args.resume:
                result = storage_gc.resume(profiles, operation_id=args.operation_id)
            else:
                policy = storage_gc_contract.default_policy(
                    keep_publication=3 if args.keep_publication is None else args.keep_publication,
                    keep_recovery=3 if args.keep_recovery is None else args.keep_recovery,
                    namespaces=sorted(set(args.namespace)) if args.namespace else storage_gc_contract.NAMESPACES)
                if args.apply:
                    result = storage_gc.apply(profiles, expected_preview_sha256=args.expected_digest,
                        operation_id=args.operation_id, policy=policy)
                else:
                    result = storage_roots.preview(profiles, policy=policy)
            encoded = json.dumps(result, ensure_ascii=False)
        except (storage_roots.RootError, storage_gc.GCError, storage_lifecycle.StorageLifecycleError, storage_members.MembershipError,
                storage_readonly.ReadonlyImageError, storage_home_roots.HomeRootError,
                storage_checkpoint_roots.CheckpointRootError, storage_backup_roots.BackupRootError,
                storage_source.SourceReadError) as exc:
            return _storage_roots_error(exc.code)
        except db.SchemaVersionError:
            return _storage_roots_error("SCHEMA_UPGRADE_REQUIRED")
        except (OSError, ValueError, TypeError):
            return _storage_roots_error("ROOTS_CONFIG_INVALID")
        except Exception:
            return _storage_roots_error("GC_OPERATION_FAILED" if args.apply or args.resume else "ROOTS_PREVIEW_FAILED")
        print(encoded)
        return 2 if (args.apply or args.resume) and result["state"] != "completed" else 0

    if args.cmd in ("storage-members", "storage-member-init"):
        from . import storage_admin, storage_lifecycle, storage_members
        try:
            if args.cmd == "storage-member-init":
                value = storage_admin.profile(database=args.db, media=args.media,
                                              reports=args.reports, checkpoints=args.checkpoints)
                result = storage_admin.setup(value)
            else:
                if (args.apply and (args.expected_digest is None or args.operation_id is None)
                        or not args.apply and (args.expected_digest is not None or args.operation_id is not None)):
                    raise storage_members.MembershipError("MEMBERSHIP_EXPECTATION_REQUIRED")
                profiles, declarations = _membership_input(args)
                if args.apply:
                    result = storage_members.enroll(profiles, expected_digest=args.expected_digest,
                        declarations=declarations, operation_id=args.operation_id)
                else:
                    result = storage_members.membership_preview(profiles, declarations=declarations)
        except (storage_members.MembershipError, storage_lifecycle.StorageLifecycleError) as exc:
            print(json.dumps({"code": exc.code, "detail": "成员登记未完成，请核对完整成员清单、存储状态与最新预览。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except (OSError, ValueError, TypeError):
            print(json.dumps({"code": "MEMBERSHIP_CONFIG_INVALID", "detail": "成员配置格式无效或不可读。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in ("storage-init", "storage-status"):
        from . import storage_admin, storage_lifecycle
        try:
            value = storage_admin.profile(database=args.db, media=args.media,
                                          reports=args.reports, checkpoints=args.checkpoints)
            result = (storage_admin.initialize(value) if args.cmd == "storage-init"
                      else storage_admin.status(value))
        except storage_lifecycle.StorageLifecycleError as exc:
            print(json.dumps({"code": exc.code, "detail": "文件保护设施操作未完成，请核对目录、权限和占用情况。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except (OSError, ValueError):
            print(json.dumps({"code": "STORAGE_CONFIG_INVALID", "detail": "文件保护设施配置无效或不可读。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "frame-cache":
        from . import frame_cache
        try:
            if args.apply != (args.expected_digest is not None):
                print(json.dumps({"code": "FRAME_CACHE_EXPECTATION_REQUIRED",
                                  "detail": "执行清理须同时提供 --apply 与预览返回的 --expected-digest。"}, ensure_ascii=False), file=sys.stderr)
                return 2
            budget = config.frame_cache_budget_bytes() if args.budget_bytes is None else args.budget_bytes
            kwargs = {"root": config.FRAME_CACHE, "budget_bytes": budget}
            result = (frame_cache.prune(**kwargs, expected_digest=args.expected_digest)
                      if args.apply else frame_cache.preview(**kwargs))
        except frame_cache.FrameCacheError as exc:
            print(json.dumps({"code": exc.code, "detail": "帧缓存操作未完成，请重新预览并核对本机缓存。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except (OSError, ValueError):
            print(json.dumps({"code": "FRAME_CACHE_CONFIG_INVALID", "detail": "帧缓存配置无效或不可读。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in ("init-db", "migrate-db"):
        from . import storage_admin, storage_backups, storage_lifecycle
        try:
            if args.cmd == "init-db":
                result = db.initialize(args.db)
            else:
                value = storage_admin.profile(database=args.db, media=args.media,
                                              reports=args.reports, checkpoints=args.checkpoints)
                if args.initialize_storage_offline:
                    storage_backups.initialize_offline(value)
                result = db.migrate(value.database, profile=value)
        except db.SchemaVersionError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except (storage_lifecycle.StorageLifecycleError, storage_backups.BackupError) as exc:
            print(json.dumps({"code": exc.code, "detail": "迁移存储保护未完成；请停止写入、核对完整路径并显式初始化离线设施。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({"schema_version": result.version, "changed": result.changed,
                          "backup_path": str(result.backup_path) if result.backup_path else None,
                          "manifest_path": str(result.manifest_path) if result.manifest_path else None}))
        return 0

    if args.cmd in ("sync-identity", "sync-pair"):
        from . import sync_access, sync_queue
        from .sync_protocol import SyncError
        try:
            if args.cmd == "sync-pair" and not config.is_cloud():
                raise SyncError("Pairing requires a cloud target", "SYNC_CLOUD_REQUIRED", 403)
            con = db.connect()  # Identity commands never initialize or migrate.
            try:
                if args.cmd == "sync-identity":
                    result = sync_access.identity(con)
                else:
                    source = sync_access.read_source_file(args.source)
                    result = (sync_queue.refresh_peer_schema if args.refresh_schema else sync_queue.bind_peer)(con, source)
                    con.commit()
            finally:
                con.close()
        except SyncError as exc:
            print(json.dumps({"code": exc.code, "detail": "同步命令失败，请核对身份文件、角色和配对状态。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except Exception:
            print(json.dumps({"code": "SYNC_ADMIN_FAILED", "detail": "同步命令失败，请检查本机配置。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in ("sync-recovery-status", "sync-recovery-checkpoints", "sync-restore", "sync-resume"):
        from . import sync_recovery, storage_admin, storage_lifecycle, storage_members
        from .sync_protocol import SyncError
        try:
            changing = args.cmd in ("sync-restore", "sync-resume")
            if changing:
                required = (args.operation_id, args.expected_library_id, args.expected_epoch,
                            args.expected_revision, args.expected_control_revision)
                if args.apply and any(value is None for value in required):
                    raise SyncError("Explicit preview version and operation ID are required", "SYNC_RECOVERY_EXPECTATION_REQUIRED", 400)
                if not args.apply and any(value is not None for value in required):
                    raise SyncError("Mutation arguments require --apply", "SYNC_RECOVERY_EXPECTATION_REQUIRED", 400)
                if args.apply:
                    # This early-return CLI branch bypasses ordinary worker
                    # startup. Mutating recovery needs the same member admission;
                    # previews deliberately perform no setup or repair.
                    storage_admin.setup()
            con = db.connect()
            try:
                if args.cmd == "sync-recovery-checkpoints":
                    result = sync_recovery.list_recovery_checkpoints(con, limit=args.limit)
                elif args.cmd == "sync-restore" and not args.apply:
                    result = sync_recovery.preview_restore(con, args.checkpoint_id, checkpoint_kind=args.checkpoint_kind)
                elif args.cmd == "sync-recovery-status" or args.cmd == "sync-resume" and not args.apply:
                    con.execute("BEGIN")
                    result = {"current_version": db.knowledge_version(con), "control": sync_recovery.state(con)}
                    result["control_revision"] = result["control"]["revision"]
                    con.rollback()
                else:
                    expected = {"library_id": args.expected_library_id, "library_epoch": args.expected_epoch,
                                "kb_revision": args.expected_revision}
                    kwargs = {"expected_version": expected, "expected_control_revision": args.expected_control_revision,
                              "operation_id": args.operation_id}
                    result = (sync_recovery.restore(con, args.checkpoint_id, checkpoint_kind=args.checkpoint_kind, **kwargs)
                              if args.cmd == "sync-restore" else sync_recovery.resume(con, **kwargs))
            finally:
                con.close()
        except (SyncError, storage_lifecycle.StorageLifecycleError, storage_members.MembershipError) as exc:
            print(json.dumps({"code": exc.code,
                "detail": "恢复命令未完成，请核对预览版本、控制版本、操作编号和本机角色；回执不确定时保留原参数重试。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except Exception:
            print(json.dumps({"code": "SYNC_RECOVERY_FAILED", "detail": "恢复命令未确认，请保留操作编号并核对本机记录。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd in ("sync-checkpoints", "sync-checkpoint-preview"):
        from . import sync_checkpoints, storage_lifecycle
        from .sync_protocol import SyncError
        try:
            con = db.connect()  # Read-only administration must never create a library.
            try:
                result = (sync_checkpoints.list_checkpoints(con, limit=args.limit)
                          if args.cmd == "sync-checkpoints"
                          else sync_checkpoints.preview(con, args.checkpoint_id))
            finally:
                con.close()
        except (SyncError, storage_lifecycle.StorageLifecycleError) as exc:
            print(json.dumps({"code": exc.code, "detail": "归档读取失败，请核对归档编号和知识库状态。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except Exception:
            print(json.dumps({"code": "SYNC_CHECKPOINT_FAILED", "detail": "归档读取失败，请检查本机配置。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False))
        return 0

    if args.cmd == "reconciliation":
        from . import reconciliation
        try:
            con = db.connect()  # Reporting never initializes or migrates a library.
            try:
                if args.markdown:
                    result = reconciliation.export_markdown(con, reason=args.reason, scope=args.scope)
                else:
                    result = json.dumps(reconciliation.query(con, reason=args.reason, scope=args.scope,
                                        offset=args.offset, limit=args.limit), ensure_ascii=False)
            finally:
                con.close()
        except reconciliation.ReconciliationError as exc:
            print(json.dumps({"code": exc.code, "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2
        except db.SchemaVersionError:
            print(json.dumps({"code": "SCHEMA_UPGRADE_REQUIRED", "detail": "知识库需要显式初始化或升级。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        except Exception:
            print(json.dumps({"code": "RECONCILIATION_FAILED", "detail": "对账读取失败，请检查本机配置。"}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(result)
        return 0

    if args.cmd == "serve":
        from .sync_access import trusted_proxy_ips
        from .sync_protocol import SyncError
        try:
            proxy_ips = trusted_proxy_ips()
        except SyncError:
            print("trusted_proxy_ips 必须是明确的代理 IP 列表；不接受通配符或网段。", file=sys.stderr)
            return 2

    if config.is_cloud() and args.cmd in (
            "import", "import-batch", "import-favlist", "run", "judge", "report", "sync-run"):
        # a cloud node never spends pipeline/LLM quota (docs/sync-plan.md §1);
        # use the web UI, which enqueues URLs for the home agent instead
        print(f"node_role=cloud: '{args.cmd}' is disabled on a cloud node "
              "(serving only; imports go through the web UI queue)")
        return 2

    if args.cmd == "sync-run":
        from .sync_agent import settings
        from .sync_protocol import SyncError
        try:
            settings()
        except SyncError as exc:
            print(json.dumps({"code": exc.code, "detail": "请配置并启用家庭同步后再运行。"}, ensure_ascii=False), file=sys.stderr)
            return 2

    # Startup owns new database initialization. Existing databases are only
    # validated; upgrades require the explicit maintenance command above.
    try:
        if not config.db_path().exists():
            db.initialize()
        with db.connect() as con:
            pass
        con.close()
    except db.SchemaVersionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.cmd in ("import", "import-batch", "import-favlist", "run", "judge", "report", "serve", "sync-run"):
        from . import storage_admin, storage_lifecycle
        try:
            # Enroll before starting a worker. Direct ASGI launch uses the same
            # explicit setup and a second startup is a read-only admission.
            storage_admin.setup()
        except (storage_lifecycle.StorageLifecycleError, OSError, ValueError) as exc:
            print(json.dumps({"code": getattr(exc, "code", "STORAGE_CONFIG_INVALID"),
                              "detail": "数据库已可用，但存储成员准入未完成；请核对目录、登记和最新成员预览。"}, ensure_ascii=False), file=sys.stderr)
            return 2

    if args.cmd in ("import", "import-batch", "import-favlist"):
        from . import import_tasks, storage_admin, tasks, worker
        profile = storage_admin.profile()
        try:
            if args.cmd == "import":
                result = import_tasks.submit_one(args.target, args.request_id, allow_local=True,
                                                 process=not args.no_process, profile=profile)
            elif args.cmd == "import-batch":
                if args.file == "-":
                    targets = [line.strip() for line in sys.stdin if line.strip() and not line.lstrip().startswith("#")]
                else:
                    with open(args.file, encoding="utf-8") as source:
                        targets = [line.strip() for line in source if line.strip() and not line.lstrip().startswith("#")]
                result = import_tasks.submit_batch(targets, args.request_id, allow_local=True, profile=profile)
            else:
                result = import_tasks.submit_favlist(args.url, args.request_id, cookies=args.cookies, limit=args.limit, profile=profile)
            print(json.dumps(result, ensure_ascii=False))
            if args.wait:
                worker.process_pending()
                ids = result.get("task_ids", [result.get("task_id")])
                completed = [tasks.get(job_id, profile=profile) for job_id in ids]
                child_ids = [job_id for value in completed if value and type(value.get("result")) is dict
                             for job_id in value["result"].get("task_ids", []) if job_id not in ids]
                completed.extend(tasks.get(job_id, profile=profile) for job_id in dict.fromkeys(child_ids))
                print(json.dumps({"tasks": completed}, ensure_ascii=False))
                if any(value and value["status"] == "failed" for value in completed):
                    return 1
                if any(value and value["status"] in ("pending", "running") for value in completed):
                    return 3
            return 0
        except tasks.TaskError as exc:
            print(json.dumps({"code": exc.code, "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 2

    if args.cmd == "run":
        from . import tasks, worker
        if args.video_id:
            task = tasks.submit("process", args.video_id, {"force": args.force})
            worker.process_pending()
            result = tasks.get(task["task_id"])
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 1 if result["status"] == "failed" else 0
        n = worker.process_pending()
        print(f"[run] processed {n} jobs")
        return 0

    if args.cmd == "sync-run":
        from . import tasks, worker
        job_id = tasks.submit_sync()
        worker.process_pending(follow=args.follow)
        result = tasks.get(job_id, include_internal=True)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["status"] == "failed" or (result.get("result") or {}).get("error_code") else 0

    if args.cmd == "status":
        con = db.connect()
        rows = con.execute(
            """SELECT video_id, status, title, duration_ms,
                      (SELECT COUNT(*) FROM knowledge_atom ka WHERE ka.video_id=va.video_id) AS atoms
               FROM video_asset va ORDER BY imported_at""").fetchall()
        for r in rows:
            print(f"{r['video_id']:22} {r['status']:12} atoms={r['atoms']:<4} "
                  f"{(r['duration_ms'] or 0)//1000:>4}s  {(r['title'] or '')[:38]}")
        jobs = con.execute("SELECT status, COUNT(*) c FROM job GROUP BY status").fetchall()
        print("jobs:", {j["status"]: j["c"] for j in jobs} or "none")
        return 0

    if args.cmd == "judge":
        from . import tasks, worker
        task = tasks.submit("judge", payload={"threshold": args.threshold})
        worker.process_pending()
        result = tasks.get(task["task_id"])
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 1 if result["status"] == "failed" else 0

    if args.cmd == "report":
        from . import tasks, worker
        task = tasks.submit("report", payload={"mode": "summary" if args.summary else "static"})
        worker.process_pending()
        result = tasks.get(task["task_id"])
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 1 if result["status"] == "failed" else 0

    if args.cmd == "serve":
        import uvicorn
        from app.main import app
        # resume jobs left pending by a previous run (import threads die with
        # the process) - steps are idempotent/meta-tracked, so this is safe.
        # Cloud nodes must not run the pipeline (docs/sync-plan.md §1).
        from . import worker
        try:
            if not config.is_cloud():
                worker.ensure_running()
            else:
                print("[serve] node_role=cloud: pipeline worker disabled", flush=True)
            uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips=proxy_ips)
        finally:
            # Also covers startup failures before ASGI lifespan is entered.
            while not worker.stop_running(timeout=5):
                pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
