# -*- coding: utf-8 -*-
import sys
import os
import logging
import subprocess
from PySide6 import QtWidgets, QtGui, QtCore

# Import ALVT theme components
try:
    from gui.theme import apply_theme, enable_brand_backgrounds
except ImportError:
    from gui.theme import apply_theme, enable_brand_backgrounds
    
from scripts.logic_diff_to_autocad import process_diff
from core.layout_config import load_layout_config


class _ArrowOverlay(QtWidgets.QWidget):
    def __init__(self, owner, mode="combo"):
        super().__init__(owner)
        self.owner = owner
        self.mode = mode
        self._arrow_color = QtGui.QColor("#e0e0e0")
        self._border_color = QtGui.QColor(255, 255, 255, 46)
        self.setFixedWidth(20)

    def set_colors(self, arrow_color, border_color):
        self._arrow_color = QtGui.QColor(arrow_color)
        self._border_color = QtGui.QColor(border_color)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setPen(self._border_color)
        painter.drawLine(0, 0, 0, self.height())
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._arrow_color)
        cx = self.width() // 2

        if self.mode == "combo":
            cy = self.height() // 2 + 1
            tri = QtGui.QPolygon([
                QtCore.QPoint(cx - 4, cy - 2),
                QtCore.QPoint(cx + 4, cy - 2),
                QtCore.QPoint(cx, cy + 3),
            ])
            painter.drawPolygon(tri)
            return

        mid_y = self.height() // 2
        painter.setPen(self._border_color)
        painter.drawLine(0, mid_y, self.width(), mid_y)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(self._arrow_color)
        up_tri = QtGui.QPolygon([
            QtCore.QPoint(cx - 4, mid_y - 2),
            QtCore.QPoint(cx + 4, mid_y - 2),
            QtCore.QPoint(cx, mid_y - 7),
        ])
        down_tri = QtGui.QPolygon([
            QtCore.QPoint(cx - 4, mid_y + 2),
            QtCore.QPoint(cx + 4, mid_y + 2),
            QtCore.QPoint(cx, mid_y + 7),
        ])
        painter.drawPolygon(up_tri)
        painter.drawPolygon(down_tri)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            super().mousePressEvent(event)
            return
        if self.mode == "combo" and isinstance(self.owner, QtWidgets.QComboBox):
            self.owner.showPopup()
            event.accept()
            return
        if self.mode == "spin" and isinstance(self.owner, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
            if event.position().y() < self.height() / 2:
                self.owner.stepUp()
            else:
                self.owner.stepDown()
            event.accept()
            return
        super().mousePressEvent(event)


class ArrowComboBox(QtWidgets.QComboBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._overlay = _ArrowOverlay(self, "combo")
        self._overlay.show()

    def set_overlay_colors(self, arrow_color, border_color):
        self._overlay.set_colors(arrow_color, border_color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.width() - 20, 0, 20, self.height())
        self._overlay.raise_()


class ArrowSpinBox(QtWidgets.QSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._overlay = _ArrowOverlay(self, "spin")
        self._overlay.show()

    def set_overlay_colors(self, arrow_color, border_color):
        self._overlay.set_colors(arrow_color, border_color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.width() - 20, 0, 20, self.height())
        self._overlay.raise_()


class ArrowDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._overlay = _ArrowOverlay(self, "spin")
        self._overlay.show()

    def set_overlay_colors(self, arrow_color, border_color):
        self._overlay.set_colors(arrow_color, border_color)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._overlay.setGeometry(self.width() - 20, 0, 20, self.height())
        self._overlay.raise_()

class StdoutRedirector:
    def __init__(self, signal):
        self.signal = signal
    def write(self, text):
        if text and text.strip():
            try: self.signal.emit(text.strip())
            except: pass
    def flush(self): pass
    def isatty(self): return False

class DiffConversionWorker(QtCore.QThread):
    log_signal = QtCore.Signal(str)
    finished_signal = QtCore.Signal(int, str)
    
    def __init__(self, args):
        super().__init__()
        self.args = args # [old, new, dest, template, font, size, bold, gen_dxf, gen_pdf, start_p, res_idx, anchor, modified_only]
        self._is_cancelled = False

    def run(self):
        try:
            import sys as pysys
            old_stdout, old_stderr = pysys.stdout, pysys.stderr
            pysys.stdout = StdoutRedirector(self.log_signal)
            pysys.stderr = StdoutRedirector(self.log_signal)
            
            logging.getLogger('ezdxf').setLevel(logging.ERROR)
            logging.getLogger('matplotlib').setLevel(logging.ERROR)
            
            try:
                # Use keyword argument for layout_config to avoid positional confusion
                params = self.args[:-1]
                layout_cfg = self.args[-1]
                process_diff(*params, cancel_check=lambda: self._is_cancelled, layout_config=layout_cfg)
                self.finished_signal.emit(0, "Success")
            finally:
                pysys.stdout, pysys.stderr = old_stdout, old_stderr
        except Exception as e:
            self.finished_signal.emit(-1, str(e))

    def cancel(self):
        self._is_cancelled = True

class StandaloneLogicDiffToCad(QtWidgets.QMainWindow):
    def __init__(self, is_dark=True):
        super().__init__()
        self.is_dark = is_dark
        self.setObjectName("StandaloneLogicDiffToCad")
        self.setWindowTitle("ALVT - Logic Differential CAD System")
        self.resize(800, 680)
        self.layout_params = load_layout_config()
        
        apply_theme(QtWidgets.QApplication.instance(), dark_mode=is_dark)
        app = QtWidgets.QApplication.instance()

        # Additional CSS — mirrors standalone_logic_cad.py exactly
        text_color = "#FFFFFF" if is_dark else "#202124"
        sub_bg = "rgba(255, 255, 255, 0.05)" if is_dark else "rgba(0, 0, 0, 0.03)"
        input_bg = "#1A1A1A" if is_dark else "#FFFFFF"
        input_border = "#333333" if is_dark else "#CCCCCC"
        card_bg = "rgba(255, 255, 255, 0.08)" if is_dark else "#FFFFFF"

        app.setStyleSheet(app.styleSheet() + f"""
            QLabel {{ 
                color: {text_color}; 
                font-size: 13px; 
                font-weight: 700; 
                padding: 2px;
                background-color: {sub_bg};
                border-radius: 4px;
            }}
            QGroupBox QLabel {{ 
                color: {text_color}; 
                background: transparent; 
                border: none;
            }}
            QCheckBox {{
                color: {text_color};
                font-weight: 600;
            }}
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {{
                color: {text_color};
                background-color: {input_bg};
                border: 1px solid {input_border};
                padding: 4px;
                border-radius: 4px;
            }}
            QPushButton {{
                background-color: {card_bg};
                border: 1px solid {input_border};
                border-radius: 4px;
                padding: 0px 10px;
                font-weight: bold;
            }}
            #StandaloneLogicDiffToCad QComboBox,
            #StandaloneLogicDiffToCad QSpinBox,
            #StandaloneLogicDiffToCad QDoubleSpinBox {{
                padding-right: 22px;
            }}
            #StandaloneLogicDiffToCad QComboBox::drop-down,
            #StandaloneLogicDiffToCad QComboBox::down-arrow,
            #StandaloneLogicDiffToCad QSpinBox::up-button,
            #StandaloneLogicDiffToCad QSpinBox::down-button,
            #StandaloneLogicDiffToCad QSpinBox::up-arrow,
            #StandaloneLogicDiffToCad QSpinBox::down-arrow,
            #StandaloneLogicDiffToCad QDoubleSpinBox::up-button,
            #StandaloneLogicDiffToCad QDoubleSpinBox::down-button,
            #StandaloneLogicDiffToCad QDoubleSpinBox::up-arrow,
            #StandaloneLogicDiffToCad QDoubleSpinBox::down-arrow {{
                width: 0px;
                height: 0px;
                border: none;
                image: none;
                background: transparent;
            }}
        """)
        
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(8)
        
        # Header
        header = QtWidgets.QLabel("Red/Green Logic - AutoCAD Circuit Generation")
        header.setProperty("class", "brand")
        header.setAlignment(QtCore.Qt.AlignCenter)
        header.setFixedHeight(40)
        main_layout.addWidget(header)
        
        # --- File Configuration ---
        file_group = QtWidgets.QGroupBox("LOGIC SOURCE CONFIGURATION")
        file_lay = QtWidgets.QVBoxLayout()
        file_lay.setContentsMargins(15, 20, 15, 10)
        file_lay.setSpacing(5)

        # Helper for browsable rows
        def create_browser_row(label, placeholder):
            row = QtWidgets.QWidget()
            row.setFixedHeight(36)
            l = QtWidgets.QHBoxLayout(row)
            l.setContentsMargins(0, 0, 0, 0)
            l.setSpacing(10)
            lbl = QtWidgets.QLabel(label)
            lbl.setFixedWidth(130)
            l.addWidget(lbl)
            edit = QtWidgets.QLineEdit()
            edit.setFixedHeight(28)
            edit.setPlaceholderText(placeholder)
            l.addWidget(edit)
            btn = QtWidgets.QPushButton("Browse")
            btn.setFixedSize(96, 28)
            l.addWidget(btn)
            return row, edit, btn

        # Old Logic
        row1, self.old_path, self.btn_old = create_browser_row("Old Logic (Base):", "Select original logic file...")
        self.btn_old.clicked.connect(lambda: self._browse("old"))
        file_lay.addWidget(row1)

        # New Logic
        row2, self.new_path, self.btn_new = create_browser_row("New Logic (Target):", "Select modified logic file...")
        self.btn_new.clicked.connect(lambda: self._browse("new"))
        file_lay.addWidget(row2)

        # Template
        row3, self.temp_path, self.btn_temp = create_browser_row("CAD Template:", "Select .dxf template...")
        self.btn_temp.clicked.connect(lambda: self._browse("temp"))
        file_lay.addWidget(row3)

        # Destination
        row4, self.dest_path, self.btn_dest = create_browser_row("Base File Name:", "Select base file path (e.g. INASRR52SNT.dxf)...")
        self.btn_dest.clicked.connect(lambda: self._browse("dest"))
        file_lay.addWidget(row4)

        file_group.setLayout(file_lay)
        main_layout.addWidget(file_group)
        
        # --- Mode & Settings ---
        settings_row = QtWidgets.QHBoxLayout()
        
        # Mode Selection
        mode_group = QtWidgets.QGroupBox("DIFFERENTIAL MODE")
        mode_lay = QtWidgets.QVBoxLayout()
        mode_lay.setContentsMargins(15, 20, 15, 15)
        mode_lay.setSpacing(8)
        self.mode_modified = QtWidgets.QRadioButton("Modified Logic Only")
        self.mode_full = QtWidgets.QRadioButton("Full Logic with Red/Green")
        self.mode_modified.setChecked(True)
        mode_lay.addWidget(self.mode_modified)
        mode_lay.addWidget(self.mode_full)
        mode_group.setLayout(mode_lay)
        settings_row.addWidget(mode_group)

        # Styling Card
        style_group = QtWidgets.QGroupBox("CAD FONT & STYLING")
        style_layout = QtWidgets.QFormLayout()
        style_layout.setSpacing(10)
        style_layout.setContentsMargins(15, 20, 15, 15)

        self.font_combo = ArrowComboBox()
        self.font_combo.addItems(["ARIAL", "SIMPLEX", "ROMANS", "STANDARD", "ISOCP"])
        style_layout.addRow("Font Family:", self.font_combo)

        self.font_size_spin = ArrowDoubleSpinBox()
        self.font_size_spin.setRange(0.5, 2.0)
        self.font_size_spin.setValue(1.5)
        style_layout.addRow("Font Size:", self.font_size_spin)

        self.bold_check = QtWidgets.QCheckBox("Bold Font")
        style_layout.addRow("Bold Weight:", self.bold_check)

        style_group.setLayout(style_layout)

        # Numbering Card
        num_group = QtWidgets.QGroupBox("CAD PARAMETERS")
        num_lay = QtWidgets.QFormLayout()
        num_lay.setSpacing(10)
        num_lay.setContentsMargins(15, 20, 15, 15)

        self.reserved_spin = ArrowSpinBox()
        self.reserved_spin.setRange(1, 999)
        self.reserved_spin.setValue(50)
        num_lay.addRow("Reserved Index:", self.reserved_spin)

        self.start_sheet_spin = ArrowSpinBox()
        self.start_sheet_spin.setRange(1, 99999)
        num_lay.addRow("Start Sheet No:", self.start_sheet_spin)

        # Add Settings Button in Parameters Group
        self.settings_btn = QtWidgets.QPushButton("Layout Settings ⚙️")
        self.settings_btn.setToolTip("Configure Margins, Column Widths, and Grid Thresholds")
        self.settings_btn.clicked.connect(self._show_layout_settings)
        num_lay.addRow("", self.settings_btn)

        num_group.setLayout(num_lay)
        settings_row.addWidget(style_group)
        settings_row.addWidget(num_group)
        
        main_layout.addLayout(settings_row)
        
        # --- Logging ---
        export_group = QtWidgets.QGroupBox("EXPORT OPTIONS")
        export_lay = QtWidgets.QHBoxLayout()
        export_lay.setContentsMargins(15, 20, 15, 15)
        self.chk_dxf = QtWidgets.QCheckBox("DXF Sheets")
        self.chk_pdf = QtWidgets.QCheckBox("Merged PDF")
        self.chk_dxf.setChecked(True); self.chk_pdf.setChecked(True)
        export_lay.addWidget(self.chk_dxf); export_lay.addWidget(self.chk_pdf)
        export_group.setLayout(export_lay)
        main_layout.addWidget(export_group)

        # --- Logging ---
        log_group = QtWidgets.QGroupBox("EXECUTION LOGS")
        log_lay = QtWidgets.QVBoxLayout()
        log_lay.setContentsMargins(15, 20, 15, 15)
        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(80)
        self.log_output.setStyleSheet(f"font-family: Consolas; font-size: 11px; background-color: {'#111' if is_dark else '#F9F9F9'}; color: {'#00FF00' if is_dark else '#006400'};")
        log_lay.addWidget(self.log_output)
        log_group.setLayout(log_lay)
        main_layout.addWidget(log_group)
        
        # Bottom Actions
        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setStyleSheet("color: #888; font-style: italic;")
        main_layout.addWidget(self.status_label)
        
        self.run_btn = QtWidgets.QPushButton("EXECUTE DIFFERENTIAL RENDER")
        self.run_btn.setProperty("class", "primary")
        self.run_btn.setFixedHeight(50)
        self.run_btn.clicked.connect(self.run_diff)
        main_layout.addWidget(self.run_btn)
        
        enable_brand_backgrounds(self)
        self._apply_overlay_colors()
        self.worker = None

    def _apply_overlay_colors(self):
        arrow_color = "#e0e0e0" if self.is_dark else "#202124"
        border_color = "rgba(255, 255, 255, 0.18)" if self.is_dark else "rgba(0, 0, 0, 0.18)"
        for w in (self.font_combo, self.font_size_spin, self.reserved_spin, self.start_sheet_spin):
            if hasattr(w, "set_overlay_colors"):
                w.set_overlay_colors(arrow_color, border_color)

    def _browse(self, mode):
        default_dir = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation) or ""
        if mode == "old":
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Old Logic", default_dir, "Logic (*.txt *.ML2)")
            if p: self.old_path.setText(p)
        elif mode == "new":
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select New Logic", default_dir, "Logic (*.txt *.ML2)")
            if p: 
                self.new_path.setText(p)
                if not self.dest_path.text(): self.dest_path.setText(os.path.splitext(p)[0] + ".dxf")
        elif mode == "temp":
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Template", default_dir, "CAD (*.dxf)")
            if p: self.temp_path.setText(p)
        elif mode == "dest":
            p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Select Base File Name", self.dest_path.text() or default_dir, "CAD Files (*.dxf);;All Files (*)")
            if p: self.dest_path.setText(p)

    def run_diff(self):
        old, new = self.old_path.text(), self.new_path.text()
        dest = self.dest_path.text()
        if not old or not new or not dest:
            QtWidgets.QMessageBox.warning(self, "Input Required", "Please specify Old Logic, New Logic, and Base File Name.")
            return
            
        out_base = dest
        
        args = [
            old, new, out_base, 
            self.temp_path.text() or "none",
            self.font_combo.currentText(), self.font_size_spin.value(), self.bold_check.isChecked(),
            self.chk_dxf.isChecked(), self.chk_pdf.isChecked(),
            self.start_sheet_spin.value(), self.reserved_spin.value(), 
            "", # Drawing Anchor

            self.mode_modified.isChecked(), # modified_only
            self.layout_params
        ]
        
        self.log_output.clear()
        self.status_label.setText("Diffing...")
        self.run_btn.setEnabled(False)
        
        self.worker = DiffConversionWorker(args)
        self.worker.log_signal.connect(self.log_output.append)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _on_finished(self, rc, err):
        self.run_btn.setEnabled(True)
        if rc == 0:
            self.status_label.setText("Success")
            QtWidgets.QMessageBox.information(self, "Success", "Differential logic rendering complete.")
        else:
            self.status_label.setText("Failed")
            self.log_output.append(f"<span style='color:#ff4444; font-weight:bold;'>ERROR: {err}</span>")
            QtWidgets.QMessageBox.critical(self, "Rendering Error", f"The differential conversion failed:\n\n{err}")
        self.worker = None

    
    def closeEvent(self, event):
        """Cleanup worker threads on exit to prevent ghost processes."""
        if self.worker and self.worker.isRunning():
            print(">>> Aborting background diff engine...")
            self.worker.cancel()
            self.worker.wait(1500) # Wait up to 1.5s
            if self.worker.isRunning():
                self.worker.terminate()
        event.accept()

    def _show_layout_settings(self):
        from gui.layout_settings_dialog import LayoutSettingsDialog
        dlg = LayoutSettingsDialog(self, is_dark=self.is_dark)
        if dlg.exec_():
            self.layout_params = load_layout_config()
            print(f"Global layout settings refreshed: {self.layout_params}")

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    app = QtWidgets.QApplication(sys.argv)
    win = StandaloneLogicDiffToCad()
    win.show()
    sys.exit(app.exec())
