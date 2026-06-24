# -*- coding: utf-8 -*-
import sys
import os
import logging
import subprocess
from PySide6 import QtWidgets, QtGui, QtCore

# Import ALVT theme components if possible
try:
    from gui.theme import apply_theme, enable_brand_backgrounds
except ImportError:
    # Fallback if run standalone without package context
    from gui.theme import apply_theme, enable_brand_backgrounds
    
from scripts.logic_to_autocad import process_logic_file
try:
    from scripts.fast_dxf_to_pdf import batch_process_parallel, consolidate_pdfs, export_dxf_list_to_pdf
except ImportError:
    # Fallback to direct import if running in a different context
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scripts.fast_dxf_to_pdf import batch_process_parallel, consolidate_pdfs, export_dxf_list_to_pdf
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

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    
    # Dev mode: base_dir is Toolhub
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # Check if resource is inside Toolhub (standard) or a sibling (current project state)
    p1 = os.path.join(base_dir, relative_path)
    if os.path.exists(p1):
        return p1
        
    p2 = os.path.join(os.path.dirname(base_dir), relative_path)
    if os.path.exists(p2):
        return p2
        
    return p1

class StdoutRedirector:
    def __init__(self, signal):
        self.signal = signal
    def write(self, text):
        if text and text.strip():
            try:
                self.signal.emit(text.strip())
            except:
                pass
    def flush(self):
        pass
    def isatty(self):
        return False

class ConversionWorker(QtCore.QThread):
    log_signal = QtCore.Signal(str)
    finished_signal = QtCore.Signal(int, str)
    
    def __init__(self, args):
        super().__init__()
        self.args = args # [logic, dest, template, limit, font, size, bold, gen_dxf, gen_pdf, start_p, res_idx, anchor]
        self._is_cancelled = False
        self.mutex = QtCore.QMutex()
        self.condition = QtCore.QWaitCondition()
        self._is_paused = False

    def run(self):
        try:
            # Redirect stdout to capture prints from the engine
            import sys as pysys
            old_stdout = pysys.stdout
            pysys.stdout = StdoutRedirector(self.log_signal)
            pysys.stderr = StdoutRedirector(self.log_signal) # capture stderr
            
            # Silence noisy loggers to avoid threading/stream issues
            logging.getLogger('ezdxf').setLevel(logging.ERROR)
            logging.getLogger('matplotlib').setLevel(logging.ERROR)
            
            try:
                # Use keyword arguments for dynamic handlers and layout
                params = self.args[:-1]
                layout_cfg = self.args[-1]
                generated_pages = process_logic_file(*params, 
                                   cancel_check=lambda: self._is_cancelled,
                                   pause_check=self._wait_if_paused,
                                   layout_config=layout_cfg)
                
                # Check if PDF was requested (gen_pdf is params[8])
                gen_pdf = params[8]
                dest_path = params[1]
                if gen_pdf and not self._is_cancelled and generated_pages:
                    self.log_signal.emit(">>> Post-processing: High-Speed PDF Generation...")
                    pdf_out = os.path.splitext(dest_path)[0] + ".pdf"
                    self.log_signal.emit(f"  Running Fast PDF Engine for {len(generated_pages)} pages...")
                    export_dxf_list_to_pdf(generated_pages, pdf_out, use_color=False, cancel_check=lambda: self._is_cancelled)
                    if not self._is_cancelled:
                        self.log_signal.emit("  PDF Consolidation Complete.")
                
                self.finished_signal.emit(0, "Success")
            finally:
                pysys.stdout = old_stdout
                
        except Exception as e:
            self.finished_signal.emit(-1, str(e))

    def _wait_if_paused(self):
        """Blocking check called by the engine to handle Pause state."""
        self.mutex.lock()
        while self._is_paused:
            self.condition.wait(self.mutex)
        self.mutex.unlock()

    def toggle_pause(self, paused):
        self.mutex.lock()
        self._is_paused = paused
        if not paused:
            self.condition.wakeAll()
        self.mutex.unlock()

    def cancel(self):
        self._is_cancelled = True
        self.toggle_pause(False)

class StandaloneLogicToCad(QtWidgets.QMainWindow):
    def __init__(self, is_dark=True):
        super().__init__()
        self.is_dark = is_dark
        self.setObjectName("StandaloneLogicToCad")
        self.setWindowTitle("ALVT - Logic to CAD Converter")
        self.resize(750, 600)
        self.layout_params = load_layout_config()
        self.worker = None # Worker instance tracking
        
        # Apply theme only if it differs from current or if running purely standalone
        app = QtWidgets.QApplication.instance()
        # Note: We don't want to OVERWRITE the main app theme if it's already correct.
        # But we need the custom styles. apply_theme handles QSS too.
        apply_theme(app, dark_mode=is_dark)
        
        # Apply additional CSS (Theme-aware)
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
            #StandaloneLogicToCad QComboBox,
            #StandaloneLogicToCad QSpinBox,
            #StandaloneLogicToCad QDoubleSpinBox {{
                padding-right: 22px;
            }}
            #StandaloneLogicToCad QComboBox::drop-down,
            #StandaloneLogicToCad QComboBox::down-arrow,
            #StandaloneLogicToCad QSpinBox::up-button,
            #StandaloneLogicToCad QSpinBox::down-button,
            #StandaloneLogicToCad QSpinBox::up-arrow,
            #StandaloneLogicToCad QSpinBox::down-arrow,
            #StandaloneLogicToCad QDoubleSpinBox::up-button,
            #StandaloneLogicToCad QDoubleSpinBox::down-button,
            #StandaloneLogicToCad QDoubleSpinBox::up-arrow,
            #StandaloneLogicToCad QDoubleSpinBox::down-arrow {{
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
        main_layout.setSpacing(15)
        
        # Header Classy Brand Label
        header = QtWidgets.QLabel("MLK-II Boolean To Equivalent Converter")
        header.setProperty("class", "brand")
        header.setAlignment(QtCore.Qt.AlignCenter)
        header.setFixedHeight(40)
        self.header = header # Keep reference
        main_layout.addWidget(header)
        
        # --- Path Configuration Card ---
        path_group = QtWidgets.QGroupBox("PATH CONFIGURATION")
        path_layout = QtWidgets.QVBoxLayout()
        path_layout.setSpacing(5) # Even tighter spacing
        path_layout.setContentsMargins(15, 10, 15, 5) # Reduced even more
        
        # Helper for a row: [Label] [LineEdit] [BrowseButton]
        def add_row(parent_layout, label_text, line_edit, button):
            row_w = QtWidgets.QWidget()
            row_w.setFixedHeight(36)
            row_l = QtWidgets.QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            row_l.setSpacing(10)
            lbl = QtWidgets.QLabel(label_text)
            lbl.setFixedWidth(130) # Fixed label width for alignment
            row_l.addWidget(lbl)
            
            line_edit.setFixedHeight(28)
            row_l.addWidget(line_edit)
            
            button.setFixedHeight(28)
            button.setFixedWidth(96)
            row_l.addWidget(button)
            parent_layout.addWidget(row_w)
        
        # Source
        self.logic_path = QtWidgets.QLineEdit()
        self.logic_path.setPlaceholderText("Select Microlok Logic file...")
        self.btn_logic = QtWidgets.QPushButton("Browse")
        self.btn_logic.setFixedSize(96, 28)
        self.btn_logic.clicked.connect(self._browse_logic)
        add_row(path_layout, "Source Logic (.txt):", self.logic_path, self.btn_logic)

        # Template
        self.temp_path = QtWidgets.QLineEdit()
        self.temp_path.setPlaceholderText("Select CAD Template (.dxf)...")
        self.btn_tpl = QtWidgets.QPushButton("Browse")
        self.btn_tpl.setFixedSize(96, 28)
        self.btn_tpl.clicked.connect(self._browse_temp)
        add_row(path_layout, "CAD Template (.dxf):", self.temp_path, self.btn_tpl)

        # Destination
        self.dest_path = QtWidgets.QLineEdit()
        self.dest_path.setPlaceholderText("Select base file path (e.g. INASRR52SNT.dxf)...")
        self.btn_dest = QtWidgets.QPushButton("Browse")
        self.btn_dest.setFixedSize(96, 28)
        self.btn_dest.clicked.connect(self._browse_dest)
        add_row(path_layout, "Base File Name:", self.dest_path, self.btn_dest)
        
        # Connect logic path signal for auto-dest
        self.logic_path.editingFinished.connect(self._auto_update_dest)
        
        path_group.setLayout(path_layout)
        main_layout.addWidget(path_group)

        # --- Settings (Style & Numbering) ---
        settings_row = QtWidgets.QHBoxLayout()
        
        # Style Card
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
        settings_row.addWidget(style_group)
        
        # Numbering Card
        num_group = QtWidgets.QGroupBox("SHEET NUMBERING & ANCHORS")
        num_layout = QtWidgets.QFormLayout()
        num_layout.setSpacing(10)
        num_layout.setContentsMargins(15, 20, 15, 15)
        
        self.start_sheet_spin = ArrowSpinBox()
        self.start_sheet_spin.setRange(1, 99999)
        num_layout.addRow("Start Sheet No:", self.start_sheet_spin)
        
        self.reserved_index_spin = ArrowSpinBox()
        self.reserved_index_spin.setRange(1, 999)
        self.reserved_index_spin.setValue(50)
        num_layout.addRow("Reserved Index:", self.reserved_index_spin)
        
        # Add Settings Button in Parameters Group
        self.settings_btn = QtWidgets.QPushButton("Layout Settings ⚙️")
        self.settings_btn.setToolTip("Configure Absolute Sheet Coordinates, Margins, and Grid Thresholds")
        self.settings_btn.clicked.connect(self._show_layout_settings)
        num_layout.addRow("", self.settings_btn)
        
        num_group.setLayout(num_layout)
        settings_row.addWidget(num_group)
        
        main_layout.addLayout(settings_row)
        
        # --- Export Options Card ---
        export_group = QtWidgets.QGroupBox("EXPORT OPTIONS")
        export_layout = QtWidgets.QHBoxLayout()
        export_layout.setContentsMargins(15, 20, 15, 15)
        self.gen_dxf_check = QtWidgets.QCheckBox("Generate CAD Sheets (.dxf)")
        self.gen_dxf_check.setChecked(True)
        self.gen_pdf_check = QtWidgets.QCheckBox("Generate Merged PDF (.pdf)")
        self.gen_pdf_check.setChecked(True)
        export_layout.addWidget(self.gen_dxf_check)
        export_layout.addWidget(self.gen_pdf_check)
        export_group.setLayout(export_layout)
        main_layout.addWidget(export_group)
        
        # --- Live Logs Card ---
        log_group = QtWidgets.QGroupBox("EXECUTION LOGS")
        log_layout = QtWidgets.QVBoxLayout(log_group)
        log_layout.setContentsMargins(15, 25, 15, 15)
        self.log_output = QtWidgets.QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setFixedHeight(150)
        
        log_bg = "#111111" if is_dark else "#F9F9F9"
        log_text = "#00FF00" if is_dark else "#006400"
        self.log_output.setStyleSheet(f"font-family: Consolas, monospace; font-size: 11px; background-color: {log_bg}; color: {log_text}; border: 1px solid {input_border};")
        log_layout.addWidget(self.log_output)
        main_layout.addWidget(log_group)
        
        main_layout.addStretch()
        
        # Bottom Actions
        self.status_label = QtWidgets.QLabel("Ready to process")
        self.status_label.setStyleSheet("color: #888; font-style: italic;")
        main_layout.addWidget(self.status_label)
        
        btn_layout = QtWidgets.QHBoxLayout()
        self.run_btn = QtWidgets.QPushButton("EXECUTE CONVERSION")
        self.run_btn.setProperty("class", "primary")
        self.run_btn.setFixedHeight(50)
        self.run_btn.clicked.connect(self.run_conversion)
        
        self.pause_btn = QtWidgets.QPushButton("PAUSE")
        self.pause_btn.setFixedHeight(50)
        self.pause_btn.setFixedWidth(100)
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._handle_pause)
        
        btn_layout.addWidget(self.run_btn, 1)
        btn_layout.addWidget(self.pause_btn)
        main_layout.addLayout(btn_layout)
        
        # Ensure ALVT backgrounds are painted
        enable_brand_backgrounds(self)
        self._apply_overlay_colors()
        
        self.worker = None

    def _apply_overlay_colors(self):
        arrow_color = "#e0e0e0" if self.is_dark else "#202124"
        border_color = "rgba(255, 255, 255, 0.18)" if self.is_dark else "rgba(0, 0, 0, 0.18)"
        for w in (self.font_combo, self.font_size_spin, self.start_sheet_spin, self.reserved_index_spin):
            if hasattr(w, "set_overlay_colors"):
                w.set_overlay_colors(arrow_color, border_color)

    def _auto_update_dest(self):
        path = self.logic_path.text()
        if path and not self.dest_path.text():
            base = os.path.splitext(path)[0]
            self.dest_path.setText(base + ".dxf")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.status_label.setText("Closing... finalising PDF, please wait.")
            self.worker.cancel()
            # Give it up to 10 seconds to save the PDF cleanly
            if not self.worker.wait(10000):
                print("Worker did not finish gracefully in 10s. Forcing exit.")
        event.accept()

    def _get_default_dir(self):
        return QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation) or ""

    def _browse_logic(self):
        default_dir = self._get_default_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Source Logic", default_dir, "Logic Files (*.txt *.ML2);;All Files (*)")
        if path:
            self.logic_path.setText(path)
            if not self.dest_path.text():
                base = os.path.splitext(path)[0]
                self.dest_path.setText(base + ".dxf")

    def _browse_temp(self):
        default_dir = self._get_default_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select DXF Template", default_dir, "CAD Files (*.dxf);;All Files (*)")
        if path:
            self.temp_path.setText(path)

    def _browse_dest(self):
        default_dir = self._get_default_dir()
        initial_path = self.dest_path.text() or default_dir
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Select Export Destination", initial_path, "CAD Files (*.dxf);;All Files (*)")
        if path:
            self.dest_path.setText(path)

    def _handle_pause(self, checked):
        if self.worker:
            self.worker.toggle_pause(checked)
            self.pause_btn.setText("RESUME" if checked else "PAUSE")
            self.status_label.setText("Conversion Paused." if checked else "Conversion Resumed...")

    def run_conversion(self):
        logic = self.logic_path.text()
        dest = self.dest_path.text()
        template = self.temp_path.text() or "none"
        
        if not logic or not dest:
            QtWidgets.QMessageBox.warning(self, "Input Required", "Please specify both Source Logic and Destination path.")
            return
            
        # Get style values
        font = self.font_combo.currentText()
        size = str(self.font_size_spin.value())
        bold = "true" if self.bold_check.isChecked() else "false"
        limit = "none" # Removed page limit option per user request
        gen_dxf = "true" if self.gen_dxf_check.isChecked() else "false"
        gen_pdf = "true" if self.gen_pdf_check.isChecked() else "false"
        # Numbering and specific settings
        start_sheet = str(self.start_sheet_spin.value())
        reserved = str(self.reserved_index_spin.value())
        drawing_anchor = "" # Removed from UI, coordinate-based now
        
        # NOTE: script_path check removed. 
        # The engine is imported as a module (process_logic_file), 
        # so we don't need to find the physical .py file at runtime.
            
        # Direct Arguments for the engine
        # logic, output, template, limit, font, size, bold, gen_dxf, gen_pdf, start_p, res_idx, anchor
        engine_args = [
            logic, dest, template, limit, 
            font, float(size), bold == "true", 
            gen_dxf == "true", gen_pdf == "true", 
            int(start_sheet), int(reserved), drawing_anchor,
            self.layout_params
        ]
        
        print(f"DEBUG: Starting conversion for {logic}")
        self.log_output.clear()
        self.log_output.append(">>> Starting Integrated Conversion Engine...")
        self.status_label.setText("Processing logic...")
        self.log_output.repaint() # Force UI refresh
        
        self.run_btn.setEnabled(False)
        self.pause_btn.setEnabled(True) # ENABLE so user can actually pause
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("PAUSE")
        
        print(f"DEBUG: Spawning worker with args: {engine_args[:3]}...")
        self.worker = ConversionWorker(engine_args)
        self.worker.log_signal.connect(self._handle_worker_logs)
        self.worker.finished_signal.connect(self._on_finished)
        self.worker.start()

    def _handle_worker_logs(self, line):
        self.log_output.append(line)
        # Update status label with a concise version of the log
        if "Generating Page" in line:
            page_info = line.split("Page")[-1].replace("...", "").strip()
            self.status_label.setText(f"Generating Sheet: {page_info}...")
        elif "Rendering Rung" in line:
            rung_info = line.split("Rung")[-1].strip()
            self.status_label.setText(f"Processing: {rung_info}")
        elif "High-Speed PDF Generation" in line:
            self.status_label.setText("Converting DXF to PDF (Parallel)...")
        elif "Completed" in line and line.startswith("["):
            self.status_label.setText(f"Converting PDF: {line}")
        elif "Generating Index" in line:
            self.status_label.setText("Building Index Sheets...")
        elif "PDF Consolidation Complete" in line:
            self.status_label.setText("Finalizing Merged PDF Archive...")
        elif "Parsed" in line:
            self.status_label.setText(f"Logic Parsed: {line.split(' ')[1]} rungs found.")
        elif "Reading logic" in line:
            self.status_label.setText("Reading logic file...")
        elif "Extracted logic block" in line:
            self.status_label.setText("Extracting logic instructions...")
            
    def _show_layout_settings(self):
        from gui.layout_settings_dialog import LayoutSettingsDialog
        dlg = LayoutSettingsDialog(self, is_dark=self.is_dark)
        if dlg.exec_():
            self.layout_params = load_layout_config()
            print(f"Global layout settings refreshed: {self.layout_params}")

    def _on_finished(self, return_code, error):
        self.run_btn.setEnabled(True)
        self.pause_btn.setEnabled(False)
        
        if self.worker and self.worker._is_cancelled:
            self.status_label.setText("Aborted.")
            self.worker = None
            return
            
        if return_code == 0:
            self.status_label.setText("Success.")
            QtWidgets.QMessageBox.information(self, "ALVT Success", "CAD/PDF generation completed successfully!")
        else:
            self.status_label.setText("Failed.")
            self.log_output.append(f"<span style='color:#ff4444; font-weight:bold;'>ERROR: {error}</span>")
            QtWidgets.QMessageBox.critical(self, "Rendering Error", f"The conversion process failed:\n\n{error}")
        
        self.worker = None
    
    def closeEvent(self, event):
        """Cleanup worker threads on exit to prevent ghost processes."""
        if self.worker and self.worker.isRunning():
            print(">>> Waiting for background tasks to stop safely...")
            self.worker.cancel()
            self.worker.wait(2000) # Wait up to 2s
            if self.worker.isRunning():
                self.worker.terminate()
        event.accept()

if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    app = QtWidgets.QApplication(sys.argv)
    win = StandaloneLogicToCad()
    win.show()
    sys.exit(app.exec())
