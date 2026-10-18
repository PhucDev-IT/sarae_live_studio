"""One-time SUPER_ADMIN setup. Does not print the password."""
import getpass
from app.identity import bootstrap_admin

def main():
    email=input("Email SUPER_ADMIN: ").strip()
    password=getpass.getpass("Mật khẩu (>=6 ký tự): ")
    if len(password)<6:raise SystemExit("Mật khẩu phải có ít nhất 6 ký tự")
    bootstrap_admin(email,password)
    print("Đã tạo SUPER_ADMIN. Đăng nhập qua /api/identity/login.")

if __name__=="__main__":main()
