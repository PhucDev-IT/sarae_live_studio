"""Command line for the sales brain's knowledge files (docs/AI_KNOWLEDGE_GUIDE.md). Run from backend/:

    python -m app.knowledge_cli validate FILE.jsonl [FILE2.jsonl ...]   # check files, no database needed
    python -m app.knowledge_cli import --owner EMAIL FILE.jsonl [--dry-run]
    python -m app.knowledge_cli export --owner EMAIL OUT.jsonl
    python -m app.knowledge_cli stats FILE.jsonl                         # how many lines of each kind/intent

Exit code 0 = no error, 1 = some lines are invalid (they are listed), 2 = bad usage.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8-sig")


def _owner_id(email: str) -> str:
    from app.identity import db_open
    with db_open() as db:
        row = db.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (email,)).fetchone()
    if row is None:
        sys.exit(f"Không có tài khoản {email}")
    return row["id"]


def _print_errors(errors: list[dict], label: str = "") -> None:
    for e in errors:
        print(f"  {label}dòng {e['line']}: {e['error']}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.knowledge_cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="kiểm tra file JSONL")
    v.add_argument("files", nargs="+")
    st = sub.add_parser("stats", help="thống kê file JSONL")
    st.add_argument("files", nargs="+")
    i = sub.add_parser("import", help="nạp file vào tài khoản")
    i.add_argument("--owner", required=True, help="email tài khoản shop")
    i.add_argument("--dry-run", action="store_true", help="chỉ kiểm tra, không ghi")
    i.add_argument("file")
    e = sub.add_parser("export", help="xuất dữ liệu của tài khoản ra JSONL")
    e.add_argument("--owner", required=True)
    e.add_argument("out")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from app import sales_brain
    if args.command in ("validate", "stats"):
        failed = False
        for path in args.files:
            records, errors = sales_brain.parse_jsonl(_read(path))
            if args.command == "stats":
                kinds = Counter(kind for _l, kind, _d in records)
                intents = Counter(d["intent"] for _l, kind, d in records if kind == "template")
                print(f"{path}: {dict(kinds)}")
                for intent, count in sorted(intents.items()):
                    print(f"  {intent}: {count}")
            ids = Counter(d["id"] for _l, kind, d in records if kind == "template" and d["id"])
            dupes = [tid for tid, n in ids.items() if n > 1]
            print(f"{path}: {len(records)} dòng hợp lệ, {len(errors)} dòng lỗi" +
                  (f", id trùng: {', '.join(dupes)}" if dupes else ""))
            _print_errors(errors)
            failed = failed or bool(errors) or bool(dupes)
        return 1 if failed else 0

    import app.main  # noqa: F401  registers every model
    from app.shop_schema import upgrade
    upgrade()
    owner = _owner_id(args.owner)
    if args.command == "import":
        report = sales_brain.import_jsonl(owner, _read(args.file), dry_run=args.dry_run)
        print(json.dumps({k: v for k, v in report.items() if k != "errors"}, ensure_ascii=False, indent=2))
        _print_errors(report["errors"])
        if args.dry_run:
            print("(chạy thử, chưa ghi gì)")
        return 1 if report["errors"] else 0
    Path(args.out).write_text(sales_brain.export_jsonl(owner), encoding="utf-8")
    print(f"Đã xuất {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
