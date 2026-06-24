# -*- coding: utf-8 -*-
import os
from PySide6 import QtWidgets, QtCore, QtGui

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    import sys
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p1 = os.path.join(base_dir, relative_path)
    if os.path.exists(p1): return p1
    p2 = os.path.join(os.path.dirname(base_dir), relative_path)
    if os.path.exists(p2): return p2
    return p1

class LogicToCadDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, default_logic_path=None):
        super().__init__(parent)
        self.setWindowTitle("Logic to CAD (DXF) Converter")
        self.resize(500, 250)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        # Grid for file selection
        grid = QtWidgets.QGridLayout()
        layout.addLayout(grid)
        
        # 1. Logic File
        grid.addWidget(QtWidgets.QLabel("Logic File (*.txt):"), 0, 0)
        self.logic_edit = QtWidgets.QLineEdit(default_logic_path or "")
        grid.addWidget(self.logic_edit, 0, 1)
        btn_logic = QtWidgets.QPushButton("...")
        btn_logic.setFixedWidth(30)
        btn_logic.clicked.connect(self._browse_logic)
        grid.addWidget(btn_logic, 0, 2)
        
        # 2. Template DXF
        grid.addWidget(QtWidgets.QLabel("Template DXF:"), 1, 0)
        self.template_edit = QtWidgets.QLineEdit("")
        # Try to find default template using robust resource path
        default_template = get_resource_path(os.path.join("resources", "templates", "Template.dxf"))
        if os.path.exists(default_template):
            self.template_edit.setText(default_template)
            
        grid.addWidget(self.template_edit, 1, 1)
        btn_temp = QtWidgets.QPushButton("...")
        btn_temp.setFixedWidth(30)
        btn_temp.clicked.connect(self._browse_template)
        grid.addWidget(btn_temp, 1, 2)
        
        # 3. Output DXF
        grid.addWidget(QtWidgets.QLabel("Output DXF:"), 2, 0)
        self.output_edit = QtWidgets.QLineEdit("")
        if default_logic_path:
            self.output_edit.setText(default_logic_path.replace(".txt", ".dxf").replace(".ML2", ".dxf"))
            
        grid.addWidget(self.output_edit, 2, 1)
        btn_out = QtWidgets.QPushButton("...")
        btn_out.setFixedWidth(30)
        btn_out.clicked.connect(self._browse_output)
        grid.addWidget(btn_out, 2, 2)
        
        # Options
        self.wrap_cb = QtWidgets.QCheckBox("Enable Automatic Wrapping (Slicing)")
        self.wrap_cb.setChecked(True)
        layout.addWidget(self.wrap_cb)
        
        layout.addStretch()
        
        # Buttons
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def _get_default_dir(self):
        return QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation) or ""

    def _browse_logic(self):
        default_dir = self._get_default_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Logic File", default_dir, "Logic Files (*.txt *.ML2);;All Files (*)")
        if path:
            self.logic_edit.setText(path)
            if not self.output_edit.text():
                self.output_edit.setText(path.replace(".txt", ".dxf").replace(".ML2", ".dxf"))

    def _browse_template(self):
        default_dir = self._get_default_dir()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select Template DXF", default_dir, "DXF Files (*.dxf);;All Files (*)")
        if path:
            self.template_edit.setText(path)

    def _browse_output(self):
        default_dir = self._get_default_dir()
        initial_path = self.output_edit.text() or default_dir
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save Output DXF", initial_path, "DXF Files (*.dxf);;All Files (*)")
        if path:
            self.output_edit.setText(path)

    def get_values(self):
        return {
            'logic': self.logic_edit.text(),
            'template': self.template_edit.text(),
            'output': self.output_edit.text(),
            'use_wrap': self.wrap_cb.isChecked()
        }
