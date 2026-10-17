"""Interactive one-time Windows Agent activation."""
import getpass
import json
import platform
from pathlib import Path
from urllib.request import Request,urlopen
from app.config import settings
from app.database import DATA_DIR

FILE=DATA_DIR/"agent_activation.json"
def main():
    server=settings.sarae_cloud_api_url.rstrip("/")
    if not server or not server.startswith(("https://","http://127.0.0.1:","http://localhost:")):
        raise SystemExit("Cần SARAE_CLOUD_API_URL qua HTTPS (hoặc localhost)")
    key=getpass.getpass("Nhập mã kích hoạt Sarae (không hiển thị): ").strip()
    payload=json.dumps({"activation_key":key,"hostname":platform.node()}).encode()
    req=Request(server+"/identity/activate",method="POST",data=payload,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=20) as res:result=json.load(res)
    FILE.write_text(json.dumps({"agent_id":result["agent_id"],"agent_credential":result["agent_credential"]}),encoding="utf-8")
    print("Đã kích hoạt Agent:",result["agent_id"])

if __name__=="__main__":main()
