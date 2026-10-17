"""Sarae Agent desktop control center (PySide6).

GUI executes in the interactive desktop session; a worker thread owns the
network/OBS loop. Closing hides to tray; Quit explicitly stops the worker.
"""
import json
import os
import platform
import sys
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from PySide6.QtCore import QObject, QThread, Signal, Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QLineEdit, QPushButton, QFileDialog, QTextEdit,
    QSystemTrayIcon, QMenu, QMessageBox, QCheckBox
)

from app.config import settings
from app.database import DATA_DIR
from agent.credentials import load as load_credentials, save as save_credentials
from agent.local_library import FOLDERS_FILE, scan as scan_media
from app.verification_monitor import status as monitor_status

SETTINGS_FILE=DATA_DIR/"agent_gui_settings.json"

def valid_server(value):
    url=value.strip().rstrip("/")
    if not url.startswith(("https://","http://localhost:","http://127.0.0.1:")):
        raise ValueError("Cloud phải dùng HTTPS (ngoại trừ localhost)")
    if not url.endswith("/api"):
        raise ValueError("Cloud API URL phải kết thúc bằng /api")
    return url

def read_gui_settings():
    try:return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError,ValueError):return {}

def persist_gui_settings(values):
    temp=SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding="utf-8")
    temp.replace(SETTINGS_FILE)

class Events(QObject):
    line=Signal(str)
    finished=Signal()

class LogWriter:
    def __init__(self,events,backup):
        self.events=events
        self.backup=backup
    def write(self,message):
        if message.strip():self.events.line.emit(message.rstrip())
    def flush(self):pass

class Worker(QThread):
    def __init__(self,events):
        super().__init__()
        self.events=events
        self.stop_flag=threading.Event()
    def run(self):
        from agent.main import main
        out,err=sys.stdout,sys.stderr
        try:
            sys.stdout=LogWriter(self.events,out)
            sys.stderr=LogWriter(self.events,err)
            main(self.stop_flag)
        except Exception as exc:
            self.events.line.emit("Agent dừng vì lỗi: "+str(exc))
        finally:
            sys.stdout,sys.stderr=out,err
            self.events.finished.emit()

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sarae Windows Agent")
        self.resize(710,660)
        self.setMinimumSize(570,540)
        self.exiting=False
        self.events=Events()
        self.events.line.connect(self.add_log)
        self.events.finished.connect(self.worker_finished)
        self.worker=None
        self.tray=QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon))
        menu=QMenu()
        show=QAction("Mở Sarae Agent",self);show.triggered.connect(self.showNormal);menu.addAction(show)
        quit_action=QAction("Thoát hoàn toàn",self);quit_action.triggered.connect(self.quit_app);menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.setToolTip("Sarae Windows Agent")
        if QSystemTrayIcon.isSystemTrayAvailable():self.tray.show()
        root=QWidget();self.setCentralWidget(root)
        layout=QVBoxLayout(root);layout.setSpacing(12)
        title=QLabel("Sarae Windows Agent");title.setObjectName("heading");layout.addWidget(title)
        description=QLabel("Kết nối Cloud · Quản lý video local · OBS & TikTok LIVE Studio")
        description.setObjectName("subheading");layout.addWidget(description)
        group=QGroupBox("Kết nối và kích hoạt");g=QVBoxLayout(group)
        self.url=QLineEdit(read_gui_settings().get("cloud_url",settings.sarae_cloud_api_url))
        self.url.setPlaceholderText("https://your-domain.com/api")
        g.addWidget(QLabel("Cloud API URL"));g.addWidget(self.url)
        self.key=QLineEdit();self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Mã kích hoạt do SUPER_ADMIN cấp")
        g.addWidget(QLabel("Mã kích hoạt (chỉ nhập lần đầu)"));g.addWidget(self.key)
        line=QHBoxLayout()
        self.activate=QPushButton("Kích hoạt thiết bị")
        self.activate.clicked.connect(self.activate_agent)
        line.addWidget(self.activate)
        self.device=QLabel("Chưa kích hoạt");line.addWidget(self.device,1)
        g.addLayout(line);layout.addWidget(group)
        group2=QGroupBox("Thư viện video trên Windows");f=QVBoxLayout(group2)
        self.folders=QTextEdit();self.folders.setReadOnly(True);self.folders.setMaximumHeight(86);f.addWidget(self.folders)
        buttons=QHBoxLayout()
        add=QPushButton("Thêm thư mục");add.clicked.connect(self.add_folder);buttons.addWidget(add)
        clear=QPushButton("Xóa danh sách thư mục");clear.clicked.connect(self.clear_folders);buttons.addWidget(clear)
        self.scan_button=QPushButton("Quét video");self.scan_button.clicked.connect(self.count_videos);buttons.addWidget(self.scan_button)
        f.addLayout(buttons);layout.addWidget(group2)
        row=QHBoxLayout()
        self.launch=QPushButton("Bắt đầu Agent");self.launch.clicked.connect(self.start_agent);row.addWidget(self.launch)
        self.halt=QPushButton("Dừng Agent");self.halt.clicked.connect(self.stop_agent);self.halt.setEnabled(False);row.addWidget(self.halt)
        layout.addLayout(row)
        self.state=QLabel("Trạng thái: Chưa chạy");layout.addWidget(self.state)
        self.auto=QCheckBox("Tự chạy Agent khi mở giao diện");self.auto.setChecked(bool(read_gui_settings().get("auto_run",True)))
        self.auto.toggled.connect(self.save_settings);layout.addWidget(self.auto)
        layout.addWidget(QLabel("Nhật ký hoạt động"))
        self.logs=QTextEdit();self.logs.setReadOnly(True);layout.addWidget(self.logs,1)
        self.refresh_folders()
        self.refresh_activation()
        self.tick=QTimer(self);self.tick.timeout.connect(self.refresh_local_status);self.tick.start(3000)
        self.setStyleSheet("""
            QMainWindow,QWidget {background:#101827;color:#e9eef7;font-size:13px}
            QLabel#heading {font-size:23px;font-weight:700}
            QLabel#subheading {color:#9cabc4}
            QGroupBox {border:1px solid #304157;border-radius:10px;margin-top:12px;padding:13px}
            QGroupBox::title {subcontrol-origin:margin;left:14px;padding:0 5px}
            QLineEdit,QTextEdit {background:#19263b;border:1px solid #34445e;border-radius:7px;padding:8px}
            QPushButton {background:#384c80;border:0;border-radius:7px;padding:9px 13px;font-weight:600}
            QPushButton:hover {background:#4a63a5}
            QPushButton:disabled {color:#73809a;background:#253048}
        """)
        if self.auto.isChecked() and self.device.text()!="Chưa kích hoạt":
            QTimer.singleShot(800,self.start_agent)

    def add_log(self,s):
        self.logs.append(s[:1200])
        if self.logs.document().blockCount()>300:
            self.logs.setPlainText("\n".join(self.logs.toPlainText().splitlines()[-200:]))
        self.logs.moveCursor(self.logs.textCursor().MoveOperation.End)

    def save_settings(self):
        persist_gui_settings({"cloud_url":self.url.text().strip(),"auto_run":self.auto.isChecked()})

    def refresh_activation(self):
        try:
            info=load_credentials()
            self.device.setText("Đã kích hoạt · "+info["agent_id"])
            self.key.setEnabled(False)
            self.activate.setEnabled(False)
        except Exception:
            self.device.setText("Chưa kích hoạt")

    def activate_agent(self):
        try:
            server=valid_server(self.url.text())
            key=self.key.text().strip()
            if not key:raise ValueError("Vui lòng nhập mã kích hoạt")
            data=json.dumps({"activation_key":key,"hostname":platform.node()}).encode("utf-8")
            req=Request(server+"/identity/activate",method="POST",data=data,
                        headers={"Content-Type":"application/json"})
            with urlopen(req,timeout=15) as res:result=json.load(res)
            save_credentials(result["agent_id"],result["agent_credential"])
            settings.sarae_cloud_api_url=server
            self.save_settings()
            self.key.clear()
            self.refresh_activation()
            self.add_log("Đã kích hoạt thành công. Agent ID: "+result["agent_id"])
        except HTTPError as exc:
            QMessageBox.warning(self,"Kích hoạt thất bại",f"Backend phản hồi HTTP {exc.code}")
        except Exception as exc:
            QMessageBox.warning(self,"Kích hoạt thất bại",str(exc))

    def get_folders(self):
        try:
            value=json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
            return [p for p in value if isinstance(p,str)]
        except (OSError,ValueError):return []

    def refresh_folders(self):
        values=self.get_folders()
        self.folders.setPlainText("\n".join(values) if values else "Chưa chọn thư mục video nào")

    def add_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Chọn thư mục chứa video MP4")
        if not folder:return
        values=self.get_folders()
        if folder not in values:values.append(folder)
        FOLDERS_FILE.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding="utf-8")
        self.refresh_folders()
        self.add_log("Đã thêm thư mục: "+folder)

    def clear_folders(self):
        if QMessageBox.question(self,"Xác nhận","Xóa tất cả thư mục đã cấu hình?")==QMessageBox.StandardButton.Yes:
            FOLDERS_FILE.write_text("[]",encoding="utf-8")
            self.refresh_folders()

    def count_videos(self):
        self.scan_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            files=scan_media()
            self.add_log(f"Đã tìm thấy {len(files)} video MP4. Chỉ metadata được đồng bộ lên Cloud.")
        except Exception as exc:
            self.add_log("Không quét được thư viện video: "+str(exc))
        finally:
            QApplication.restoreOverrideCursor()
            self.scan_button.setEnabled(True)

    def start_agent(self):
        if self.worker and self.worker.isRunning():return
        try:
            settings.sarae_cloud_api_url=valid_server(self.url.text())
            load_credentials()
            self.save_settings()
        except Exception as exc:
            QMessageBox.warning(self,"Chưa thể chạy Agent",str(exc));return
        self.worker=Worker(self.events)
        self.worker.start()
        self.launch.setEnabled(False);self.halt.setEnabled(True)
        self.state.setText("Trạng thái: Agent đang chạy")
        self.add_log("Đã khởi động Agent. Bạn có thể thu nhỏ ứng dụng xuống System Tray.")

    def stop_agent(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop_flag.set()
            self.state.setText("Trạng thái: Đang dừng...")

    def worker_finished(self):
        self.launch.setEnabled(True);self.halt.setEnabled(False)
        self.state.setText("Trạng thái: Đã dừng")

    def refresh_local_status(self):
        try:
            data=monitor_status()
            label=data["state"].get("status","idle")
            self.statusBar().showMessage("CAPTCHA: "+label+" · Monitor "+("đang chạy" if data["state"].get("running") else "đã dừng"))
        except Exception:pass

    def tray_activated(self,reason):
        if reason==QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal();self.activateWindow()

    def closeEvent(self,event):
        if not self.exiting and self.tray.isVisible():
            self.hide();event.ignore()
            self.tray.showMessage("Sarae Agent","Agent tiếp tục hoạt động dưới khay hệ thống.")
        else:event.accept()

    def quit_app(self):
        self.exiting=True
        self.stop_agent()
        if self.worker and self.worker.isRunning():
            if not self.worker.wait(16000):
                self.exiting=False
                QMessageBox.warning(self,"Agent đang dừng","Chờ Agent hoàn tất yêu cầu trước khi thoát.")
                return
        self.tray.hide()
        QApplication.instance().quit()

def main():
    app=QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window=Window()
    window.show()
    sys.exit(app.exec())

if __name__=="__main__":
    main()
