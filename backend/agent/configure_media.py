"""Set local video folders in Agent configuration, never on the Cloud server."""
import json
from pathlib import Path
from agent.local_library import FOLDERS_FILE

def main():
    print("Nhập các thư mục chứa MP4 trên máy Windows (dòng trống để kết thúc).")
    found=[]
    while True:
        value=input("Thư mục video: ").strip().strip('"')
        if not value:break
        path=Path(value).expanduser().resolve()
        if not path.is_dir():
            print("Không tìm thấy thư mục:",path)
            continue
        if str(path) not in found:found.append(str(path))
    if not found:
        print("Chưa thay đổi cấu hình thư mục.")
        return
    FOLDERS_FILE.write_text(json.dumps(found,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Đã lưu",len(found),"thư mục. Agent sẽ đồng bộ danh sách MP4 định kỳ.")

if __name__=="__main__":
    main()
