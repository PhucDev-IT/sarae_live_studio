"""Quản lý khách hàng Studio trên server PC nhà (docs/HYBRID_STUDIO_SERVER.md, mục "Vận hành server").

Chạy trong thư mục backend/:  python -m tools.studio_admin <lệnh> ...
Đọc/ghi thẳng SQLite trong DATA_DIR (biến SARAE_DATA_DIR, mặc định backend/data): identity.sqlite3 và
studio_gateway.sqlite3. Không cần gateway đang chạy. Khách không đăng nhập: mật khẩu được tạo ngẫu nhiên và
không in ra; khách chỉ dùng mã kích hoạt.
"""
from __future__ import annotations

import argparse
import secrets
import sqlite3
import sys
import uuid
from datetime import date, datetime, time, timezone

from app import identity, studio_gateway
from app.identity import db_open, license_not_expired, pass_hash, secret_hash


class AdminError(Exception):
    pass


def expiry_from_date(value: str) -> str:
    """'2027-01-31' -> hết ngày 31/01/2027 giờ Việt Nam, lưu dạng ISO UTC."""
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise AdminError(f"Ngày không hợp lệ: {value} (cần dạng YYYY-MM-DD)") from None
    return datetime.combine(day, time(23, 59, 59), studio_gateway.VN).astimezone(timezone.utc).isoformat()


def show_expiry(expires_at: str | None) -> str:
    if not expires_at:
        return "vô thời hạn"
    try:
        at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return at.astimezone(studio_gateway.VN).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return expires_at


def owner_by_email(db, email: str):
    row = db.execute("""SELECT u.id, u.email, u.active, l.expires_at, l.max_agents, l.enabled FROM users u
        JOIN licenses l ON l.owner_id=u.id WHERE u.email=? AND u.role='OWNER'""", (email.strip().lower(),)).fetchone()
    if row is None:
        raise AdminError(f"Không tìm thấy khách {email.strip().lower()}")
    return row


# --- operations (also used by tests) -----------------------------------------------------------------------------
def create_customer(email: str, max_devices: int, expires_at: str | None) -> str:
    email = email.strip().lower()
    if "@" not in email or not 5 <= len(email) <= 254:
        raise AdminError("Email không hợp lệ")
    if not 1 <= max_devices <= 100:
        raise AdminError("Số máy phải từ 1 đến 100")
    owner_id, salt = str(uuid.uuid4()), secrets.token_hex(16)
    with db_open() as db:
        try:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?,1)",
                       (owner_id, email, pass_hash(secrets.token_urlsafe(32), salt), salt, "OWNER"))
            db.execute("INSERT INTO licenses VALUES(?,?,?,1)", (owner_id, expires_at, max_devices))
        except sqlite3.IntegrityError:
            raise AdminError(f"Email {email} đã tồn tại") from None
    return owner_id


def issue_key(email: str) -> str:
    key = "SARAE-ACT-" + secrets.token_urlsafe(30)
    with db_open() as db:
        owner = owner_by_email(db, email)
        db.execute("INSERT INTO activation_keys VALUES(?,?,NULL,?)", (secret_hash(key), owner["id"], identity.now()))
    return key


def set_expiry(email: str, expires_at: str | None) -> None:
    with db_open() as db:
        owner = owner_by_email(db, email)
        db.execute("UPDATE licenses SET expires_at=? WHERE owner_id=?", (expires_at, owner["id"]))


def set_enabled(email: str, enabled: bool) -> None:
    with db_open() as db:
        owner = owner_by_email(db, email)
        db.execute("UPDATE licenses SET enabled=? WHERE owner_id=?", (int(enabled), owner["id"]))


def revoke_device(agent_id: str) -> str:
    with db_open() as db:
        row = db.execute("SELECT hostname FROM registered_agents WHERE id=?", (agent_id.strip(),)).fetchone()
        if row is None:
            raise AdminError(f"Không tìm thấy máy {agent_id.strip()}")
        db.execute("UPDATE registered_agents SET enabled=0 WHERE id=?", (agent_id.strip(),))
    return row["hostname"]


def customers() -> list[dict]:
    with db_open() as db:
        rows = db.execute("""SELECT u.id, u.email, u.active, l.expires_at, l.max_agents, l.enabled,
            (SELECT COUNT(*) FROM registered_agents a WHERE a.owner_id=u.id AND a.enabled=1) AS devices
            FROM users u JOIN licenses l ON l.owner_id=u.id WHERE u.role='OWNER' ORDER BY u.email""").fetchall()
    return [dict(r) for r in rows]


def devices(email: str) -> list[dict]:
    with db_open() as db:
        owner = owner_by_email(db, email)
        rows = db.execute("""SELECT id, hostname, platform, enabled, created_at FROM registered_agents
            WHERE owner_id=? ORDER BY created_at""", (owner["id"],)).fetchall()
    return [dict(r) for r in rows]


def status_text(row: dict) -> str:
    if not row["active"] or not row["enabled"]:
        return "đang khóa"
    return "hợp lệ" if license_not_expired(row["expires_at"]) else "hết hạn"


# --- CLI ---------------------------------------------------------------------------------------------------------
def _expiry_arg(args) -> str | None:
    return None if args.never else expiry_from_date(args.expires)


def run(args) -> None:
    cmd = args.command
    if cmd == "create-customer":
        create_customer(args.email, args.max_devices, _expiry_arg(args))
        print(f"Đã tạo khách {args.email.strip().lower()}: tối đa {args.max_devices} máy, "
              f"hạn {show_expiry(_expiry_arg(args))}.")
        print(f"Cấp mã kích hoạt: python -m tools.studio_admin issue-key --email {args.email.strip().lower()}")
    elif cmd == "issue-key":
        key = issue_key(args.email)
        print("Mã kích hoạt (chỉ hiện MỘT lần, dùng cho MỘT máy):")
        print(key)
    elif cmd == "list":
        rows = customers()
        if not rows:
            print("Chưa có khách nào.")
        priority = studio_gateway.priority_owners()
        for r in rows:
            print(f"{r['email']:<36} {status_text(r):<10} máy {r['devices']}/{r['max_agents']:<4} "
                  f"hạn {show_expiry(r['expires_at'])}{'  [ưu tiên]' if r['id'] in priority else ''}")
    elif cmd == "priority":
        with db_open() as db:
            owner_id = owner_by_email(db, args.email)["id"]
        studio_gateway.set_owner_priority(owner_id, 1 if args.on else 0)
        print(f"Đã {'bật' if args.on else 'tắt'} ưu tiên TTS cho {args.email.strip().lower()}"
              + (": câu của tài khoản này được tạo trước các câu đang chờ của khách khác." if args.on else "."))
    elif cmd == "extend":
        set_expiry(args.email, _expiry_arg(args))
        print(f"Đã đặt hạn license của {args.email.strip().lower()}: {show_expiry(_expiry_arg(args))}.")
    elif cmd in ("disable", "enable"):
        set_enabled(args.email, cmd == "enable")
        print(f"Đã {'mở' if cmd == 'enable' else 'khóa'} license của {args.email.strip().lower()}.")
    elif cmd == "devices":
        rows = devices(args.email)
        if not rows:
            print("Khách này chưa kích hoạt máy nào.")
        for r in rows:
            state = "đang dùng" if r["enabled"] else "đã gỡ"
            print(f"{r['id']}  {r['hostname']:<24} {r['platform']:<8} {state:<10} kích hoạt {show_expiry(r['created_at'])}")
    elif cmd == "revoke-device":
        hostname = revoke_device(args.agent_id)
        print(f"Đã gỡ máy {hostname} ({args.agent_id.strip()}). Studio trên máy đó sẽ bị khóa ở lần kiểm tra license tới.")
    elif cmd == "usage":
        owner_id, emails = None, {r["id"]: r["email"] for r in customers()}
        if args.email:
            with db_open() as db:
                owner_id = owner_by_email(db, args.email)["id"]
        rows = studio_gateway.usage_rows(owner_id, args.days)
        if not rows:
            print(f"Không có usage TTS trong {args.days} ngày gần nhất.")
            return
        print(f"{'Ngày':<11} {'Khách':<32} {'Máy':<36} {'Câu':>6} {'Lỗi':>5} {'Ký tự':>8} {'Audio MB':>9}")
        for r in rows:
            print(f"{r['day']:<11} {emails.get(r['owner_id'], r['owner_id'])[:32]:<32} {r['agent_id'][:36]:<36} "
                  f"{r['requests']:>6} {r['failures']:>5} {r['chars']:>8} {r['audio_bytes'] / 1048576:>9.1f}")
        print(f"Tổng: {sum(r['requests'] for r in rows)} câu, {sum(r['failures'] for r in rows)} lỗi, "
              f"{sum(r['chars'] for r in rows)} ký tự, {sum(r['audio_bytes'] for r in rows) / 1048576:.1f} MB audio.")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m tools.studio_admin", description="Quản lý khách hàng Sarae Studio.")
    sub = p.add_subparsers(dest="command", required=True, metavar="<lệnh>")

    def with_expiry(cmd):
        group = cmd.add_mutually_exclusive_group(required=True)
        group.add_argument("--expires", metavar="YYYY-MM-DD", help="hạn dùng, hết ngày đó theo giờ Việt Nam")
        group.add_argument("--never", action="store_true", help="không hết hạn")

    cmd = sub.add_parser("create-customer", help="tạo khách và license")
    cmd.add_argument("--email", required=True)
    cmd.add_argument("--max-devices", type=int, default=1, help="số máy tối đa (mặc định 1)")
    with_expiry(cmd)
    sub.add_parser("issue-key", help="cấp một mã kích hoạt dùng một lần").add_argument("--email", required=True)
    sub.add_parser("list", help="danh sách khách")
    with_expiry(cmd := sub.add_parser("extend", help="đổi hạn license"))
    cmd.add_argument("--email", required=True)
    sub.add_parser("disable", help="khóa license").add_argument("--email", required=True)
    sub.add_parser("enable", help="mở lại license").add_argument("--email", required=True)
    sub.add_parser("devices", help="các máy đã kích hoạt của khách").add_argument("--email", required=True)
    sub.add_parser("revoke-device", help="gỡ một máy").add_argument("--agent-id", required=True)
    cmd = sub.add_parser("priority", help="bật/tắt ưu tiên hàng đợi TTS cho một tài khoản")
    cmd.add_argument("--email", required=True)
    toggle = cmd.add_mutually_exclusive_group(required=True)
    toggle.add_argument("--on", action="store_true", help="câu của tài khoản này đi trước các câu đang chờ")
    toggle.add_argument("--off", action="store_true", help="xếp hàng như mọi khách")
    cmd = sub.add_parser("usage", help="usage TTS theo ngày (UTC+7)")
    cmd.add_argument("--email")
    cmd.add_argument("--days", type=int, default=7)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parser().parse_args(argv)
    try:
        run(args)
    except AdminError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
