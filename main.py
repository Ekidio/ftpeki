import ftplib
import os
import sys
import tempfile

from PyQt6.QtCore import QSettings, QThread, QMimeData, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QDragEnterEvent, QDropEvent, QFont, QIntValidator
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "FTPEKI Apple Silicon"
SETTINGS_ORG = "EKIDIO"
SETTINGS_APP = "FTPEKI_AppleSilicon"


# --- HÁTTÉR SZÁLAK A FLUID UI ÉRDEKÉBEN ---
class WorkerThread(QThread):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, target, *args):
        super().__init__()
        self.target = target
        self.args = args

    def run(self):
        try:
            result = self.target(*self.args)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class MacFTPApp(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(520, 700)

        # Aktív háttérszálak tárolója (megakadályozza a szálak idő előtti GC megsemmisítését)
        self.active_workers = []

        # Beállítások (kapcsolódási adatok mentése/betöltése)
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

        self.ftp = None
        self.current_dir = "/"

        self.init_ui()
        self.apply_mac_stylesheet()
        self.load_connection_settings()

    # --- KAPCSOLÓDÁSI ADATOK MENTÉSE / BETÖLTÉSE ---

    def load_connection_settings(self):
        self.input_host.setText(self.settings.value("host", "wh33.rackhost.hu"))
        self.input_port.setText(str(self.settings.value("port", 21)))
        self.input_user.setText(self.settings.value("user", "c70578ekidio_hu"))
        self.input_pass.setText(self.settings.value("password", ""))

    def save_connection_settings(self):
        self.settings.setValue("host", self.input_host.text().strip())
        self.settings.setValue("port", self.input_port.text().strip() or "21")
        self.settings.setValue("user", self.input_user.text().strip())
        self.settings.setValue("password", self.input_pass.text())
        self.settings.sync()
        self.set_status("💾 Kapcsolódási adatok elmentve.")

    def _start_worker(self, target_func, on_success, on_error=None):
        """Biztonságos háttérszál indító helper"""
        worker = WorkerThread(target_func)

        def handle_success(result):
            if worker in self.active_workers:
                self.active_workers.remove(worker)
            on_success(result)

        def handle_error(err_msg):
            if worker in self.active_workers:
                self.active_workers.remove(worker)
            if on_error:
                on_error(err_msg)
            else:
                QMessageBox.critical(self, "Hiba", f"Hiba történt:\n{err_msg}")

        worker.finished.connect(handle_success)
        worker.error.connect(handle_error)

        self.active_workers.append(worker)
        worker.start()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 15)
        main_layout.setSpacing(15)

        # --- FEJLÉC ---
        header_layout = QHBoxLayout()

        self.title_label = QLabel(f"⚡ {APP_NAME}")
        title_font = QFont(".AppleSystemUIFont", 14)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        header_layout.addWidget(self.title_label)

        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        # --- KAPCSOLÓDÁSI ADATOK PANEL ---
        conn_group = QGroupBox("🔑 Kapcsolódási adatok")
        conn_form = QFormLayout()
        conn_form.setSpacing(8)

        self.input_host = QLineEdit()
        self.input_host.setPlaceholderText("pl. wh33.rackhost.hu")

        self.input_port = QLineEdit()
        self.input_port.setPlaceholderText("21")
        self.input_port.setValidator(QIntValidator(1, 65535, self))
        self.input_port.setFixedWidth(80)

        self.input_user = QLineEdit()
        self.input_user.setPlaceholderText("felhasználónév")

        self.input_pass = QLineEdit()
        self.input_pass.setPlaceholderText("jelszó")
        self.input_pass.setEchoMode(QLineEdit.EchoMode.Password)

        conn_form.addRow("Host:", self.input_host)
        conn_form.addRow("Port:", self.input_port)
        conn_form.addRow("Felhasználó:", self.input_user)
        conn_form.addRow("Jelszó:", self.input_pass)

        conn_group.setLayout(conn_form)
        main_layout.addWidget(conn_group)

        # --- KAPCSOLÓDÁSI GOMBOK ---
        conn_btn_layout = QHBoxLayout()

        self.btn_save_conn = QPushButton("💾 Mentés")
        self.btn_save_conn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_conn.setObjectName("btnSave")
        self.btn_save_conn.clicked.connect(self.save_connection_settings)
        conn_btn_layout.addWidget(self.btn_save_conn)

        conn_btn_layout.addStretch()

        self.btn_connect = QPushButton("🔌 Csatlakozás Szerverhez")
        self.btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_connect.setObjectName("btnConnect")
        self.btn_connect.clicked.connect(self.connect_ftp)
        conn_btn_layout.addWidget(self.btn_connect)

        main_layout.addLayout(conn_btn_layout)

        # --- FÁJL STRUKTÚRA KONTÉNER ---
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(15)
        self.tree.setAnimated(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)

        # Bejövő húzás (Desktop -> GUI = feltöltés) ÉS kimenő húzás (GUI -> Desktop = letöltés)
        self.tree.setAcceptDrops(True)
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.itemDoubleClicked.connect(self.on_double_click)

        self.tree.dragEnterEvent = self.dragEnterEvent
        self.tree.dropEvent = self.dropEvent
        self.tree.startDrag = self.tree_start_drag

        main_layout.addWidget(self.tree)

        # --- MŰVELETI GOMBOK ---
        btn_layout = QHBoxLayout()

        self.btn_delete = QPushButton("🗑️ Törlés")
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.setObjectName("btnDelete")
        self.btn_delete.clicked.connect(self.delete_item)
        btn_layout.addWidget(self.btn_delete)

        btn_layout.addStretch()

        self.btn_upload = QPushButton("⬆️ Feltöltés")
        self.btn_upload.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_upload.setObjectName("btnUpload")
        self.btn_upload.clicked.connect(self.upload_file)
        btn_layout.addWidget(self.btn_upload)

        self.btn_download = QPushButton("⬇️ Letöltés")
        self.btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_download.setObjectName("btnDownload")
        self.btn_download.clicked.connect(self.download_file)
        btn_layout.addWidget(self.btn_download)

        self.btn_refresh = QPushButton("🔄 Frissítés")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.setObjectName("btnRefresh")
        self.btn_refresh.clicked.connect(self.refresh_list)
        btn_layout.addWidget(self.btn_refresh)

        main_layout.addLayout(btn_layout)

        # --- STATUS BAR + JOBB ALSÓ SAROK FELIRAT ---
        status_layout = QHBoxLayout()

        self.status_label = QLabel("🟡 Nincs csatlakozva")
        self.status_label.setObjectName("statusBar")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        credit_label = QLabel("Vibe Coder: ECKERT ANDRAS")
        credit_label.setObjectName("creditLabel")
        status_layout.addWidget(credit_label)

        main_layout.addLayout(status_layout)

    def apply_mac_stylesheet(self):
        """macOS Dark Mode Stílusok"""
        qss = """
            QMainWindow {
                background-color: #1e1e1e;
            }
            QWidget {
                color: #f5f5f7;
                font-family: ".AppleSystemUIFont", "SF Pro Text", "Helvetica Neue", sans-serif;
                font-size: 13px;
            }

            QTreeWidget {
                background-color: #28282e;
                border: 1px solid #3a3a3c;
                border-radius: 10px;
                padding: 8px;
                outline: none;
            }

            QMessageBox {
                background-color: #2c2c2e;
            }
            QMessageBox QLabel {
                color: #f5f5f7;
                background-color: transparent;
            }
            QMessageBox QPushButton {
                background-color: #3a3a3c;
                color: #f5f5f7;
                min-width: 70px;
                padding: 6px 14px;
            }
            QMessageBox QPushButton:hover {
                background-color: #0a84ff;
                color: white;
            }
            QTreeWidget::item {
                height: 32px;
                border-radius: 6px;
                padding-left: 5px;
            }
            QTreeWidget::item:hover {
                background-color: rgba(255, 255, 255, 0.05);
            }
            QTreeWidget::item:selected {
                background-color: #007aff;
                color: #ffffff;
            }

            QPushButton {
                border-radius: 8px;
                padding: 6px 14px;
                font-weight: 600;
                border: none;
            }

            QGroupBox {
                border: 1px solid #3a3a3c;
                border-radius: 10px;
                margin-top: 8px;
                padding: 10px;
                font-weight: 600;
                color: #d1d1d6;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
            }

            QLineEdit {
                background-color: #1c1c1e;
                border: 1px solid #3a3a3c;
                border-radius: 6px;
                padding: 5px 8px;
                selection-background-color: #0a84ff;
            }
            QLineEdit:focus {
                border: 1px solid #0a84ff;
            }

            QPushButton#btnConnect {
                background-color: #0a84ff;
                color: white;
            }
            QPushButton#btnConnect:hover { background-color: #409cff; }

            QPushButton#btnSave {
                background-color: rgba(10, 132, 255, 0.15);
                color: #0a84ff;
                border: 1px solid rgba(10, 132, 255, 0.4);
            }
            QPushButton#btnSave:hover { background-color: #0a84ff; color: white; }

            QPushButton#btnDelete {
                background-color: rgba(255, 69, 58, 0.2);
                color: #ff453a;
                border: 1px solid rgba(255, 69, 58, 0.4);
            }
            QPushButton#btnDelete:hover { background-color: #ff453a; color: white; }

            QPushButton#btnUpload {
                background-color: rgba(255, 159, 10, 0.2);
                color: #ff9f0a;
                border: 1px solid rgba(255, 159, 10, 0.4);
            }
            QPushButton#btnUpload:hover { background-color: #ff9f0a; color: white; }

            QPushButton#btnDownload {
                background-color: rgba(50, 215, 75, 0.2);
                color: #32d74b;
                border: 1px solid rgba(50, 215, 75, 0.4);
            }
            QPushButton#btnDownload:hover { background-color: #32d74b; color: white; }

            QPushButton#btnRefresh {
                background-color: rgba(255, 214, 10, 0.2);
                color: #ffd60a;
                border: 1px solid rgba(255, 214, 10, 0.4);
            }
            QPushButton#btnRefresh:hover { background-color: #ffd60a; color: #1c1c1e; }

            QLabel#statusBar {
                color: #8e8e93;
                font-size: 11px;
                padding-top: 5px;
            }

            QLabel#creditLabel {
                color: #5c5c66;
                font-size: 11px;
                font-weight: 500;
                font-style: italic;
                padding-top: 5px;
            }
        """
        self.setStyleSheet(qss)

    def set_status(self, text):
        self.status_label.setText(text)

    # --- FTP MŰVELETEK ---

    def connect_ftp(self):
        host = self.input_host.text().strip()
        try:
            port = int(self.input_port.text().strip() or "21")
        except ValueError:
            QMessageBox.warning(self, "Figyelem", "A port mezőnek számnak kell lennie!")
            return
        user = self.input_user.text().strip()
        password = self.input_pass.text()

        if not host or not user:
            QMessageBox.warning(
                self, "Figyelem", "Add meg legalább a host-ot és a felhasználónevet!"
            )
            return

        self.set_status(f"🔵 Kapcsolódás: {host}...")

        def task():
            ftp = ftplib.FTP()
            ftp.connect(host, port)
            ftp.login(user, password)
            return ftp

        def on_success(ftp_obj):
            self.ftp = ftp_obj
            self.set_status("🟢 Sikeres csatlakozás!")
            self.refresh_list()

        def on_error(err):
            QMessageBox.critical(self, "Hiba", f"Nem sikerült csatlakozni:\n{err}")
            self.set_status("🔴 Csatlakozási hiba.")

        self._start_worker(task, on_success, on_error)

    def refresh_list(self):
        if not self.ftp:
            return

        self.set_status("⏳ Fájllista lekérése...")

        def task():
            lines = []
            self.ftp.dir(lines.append)
            return lines

        def on_success(lines):
            self._populate_tree(lines)

        def on_error(err):
            QMessageBox.critical(self, "Hiba", f"Sikertelen frissítés:\n{err}")

        self._start_worker(task, on_success, on_error)

    def _populate_tree(self, lines):
        self.tree.clear()

        up_item = QTreeWidgetItem(["📁 .. (Vissza)"])
        up_item.setForeground(0, QColor("#30d158"))
        up_item.setData(0, Qt.ItemDataRole.UserRole, ("folder", ".."))
        self.tree.addTopLevelItem(up_item)

        for line in lines:
            parts = line.split(maxsplit=8)
            if len(parts) < 9:
                continue

            permissions, size, name = parts[0], parts[4], parts[8]
            if name in [".", ".."]:
                continue

            is_dir = permissions.startswith("d")

            if is_dir:
                item = QTreeWidgetItem([f"📁  {name}"])
                item.setForeground(0, QColor("#30d158"))
                item.setData(0, Qt.ItemDataRole.UserRole, ("folder", name))
            else:
                size_mb = int(size) / (1024 * 1024)
                size_str = f"{size_mb:.2f} MB" if size_mb >= 1 else f"{int(size):,} B"

                display_name = f"📄  {name}    ({size_str})"
                item = QTreeWidgetItem([display_name])

                if name.lower() == "index.html":
                    item.setForeground(0, QColor("#ff453a"))
                else:
                    item.setForeground(0, QColor("#0a84ff"))

                item.setData(0, Qt.ItemDataRole.UserRole, ("file", name))

            self.tree.addTopLevelItem(item)

        self.set_status("🟢 Fájllista frissítve.")

    def on_double_click(self, item, column):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return

        item_type, item_name = data
        if item_type == "folder":
            try:
                self.ftp.cwd(item_name)
                self.refresh_list()
            except Exception as e:
                QMessageBox.critical(self, "Hiba", f"Nem sikerült belépni:\n{e}")

    # --- FELTÖLTÉS (GOMB ÉS DRAG & DROP) ---

    def upload_files_from_paths(self, file_paths):
        if not self.ftp:
            QMessageBox.warning(
                self, "Figyelem", "Először csatlakozz az FTP szerverhez!"
            )
            return

        if not file_paths:
            return

        def task():
            for file_path in file_paths:
                filename = os.path.basename(file_path)
                with open(file_path, "rb") as f:
                    self.ftp.storbinary(f"STOR {filename}", f)

        def on_success(_):
            self.set_status("🟢 Feltöltés sikeres!")
            self.refresh_list()

        def on_error(err):
            QMessageBox.critical(self, "Hiba", f"Hiba a feltöltésnél:\n{err}")

        self.set_status("⏳ Feltöltés folyamatban...")
        self._start_worker(task, on_success, on_error)

    def upload_file(self):
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "Fájlok kiválasztása feltöltéshez", ""
        )
        if file_paths:
            self.upload_files_from_paths(file_paths)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        files = [
            url.toLocalFile() for url in urls if os.path.isfile(url.toLocalFile())
        ]
        self.upload_files_from_paths(files)

    # --- KIMENŐ HÚZÁS (GUI -> DESKTOP = LETÖLTÉS) ---

    def tree_start_drag(self, supportedActions):
        """A fa widgetből kifelé húzott elem(ek)et letölti egy ideiglenes
        mappába, majd valódi helyi file:// URL-lel indít natív drag-et,
        amit a Finder/Asztal el tud fogadni (drag-to-download minta)."""
        items = self.tree.selectedItems()
        file_names = []
        for item in items:
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data[0] == "file":
                file_names.append(data[1])

        if not file_names:
            return

        if not self.ftp:
            QMessageBox.warning(
                self, "Figyelem", "Először csatlakozz az FTP szerverhez!"
            )
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.set_status("⏳ Előkészítés húzáshoz (letöltés)...")

        local_paths = []
        try:
            cache_dir = os.path.join(tempfile.gettempdir(), "FTPEKI_DragCache")
            os.makedirs(cache_dir, exist_ok=True)
            for name in file_names:
                local_path = os.path.join(cache_dir, name)
                with open(local_path, "wb") as f:
                    self.ftp.retrbinary(f"RETR {name}", f.write)
                local_paths.append(local_path)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Hiba", f"Hiba a húzáshoz letöltésnél:\n{e}")
            self.set_status("🔴 Húzás sikertelen.")
            return

        QApplication.restoreOverrideCursor()
        self.set_status(f"🟢 Húzható: {', '.join(file_names)}")

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(p) for p in local_paths])

        drag = QDrag(self.tree)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)

    # --- LETÖLTÉS ÉS TÖRLESE ---

    def download_file(self):
        selected = self.tree.currentItem()
        if not selected:
            QMessageBox.warning(
                self, "Kijelölés", "Válassz ki egy fájlt a letöltéshez!"
            )
            return

        data = selected.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[0] == "folder":
            QMessageBox.warning(
                self, "Figyelem", "Mappákat nem tudsz közvetlenül letölteni!"
            )
            return

        item_name = data[1]
        save_path, _ = QFileDialog.getSaveFileName(self, "Fájl mentése", item_name)
        if not save_path:
            return

        def task():
            with open(save_path, "wb") as f:
                self.ftp.retrbinary(f"RETR {item_name}", f.write)

        def on_success(_):
            self.set_status(f"🟢 Sikeresen letöltve: {item_name}")

        def on_error(err):
            QMessageBox.critical(self, "Hiba", f"Hiba a letöltésnél:\n{err}")

        self.set_status(f"⏳ Letöltés: {item_name}...")
        self._start_worker(task, on_success, on_error)

    def delete_item(self):
        selected = self.tree.currentItem()
        if not selected:
            QMessageBox.warning(
                self, "Kijelölés", "Válassz ki egy elemet a törléshez!"
            )
            return

        data = selected.data(0, Qt.ItemDataRole.UserRole)
        if not data or data[1] == "..":
            return

        item_type, item_name = data

        reply = QMessageBox.question(
            self,
            "Törlés megerősítése",
            f"Biztosan törölni szeretnéd a következőt:\n'{item_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        def task():
            if item_type == "folder":
                self.ftp.rmd(item_name)
            else:
                self.ftp.delete(item_name)

        def on_success(_):
            self.set_status(f"🟢 Törölve: {item_name}")
            self.refresh_list()

        def on_error(err):
            QMessageBox.critical(self, "Hiba", f"Sikertelen törlés:\n{err}")

        self._start_worker(task, on_success, on_error)

    def closeEvent(self, event):
        """Ablak bezárásakor megvárjuk, hogy leálljanak a futó szálak"""
        for worker in self.active_workers:
            worker.quit()
            worker.wait()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    window = MacFTPApp()
    window.show()
    sys.exit(app.exec())