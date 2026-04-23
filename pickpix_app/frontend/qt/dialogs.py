"""Dialog widgets for PickPix PySide6 UI."""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    def __init__(self, patterns: list[str], max_zoom: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setModal(True)
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入文件名模板（每行一个，使用 {number} 表示帧号，* 表示任意文本）"))

        self.pattern_edit = QPlainTextEdit(self)
        self.pattern_edit.setPlainText("\n".join(patterns))
        layout.addWidget(self.pattern_edit)

        hint = QLabel("示例: frame{number}.exr\n示例: *.{number}.exr")
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

        form = QFormLayout()
        self.max_zoom_spin = QDoubleSpinBox(self)
        self.max_zoom_spin.setRange(1.0, 100.0)
        self.max_zoom_spin.setDecimals(2)
        self.max_zoom_spin.setSingleStep(0.5)
        self.max_zoom_spin.setValue(float(max_zoom))
        form.addRow("最大放大倍率:", self.max_zoom_spin)
        layout.addLayout(form)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._result: dict | None = None

    def _on_accept(self) -> None:
        patterns = [line.strip() for line in self.pattern_edit.toPlainText().splitlines() if line.strip()]
        if not patterns:
            QMessageBox.warning(self, "警告", "请至少保留一个输入文件名模板")
            return
        if any("{number}" not in pattern for pattern in patterns):
            QMessageBox.warning(self, "警告", "每个模板都必须包含 {number} 占位符")
            return
        self._result = {"patterns": patterns, "max_zoom": float(self.max_zoom_spin.value())}
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


class RemoteSourceDialog(QDialog):
    def __init__(
        self,
        title: str,
        path_label: str,
        confirm_text: str,
        presets: Sequence[dict],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 320)
        self._presets = list(presets)
        self._result: dict | None = None

        main_layout = QVBoxLayout(self)
        form = QFormLayout()

        self.preset_combo = QComboBox(self)
        self.preset_combo.addItems([preset["label"] for preset in self._presets])
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        form.addRow("服务器:", self.preset_combo)

        self.host_edit = QLineEdit(self)
        self.host_edit.setReadOnly(True)
        form.addRow("地址:", self.host_edit)

        self.port_edit = QLineEdit(self)
        self.port_edit.setReadOnly(True)
        form.addRow("端口:", self.port_edit)

        self.user_edit = QLineEdit(self)
        self.user_edit.setReadOnly(True)
        form.addRow("账号:", self.user_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setReadOnly(True)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("密码:", self.password_edit)

        self.path_edit = QLineEdit(self)
        form.addRow(f"{path_label}:", self.path_edit)

        main_layout.addLayout(form)

        button_box = QDialogButtonBox(self)
        button_box.addButton(confirm_text, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        main_layout.addWidget(button_box)

        if self._presets:
            self._apply_preset()

    def _apply_preset(self) -> None:
        if not self._presets:
            return
        index = max(0, self.preset_combo.currentIndex())
        preset = self._presets[index]
        self.host_edit.setText(str(preset.get("host", "")))
        self.port_edit.setText(str(preset.get("port", 22)))
        self.user_edit.setText(str(preset.get("username", "")))
        self.password_edit.setText(str(preset.get("password", "")))

    def _on_accept(self) -> None:
        if not self._presets:
            QMessageBox.warning(self, "警告", "config 中没有可用的服务器预设")
            return
        preset = self._presets[max(0, self.preset_combo.currentIndex())]
        remote_path = self.path_edit.text().strip()
        if not remote_path:
            QMessageBox.warning(self, "警告", "请填写远程路径")
            return
        if not remote_path.startswith("/"):
            QMessageBox.warning(self, "警告", "远程路径必须是绝对路径，并以 / 开头")
            return
        try:
            port = int(str(preset.get("port", 22)))
        except ValueError:
            QMessageBox.warning(self, "警告", "端口必须是整数")
            return

        self._result = {
            "type": "sftp",
            "host": str(preset.get("host", "")),
            "port": port,
            "username": str(preset.get("username", "")),
            "password": str(preset.get("password", "")),
            "path": remote_path,
            "server_key": str(preset.get("key", "")),
            "server_label": str(preset.get("label", "")),
        }
        self.accept()

    def get_result(self) -> dict | None:
        return self._result


class ErrormapDialog(QDialog):
    def __init__(self, candidate_methods: Sequence[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("生成差分方法")
        self.setModal(True)
        self._result: tuple[str, str] | None = None

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.combo_a = QComboBox(self)
        self.combo_b = QComboBox(self)
        self.combo_a.addItems(candidate_methods)
        self.combo_b.addItems(candidate_methods)
        if len(candidate_methods) >= 2:
            self.combo_b.setCurrentIndex(1)

        form.addRow("方法 A:", self.combo_a)
        form.addRow("方法 B:", self.combo_b)
        layout.addLayout(form)

        button_box = QDialogButtonBox(self)
        button_box.addButton("添加差分方法", QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_accept(self) -> None:
        method_a = self.combo_a.currentText().strip()
        method_b = self.combo_b.currentText().strip()
        if not method_a or not method_b:
            QMessageBox.warning(self, "警告", "请选择两个方法")
            return
        if method_a == method_b:
            QMessageBox.warning(self, "警告", "请选择两个不同的方法")
            return
        self._result = (method_a, method_b)
        self.accept()

    def get_result(self) -> tuple[str, str] | None:
        return self._result
