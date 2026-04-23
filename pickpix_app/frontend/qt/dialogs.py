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
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
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
        manage_servers: Callable[[], Sequence[dict]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(420, 320)
        self._presets = list(presets)
        self._manage_servers = manage_servers
        self._result: dict | None = None

        main_layout = QVBoxLayout(self)
        form = QFormLayout()

        server_row = QHBoxLayout()
        self.preset_combo = QComboBox(self)
        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        server_row.addWidget(self.preset_combo, stretch=1)
        if self._manage_servers is not None:
            manage_button = QPushButton("管理服务器", self)
            manage_button.clicked.connect(self._open_server_manager)
            server_row.addWidget(manage_button)
        form.addRow("服务器:", server_row)

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

        self._reload_presets()

    def _reload_presets(self, preferred_key: str | None = None) -> None:
        current_key = preferred_key or self._get_current_preset_key()
        self.preset_combo.blockSignals(True)
        self.preset_combo.clear()
        for preset in self._presets:
            self.preset_combo.addItem(str(preset.get("label", "")), str(preset.get("key", "")))
        self.preset_combo.blockSignals(False)

        if not self._presets:
            self.host_edit.clear()
            self.port_edit.clear()
            self.user_edit.clear()
            self.password_edit.clear()
            return

        target_index = 0
        if current_key:
            for index, preset in enumerate(self._presets):
                if str(preset.get("key", "")) == current_key:
                    target_index = index
                    break
        self.preset_combo.setCurrentIndex(target_index)
        self._apply_preset()

    def _get_current_preset_key(self) -> str:
        if self.preset_combo.count() <= 0:
            return ""
        return str(self.preset_combo.currentData() or "")

    def _open_server_manager(self) -> None:
        if self._manage_servers is None:
            return
        updated_presets = self._manage_servers()
        self._presets = list(updated_presets)
        self._reload_presets()

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
            QMessageBox.warning(self, "警告", "没有可用服务器，请先添加服务器配置")
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


class ServerManagerDialog(QDialog):
    def __init__(
        self,
        presets: Sequence[dict],
        save_server: Callable[[dict, str | None], dict],
        delete_server: Callable[[str], bool],
        test_server: Callable[[dict], tuple[bool, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("服务器管理")
        self.setModal(True)
        self.resize(720, 420)

        self._save_server = save_server
        self._delete_server = delete_server
        self._test_server = test_server
        self._servers = [dict(preset) for preset in presets]
        self._new_server_counter = 1

        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        left.addWidget(QLabel("服务器列表"))
        self.server_list = QListWidget(self)
        self.server_list.currentRowChanged.connect(self._on_server_selected)
        left.addWidget(self.server_list, stretch=1)

        left_buttons = QHBoxLayout()
        add_button = QPushButton("新增", self)
        add_button.clicked.connect(self._add_server)
        left_buttons.addWidget(add_button)

        self.delete_button = QPushButton("删除", self)
        self.delete_button.clicked.connect(self._delete_selected_server)
        left_buttons.addWidget(self.delete_button)
        left.addLayout(left_buttons)

        layout.addLayout(left, stretch=1)

        right = QVBoxLayout()
        form = QFormLayout()

        self.label_edit = QLineEdit(self)
        form.addRow("名称:", self.label_edit)

        self.host_edit = QLineEdit(self)
        form.addRow("地址:", self.host_edit)

        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        form.addRow("端口:", self.port_spin)

        self.user_edit = QLineEdit(self)
        form.addRow("用户名:", self.user_edit)

        self.password_edit = QLineEdit(self)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("密码:", self.password_edit)

        right.addLayout(form)

        action_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接", self)
        self.test_button.clicked.connect(self._test_selected_server)
        action_row.addWidget(self.test_button)

        self.save_button = QPushButton("保存服务器", self)
        self.save_button.clicked.connect(self._save_selected_server)
        action_row.addWidget(self.save_button)
        action_row.addStretch(1)
        right.addLayout(action_row)
        right.addStretch(1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_button = QPushButton("关闭", self)
        close_button.clicked.connect(self.accept)
        close_row.addWidget(close_button)
        right.addLayout(close_row)

        layout.addLayout(right, stretch=2)

        self._refresh_server_list()
        if self._servers:
            self.server_list.setCurrentRow(0)
        else:
            self._set_form_enabled(False)

    def get_presets(self) -> list[dict]:
        return [dict(server) for server in self._servers]

    def _set_form_enabled(self, enabled: bool) -> None:
        for widget in (self.label_edit, self.host_edit, self.port_spin, self.user_edit, self.password_edit):
            widget.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)
        self.test_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)

    def _refresh_server_list(self) -> None:
        current_key = self._get_selected_server_key()
        self.server_list.blockSignals(True)
        self.server_list.clear()
        for server in self._servers:
            label = str(server.get("label", "未命名服务器")).strip() or "未命名服务器"
            host = str(server.get("host", "")).strip()
            text = f"{label} ({host})" if host else label
            self.server_list.addItem(text)
        self.server_list.blockSignals(False)

        if not self._servers:
            self._clear_form()
            self._set_form_enabled(False)
            return

        target_row = 0
        if current_key:
            for index, server in enumerate(self._servers):
                if str(server.get("key", "")) == current_key:
                    target_row = index
                    break
        self.server_list.setCurrentRow(target_row)

    def _clear_form(self) -> None:
        self.label_edit.clear()
        self.host_edit.clear()
        self.port_spin.setValue(22)
        self.user_edit.clear()
        self.password_edit.clear()

    def _get_selected_server_key(self) -> str:
        row = self.server_list.currentRow()
        if row < 0 or row >= len(self._servers):
            return ""
        return str(self._servers[row].get("key", ""))

    def _on_server_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._servers):
            self._clear_form()
            self._set_form_enabled(False)
            return
        self._set_form_enabled(True)
        server = self._servers[row]
        self.label_edit.setText(str(server.get("label", "")))
        self.host_edit.setText(str(server.get("host", "")))
        self.port_spin.setValue(int(server.get("port", 22) or 22))
        self.user_edit.setText(str(server.get("username", "")))
        self.password_edit.setText(str(server.get("password", "")))

    def _collect_form_data(self) -> dict | None:
        row = self.server_list.currentRow()
        if row < 0 or row >= len(self._servers):
            QMessageBox.warning(self, "警告", "请先选择一个服务器")
            return None
        label = self.label_edit.text().strip()
        host = self.host_edit.text().strip()
        username = self.user_edit.text().strip()
        password = self.password_edit.text()
        port = int(self.port_spin.value())

        if not label:
            QMessageBox.warning(self, "警告", "服务器名称不能为空")
            return None
        if not host:
            QMessageBox.warning(self, "警告", "服务器地址不能为空")
            return None
        if not username:
            QMessageBox.warning(self, "警告", "用户名不能为空")
            return None

        return {
            "key": str(self._servers[row].get("key", "")),
            "label": label,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }

    def _add_server(self) -> None:
        temp_key = f"__new_server_{self._new_server_counter}"
        self._new_server_counter += 1
        self._servers.append(
            {
                "key": temp_key,
                "label": f"新服务器 {len(self._servers) + 1}",
                "host": "",
                "port": 22,
                "username": "",
                "password": "",
            }
        )
        self._refresh_server_list()
        self.server_list.setCurrentRow(len(self._servers) - 1)
        self.label_edit.selectAll()
        self.label_edit.setFocus()

    def _delete_selected_server(self) -> None:
        row = self.server_list.currentRow()
        if row < 0 or row >= len(self._servers):
            return
        server = self._servers[row]
        label = str(server.get("label", "服务器"))
        result = QMessageBox.question(self, "确认删除", f"确定删除服务器“{label}”吗？")
        if result != QMessageBox.StandardButton.Yes:
            return

        server_key = str(server.get("key", ""))
        if server_key and not server_key.startswith("__new_server_"):
            self._delete_server(server_key)
        self._servers.pop(row)
        self._refresh_server_list()

    def _test_selected_server(self) -> None:
        data = self._collect_form_data()
        if data is None:
            return
        success, message = self._test_server(data)
        if success:
            QMessageBox.information(self, "连接测试", message)
        else:
            QMessageBox.critical(self, "连接测试失败", message)

    def _save_selected_server(self) -> None:
        data = self._collect_form_data()
        if data is None:
            return
        row = self.server_list.currentRow()
        server_key = data.get("key", "")
        if server_key.startswith("__new_server_"):
            server_key = None
        saved = self._save_server(data, server_key)
        self._servers[row] = dict(saved)
        self._refresh_server_list()
        self.server_list.setCurrentRow(row)
        QMessageBox.information(self, "已保存", f"服务器“{saved['label']}”已保存")


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
