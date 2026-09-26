# -*- coding: utf-8 -*-
"""reno CLI: import / run / status / judge / report / serve"""
import argparse
import json
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="reno")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("import", help="ingest URL or local file")
    p.add_argument("target")
    p.add_argument("--no-process", action="store_true")

    p = sub.add_parser("import-batch", help="ingest one URL per line from file (or '-' for stdin)")
    p.add_argument("file")

    p = sub.add_parser("import-favlist", help="enumerate a bilibili favorites list and ingest all")
    p.add_argument("url", help="e.g. https://space.bilibili.com/<mid>/favlist?fid=<fid>")
    p.add_argument("--cookies", default=None, help="Netscape cookie file for private lists")
    p.add_argument("--limit", type=int, default=0)

    p = sub.add_parser("run", help="process pending jobs (or one video)")
    p.add_argument("video_id", nargs="?")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("status")
    p = sub.add_parser("judge", help="cross-video dedup+conflict")
    p.add_argument("--threshold", type=float, default=0.22)
    p = sub.add_parser("report")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)

    args = ap.parse_args(argv)
    from . import db

    if args.cmd == "import":
        from . import ingest
        target = args.target
        if target.startswith("http"):
            res = ingest.ingest_url(target)
        else:
            res = ingest.ingest_file(target)
        print(json.dumps(res, ensure_ascii=False))
        if res["status"] == "duplicate":
            print(f"[duplicate] 与 {res.get('duplicate_of')} 内容相同,未重复入库")
        return 0

    if args.cmd == "import-batch":
        from .favlist import import_urls
        src = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
        urls = [ln.strip() for ln in src if ln.strip() and not ln.startswith("#")]
        print(f"[batch] {len(urls)} urls")
        stats = import_urls(urls)
        print(json.dumps({k: len(v) for k, v in stats.items()}, ensure_ascii=False))
        for f in stats["failed"][:5]:
            print("  failed:", f)
        return 0

    if args.cmd == "import-favlist":
        from .favlist import import_favlist
        try:
            from .favlist import enumerate_favlist
            items = enumerate_favlist(args.url, args.cookies)
            print(f"[favlist] {len(items)} items found")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "private" in msg.lower() or "log in" in msg.lower():
                print("[favlist] 这是私密收藏夹:需要你本人的 B 站 cookie。")
                print("  1) 浏览器装 'Get cookies.txt' 类插件,在 bilibili.com 导出 Netscape 格式")
                print("  2) config.local.json 加 \"ytdlp_cookies\": \"<cookies.txt路径>\"")
                print("  3) 重新执行本命令")
            else:
                print(f"[favlist] 枚举失败: {msg[:300]}")
            return 1
        stats = import_favlist(args.url, args.cookies, args.limit)
        print(json.dumps({k: len(v) for k, v in stats.items()}, ensure_ascii=False))
        for f in stats["failed"][:5]:
            print("  failed:", f)
        return 0

    if args.cmd == "run":
        from . import pipeline, worker
        if args.video_id:
            res = pipeline.run_video(args.video_id, force=args.force)
            print(json.dumps(res, ensure_ascii=False, default=str)[:2000])
            return 1 if any(isinstance(v, str) and v.startswith("ERROR")
                            for v in res.values()) else 0
        n = worker.process_pending()
        print(f"[run] processed {n} jobs")
        return 0

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
        from . import judge as j
        con = db.connect()
        print(json.dumps(j.run(con, args.threshold), ensure_ascii=False, default=str))
        return 0

    if args.cmd == "report":
        from . import report
        print(json.dumps(report.run_all(), ensure_ascii=False))
        return 0

    if args.cmd == "serve":
        import threading
        import uvicorn
        from app.main import app
        # resume jobs left pending by a previous run (import threads die with
        # the process) - steps are idempotent/meta-tracked, so this is safe
        from . import worker
        threading.Thread(target=worker.process_pending,
                         kwargs={"follow": False}, daemon=True).start()
        uvicorn.run(app, host=args.host, port=args.port)
        return 0


if __name__ == "__main__":
    sys.exit(main())
