"""PickPix PySide6 application.

This module is a feature-complete rewrite of the original Tkinter GUI in
:mod:`pickpix_app.frontend.gui`.  All business state semantics are preserved
so workspace files, bookmarks, clone/errormap methods, batch cropping and
remote SFTP IO continue to behave identically.
"""

from __future__ import annotations

import gc
import os
import sys
from typing import Any, Callable

os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QImage, QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from pickpix_app.backend import PickPixBackend
from pickpix_app.config import AppConfig

from .dialogs import ErrormapDialog, RemoteSourceDialog, SettingsDialog
from .flow_layout import FlowLayout
from .preview_canvas import PreviewCanvas, pil_to_qimage


BOX_COLORS: list[str] = [
    "#FF0000", "#00FF00", "#0000FF", "#FFFF00",
    "#FF00FF", "#00FFFF", "#FFA500", "#800080",
]


def shorten_text(text: str, max_chars: int) -> str:
    text = str(text)
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3] + "..."


class MethodRowWidget(QWidget):
    """One row in the method panel: checkbox + offset spinbox + clone/remove buttons."""

    def __init__(
        self,
        method: str,
        label: str,
        selected: bool,
        offset: int,
        on_changed: Callable[[], None],
        on_clone: Callable[[str], None],
        on_remove: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.method = method

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(2)

        self.checkbox = QCheckBox(label, self)
        self.checkbox.setChecked(selected)
        self.checkbox.toggled.connect(lambda _: on_changed())
        root.addWidget(self.checkbox)

        controls = QHBoxLayout()
        controls.setContentsMargins(24, 0, 0, 0)
        controls.setSpacing(6)

        offset_label = QLabel("偏移")
        offset_label.setStyleSheet("color: gray;")
        controls.addWidget(offset_label)

        self.offset_spin = QSpinBox(self)
        self.offset_spin.setRange(-9999, 9999)
        self.offset_spin.setValue(int(offset))
        self.offset_spin.setFixedWidth(72)
        self.offset_spin.valueChanged.connect(lambda _: on_changed())
        controls.addWidget(self.offset_spin)
        controls.addStretch(1)

        clone_button = QPushButton("克隆", self)
        clone_button.setFixedWidth(54)
        clone_button.clicked.connect(lambda: on_clone(self.method))
        controls.addWidget(clone_button)

        remove_button = QPushButton("移除", self)
        remove_button.setFixedWidth(54)
        remove_button.clicked.connect(lambda: on_remove(self.method))
        controls.addWidget(remove_button)

        root.addLayout(controls)

    def is_selected(self) -> bool:
        return self.checkbox.isChecked()

    def get_offset(self) -> int:
        return int(self.offset_spin.value())

    def set_selected(self, selected: bool, silent: bool = True) -> None:
        if silent:
            self.checkbox.blockSignals(True)
        self.checkbox.setChecked(selected)
        if silent:
            self.checkbox.blockSignals(False)


class PreviewCell(QFrame):
    def __init__(self, method: str, title: str, view_size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.title_label = QLabel(title, self)
        self.title_label.setStyleSheet("background-color: #E3F2FD; color: #102030; padding: 4px;")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setBold(True)
        self.title_label.setFont(font)
        layout.addWidget(self.title_label)

        self.canvas = PreviewCanvas(method, self)
        self.canvas.set_fixed_view_size(view_size, view_size)
        layout.addWidget(self.canvas)

        self.setFixedSize(view_size + 16, view_size + 46)


class PickPixMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.config = AppConfig()
        self.backend = PickPixBackend(self.config.input_filename_patterns)
        self.setWindowTitle(self.config.title)
        self.resize(1600, 1000)

        # --- state ---
        self.input_folder: str = ""
        self.input_folders: list[str] = []
        self.input_sources: list[dict] = []
        self.output_folder: str = str(self.config.default_output_dir)
        self.output_target: dict | None = None

        self.methods: list[str] = []
        self.all_methods: list[str] = []
        self.scanned_methods: list[str] = []
        self.method_entries: dict[str, dict] = {}
        self.method_paths: dict[str, str] = {}
        self.method_sources: dict[str, dict] = {}
        self.methods_with_frames: list[str] = []
        self.method_ui_defaults: dict[str, dict] = {}
        self.method_rows: dict[str, MethodRowWidget] = {}
        self.method_filter_pending_changes = False
        self.is_updating_method_filter_controls = False
        self.last_scan_errors: list[str] = []

        self.frame_numbers: list[str] = []
        self.current_frame_index: int = 0

        self.workspace_file_path: str | None = None
        self.workspace_dirty: bool = False
        self.is_restoring_workspace: bool = False

        self.bookmarked_frames: set[str] = set()
        self.bookmark_values: list[str] = []

        self.method_images: dict[str, Image.Image] = {}
        self.preview_cells: dict[str, PreviewCell] = {}

        self.method_view_size_min = 180
        self.method_view_size_max = 960
        self.method_view_size = 320

        self.zoom_level: float = 1.0
        self.min_zoom: float = 0.1
        self.max_zoom: float = self.config.max_zoom

        self.pan_offset_x: int = 0
        self.pan_offset_y: int = 0

        # current crop selection (in image coords)
        self.crop_start_x: int | None = None
        self.crop_start_y: int | None = None
        self.crop_end_x: int | None = None
        self.crop_end_y: int | None = None
        self.crop_boxes: list[tuple[int, int, int, int]] = []
        self.frame_crop_boxes: dict[str, list[tuple[int, int, int, int]]] = {}

        self._resize_job: QTimer | None = None

        self._build_ui()
        self._update_workspace_label()
        self._update_bookmark_controls()
        self._rebuild_method_panel()
        self._update_output_label()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 4)
        outer.setSpacing(4)

        outer.addLayout(self._build_top_controls())
        outer.addLayout(self._build_workspace_bar())

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.addWidget(self._build_left_panel())
        self.main_splitter.addWidget(self._build_right_panel())
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setSizes([1100, 360])
        outer.addWidget(self.main_splitter, stretch=1)

        self.status = QStatusBar(self)
        self.setStatusBar(self.status)
        self.status.showMessage("就绪")

    def _build_top_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        def btn(text: str, color: str, handler) -> QPushButton:
            b = QPushButton(text)
            b.setStyleSheet(f"background-color: {color}; color: white; padding: 6px 10px;")
            b.clicked.connect(handler)
            return b

        row.addWidget(btn("添加输入文件夹", "#4CAF50", self.select_input_folder))
        row.addWidget(btn("添加远程输入", "#607D8B", self.open_remote_input_dialog))
        self.input_label = QLabel("未选择文件夹")
        self.input_label.setStyleSheet("color: gray;")
        row.addWidget(self.input_label)
        row.addWidget(btn("清空输入", "#9E9E9E", self.clear_input_folders))
        row.addWidget(btn("设置", "#795548", self.open_settings_dialog))

        row.addSpacing(12)
        row.addWidget(btn("选择输出文件夹", "#2196F3", self.select_output_folder))
        row.addWidget(btn("选择远程输出", "#455A64", self.open_remote_output_dialog))
        self.output_label = QLabel("未选择文件夹")
        self.output_label.setStyleSheet("color: gray;")
        row.addWidget(self.output_label)

        row.addSpacing(12)
        zoom_label = QLabel("缩放(Ctrl+滚轮):")
        row.addWidget(zoom_label)
        reset_btn = QPushButton("重置1:1")
        reset_btn.setStyleSheet("background-color: #FF9800; color: white; padding: 6px 10px;")
        reset_btn.clicked.connect(self.reset_zoom)
        row.addWidget(reset_btn)

        row.addSpacing(12)
        row.addWidget(QLabel("预览大小:"))
        self.view_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.view_size_slider.setRange(self.method_view_size_min, self.method_view_size_max)
        self.view_size_slider.setSingleStep(20)
        self.view_size_slider.setPageStep(40)
        self.view_size_slider.setValue(self.method_view_size)
        self.view_size_slider.setFixedWidth(180)
        self.view_size_slider.valueChanged.connect(self._on_view_size_changed)
        row.addWidget(self.view_size_slider)
        self.view_size_value_label = QLabel(f"{self.method_view_size}px")
        self.view_size_value_label.setMinimumWidth(60)
        row.addWidget(self.view_size_value_label)

        row.addStretch(1)
        return row

    def _build_workspace_bar(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        def btn(text: str, color: str, handler) -> QPushButton:
            b = QPushButton(text)
            b.setStyleSheet(f"background-color: {color}; color: white; padding: 6px 10px;")
            b.clicked.connect(handler)
            return b

        row.addWidget(btn("保存工程", "#00897B", self.save_workspace))
        row.addWidget(btn("工程另存", "#26A69A", self.save_workspace_as))
        row.addWidget(btn("导入工程", "#546E7A", self.load_workspace))
        self.workspace_label = QLabel("工程: 未保存")
        self.workspace_label.setStyleSheet("color: gray;")
        row.addWidget(self.workspace_label)
        row.addStretch(1)
        return row

    def _build_left_panel(self) -> QWidget:
        container = QWidget(self)
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)

        self.preview_scroll = QScrollArea(container)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setStyleSheet("background-color: #2d2d2d;")
        self.preview_host = QWidget()
        self.preview_host.setStyleSheet("background-color: #2d2d2d;")
        self.preview_layout = FlowLayout(self.preview_host, margin=6, h_spacing=8, v_spacing=8)
        self.preview_host.setLayout(self.preview_layout)
        self.preview_scroll.setWidget(self.preview_host)
        v.addWidget(self.preview_scroll, stretch=1)

        nav_row = QHBoxLayout()
        prev_btn = QPushButton("◀ 上一帧")
        prev_btn.clicked.connect(self.prev_frame)
        nav_row.addWidget(prev_btn)
        next_btn = QPushButton("下一帧 ▶")
        next_btn.clicked.connect(self.next_frame)
        nav_row.addWidget(next_btn)
        self.frame_info_label = QLabel("帧: 0 / 0")
        self.frame_info_label.setStyleSheet("font-weight: bold;")
        nav_row.addSpacing(8)
        nav_row.addWidget(self.frame_info_label)
        nav_row.addSpacing(16)
        nav_row.addWidget(QLabel("跳转到帧号:"))
        self.frame_jump_edit = QLineEdit()
        self.frame_jump_edit.setFixedWidth(96)
        self.frame_jump_edit.returnPressed.connect(self.jump_to_frame)
        nav_row.addWidget(self.frame_jump_edit)
        jump_btn = QPushButton("跳转")
        jump_btn.clicked.connect(self.jump_to_frame)
        nav_row.addWidget(jump_btn)
        nav_row.addStretch(1)
        v.addLayout(nav_row)

        bookmark_row = QHBoxLayout()
        self.bookmark_toggle_button = QPushButton("收藏当前帧")
        self.bookmark_toggle_button.clicked.connect(self.toggle_current_bookmark)
        bookmark_row.addWidget(self.bookmark_toggle_button)
        prev_bm = QPushButton("上一书签")
        prev_bm.clicked.connect(lambda: self.jump_relative_bookmark(-1))
        bookmark_row.addWidget(prev_bm)
        next_bm = QPushButton("下一书签")
        next_bm.clicked.connect(lambda: self.jump_relative_bookmark(1))
        bookmark_row.addWidget(next_bm)
        bookmark_row.addSpacing(12)
        bookmark_row.addWidget(QLabel("书签:"))
        self.bookmark_combo = QComboBox()
        self.bookmark_combo.setFixedWidth(120)
        bookmark_row.addWidget(self.bookmark_combo)
        jump_bm = QPushButton("跳转书签")
        jump_bm.clicked.connect(self.jump_to_bookmark)
        bookmark_row.addWidget(jump_bm)
        bookmark_row.addStretch(1)
        v.addLayout(bookmark_row)

        return container

    def _build_right_panel(self) -> QWidget:
        wrapper = QScrollArea(self)
        wrapper.setMinimumWidth(320)
        wrapper.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        wrapper.setWidgetResizable(True)
        inner = QWidget(wrapper)
        wrapper.setWidget(inner)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # method list group
        methods_group = QGroupBox("检测到的方法")
        methods_layout = QVBoxLayout(methods_group)
        toolbar = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        select_all_btn.clicked.connect(lambda: self._set_all_method_filters(True))
        deselect_all_btn = QPushButton("全不选")
        deselect_all_btn.clicked.connect(lambda: self._set_all_method_filters(False))
        self.apply_filter_btn = QPushButton("确定")
        self.apply_filter_btn.setEnabled(False)
        self.apply_filter_btn.clicked.connect(self.refresh_visible_methods)
        gen_diff_btn = QPushButton("生成差分")
        gen_diff_btn.clicked.connect(self.open_errormap_dialog)
        toolbar.addWidget(select_all_btn)
        toolbar.addWidget(deselect_all_btn)
        toolbar.addWidget(self.apply_filter_btn)
        toolbar.addWidget(gen_diff_btn)
        toolbar.addStretch(1)
        methods_layout.addLayout(toolbar)

        self.methods_summary_label = QLabel("显示 0 / 0")
        self.methods_summary_label.setStyleSheet("color: gray;")
        methods_layout.addWidget(self.methods_summary_label)

        self.method_scroll = QScrollArea(inner)
        self.method_scroll.setWidgetResizable(True)
        self.method_scroll.setMinimumHeight(220)
        self.method_scroll_host = QWidget()
        self.method_scroll_layout = QVBoxLayout(self.method_scroll_host)
        self.method_scroll_layout.setContentsMargins(4, 4, 4, 4)
        self.method_scroll_layout.setSpacing(2)
        self.method_scroll_layout.addStretch(1)
        self.method_scroll.setWidget(self.method_scroll_host)
        methods_layout.addWidget(self.method_scroll)
        layout.addWidget(methods_group)

        # current crop info
        info_group = QGroupBox("当前裁剪框")
        info_layout = QGridLayout(info_group)
        self.coord_label = QLabel("(-, -)")
        self.coord_label.setStyleSheet("font-weight: bold;")
        self.size_label = QLabel("- × -")
        self.size_label.setStyleSheet("font-weight: bold;")
        self.end_coord_label = QLabel("(-, -)")
        info_layout.addWidget(QLabel("起始坐标 (x, y):"), 0, 0)
        info_layout.addWidget(self.coord_label, 0, 1)
        info_layout.addWidget(QLabel("裁剪尺寸 (w × h):"), 1, 0)
        info_layout.addWidget(self.size_label, 1, 1)
        info_layout.addWidget(QLabel("结束坐标:"), 2, 0)
        info_layout.addWidget(self.end_coord_label, 2, 1)
        add_box_btn = QPushButton("✓ 添加到裁剪列表")
        add_box_btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; padding: 6px;")
        add_box_btn.clicked.connect(self.add_crop_box)
        info_layout.addWidget(add_box_btn, 3, 0, 1, 2)
        layout.addWidget(info_group)

        # boxes list
        boxes_group = QGroupBox("裁剪框列表")
        boxes_layout = QVBoxLayout(boxes_group)
        self.boxes_list = QListWidget()
        self.boxes_list.setFixedHeight(96)
        self.boxes_list.currentRowChanged.connect(self._select_crop_box_by_row)
        self.boxes_list.itemDoubleClicked.connect(self.remove_selected_box)
        boxes_layout.addWidget(self.boxes_list)
        hint = QLabel("双击删除选中的裁剪框")
        hint.setStyleSheet("color: gray;")
        boxes_layout.addWidget(hint)
        clear_btn = QPushButton("清空所有裁剪框")
        clear_btn.setStyleSheet("background-color: #f44336; color: white;")
        clear_btn.clicked.connect(self.clear_all_boxes)
        boxes_layout.addWidget(clear_btn)
        layout.addWidget(boxes_group)

        # manual coords
        manual_group = QGroupBox("手动输入坐标")
        manual_layout = QGridLayout(manual_group)
        self.x_edit = QLineEdit()
        self.y_edit = QLineEdit()
        self.w_edit = QLineEdit()
        self.h_edit = QLineEdit()
        for edit in (self.x_edit, self.y_edit, self.w_edit, self.h_edit):
            edit.setValidator(QIntValidator(-999999, 999999, self))
            edit.setFixedWidth(80)
        manual_layout.addWidget(QLabel("X:"), 0, 0)
        manual_layout.addWidget(self.x_edit, 0, 1)
        manual_layout.addWidget(QLabel("Y:"), 0, 2)
        manual_layout.addWidget(self.y_edit, 0, 3)
        manual_layout.addWidget(QLabel("宽度:"), 1, 0)
        manual_layout.addWidget(self.w_edit, 1, 1)
        manual_layout.addWidget(QLabel("高度:"), 1, 2)
        manual_layout.addWidget(self.h_edit, 1, 3)
        apply_manual_btn = QPushButton("应用坐标")
        apply_manual_btn.setStyleSheet("background-color: #FF9800; color: white;")
        apply_manual_btn.clicked.connect(self.apply_manual_coords)
        manual_layout.addWidget(apply_manual_btn, 2, 0, 1, 4)
        layout.addWidget(manual_group)

        # preview
        preview_group = QGroupBox("裁剪预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(260, 200)
        self.preview_label.setStyleSheet("background-color: #404040;")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(preview_group)

        # actions
        action_group = QGroupBox("操作")
        action_layout = QVBoxLayout(action_group)
        crop_current_btn = QPushButton("🗸 批量裁剪当前帧")
        crop_current_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px;")
        crop_current_btn.clicked.connect(self.crop_current_frame)
        action_layout.addWidget(crop_current_btn)
        batch_crop_btn = QPushButton("批量裁剪所有帧")
        batch_crop_btn.setStyleSheet("background-color: #2196F3; color: white; padding: 8px;")
        batch_crop_btn.clicked.connect(self.batch_crop_all)
        action_layout.addWidget(batch_crop_btn)
        layout.addWidget(action_group)

        layout.addStretch(1)
        return wrapper

    # ------------------------------------------------------------------ workspace state
    def _update_workspace_label(self) -> None:
        if self.workspace_file_path:
            base = os.path.basename(self.workspace_file_path)
            prefix = "* " if self.workspace_dirty else ""
            self.workspace_label.setText(f"工程: {prefix}{shorten_text(base, 36)}")
            self.workspace_label.setStyleSheet("color: black;")
        else:
            prefix = "* " if self.workspace_dirty else ""
            self.workspace_label.setText(f"工程: {prefix}未保存")
            self.workspace_label.setStyleSheet("color: gray;")

    def _set_workspace_file_path(self, path: str | None) -> None:
        self.workspace_file_path = path
        self._update_workspace_label()

    def _mark_workspace_dirty(self) -> None:
        if self.is_restoring_workspace:
            return
        self.workspace_dirty = True
        self._update_workspace_label()

    def _mark_workspace_clean(self) -> None:
        self.workspace_dirty = False
        self._update_workspace_label()

    def _copy_mapping(self, data: Any) -> dict:
        return dict(data) if isinstance(data, dict) else {}

    def _get_current_frame_num(self) -> str | None:
        if not self.frame_numbers:
            return None
        if self.current_frame_index < 0 or self.current_frame_index >= len(self.frame_numbers):
            return None
        return self.frame_numbers[self.current_frame_index]

    def _clone_crop_boxes(self, boxes: list[tuple[int, int, int, int]] | tuple[tuple[int, int, int, int], ...]) -> list[tuple[int, int, int, int]]:
        return [tuple(int(v) for v in box) for box in boxes if len(box) == 4]

    def _sync_current_frame_crop_boxes(self) -> None:
        frame_num = self._get_current_frame_num()
        if not frame_num:
            return
        if self.crop_boxes:
            self.frame_crop_boxes[frame_num] = self._clone_crop_boxes(self.crop_boxes)
        else:
            self.frame_crop_boxes.pop(frame_num, None)

    def _restore_crop_boxes_for_frame(self, frame_num: str | None) -> None:
        if frame_num:
            self.crop_boxes = self._clone_crop_boxes(self.frame_crop_boxes.get(frame_num, []))
        else:
            self.crop_boxes = []
        self._clear_active_crop_selection(update_view=False)
        self._rebuild_crop_boxes_list()

    def _get_crop_boxes_for_frame(self, frame_num: str | None) -> list[tuple[int, int, int, int]]:
        if not frame_num:
            return []
        current_frame = self._get_current_frame_num()
        if frame_num == current_frame:
            return self._clone_crop_boxes(self.crop_boxes)
        return self._clone_crop_boxes(self.frame_crop_boxes.get(frame_num, []))

    def _clear_active_crop_selection(self, update_view: bool = True) -> None:
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        self.preview_label.clear()
        if update_view:
            self._redraw_all_crop_boxes()

    # ------------------------------------------------------------------ method entries
    def _get_method_entry(self, method: str) -> dict | None:
        return self.method_entries.get(method)

    def _is_source_method(self, method: str) -> bool:
        entry = self._get_method_entry(method)
        return bool(entry) and entry.get("type") == "source"

    def _build_source_method_entry(self, source: dict, path: str, origin: str = "scan") -> dict:
        return {
            "type": "source",
            "origin": origin,
            "source": dict(source),
            "path": str(path),
        }

    def _build_errormap_method_entry(self, method_a: str, method_b: str, origin: str = "errormap") -> dict:
        return {
            "type": "errormap",
            "origin": origin,
            "parents": (method_a, method_b),
        }

    def _rebuild_method_source_maps(self) -> None:
        self.method_paths = {}
        self.method_sources = {}
        for method in self.all_methods:
            entry = self.method_entries.get(method)
            if not entry or entry.get("type") != "source":
                continue
            self.method_paths[method] = str(entry.get("path", ""))
            src = entry.get("source")
            if src:
                self.method_sources[method] = dict(src)

    def _get_method_list_label(self, method: str) -> str:
        entry = self._get_method_entry(method) or {}
        tags: list[str] = []
        if entry.get("origin") == "clone":
            tags.append("克隆")
        if entry.get("type") == "errormap":
            tags.append("差分")
        if not tags:
            return shorten_text(method, 24)
        return shorten_text(f"{method} [{' / '.join(tags)}]", 24)

    def _make_unique_session_method_name(self, base_name: str) -> str:
        candidate = str(base_name).strip() or "method"
        if candidate not in self.all_methods:
            return candidate
        index = 2
        while True:
            value = f"{candidate}_{index}"
            if value not in self.all_methods:
                return value
            index += 1

    def _get_method_offset(self, method: str) -> int:
        row = self.method_rows.get(method)
        if row is None:
            return 0
        return row.get_offset()

    def _get_method_render_frame_num(self, method: str, logical_frame_num: str) -> str | None:
        offset = self._get_method_offset(method)
        if offset == 0:
            return logical_frame_num
        target = int(logical_frame_num) + offset
        if target < 0:
            return None
        return str(target).zfill(len(logical_frame_num))

    def _get_method_title(self, method: str, logical_frame_num: str) -> str:
        entry = self._get_method_entry(method) or {}
        base_title = self._get_method_list_label(method)
        render_frame_num = self._get_method_render_frame_num(method, logical_frame_num)
        offset = self._get_method_offset(method)
        details: list[str] = []

        if offset != 0:
            if render_frame_num is None:
                details.append(f"偏移 {offset:+d}")
            else:
                details.append(f"偏移 {offset:+d} -> {render_frame_num}")

        if entry.get("type") == "errormap":
            parent_frames: list[str] = []
            parent_render = render_frame_num if render_frame_num is not None else logical_frame_num
            for parent in entry.get("parents", ()):
                actual = self._get_method_render_frame_num(parent, parent_render)
                parent_frames.append(f"{parent}:{actual if actual is not None else '-'}")
            if parent_frames:
                details.append("差分 " + " vs ".join(parent_frames))

        if not details:
            return shorten_text(base_title, 32)
        return shorten_text(f"{base_title} ({'; '.join(details)})", 32)

    def _get_source_frame_image_entry(self, source: dict, frame_num: str) -> str | None:
        return self.backend.get_frame_image_entry({"__src__": source}, "__src__", frame_num)

    def _load_source_frame_image(self, source: dict, frame_num: str) -> Image.Image | None:
        return self.backend.load_method_frame_image({"__src__": source}, "__src__", frame_num)

    def load_method_frame_image(self, method: str, frame_num: str) -> Image.Image:
        entry = self._get_method_entry(method)
        if not entry:
            raise ValueError(f"unknown method: {method}")
        render_frame_num = self._get_method_render_frame_num(method, frame_num)
        if render_frame_num is None:
            raise ValueError(f"方法 {method} 的偏移导致帧号超出范围")

        if entry.get("type") == "source":
            src = entry.get("source", {})
            image_entry = self._get_source_frame_image_entry(src, render_frame_num)
            if image_entry is None:
                raise ValueError(f"方法 {method} 缺少帧 {render_frame_num}")
            image = self._load_source_frame_image(src, render_frame_num)
            if image is None:
                raise ValueError(f"方法 {method} 无法读取帧 {render_frame_num}")
            return image

        parent_a, parent_b = entry.get("parents", (None, None))
        if not parent_a or not parent_b:
            raise ValueError(f"差分方法 {method} 缺少来源方法")

        first = None
        second = None
        try:
            first = self.load_method_frame_image(parent_a, render_frame_num)
            second = self.load_method_frame_image(parent_b, render_frame_num)
            return self.backend.crop.create_absolute_error_map_image(first, second)
        finally:
            if first is not None:
                first.close()
            if second is not None:
                second.close()

    # ------------------------------------------------------------------ method panel
    def _clear_method_rows(self) -> None:
        while self.method_scroll_layout.count() > 0:
            item = self.method_scroll_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.method_rows = {}

    def _rebuild_method_panel(self) -> None:
        self._clear_method_rows()
        self.is_updating_method_filter_controls = True
        try:
            if not self.all_methods:
                placeholder = QLabel("扫描后将在这里列出方法")
                placeholder.setStyleSheet("color: gray;")
                self.method_scroll_layout.addWidget(placeholder)
                self.method_scroll_layout.addStretch(1)
                self._set_method_filter_pending(False)
                self._update_method_filter_summary()
                return

            for method in self.all_methods:
                defaults = self.method_ui_defaults.pop(method, None) if method not in self.method_rows else None
                selected = True
                offset_value = 0
                if defaults is not None:
                    selected = bool(defaults.get("selected", True))
                    try:
                        offset_value = int(defaults.get("offset", 0))
                    except (TypeError, ValueError):
                        offset_value = 0

                label = self._get_method_list_label(method)
                row = MethodRowWidget(
                    method=method,
                    label=label,
                    selected=selected,
                    offset=offset_value,
                    on_changed=self._on_method_filter_changed,
                    on_clone=self.clone_method,
                    on_remove=self.remove_method,
                )
                self.method_scroll_layout.addWidget(row)
                self.method_rows[method] = row
            self.method_scroll_layout.addStretch(1)
            self._set_method_filter_pending(False)
            self._update_method_filter_summary()
        finally:
            self.is_updating_method_filter_controls = False

    def _update_method_filter_summary(self) -> None:
        selected = sum(1 for row in self.method_rows.values() if row.is_selected())
        prefix = "待应用" if self.method_filter_pending_changes else "显示"
        self.methods_summary_label.setText(f"{prefix} {selected} / {len(self.all_methods)}")

    def _set_method_filter_pending(self, pending: bool) -> None:
        self.method_filter_pending_changes = pending
        self.apply_filter_btn.setEnabled(pending)
        self._update_method_filter_summary()

    def _on_method_filter_changed(self) -> None:
        if self.is_updating_method_filter_controls:
            return
        self._set_method_filter_pending(True)

    def _set_all_method_filters(self, selected: bool) -> None:
        self.is_updating_method_filter_controls = True
        for row in self.method_rows.values():
            row.set_selected(selected, silent=True)
        self.is_updating_method_filter_controls = False
        self._set_method_filter_pending(True)

    def refresh_visible_methods(self, reload_frame: bool = True) -> None:
        if self.method_rows:
            self.methods = [method for method in self.all_methods if self.method_rows[method].is_selected()]
        else:
            self.methods = []
        self._set_method_filter_pending(False)
        self._update_method_status()
        if reload_frame and self.frame_numbers:
            self.load_current_frame()
        elif not self.methods:
            self._clear_preview_cells()
        self._mark_workspace_dirty()

    def _update_method_status(self) -> None:
        self._update_method_filter_summary()
        self.status.showMessage(
            f"方法列表 {len(self.all_methods)} 个，当前显示 {len(self.methods)} 个，{len(self.frame_numbers)} 帧"
        )

    def clone_method(self, method: str) -> None:
        entry = self._get_method_entry(method)
        if not entry:
            return
        clone_name = self._make_unique_session_method_name(f"{method}_clone")
        offset_value = self._get_method_offset(method)

        if entry.get("type") == "source":
            clone_entry = self._build_source_method_entry(entry.get("source", {}), entry.get("path", ""), origin="clone")
        else:
            parent_a, parent_b = entry.get("parents", (None, None))
            if not parent_a or not parent_b:
                QMessageBox.warning(self, "警告", f"方法 {method} 没有可克隆的差分来源")
                return
            clone_entry = self._build_errormap_method_entry(parent_a, parent_b, origin="clone")

        row = self.method_rows.get(method)
        selected = row.is_selected() if row is not None else True
        self.method_entries[clone_name] = clone_entry
        self.all_methods.append(clone_name)
        self.method_ui_defaults[clone_name] = {"selected": selected, "offset": offset_value}
        self._rebuild_method_source_maps()
        self._rebuild_method_panel()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self._mark_workspace_dirty()
        self.status.showMessage(f"已克隆方法: {method} -> {clone_name}")

    def remove_method(self, method: str) -> None:
        if method not in self.method_entries:
            return
        to_remove = {method}
        changed = True
        while changed:
            changed = False
            for name, entry in self.method_entries.items():
                if name in to_remove or entry.get("type") != "errormap":
                    continue
                parents = entry.get("parents", ())
                if any(parent in to_remove for parent in parents):
                    to_remove.add(name)
                    changed = True

        ordered_removal = [name for name in self.all_methods if name in to_remove]
        if not ordered_removal:
            return
        message = f"确定从列表中移除 {method} 吗？"
        dependents = [name for name in ordered_removal if name != method]
        if dependents:
            message += "\n\n以下差分方法也会一起移除:\n" + "\n".join(dependents)
        result = QMessageBox.question(self, "确认移除", message, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if result != QMessageBox.StandardButton.Yes:
            return

        for name in ordered_removal:
            self.method_entries.pop(name, None)
            self.method_ui_defaults.pop(name, None)
            if name in self.all_methods:
                self.all_methods.remove(name)
            if name in self.methods:
                self.methods.remove(name)
            self.method_rows.pop(name, None)

        self._rebuild_method_source_maps()
        self._rebuild_method_panel()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self._mark_workspace_dirty()
        self.status.showMessage(f"已移除方法: {', '.join(ordered_removal)}")

    def open_errormap_dialog(self) -> None:
        candidates = [m for m in self.all_methods if self._is_source_method(m)]
        if len(candidates) < 2:
            QMessageBox.warning(self, "警告", "至少需要两个可读取源图的方法才能生成差分方法")
            return
        dialog = ErrormapDialog(candidates, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if result is None:
            return
        method_a, method_b = result
        name = self._make_unique_session_method_name(f"{method_a}_vs_{method_b}_errormap")
        self.method_entries[name] = self._build_errormap_method_entry(method_a, method_b)
        self.all_methods.append(name)
        self.method_ui_defaults[name] = {"selected": True, "offset": 0}
        self._rebuild_method_source_maps()
        self._rebuild_method_panel()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self._mark_workspace_dirty()
        self.status.showMessage(f"已添加差分方法: {name}")

    # ------------------------------------------------------------------ scanning
    def select_input_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择方法文件夹或包含方法文件夹的根目录")
        if not folder:
            return
        self.input_folder = folder
        if folder not in self.input_folders:
            self.input_folders.append(folder)
            self.input_sources.append({"type": "local", "path": folder})
        self.scan_methods_and_frames()

    def open_remote_input_dialog(self) -> None:
        if not self.backend.is_remote_available:
            QMessageBox.warning(self, "警告", "未安装 paramiko，暂时无法使用远程 SFTP 输入")
            return
        presets = self._get_server_presets()
        if not presets:
            QMessageBox.warning(self, "警告", "config 中没有可用的服务器预设")
            return
        dialog = RemoteSourceDialog("添加远程 SFTP 输入", "远程路径", "添加", presets, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if result is None:
            return
        self.input_sources.append(result)
        self.scan_methods_and_frames()

    def _get_server_presets(self) -> list[dict]:
        presets: list[dict] = []
        for key, preset in self.config.server_presets.items():
            presets.append(
                {
                    "key": key,
                    "label": str(preset.get("label", key)),
                    "host": str(preset.get("host", "")),
                    "port": str(preset.get("port", 22)),
                    "username": str(preset.get("username", "")),
                    "password": str(preset.get("password", "")),
                }
            )
        return presets

    def clear_input_folders(self) -> None:
        if not self.is_restoring_workspace and not self._prompt_save_workspace_if_dirty("清空输入"):
            return
        self._clear_workspace_state(clear_output=False, preserve_workspace_path=True)
        self._mark_workspace_dirty()
        self.status.showMessage("已清空输入文件夹")

    def select_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出文件夹")
        if not folder:
            return
        self.output_folder = folder
        self.output_target = {"type": "local", "path": folder}
        self._update_output_label()
        self._mark_workspace_dirty()

    def open_remote_output_dialog(self) -> None:
        if not self.backend.is_remote_available:
            QMessageBox.warning(self, "警告", "未安装 paramiko，暂时无法使用远程 SFTP 输出")
            return
        presets = self._get_server_presets()
        if not presets:
            QMessageBox.warning(self, "警告", "config 中没有可用的服务器预设")
            return
        dialog = RemoteSourceDialog("设置远程 SFTP 输出", "远程输出路径", "确定", presets, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if result is None:
            return
        try:
            sftp = self.backend.storage.get_sftp_client(result)
            self.backend.storage.ensure_remote_dir(sftp, result["path"])
        except Exception as exc:
            QMessageBox.critical(self, "错误", f"远程输出路径不可用: {exc}")
            return
        self.output_target = result
        self.output_folder = result["path"]
        self._update_output_label()
        self._mark_workspace_dirty()

    def _update_output_label(self) -> None:
        if self.output_target:
            if self.output_target.get("type") == "local":
                text = os.path.basename(self.output_target.get("path", "")) or str(self.output_target.get("path", ""))
            else:
                server_name = self.output_target.get("server_label", self.output_target.get("host", ""))
                text = f"{server_name}:{self.output_target.get('path', '')}"
            self.output_label.setText(text)
            self.output_label.setStyleSheet("color: black;")
        else:
            self.output_label.setText("未选择文件夹")
            self.output_label.setStyleSheet("color: gray;")

    def _has_output_target(self) -> bool:
        return self.backend.crop.has_output_target(self.output_target)

    def _get_output_display_name(self) -> str:
        return self.backend.crop.get_output_display_name(self.output_target)

    def scan_methods_and_frames(self) -> None:
        if not self.input_sources:
            return
        result = self.backend.scan.scan(self.input_sources)
        self.last_scan_errors = result.errors

        if not result.methods:
            message = "未找到符合当前输入模板的本地/远程方法路径"
            message += f"\n\n当前模板: {self._get_input_pattern_summary()}"
            if self.last_scan_errors:
                message += "\n\n连接错误:\n" + "\n".join(self.last_scan_errors[:3])
            QMessageBox.warning(self, "警告", message)
            return

        self.scanned_methods = list(result.methods)
        self.method_entries = {
            method: self._build_source_method_entry(result.method_sources[method], result.method_paths[method], origin="scan")
            for method in result.methods
        }
        self.all_methods = list(result.methods)
        self.methods = list(result.methods)
        self._rebuild_method_source_maps()
        self.method_ui_defaults = {}
        self.methods_with_frames = result.methods_with_frames
        self.frame_numbers = result.frame_numbers

        self._rebuild_method_panel()
        self.refresh_visible_methods(reload_frame=False)

        if not self.frame_numbers:
            QMessageBox.warning(
                self,
                "警告",
                f"在已选择的文件夹中都未找到符合当前模板的文件\n"
                f"当前模板: {self._get_input_pattern_summary()}\n"
                f"已扫描的方法: {', '.join(self.methods)}",
            )
            return

        self.input_label.setText(f"已打开 {len(self.input_sources)} 个输入")
        self.input_label.setStyleSheet("color: black;")
        self.status.showMessage(
            f"方法列表 {len(self.all_methods)} 个，当前显示 {len(self.methods)} 个，"
            f"{len(self.frame_numbers)} 帧 (有帧的方法: {', '.join(self.methods_with_frames)})"
        )
        self.current_frame_index = 0
        self.load_current_frame()
        self._mark_workspace_dirty()

    def _get_input_pattern_summary(self) -> str:
        return "、".join(self.config.input_filename_patterns)

    # ------------------------------------------------------------------ preview grid
    def _clear_preview_cells(self) -> None:
        for method, cell in list(self.preview_cells.items()):
            cell.setParent(None)
            cell.deleteLater()
        self.preview_cells.clear()
        for img in self.method_images.values():
            try:
                img.close()
            except Exception:
                pass
        self.method_images.clear()
        while self.preview_layout.count() > 0:
            item = self.preview_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def load_current_frame(self) -> None:
        if not self.frame_numbers:
            return
        frame_num = self.frame_numbers[self.current_frame_index]
        self._clear_preview_cells()
        gc.collect()

        bookmark_mark = " ★" if frame_num in self.bookmarked_frames else ""
        self.frame_info_label.setText(
            f"帧: {self.current_frame_index + 1} / {len(self.frame_numbers)} (帧号: {frame_num}){bookmark_mark}"
        )
        self._update_bookmark_controls()
        self._restore_crop_boxes_for_frame(frame_num)

        if not self.methods:
            placeholder = QLabel("当前没有选中的方法，请在右侧勾选要显示的子文件夹")
            placeholder.setStyleSheet("color: #bbbbbb; padding: 20px;")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview_layout.addWidget(placeholder)
            self.preview_host.update()
            return

        view_size = self.method_view_size
        for method in self.methods:
            title = self._get_method_title(method, frame_num)
            cell = PreviewCell(method, title, view_size, self.preview_host)
            cell.canvas.crop_started.connect(self._on_crop_started)
            cell.canvas.crop_dragged.connect(self._on_crop_dragged)
            cell.canvas.crop_released.connect(self._on_crop_released)
            cell.canvas.pan_started.connect(self._on_pan_started)
            cell.canvas.pan_moved.connect(self._on_pan_moved)
            cell.canvas.pan_ended.connect(self._on_pan_ended)
            cell.canvas.zoom_requested.connect(self._on_zoom_requested)
            self.preview_layout.addWidget(cell)
            self.preview_cells[method] = cell

            try:
                img = self.load_method_frame_image(method, frame_num)
                self.method_images[method] = img
                cell.canvas.set_pil_image(img)
            except Exception as exc:
                cell.canvas.set_pil_image(None, error=f"加载失败: {exc}")

        self._sync_view_state()
        self._redraw_all_crop_boxes()
        self.preview_host.update()

    def _sync_view_state(self) -> None:
        for cell in self.preview_cells.values():
            cell.canvas.set_view_state(self.zoom_level, self.pan_offset_x, self.pan_offset_y)

    def _redraw_all_crop_boxes(self) -> None:
        current_box = None
        if self.crop_start_x is not None and self.crop_end_x is not None:
            current_box = (
                min(self.crop_start_x, self.crop_end_x),
                min(self.crop_start_y, self.crop_end_y),
                max(self.crop_start_x, self.crop_end_x),
                max(self.crop_start_y, self.crop_end_y),
            )
        for cell in self.preview_cells.values():
            cell.canvas.set_crop_state(self.crop_boxes, BOX_COLORS, current_box)
        self._update_crop_info_labels()

    def _update_crop_info_labels(self) -> None:
        if self.crop_start_x is not None and self.crop_end_x is not None:
            width = abs(self.crop_end_x - self.crop_start_x)
            height = abs(self.crop_end_y - self.crop_start_y)
            self.coord_label.setText(f"({self.crop_start_x}, {self.crop_start_y})")
            self.size_label.setText(f"{width} × {height}")
            self.end_coord_label.setText(f"({self.crop_end_x}, {self.crop_end_y})")
            self.x_edit.setText(str(self.crop_start_x))
            self.y_edit.setText(str(self.crop_start_y))
            self.w_edit.setText(str(width))
            self.h_edit.setText(str(height))
        else:
            self.coord_label.setText("(-, -)")
            self.size_label.setText("- × -")
            self.end_coord_label.setText("(-, -)")
            self.x_edit.clear()
            self.y_edit.clear()
            self.w_edit.clear()
            self.h_edit.clear()

    # ------------------------------------------------------------------ preview interactions
    def _on_crop_started(self, method: str, x: int, y: int) -> None:
        self.crop_start_x = x
        self.crop_start_y = y
        self.crop_end_x = x
        self.crop_end_y = y
        self._redraw_all_crop_boxes()

    def _on_crop_dragged(self, method: str, x: int, y: int, shift: bool) -> None:
        if self.crop_start_x is None:
            return
        if shift:
            width = abs(x - self.crop_start_x)
            height = abs(y - self.crop_start_y)
            size = max(width, height)
            self.crop_end_x = self.crop_start_x + size if x >= self.crop_start_x else self.crop_start_x - size
            self.crop_end_y = self.crop_start_y + size if y >= self.crop_start_y else self.crop_start_y - size
        else:
            self.crop_end_x = x
            self.crop_end_y = y
        self._redraw_all_crop_boxes()

    def _on_crop_released(self, method: str) -> None:
        if self.crop_start_x is None or self.crop_end_x is None:
            return
        x1 = min(self.crop_start_x, self.crop_end_x)
        y1 = min(self.crop_start_y, self.crop_end_y)
        x2 = max(self.crop_start_x, self.crop_end_x)
        y2 = max(self.crop_start_y, self.crop_end_y)
        self.crop_start_x, self.crop_start_y = x1, y1
        self.crop_end_x, self.crop_end_y = x2, y2
        self._redraw_all_crop_boxes()
        self._update_preview_label()

    def _on_pan_started(self) -> None:
        pass

    def _on_pan_moved(self, dx: int, dy: int) -> None:
        self.pan_offset_x += dx
        self.pan_offset_y += dy
        self._sync_view_state()

    def _on_pan_ended(self) -> None:
        pass

    def _on_zoom_requested(self, sign: int) -> None:
        if not self.method_images:
            return
        factor = 1.1 if sign > 0 else 0.9
        new_zoom = self.zoom_level * factor
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        if abs(new_zoom - self.zoom_level) < 1e-6:
            return
        self.zoom_level = new_zoom
        self._sync_view_state()
        self.status.showMessage(f"缩放: {self.zoom_level:.2f}x")

    def reset_zoom(self) -> None:
        self.zoom_level = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        self._sync_view_state()
        self.status.showMessage("缩放已重置到1:1")

    def _on_view_size_changed(self, value: int) -> None:
        step = 20
        snapped = max(self.method_view_size_min, min(self.method_view_size_max, round(value / step) * step))
        self.view_size_value_label.setText(f"{snapped}px")
        if self._resize_job is not None:
            self._resize_job.stop()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda v=snapped: self._apply_view_size(v))
        timer.start(120)
        self._resize_job = timer

    def _apply_view_size(self, size_value: int) -> None:
        self._resize_job = None
        size_value = max(self.method_view_size_min, min(self.method_view_size_max, size_value))
        if size_value == self.method_view_size:
            return
        self.method_view_size = size_value
        if self.frame_numbers:
            self.load_current_frame()
        self._mark_workspace_dirty()
        self.status.showMessage(f"预览大小已调整为 {self.method_view_size}px")

    # ------------------------------------------------------------------ crop boxes
    def apply_manual_coords(self) -> None:
        if not self.method_images:
            QMessageBox.warning(self, "警告", "请先加载图片")
            return
        try:
            x = int(self.x_edit.text())
            y = int(self.y_edit.text())
            w = int(self.w_edit.text())
            h = int(self.h_edit.text())
        except ValueError:
            QMessageBox.critical(self, "错误", "请输入有效的数字")
            return
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            QMessageBox.warning(self, "警告", "坐标和尺寸必须为正数")
            return
        first_method = next((m for m in self.methods if m in self.method_images), None)
        if first_method is None:
            QMessageBox.warning(self, "警告", "当前没有可用图片")
            return
        img = self.method_images[first_method]
        if x + w > img.width or y + h > img.height:
            QMessageBox.warning(self, "警告", "裁剪区域超出图片范围")
            return
        self.crop_start_x = x
        self.crop_start_y = y
        self.crop_end_x = x + w
        self.crop_end_y = y + h
        self._redraw_all_crop_boxes()
        self._update_preview_label()

    def add_crop_box(self) -> None:
        if self.crop_start_x is None:
            QMessageBox.warning(self, "警告", "请先绘制一个裁剪框")
            return
        x1 = min(self.crop_start_x, self.crop_end_x)
        y1 = min(self.crop_start_y, self.crop_end_y)
        x2 = max(self.crop_start_x, self.crop_end_x)
        y2 = max(self.crop_start_y, self.crop_end_y)
        if x2 - x1 < 1 or y2 - y1 < 1:
            QMessageBox.warning(self, "警告", "裁剪框太小")
            return
        self.crop_boxes.append((x1, y1, x2, y2))
        self._sync_current_frame_crop_boxes()
        self._rebuild_crop_boxes_list()

        self._clear_active_crop_selection(update_view=False)
        self._redraw_all_crop_boxes()
        self._mark_workspace_dirty()
        self.status.showMessage(f"已添加裁剪框 {len(self.crop_boxes)}")

    def remove_selected_box(self, _item=None) -> None:
        row = self.boxes_list.currentRow()
        if row < 0 or row >= len(self.crop_boxes):
            return
        self.crop_boxes.pop(row)
        self._sync_current_frame_crop_boxes()
        self._rebuild_crop_boxes_list()
        self._redraw_all_crop_boxes()
        self._mark_workspace_dirty()
        self.status.showMessage("已删除裁剪框")

    def clear_all_boxes(self) -> None:
        if not self.crop_boxes and self.crop_start_x is None:
            return
        self.crop_boxes.clear()
        self._sync_current_frame_crop_boxes()
        self._clear_active_crop_selection(update_view=False)
        self._rebuild_crop_boxes_list()
        self._redraw_all_crop_boxes()
        self._mark_workspace_dirty()
        self.status.showMessage("已清空所有裁剪框")

    def _select_crop_box_by_row(self, row: int) -> None:
        if row < 0 or row >= len(self.crop_boxes):
            return
        x1, y1, x2, y2 = self.crop_boxes[row]
        self.crop_start_x = x1
        self.crop_start_y = y1
        self.crop_end_x = x2
        self.crop_end_y = y2
        self._redraw_all_crop_boxes()
        self._update_preview_label()

    def _rebuild_crop_boxes_list(self) -> None:
        self.boxes_list.clear()
        for idx, (x1, y1, x2, y2) in enumerate(self.crop_boxes, 1):
            self.boxes_list.addItem(f"框{idx}: ({x1}, {y1}) -> ({x2}, {y2}) [{x2-x1}×{y2-y1}]")

    def _update_preview_label(self) -> None:
        if not self.method_images or self.crop_start_x is None:
            return
        first_method = next((m for m in self.methods if m in self.method_images), None)
        if first_method is None:
            return
        img = self.method_images[first_method]
        x1 = min(self.crop_start_x, self.crop_end_x)
        y1 = min(self.crop_start_y, self.crop_end_y)
        x2 = max(self.crop_start_x, self.crop_end_x)
        y2 = max(self.crop_start_y, self.crop_end_y)
        if x2 - x1 < 5 or y2 - y1 < 5:
            return
        cropped = img.crop((x1, y1, x2, y2))
        cropped.thumbnail((260, 200), Image.Resampling.LANCZOS)
        qimg = pil_to_qimage(cropped)
        pix = QPixmap.fromImage(qimg)
        self.preview_label.setPixmap(pix)
        cropped.close()

    # ------------------------------------------------------------------ frame navigation
    def prev_frame(self) -> None:
        if not self.frame_numbers:
            return
        target = (self.current_frame_index - 1) % len(self.frame_numbers)
        self._set_current_frame_by_num(self.frame_numbers[target])

    def next_frame(self) -> None:
        if not self.frame_numbers:
            return
        target = (self.current_frame_index + 1) % len(self.frame_numbers)
        self._set_current_frame_by_num(self.frame_numbers[target])

    def jump_to_frame(self) -> None:
        text = self.frame_jump_edit.text().strip()
        if not text:
            return
        try:
            frame_num = text.zfill(4)
        except Exception:
            QMessageBox.critical(self, "错误", "无效的帧号")
            return
        if frame_num not in self.frame_numbers:
            QMessageBox.warning(self, "警告", f"帧号 {frame_num} 不存在")
            return
        self._set_current_frame_by_num(frame_num)

    def _set_current_frame_by_num(self, frame_num: str, clear_boxes: bool = True) -> bool:
        if frame_num not in self.frame_numbers:
            return False
        self._sync_current_frame_crop_boxes()
        self.current_frame_index = self.frame_numbers.index(frame_num)
        self.load_current_frame()
        self._mark_workspace_dirty()
        return True

    # ------------------------------------------------------------------ bookmarks
    def _get_sorted_bookmarks(self) -> list[str]:
        order = {frame: index for index, frame in enumerate(self.frame_numbers)}
        return sorted(self.bookmarked_frames, key=lambda frame: order.get(frame, len(order)))

    def _update_bookmark_controls(self) -> None:
        current = self._get_current_frame_num()
        is_bookmarked = current in self.bookmarked_frames if current else False
        self.bookmark_toggle_button.setText("取消收藏" if is_bookmarked else "收藏当前帧")
        self.bookmark_values = self._get_sorted_bookmarks()
        self.bookmark_combo.blockSignals(True)
        self.bookmark_combo.clear()
        self.bookmark_combo.addItems(self.bookmark_values)
        if current in self.bookmarked_frames:
            self.bookmark_combo.setCurrentText(current)
        elif self.bookmark_values:
            self.bookmark_combo.setCurrentIndex(0)
        self.bookmark_combo.blockSignals(False)

    def toggle_current_bookmark(self) -> None:
        current = self._get_current_frame_num()
        if current is None:
            return
        if current in self.bookmarked_frames:
            self.bookmarked_frames.remove(current)
            self.status.showMessage(f"已取消收藏帧 {current}")
        else:
            self.bookmarked_frames.add(current)
            self.status.showMessage(f"已收藏帧 {current}")
        self._mark_workspace_dirty()
        self._update_bookmark_controls()

    def jump_to_bookmark(self) -> None:
        frame = self.bookmark_combo.currentText().strip()
        if not frame:
            return
        if not self._set_current_frame_by_num(frame):
            QMessageBox.warning(self, "警告", f"书签帧 {frame} 不存在")

    def jump_relative_bookmark(self, direction: int) -> None:
        current = self._get_current_frame_num()
        if current is None:
            return
        bookmarks = self._get_sorted_bookmarks()
        if not bookmarks:
            QMessageBox.information(self, "提示", "当前没有收藏的帧")
            return
        if current in bookmarks:
            index = bookmarks.index(current)
            target = (index + direction) % len(bookmarks)
        else:
            target = 0 if direction > 0 else len(bookmarks) - 1
        self._set_current_frame_by_num(bookmarks[target])

    # ------------------------------------------------------------------ settings
    def open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self.config.input_filename_patterns, self.config.max_zoom, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result = dialog.get_result()
        if result is None:
            return
        patterns = result["patterns"]
        max_zoom = result["max_zoom"]
        self.config.save_input_filename_patterns(patterns)
        self.config.save_max_zoom(max_zoom)
        self.backend.update_input_filename_patterns(patterns)
        self.max_zoom = self.config.max_zoom
        if self.zoom_level > self.max_zoom:
            self.zoom_level = self.max_zoom
            self._sync_view_state()
            self._redraw_all_crop_boxes()
        self.status.showMessage(f"输入模板和最大放大倍率已更新，当前上限 {self.max_zoom:g}x")
        if self.input_sources:
            self.scan_methods_and_frames()

    # ------------------------------------------------------------------ crop actions
    def crop_current_frame(self) -> None:
        if not self.crop_boxes:
            QMessageBox.warning(self, "警告", "请先添加至少一个裁剪框")
            return
        if not self._has_output_target():
            QMessageBox.warning(self, "警告", "请先选择输出文件夹")
            return
        frame_num = self.frame_numbers[self.current_frame_index]
        success_count, collage_data = self.backend.crop.crop_loaded_images(
            frame_num,
            self.methods,
            self.method_images,
            self.crop_boxes,
            self.output_target,
            BOX_COLORS,
        )
        QMessageBox.information(
            self,
            "完成",
            f"当前帧批量裁剪完成！\n成功: {success_count} 个方法\n每个方法裁剪了 {len(self.crop_boxes)} 个区域\n输出位置: {self._get_output_display_name()}",
        )
        self.status.showMessage(f"完成！成功处理 {success_count} 个方法")
        if collage_data:
            self.backend.crop.save_current_frame_collage(frame_num, collage_data, self.output_target, self.crop_boxes)
            for item in collage_data:
                item["full"].close()
                for crop_img in item["crops"]:
                    crop_img.close()

    def batch_crop_all(self) -> None:
        self._sync_current_frame_crop_boxes()
        frame_boxes_map = {
            frame_num: self._get_crop_boxes_for_frame(frame_num)
            for frame_num in self.frame_numbers
        }
        frame_boxes_map = {frame_num: boxes for frame_num, boxes in frame_boxes_map.items() if boxes}
        if not frame_boxes_map:
            QMessageBox.warning(self, "警告", "请先至少为一帧添加一个裁剪框")
            return
        if not self._has_output_target():
            QMessageBox.warning(self, "警告", "请先选择输出文件夹")
            return
        total_images = len(self.methods) * len(frame_boxes_map)
        total_crop_outputs = len(self.methods) * sum(len(boxes) for boxes in frame_boxes_map.values())
        if total_images == 0:
            return
        confirm = QMessageBox.question(
            self,
            "确认批量裁剪",
            f"即将处理:\n- {len(self.methods)} 个方法\n- {len(frame_boxes_map)} 帧（仅包含有裁剪框的帧）\n"
            f"- 共 {total_images} 张图片\n- 总共生成 {total_crop_outputs} 个裁剪图\n"
            f"输出到: {self._get_output_display_name()}\n\n确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        progress = QProgressDialog("准备中...", "取消", 0, total_images, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        success_count = 0
        fail_count = 0
        total = 0
        cancelled = False

        for method in self.methods:
            output_method_folder = self.backend.storage.join_path(
                self.output_target, str(self.output_target["path"]), method
            )
            for frame_num, frame_boxes in frame_boxes_map.items():
                if progress.wasCanceled():
                    cancelled = True
                    break
                total += 1
                progress.setValue(total - 1)
                progress.setLabelText(f"处理中... {total}/{total_images} ({method} - 帧{frame_num})")
                QApplication.processEvents()

                img = None
                try:
                    img = self.load_method_frame_image(method, frame_num)
                    for idx, (x1, y1, x2, y2) in enumerate(frame_boxes, 1):
                        if x2 > img.width or y2 > img.height:
                            continue
                        cropped = img.crop((x1, y1, x2, y2))
                        output_path = self.backend.storage.join_path(
                            self.output_target, output_method_folder, f"frame{frame_num}_box{idx}.png"
                        )
                        self.backend.crop.save_output_image(cropped, output_path, self.output_target)
                        cropped.close()
                    self.backend.crop.save_visualization_map(
                        img, output_method_folder, frame_num, self.output_target, frame_boxes, BOX_COLORS
                    )
                    success_count += 1
                except Exception as exc:
                    print(f"处理 {method} 帧 {frame_num} 失败: {exc}")
                    fail_count += 1
                finally:
                    if img is not None:
                        img.close()
                    if total % 10 == 0:
                        gc.collect()
            if cancelled:
                break

        progress.setValue(total_images)
        if cancelled:
            QMessageBox.information(self, "已取消", f"已取消\n成功: {success_count}, 失败: {fail_count}")
        else:
            QMessageBox.information(
                self,
                "完成",
                f"批量裁剪完成！\n成功: {success_count} 张\n失败: {fail_count} 张\n输出位置: {self._get_output_display_name()}",
            )
        self.status.showMessage(f"完成！成功 {success_count} 张，失败 {fail_count} 张")

    # ------------------------------------------------------------------ workspace serialization
    def _serialize_method_entry(self, entry: dict) -> dict:
        if entry.get("type") == "errormap":
            return {
                "type": "errormap",
                "origin": entry.get("origin", "errormap"),
                "parents": list(entry.get("parents", ())),
            }
        return {
            "type": "source",
            "origin": entry.get("origin", "scan"),
            "path": str(entry.get("path", "")),
            "source": self._copy_mapping(entry.get("source", {})),
        }

    def _get_workspace_methods_state(self) -> list[dict]:
        states: list[dict] = []
        for method in self.all_methods:
            entry = self._get_method_entry(method)
            if not entry:
                continue
            row = self.method_rows.get(method)
            selected = row.is_selected() if row is not None else (method in self.methods)
            offset = row.get_offset() if row is not None else 0
            states.append(
                {
                    "name": method,
                    "entry": self._serialize_method_entry(entry),
                    "selected": bool(selected),
                    "offset": int(offset),
                }
            )
        return states

    def _build_workspace_data(self) -> dict:
        self._sync_current_frame_crop_boxes()
        return {
            "workspace": {
                "version": 1,
                "input_sources": [self._copy_mapping(src) for src in self.input_sources],
                "output_target": self._copy_mapping(self.output_target),
                "current_frame": self._get_current_frame_num(),
                "methods": self._get_workspace_methods_state(),
                "crop_boxes": [list(box) for box in self.crop_boxes],
                "frame_crop_boxes": {
                    frame: [list(box) for box in boxes]
                    for frame, boxes in self.frame_crop_boxes.items()
                    if boxes
                },
                "bookmarked_frames": list(self.bookmarked_frames),
                "method_view_size": int(self.method_view_size),
                "zoom_level": float(self.zoom_level),
                "pan_offset_x": int(self.pan_offset_x),
                "pan_offset_y": int(self.pan_offset_y),
            }
        }

    def _normalize_workspace_data(self, data: Any) -> dict:
        workspace = data.get("workspace", {}) if isinstance(data, dict) else {}
        if not isinstance(workspace, dict):
            workspace = {}
        version = workspace.get("version", 1)
        if version != 1:
            raise ValueError(f"不支持的工程文件版本: {version}")
        return workspace

    def save_workspace(self) -> bool:
        if not self.workspace_file_path:
            return self.save_workspace_as()
        return self._save_workspace_to_path(self.workspace_file_path)

    def save_workspace_as(self) -> bool:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存工程文件",
            "",
            "PickPix 工程 (*.pickpix-workspace.yaml);;YAML 文件 (*.yaml);;所有文件 (*.*)",
        )
        if not file_path:
            return False
        return self._save_workspace_to_path(file_path)

    def _save_workspace_to_path(self, file_path: str) -> bool:
        try:
            self.config.save_yaml_file(file_path, self._build_workspace_data())
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"无法保存工程文件:\n{exc}")
            return False
        self._set_workspace_file_path(file_path)
        self._mark_workspace_clean()
        self.status.showMessage(f"工程已保存: {os.path.basename(file_path)}")
        return True

    def load_workspace(self) -> bool:
        if not self._prompt_save_workspace_if_dirty("导入其他工程"):
            return False
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入工程文件",
            "",
            "PickPix 工程 (*.pickpix-workspace.yaml);;YAML 文件 (*.yaml);;所有文件 (*.*)",
        )
        if not file_path:
            return False
        return self._load_workspace_from_path(file_path)

    def _load_workspace_from_path(self, file_path: str) -> bool:
        try:
            data = self.config.load_yaml_file(file_path)
            workspace = self._normalize_workspace_data(data)
            return self._apply_workspace_data(workspace, file_path)
        except Exception as exc:
            QMessageBox.critical(self, "导入失败", f"无法导入工程文件:\n{exc}")
            return False

    def _apply_workspace_data(self, workspace: dict, file_path: str) -> bool:
        warnings: list[str] = []
        saved_methods = workspace.get("methods", [])
        if not isinstance(saved_methods, list):
            saved_methods = []

        self.is_restoring_workspace = True
        try:
            self._clear_workspace_state(clear_output=True, preserve_workspace_path=False)

            self.input_sources = [self._copy_mapping(src) for src in workspace.get("input_sources", []) if isinstance(src, dict)]
            self.input_folders = [str(src.get("path", "")) for src in self.input_sources if src.get("type") == "local"]
            self.input_folder = self.input_folders[0] if self.input_folders else ""

            output_target = workspace.get("output_target", {})
            if isinstance(output_target, dict) and output_target:
                self.output_target = self._copy_mapping(output_target)
                self.output_folder = str(self.output_target.get("path", self.output_folder))
            else:
                self.output_target = None
                self.output_folder = str(self.config.default_output_dir)
            self._update_output_label()

            view_size = int(workspace.get("method_view_size", self.method_view_size))
            view_size = max(self.method_view_size_min, min(self.method_view_size_max, view_size))
            self.method_view_size = view_size
            self.view_size_slider.blockSignals(True)
            self.view_size_slider.setValue(view_size)
            self.view_size_slider.blockSignals(False)
            self.view_size_value_label.setText(f"{view_size}px")

            self.zoom_level = max(self.min_zoom, min(self.max_zoom, float(workspace.get("zoom_level", 1.0))))
            self.pan_offset_x = int(workspace.get("pan_offset_x", 0))
            self.pan_offset_y = int(workspace.get("pan_offset_y", 0))

            restored_frame_crop_boxes: dict[str, list[tuple[int, int, int, int]]] = {}
            raw_frame_crop_boxes = workspace.get("frame_crop_boxes", {})
            if isinstance(raw_frame_crop_boxes, dict):
                for frame_num, boxes in raw_frame_crop_boxes.items():
                    normalized: list[tuple[int, int, int, int]] = []
                    if isinstance(boxes, list):
                        for box in boxes:
                            if isinstance(box, (list, tuple)) and len(box) == 4:
                                normalized.append(tuple(int(v) for v in box))
                    if normalized:
                        restored_frame_crop_boxes[str(frame_num)] = normalized
            self.frame_crop_boxes = restored_frame_crop_boxes

            if self.input_sources:
                result = self.backend.scan.scan(self.input_sources)
                self.last_scan_errors = result.errors
                self.scanned_methods = list(result.methods)

                scanned_entry_map = {
                    m: self._build_source_method_entry(result.method_sources[m], result.method_paths[m], origin="scan")
                    for m in result.methods
                }

                self.method_entries = {}
                self.all_methods = []
                saved_map: dict[str, dict] = {}

                for item in saved_methods:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    entry_data = item.get("entry", {})
                    if not name or not isinstance(entry_data, dict):
                        continue
                    saved_map[name] = item
                    etype = entry_data.get("type", "source")
                    origin = str(entry_data.get("origin", "scan"))
                    if etype == "source" and origin == "scan":
                        if name in scanned_entry_map:
                            self.method_entries[name] = scanned_entry_map[name]
                            self.all_methods.append(name)
                        else:
                            warnings.append(f"方法 {name} 已不存在，已跳过")
                    elif etype == "source":
                        source = entry_data.get("source", {})
                        self.method_entries[name] = self._build_source_method_entry(source, entry_data.get("path", ""), origin=origin)
                        self.all_methods.append(name)
                    elif etype == "errormap":
                        parents = entry_data.get("parents", [])
                        if not isinstance(parents, list) or len(parents) != 2:
                            warnings.append(f"差分方法 {name} 缺少有效来源，已跳过")
                            continue
                        self.method_entries[name] = self._build_errormap_method_entry(str(parents[0]), str(parents[1]), origin=origin)
                        self.all_methods.append(name)

                if not self.all_methods:
                    self.method_entries = dict(scanned_entry_map)
                    self.all_methods = list(result.methods)
                    saved_map = {}

                valid_names = set(self.method_entries.keys())
                for name in list(self.all_methods):
                    entry = self.method_entries.get(name)
                    if not entry or entry.get("type") != "errormap":
                        continue
                    parents = entry.get("parents", ())
                    if any(parent not in valid_names for parent in parents):
                        self.all_methods.remove(name)
                        self.method_entries.pop(name, None)
                        warnings.append(f"差分方法 {name} 的来源方法缺失，已跳过")

                self.methods = list(self.all_methods)
                self._rebuild_method_source_maps()
                self.methods_with_frames = result.methods_with_frames
                self.frame_numbers = result.frame_numbers
                self.method_ui_defaults = {}
                for name in self.all_methods:
                    state = saved_map.get(name, {})
                    self.method_ui_defaults[name] = {
                        "selected": bool(state.get("selected", True)),
                        "offset": int(state.get("offset", 0) or 0),
                    }

                self._rebuild_method_panel()
                self.refresh_visible_methods(reload_frame=False)

                self.input_label.setText(f"已打开 {len(self.input_sources)} 个输入")
                self.input_label.setStyleSheet("color: black;")
                current_frame = str(workspace.get("current_frame", "")).strip()
                if self.frame_numbers:
                    if current_frame and current_frame in self.frame_numbers:
                        self.current_frame_index = self.frame_numbers.index(current_frame)
                    else:
                        self.current_frame_index = 0
                        if current_frame:
                            warnings.append(f"保存的帧 {current_frame} 不存在，已回退到首帧")
                    self.load_current_frame()
                else:
                    self.frame_info_label.setText("帧: 0 / 0")
            else:
                self.input_label.setText("未选择文件夹")
                self.input_label.setStyleSheet("color: gray;")

            if not self.frame_crop_boxes:
                crop_boxes: list[tuple[int, int, int, int]] = []
                for box in workspace.get("crop_boxes", []):
                    if isinstance(box, (list, tuple)) and len(box) == 4:
                        crop_boxes.append(tuple(int(v) for v in box))
                current_frame = self._get_current_frame_num()
                if current_frame and crop_boxes:
                    self.frame_crop_boxes[current_frame] = crop_boxes

            self._restore_crop_boxes_for_frame(self._get_current_frame_num())
            self._redraw_all_crop_boxes()

            restored_bookmarks: set[str] = set()
            for frame in workspace.get("bookmarked_frames", []):
                text = str(frame).strip()
                if not text:
                    continue
                if self.frame_numbers and text not in self.frame_numbers:
                    warnings.append(f"书签帧 {text} 不存在，已跳过")
                    continue
                restored_bookmarks.add(text)
            self.bookmarked_frames = restored_bookmarks
            self._update_bookmark_controls()

            self._set_workspace_file_path(file_path)
            self._mark_workspace_clean()
            if warnings:
                QMessageBox.warning(self, "导入工程完成", "\n".join(warnings[:10]))
            self.status.showMessage(f"已导入工程: {os.path.basename(file_path)}")
            return True
        finally:
            self.is_restoring_workspace = False

    def _prompt_save_workspace_if_dirty(self, action_name: str = "继续") -> bool:
        if not self.workspace_dirty:
            return True
        result = QMessageBox.question(
            self,
            "未保存的工程",
            f"当前工程有未保存的修改，是否在{action_name}前先保存？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Cancel:
            return False
        if result == QMessageBox.StandardButton.Yes:
            return self.save_workspace()
        return True

    def _clear_workspace_state(self, clear_output: bool = True, preserve_workspace_path: bool = False) -> None:
        self.input_folder = ""
        self.input_folders = []
        self.input_sources = []
        self.methods = []
        self.all_methods = []
        self.scanned_methods = []
        self.method_entries = {}
        self.method_paths = {}
        self.method_sources = {}
        self.methods_with_frames = []
        self.method_ui_defaults = {}
        self.method_rows = {}
        self.frame_numbers = []
        self.current_frame_index = 0
        self.bookmarked_frames.clear()
        self.backend.storage.close_all_remote_connections()

        if clear_output:
            self.output_target = None
            self.output_folder = str(self.config.default_output_dir)

        for img in self.method_images.values():
            try:
                img.close()
            except Exception:
                pass
        self.method_images.clear()
        self.crop_boxes.clear()
        self.frame_crop_boxes.clear()
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        self.zoom_level = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        self._clear_preview_cells()
        self._rebuild_method_panel()
        self._rebuild_crop_boxes_list()
        self.preview_label.clear()
        self._update_bookmark_controls()
        self._update_output_label()
        self.input_label.setText("未选择文件夹")
        self.input_label.setStyleSheet("color: gray;")
        self.frame_info_label.setText("帧: 0 / 0")
        self.coord_label.setText("(-, -)")
        self.size_label.setText("- × -")
        self.end_coord_label.setText("(-, -)")

        if not preserve_workspace_path:
            self._set_workspace_file_path(None)

    # ------------------------------------------------------------------ close
    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._prompt_save_workspace_if_dirty("退出软件"):
            event.ignore()
            return
        try:
            self.backend.close()
        except Exception:
            pass
        event.accept()


def main() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    window = PickPixMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
