from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple

from PyQt5 import QtCore, QtGui, QtWidgets

# --------------------------------------------------------------------------------------
# Optional pyzbar import (decoding disabled if zbar dlls not available)
# --------------------------------------------------------------------------------------
HAS_ZBAR = True
ZBAR_ERROR = ""
try:
    # Make zbar DLLs discoverable when frozen (PyInstaller onefile)
    def _add_pyzbar_dll_dir_if_frozen() -> None:
        base = getattr(sys, "_MEIPASS", None)
        if not base:
            return
        cand = os.path.join(base, "pyzbar")  # we will copy DLLs in _internal/pyzbar
        if os.path.isdir(cand):
            try:
                os.add_dll_directory(cand)  # Py3.8+
            except Exception:
                os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")

    _add_pyzbar_dll_dir_if_frozen()
    from pyzbar.pyzbar import decode as zbar_decode  # type: ignore
except Exception as e:  # pragma: no cover
    HAS_ZBAR = False
    ZBAR_ERROR = str(e)

try:
    from PIL import Image, ImageQt
except Exception as exc:
    raise SystemExit(
        "Pillow non installato. Esegui: pip install Pillow"
    ) from exc


@dataclass
class Row:
    index: int
    code: str
    suggestion: str
    note: str = ""


# ------------------------------- utils ---------------------------------------------

def suggest_structure(s: str) -> str:
    """
    Crea una scomposizione leggibile: gruppi numerici/alfabetici,
    pattern tipo N{6}L, ed evidenzia prefissi/suffissi semplici.
    """
    if not s:
        return ""
    parts: List[str] = []
    cur = s[0]
    buf = [cur]
    for ch in s[1:]:
        if (ch.isdigit() and buf[-1].isdigit()) or (ch.isalpha() and buf[-1].isalpha()):
            buf.append(ch)
        else:
            parts.append("".join(buf))
            buf = [ch]
    parts.append("".join(buf))

    # pattern
    def token_pat(tok: str) -> str:
        kind = "N" if tok.isdigit() else "L" if tok.isalpha() else "X"
        return f"{kind}{{{len(tok)}}}"

    pattern = "".join(token_pat(t) for t in parts)
    groups = " + ".join(parts)

    # alcuni hint semplici
    hints = []
    if s[0] == "9" and len(s) >= 2:
        hints.append("inizia con 9 (flag)")
    if s[-1:].isalpha():
        hints.append("suffisso lettera")
    if any(t.isdigit() and len(t) in (5, 6, 7) for t in parts):
        hints.append("possibile serial numerico")

    hint_txt = f" | {', '.join(hints)}" if hints else ""
    return f"{groups}  [{pattern}]" + hint_txt


def pil_to_qpixmap(img: Image.Image) -> QtGui.QPixmap:
    return QtGui.QPixmap.fromImage(ImageQt.ImageQt(img.convert("RGBA")))


# ------------------------------- Graphics items -------------------------------------

class BoxItem(QtWidgets.QGraphicsRectItem):
    def __init__(self, rect: QtCore.QRectF):
        super().__init__(rect)
        pen = QtGui.QPen(QtCore.Qt.red)
        pen.setWidth(2)
        self.setPen(pen)
        self.setBrush(QtCore.Qt.transparent)
        self.setZValue(10)


class ImageView(QtWidgets.QGraphicsView):
    """QGraphicsView with panning + wheel zoom + drag&drop for images."""
    imageDropped = QtCore.pyqtSignal(Image.Image)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setAcceptDrops(True)
        self._pix_item: Optional[QtWidgets.QGraphicsPixmapItem] = None

    def set_image(self, img: Image.Image):
        self.scene().clear()
        pm = pil_to_qpixmap(img)
        self._pix_item = self.scene().addPixmap(pm)
        self.setSceneRect(self.scene().itemsBoundingRect())
        self.fitInView(self.sceneRect(), QtCore.Qt.KeepAspectRatio)

    def add_boxes(self, rects: List[Tuple[int, int, int, int]]):
        for (x, y, w, h) in rects:
            self.scene().addItem(BoxItem(QtCore.QRectF(x, y, w, h)))

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        # Zoom on wheel
        if event.angleDelta().y() > 0:
            self.scale(1.15, 1.15)
        else:
            self.scale(1 / 1.15, 1 / 1.15)

    # Drag&Drop
    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if path:
                try:
                    img = Image.open(path)
                    self.imageDropped.emit(img)
                    break
                except Exception:
                    pass


# ------------------------------- Main Window ---------------------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Barcode Analyzer")
        self.resize(1100, 720)

        # Icona
        self._set_icon()

        # Central splitter
        splitter = QtWidgets.QSplitter(self)
        self.setCentralWidget(splitter)

        # Left: image view
        self.view = ImageView()
        splitter.addWidget(self.view)

        # Right: panel with filter + table
        right = QtWidgets.QWidget()
        vbox = QtWidgets.QVBoxLayout(right)
        vbox.setContentsMargins(6, 6, 6, 6)

        filter_box = QtWidgets.QHBoxLayout()
        filter_label = QtWidgets.QLabel("Filtro:")
        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText("Cerca in Codice / Suggerimento / Nota...")
        filter_box.addWidget(filter_label)
        filter_box.addWidget(self.filter_edit)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["#", "Codice", "Suggerimento", "Nota"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)

        vbox.addLayout(filter_box)
        vbox.addWidget(self.table)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        # Toolbar
        tb = self.addToolBar("Main")
        tb.setMovable(False)
        act_open = tb.addAction("Apri immagine")
        act_retry = tb.addAction("Riprova analisi")
        tb.addSeparator()
        act_copy = tb.addAction("Copia tutti i codici")
        tb.addSeparator()
        act_exp_csv = tb.addAction("Export CSV")
        act_exp_json = tb.addAction("Export JSON")
        act_imp_json = tb.addAction("Import JSON")
        tb.addSeparator()
        act_zoom_out = tb.addAction("Zoom −")
        act_zoom_in = tb.addAction("Zoom +")

        self.status = QtWidgets.QStatusBar()
        self.setStatusBar(self.status)

        # Data
        self.image: Optional[Image.Image] = None
        self.rows: List[Row] = []
        self._decoded_rects: List[Tuple[int, int, int, int]] = []

        # Signals
        act_open.triggered.connect(self.open_image)
        act_retry.triggered.connect(self.decode_current)
        act_copy.triggered.connect(self.copy_all_codes)
        act_exp_csv.triggered.connect(self.export_csv)
        act_exp_json.triggered.connect(self.export_json)
        act_imp_json.triggered.connect(self.import_json)
        act_zoom_in.triggered.connect(lambda: self.view.scale(1.15, 1.15))
        act_zoom_out.triggered.connect(lambda: self.view.scale(1 / 1.15, 1 / 1.15))
        self.view.imageDropped.connect(self._set_image)
        self.filter_edit.textChanged.connect(self.apply_filter)

        # Shortcuts
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+O"), self, activated=self.open_image)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+C"), self, activated=self.copy_all_codes)
        QtWidgets.QShortcut(QtGui.QKeySequence("Ctrl+E"), self, activated=self.export_csv)

        # If decoding not available, show warning
        if not HAS_ZBAR:
            self.status.showMessage(f"Decodifica disabilitata: {ZBAR_ERROR}", 8000)

    # ----------------------------- Icon --------------------------------------------
    def _set_icon(self):
        # Use embedded minimal icon (fallback) if file barcode.ico is absent
        icon = QtGui.QIcon()
        ico_path = os.path.join(os.path.dirname(__file__), "barcode.ico")
        if os.path.exists(ico_path):
            icon.addFile(ico_path)
        else:
            # tiny generated pixmap
            pm = QtGui.QPixmap(32, 32)
            pm.fill(QtCore.Qt.white)
            painter = QtGui.QPainter(pm)
            painter.setPen(QtGui.QPen(QtCore.Qt.black, 3))
            for x in (4, 8, 12, 20, 24, 28):
                painter.drawLine(x, 4, x, 28)
            painter.end()
            icon.addPixmap(pm)
        self.setWindowIcon(icon)
        QtWidgets.QApplication.setWindowIcon(icon)

    # ----------------------------- Image / Decode ----------------------------------
    def open_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Apri immagine", "", "Immagini (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            img = Image.open(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Errore", f"Impossibile aprire immagine:\n{exc}")
            return
        self._set_image(img)

    def _set_image(self, img: Image.Image):
        self.image = img.convert("RGB")
        self.view.set_image(self.image)
        self.rows.clear()
        self.table.setRowCount(0)
        self._decoded_rects = []
        self.decode_current()

    def decode_current(self):
        if self.image is None:
            return
        if not HAS_ZBAR:
            QtWidgets.QMessageBox.warning(self, "Decodifica non disponibile", f"pyzbar/zbar non disponibili.\n{ZBAR_ERROR}")
            return

        # decode
        try:
            results = zbar_decode(self.image)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Errore decodifica", str(exc))
            return

        # Keep only Code128
        res = [r for r in results if getattr(r, "type", "") == "CODE128"]

        # Sort: top, then left
        def key_of(r):
            rect = r.rect
            return (rect.top, rect.left)

        res.sort(key=key_of)

        # build rows
        self.rows = []
        self._decoded_rects = []
        for i, r in enumerate(res[:10], 1):
            try:
                code = r.data.decode("utf-8", errors="replace")
            except Exception:
                code = str(r.data)
            self.rows.append(Row(index=i, code=code, suggestion=suggest_structure(code)))
            rr = r.rect
            self._decoded_rects.append((rr.left, rr.top, rr.width, rr.height))

        self.populate_table()
        self.draw_boxes()
        msg = f"{len(self.rows)} codici Code128 letti"
        if len(res) != len(self.rows):
            msg += f" (totale trovati: {len(res)})"
        if len(self.rows) != 10:
            msg += " — Atteso 10"
        self.status.showMessage(msg, 6000)

    # ----------------------------- Table / Filter ----------------------------------
    def populate_table(self):
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            self.table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(row.index)))
            self.table.item(r, 0).setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(row.code))
            self.table.item(r, 1).setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(row.suggestion))
            self.table.item(r, 2).setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsSelectable)

            item_note = QtWidgets.QTableWidgetItem(row.note)
            self.table.setItem(r, 3, item_note)

    def apply_filter(self, text: str):
        t = text.strip().lower()
        for r in range(self.table.rowCount()):
            code = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").lower()
            sug = (self.table.item(r, 2).text() if self.table.item(r, 2) else "").lower()
            note = (self.table.item(r, 3).text() if self.table.item(r, 3) else "").lower()
            vis = (t in code) or (t in sug) or (t in note) or (t == "")
            self.table.setRowHidden(r, not vis)

    def sync_notes_from_table(self):
        for r in range(self.table.rowCount()):
            if r < len(self.rows):
                self.rows[r].note = self.table.item(r, 3).text() if self.table.item(r, 3) else ""

    # ----------------------------- Boxes -------------------------------------------
    def draw_boxes(self):
        # re-draw image to clear and add rectangles
        if self.image is None:
            return
        self.view.set_image(self.image)
        self.view.add_boxes(self._decoded_rects)

    # ----------------------------- Clipboard / Export -------------------------------
    def copy_all_codes(self):
        codes = []
        for r in range(self.table.rowCount()):
            if not self.table.isRowHidden(r):
                it = self.table.item(r, 1)
                if it:
                    codes.append(it.text())
        QtWidgets.QApplication.clipboard().setText("\n".join(codes))
        self.status.showMessage(f"Copiati {len(codes)} codici negli appunti", 3000)

    def export_csv(self):
        self.sync_notes_from_table()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Esporta CSV", "barcodes.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["index", "code", "suggestion", "note"])
                for row in self.rows:
                    w.writerow([row.index, row.code, row.suggestion, row.note])
            self.status.showMessage(f"CSV salvato in {path}", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Errore salvataggio", str(exc))

    def export_json(self):
        self.sync_notes_from_table()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Esporta JSON", "mapping.json", "JSON (*.json)")
        if not path:
            return
        try:
            data = [asdict(r) for r in self.rows]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.status.showMessage(f"JSON salvato in {path}", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Errore salvataggio", str(exc))

    def import_json(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Importa JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                arr = json.load(f)
            # Apply only notes (non distruggo i codici correnti)
            notes_by_index = {int(x.get("index", 0)): x.get("note", "") for x in arr if isinstance(x, dict)}
            for r in range(self.table.rowCount()):
                idx = int(self.table.item(r, 0).text()) if self.table.item(r, 0) else (r + 1)
                note = notes_by_index.get(idx, "")
                self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(note))
            self.sync_notes_from_table()
            self.status.showMessage("Mappatura (note) importata", 4000)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Errore importazione", str(exc))


# ------------------------------------ main -----------------------------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
