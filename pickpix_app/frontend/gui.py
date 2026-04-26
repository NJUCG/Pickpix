import gc
import io
import os
import posixpath
import re
# 必须在导入 cv2 之前设置环境变量
os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
import cv2
import numpy as np

from pickpix_app.backend import PickPixBackend
from pickpix_app.config import AppConfig


class MultiMethodCropperGUI:
    def __init__(self, root):
        self.root = root
        self.config = AppConfig()
        self.backend = PickPixBackend(self.config.input_filename_patterns)
        self.root.title(self.config.title)
        self.root.geometry(self.config.geometry)
        
        # 变量
        self.input_folder = ""
        self.input_folders = []
        self.input_sources = []
        self.output_folder = str(self.config.default_output_dir)
        self.output_target = None
        self.methods = []  # 子文件夹列表（方法名）
        self.all_methods = []  # 扫描得到的全部方法名
        self.scanned_methods = []
        self.method_entries = {}  # {method_name: {type, source/path or parents, origin}}
        self.method_paths = {}  # {method_name: folder_path}
        self.method_sources = {}  # {method_name: source_config}
        self.methods_with_frames = []
        self.method_filter_vars = {}
        self.method_offset_vars = {}
        self.method_ui_defaults = {}
        self.methods_summary_label = None
        self.method_filter_apply_button = None
        self.method_filter_canvas = None
        self.method_filter_content = None
        self.method_filter_canvas_window = None
        self.method_filter_scrollbar = None
        self.method_filter_pending_changes = False
        self.is_updating_method_filter_controls = False
        self.last_scan_errors = []
        self.frame_numbers = []  # 帧号列表
        self.current_frame_index = 0
        self.workspace_file_path = None
        self.workspace_dirty = False
        self.workspace_label = None
        self.is_restoring_workspace = False
        self.bookmarked_frames = set()
        self.bookmark_toggle_button = None
        self.bookmark_combo = None
        self.bookmark_values = []
        
        # 每个方法的图片数据
        self.method_images = {}  # {method_name: {frame: PIL.Image}}
        self.display_images = {}  # {method_name: display_image}
        self.photo_images = {}  # {method_name: PhotoImage}
        self.canvases = {}  # {method_name: canvas}
        self.scale_factor = 1.0
        self.method_view_size_min = 180
        self.method_view_size_max = 960
        self.method_view_size = 320
        self.method_view_size_var = tk.IntVar(value=self.method_view_size)
        self.method_view_size_value_label = None
        self.method_view_resize_job = None
        
        # 缩放级别
        self.zoom_level = 1.0  # 缩放倍数
        self.min_zoom = 0.1
        self.max_zoom = self.config.max_zoom
        
        # 平移偏移（用于拖动查看）
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        self.is_panning = False
        self.pan_start_x = 0
        self.pan_start_y = 0
        
        # 当前正在绘制的裁剪框坐标（原始图片坐标）
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        
        # 已保存的多个裁剪框列表
        self.crop_boxes = []  # [(x1, y1, x2, y2), ...]
        self.rect_ids = {}  # {method_name: [rect_id1, rect_id2, ...]}
        self.current_rect_ids = {}  # 当前正在绘制的框
        
        self.is_dragging = False
        self.active_canvas = None
        
        # 颜色列表用于区分不同裁剪框
        self.box_colors = ['#FF0000', '#00FF00', '#0000FF', '#FFFF00', '#FF00FF', '#00FFFF', '#FFA500', '#800080']
        
        self.setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def get_server_presets(self):
        presets = []
        for preset_key, preset in self.config.server_presets.items():
            presets.append(
                {
                    "key": preset_key,
                    "label": str(preset.get("label", preset_key)),
                    "host": str(preset.get("host", "")),
                    "port": str(preset.get("port", 22)),
                    "username": str(preset.get("username", "")),
                    "password": str(preset.get("password", "")),
                }
            )
        return presets

    def get_input_pattern_text(self):
        return "\n".join(self.config.input_filename_patterns)

    def get_input_pattern_summary(self):
        return "、".join(self.config.input_filename_patterns)

    def set_workspace_file_path(self, file_path):
        self.workspace_file_path = file_path
        self.update_workspace_label()

    def update_workspace_label(self):
        if self.workspace_label is None:
            return

        if self.workspace_file_path:
            file_name = os.path.basename(self.workspace_file_path)
            prefix = "* " if self.workspace_dirty else ""
            self.workspace_label.config(text=f"工程: {prefix}{self.shorten_text(file_name, 36)}", fg="black")
        else:
            prefix = "* " if self.workspace_dirty else ""
            self.workspace_label.config(text=f"工程: {prefix}未保存", fg="gray")

    def mark_workspace_dirty(self):
        if self.is_restoring_workspace:
            return
        self.workspace_dirty = True
        self.update_workspace_label()

    def mark_workspace_clean(self):
        self.workspace_dirty = False
        self.update_workspace_label()

    def copy_mapping(self, data):
        return dict(data) if isinstance(data, dict) else {}

    def get_current_frame_num(self):
        if not self.frame_numbers:
            return None
        if self.current_frame_index < 0 or self.current_frame_index >= len(self.frame_numbers):
            return None
        return self.frame_numbers[self.current_frame_index]

    def get_workspace_methods_state(self):
        method_states = []
        for method in self.all_methods:
            entry = self.get_method_entry(method)
            if not entry:
                continue
            method_states.append(
                {
                    "name": method,
                    "entry": self.serialize_method_entry(entry),
                    "selected": bool(self.method_filter_vars.get(method).get()) if method in self.method_filter_vars else method in self.methods,
                    "offset": self.get_method_frame_offset(method),
                }
            )
        return method_states

    def apply_scan_result_preserving_methods(self, result):
        previous_scanned_methods = set(self.scanned_methods)
        state_map = {
            str(item.get("name", "")).strip(): item
            for item in self.get_workspace_methods_state()
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
        scanned_entry_map = {
            method: self.build_source_method_entry(result.method_sources[method], result.method_paths[method], origin="scan")
            for method in result.methods
        }

        next_entries = {}
        next_methods = []
        next_defaults = {}

        for method in self.all_methods:
            entry = self.method_entries.get(method)
            if not entry:
                continue

            state = state_map.get(method, {})
            serialized_entry = state.get("entry") if isinstance(state, dict) else None
            if not isinstance(serialized_entry, dict):
                serialized_entry = self.serialize_method_entry(entry)

            entry_type = serialized_entry.get("type", entry.get("type", "source"))
            origin = str(serialized_entry.get("origin", entry.get("origin", "scan")))

            if entry_type == "source" and origin == "scan":
                scanned_entry = scanned_entry_map.get(method)
                if scanned_entry is None:
                    continue
                next_entries[method] = scanned_entry
            elif entry_type == "source":
                next_entries[method] = self.build_source_method_entry(entry.get("source", {}), entry.get("path", ""), origin=origin)
            elif entry_type == "errormap":
                parents = entry.get("parents", ())
                if len(parents) != 2:
                    continue
                next_entries[method] = self.build_errormap_method_entry(str(parents[0]), str(parents[1]), origin=origin)
            else:
                continue

            next_methods.append(method)
            next_defaults[method] = {
                "selected": bool(state.get("selected", method in self.methods)),
                "offset": str(state.get("offset", 0)),
            }

        for method in result.methods:
            if method in next_entries:
                continue
            if method in previous_scanned_methods:
                continue
            next_entries[method] = scanned_entry_map[method]
            next_methods.append(method)
            next_defaults[method] = {"selected": True, "offset": "0"}

        valid_names = set(next_entries.keys())
        filtered_methods = []
        for method in next_methods:
            entry = next_entries.get(method)
            if not entry:
                continue
            if entry.get("type") == "errormap":
                parents = entry.get("parents", ())
                if any(parent not in valid_names for parent in parents):
                    next_entries.pop(method, None)
                    next_defaults.pop(method, None)
                    continue
            filtered_methods.append(method)

        self.scanned_methods = list(result.methods)
        self.method_entries = next_entries
        self.all_methods = filtered_methods
        self.methods = list(filtered_methods)
        self.method_ui_defaults = next_defaults
        self.rebuild_method_source_maps()
        self.methods_with_frames = [method for method in result.methods_with_frames if method in next_entries]
        self.frame_numbers = result.frame_numbers

    def serialize_method_entry(self, entry):
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
            "source": self.copy_mapping(entry.get("source", {})),
        }

    def build_workspace_data(self):
        return {
            "workspace": {
                "version": 1,
                "input_sources": [self.copy_mapping(source) for source in self.input_sources],
                "output_target": self.copy_mapping(self.output_target),
                "current_frame": self.get_current_frame_num(),
                "methods": self.get_workspace_methods_state(),
                "crop_boxes": [list(box) for box in self.crop_boxes],
                "bookmarked_frames": list(self.bookmarked_frames),
                "method_view_size": int(self.method_view_size),
                "zoom_level": float(self.zoom_level),
                "pan_offset_x": int(self.pan_offset_x),
                "pan_offset_y": int(self.pan_offset_y),
            }
        }

    def normalize_workspace_data(self, data):
        workspace = data.get("workspace", {}) if isinstance(data, dict) else {}
        if not isinstance(workspace, dict):
            workspace = {}
        version = workspace.get("version", 1)
        if version != 1:
            raise ValueError(f"不支持的工程文件版本: {version}")
        return workspace

    def ask_workspace_save_path(self):
        return filedialog.asksaveasfilename(
            title="保存工程文件",
            defaultextension=".pickpix-workspace.yaml",
            filetypes=[("PickPix 工程", "*.pickpix-workspace.yaml"), ("YAML 文件", "*.yaml"), ("所有文件", "*.*")],
        )

    def save_workspace_to_path(self, file_path):
        try:
            self.config.save_yaml_file(file_path, self.build_workspace_data())
        except Exception as exc:
            messagebox.showerror("保存失败", f"无法保存工程文件:\n{exc}")
            return False

        self.set_workspace_file_path(file_path)
        self.mark_workspace_clean()
        self.status_label.config(text=f"工程已保存: {os.path.basename(file_path)}")
        return True

    def save_workspace(self):
        if not self.workspace_file_path:
            return self.save_workspace_as()
        return self.save_workspace_to_path(self.workspace_file_path)

    def save_workspace_as(self):
        file_path = self.ask_workspace_save_path()
        if not file_path:
            return False
        return self.save_workspace_to_path(file_path)

    def ask_workspace_open_path(self):
        return filedialog.askopenfilename(
            title="导入工程文件",
            filetypes=[("PickPix 工程", "*.pickpix-workspace.yaml"), ("YAML 文件", "*.yaml"), ("所有文件", "*.*")],
        )

    def load_workspace(self):
        if not self.prompt_save_workspace_if_dirty("导入其他工程"):
            return False
        file_path = self.ask_workspace_open_path()
        if not file_path:
            return False
        return self.load_workspace_from_path(file_path)

    def load_workspace_from_path(self, file_path):
        try:
            data = self.config.load_yaml_file(file_path)
            workspace = self.normalize_workspace_data(data)
            return self.apply_workspace_data(workspace, file_path)
        except Exception as exc:
            messagebox.showerror("导入失败", f"无法导入工程文件:\n{exc}")
            return False

    def apply_workspace_data(self, workspace, file_path):
        warnings = []
        saved_methods = workspace.get("methods", [])
        if not isinstance(saved_methods, list):
            saved_methods = []

        self.is_restoring_workspace = True
        try:
            self.clear_workspace_state(clear_output=True, preserve_workspace_path=False)

            self.input_sources = [self.copy_mapping(source) for source in workspace.get("input_sources", []) if isinstance(source, dict)]
            self.input_folders = [str(source.get("path", "")) for source in self.input_sources if source.get("type") == "local"]
            self.input_folder = self.input_folders[0] if self.input_folders else ""

            output_target = workspace.get("output_target", {})
            if isinstance(output_target, dict) and output_target:
                self.output_target = self.copy_mapping(output_target)
                self.output_folder = str(self.output_target.get("path", self.output_folder))
            else:
                self.output_target = None
                self.output_folder = str(self.config.default_output_dir)
            self.update_output_label()

            method_view_size = int(workspace.get("method_view_size", self.method_view_size))
            method_view_size = max(self.method_view_size_min, min(self.method_view_size_max, method_view_size))
            self.method_view_size = method_view_size
            self.method_view_size_var.set(method_view_size)
            self.update_method_view_size_label(method_view_size)

            self.zoom_level = float(workspace.get("zoom_level", 1.0))
            self.zoom_level = max(self.min_zoom, min(self.max_zoom, self.zoom_level))
            self.pan_offset_x = int(workspace.get("pan_offset_x", 0))
            self.pan_offset_y = int(workspace.get("pan_offset_y", 0))

            if self.input_sources:
                result = self.backend.scan.scan(self.input_sources)
                self.last_scan_errors = result.errors
                self.scanned_methods = list(result.methods)
                scanned_entry_map = {
                    method: self.build_source_method_entry(result.method_sources[method], result.method_paths[method], origin="scan")
                    for method in result.methods
                }

                self.method_entries = {}
                self.all_methods = []
                saved_method_state_map = {}

                for item in saved_methods:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name", "")).strip()
                    entry_data = item.get("entry", {})
                    if not name or not isinstance(entry_data, dict):
                        continue
                    saved_method_state_map[name] = item

                    entry_type = entry_data.get("type", "source")
                    origin = str(entry_data.get("origin", "scan"))
                    if entry_type == "source" and origin == "scan":
                        if name in scanned_entry_map:
                            self.method_entries[name] = scanned_entry_map[name]
                            self.all_methods.append(name)
                        else:
                            warnings.append(f"方法 {name} 已不存在，已跳过")
                    elif entry_type == "source":
                        source = entry_data.get("source", {})
                        self.method_entries[name] = self.build_source_method_entry(source, entry_data.get("path", ""), origin=origin)
                        self.all_methods.append(name)
                    elif entry_type == "errormap":
                        parents = entry_data.get("parents", [])
                        if not isinstance(parents, list) or len(parents) != 2:
                            warnings.append(f"差分方法 {name} 缺少有效来源，已跳过")
                            continue
                        self.method_entries[name] = self.build_errormap_method_entry(str(parents[0]), str(parents[1]), origin=origin)
                        self.all_methods.append(name)

                if not self.all_methods:
                    self.method_entries = dict(scanned_entry_map)
                    self.all_methods = list(result.methods)
                    saved_method_state_map = {}

                valid_method_names = set(self.method_entries.keys())
                for method in list(self.all_methods):
                    entry = self.method_entries.get(method)
                    if not entry or entry.get("type") != "errormap":
                        continue
                    parents = entry.get("parents", ())
                    if any(parent not in valid_method_names for parent in parents):
                        self.all_methods.remove(method)
                        self.method_entries.pop(method, None)
                        warnings.append(f"差分方法 {method} 的来源方法缺失，已跳过")

                self.methods = list(self.all_methods)
                self.rebuild_method_source_maps()
                self.methods_with_frames = result.methods_with_frames
                self.frame_numbers = result.frame_numbers
                self.method_ui_defaults = {}
                for method in self.all_methods:
                    state = saved_method_state_map.get(method, {})
                    self.method_ui_defaults[method] = {
                        "selected": bool(state.get("selected", True)),
                        "offset": str(state.get("offset", 0)),
                    }

                self.rebuild_method_filter_ui()
                self.refresh_visible_methods(reload_frame=False)

                self.input_label.config(text=f"已打开 {len(self.input_sources)} 个输入", fg="black")
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
                    self.frame_info_label.config(text="帧: 0 / 0")
            else:
                self.input_label.config(text="未选择文件夹", fg="gray")

            crop_boxes = []
            for box in workspace.get("crop_boxes", []):
                if isinstance(box, (list, tuple)) and len(box) == 4:
                    crop_boxes.append(tuple(int(value) for value in box))
            self.crop_boxes = crop_boxes
            self.rebuild_crop_boxes_list()
            if self.canvases:
                self.redraw_all_rectangles()

            restored_bookmarks = set()
            for frame in workspace.get("bookmarked_frames", []):
                frame_text = str(frame).strip()
                if not frame_text:
                    continue
                if self.frame_numbers and frame_text not in self.frame_numbers:
                    warnings.append(f"书签帧 {frame_text} 不存在，已跳过")
                    continue
                restored_bookmarks.add(frame_text)
            self.bookmarked_frames = restored_bookmarks
            self.update_bookmark_controls()

            self.set_workspace_file_path(file_path)
            self.mark_workspace_clean()
            if warnings:
                messagebox.showwarning("导入工程完成", "\n".join(warnings[:10]))
            self.status_label.config(text=f"已导入工程: {os.path.basename(file_path)}")
            return True
        finally:
            self.is_restoring_workspace = False

    def prompt_save_workspace_if_dirty(self, action_name="继续"): 
        if not self.workspace_dirty:
            return True

        result = messagebox.askyesnocancel(
            "未保存的工程",
            f"当前工程有未保存的修改，是否在{action_name}前先保存？",
        )
        if result is None:
            return False
        if result:
            return self.save_workspace()
        return True

    def update_output_label(self):
        if self.output_target:
            if self.output_target.get("type") == "local":
                text = os.path.basename(self.output_target.get("path", "")) or str(self.output_target.get("path", ""))
            else:
                server_name = self.output_target.get("server_label", self.output_target.get("host", ""))
                text = f"{server_name}:{self.output_target.get('path', '')}"
            self.output_label.config(text=text, fg="black")
        else:
            self.output_label.config(text="未选择文件夹", fg="gray")

    def rebuild_crop_boxes_list(self):
        self.boxes_listbox.delete(0, tk.END)
        for index, (x1, y1, x2, y2) in enumerate(self.crop_boxes, 1):
            self.boxes_listbox.insert(tk.END, f"框{index}: ({x1}, {y1}) -> ({x2}, {y2}) [{x2-x1}×{y2-y1}]")

    def get_sorted_bookmarks(self):
        frame_order = {frame: index for index, frame in enumerate(self.frame_numbers)}
        return sorted(self.bookmarked_frames, key=lambda frame: frame_order.get(frame, len(frame_order)))

    def update_bookmark_controls(self):
        current_frame = self.get_current_frame_num()
        is_bookmarked = current_frame in self.bookmarked_frames if current_frame else False

        if self.bookmark_toggle_button is not None:
            self.bookmark_toggle_button.config(text="取消收藏" if is_bookmarked else "收藏当前帧")

        self.bookmark_values = self.get_sorted_bookmarks()
        if self.bookmark_combo is not None:
            self.bookmark_combo.configure(values=self.bookmark_values)
            if current_frame in self.bookmarked_frames:
                self.bookmark_combo.set(current_frame)
            elif self.bookmark_values:
                self.bookmark_combo.set(self.bookmark_values[0])
            elif not self.bookmark_values:
                self.bookmark_combo.set("")

    def set_current_frame_by_num(self, frame_num, clear_boxes=True):
        if frame_num not in self.frame_numbers:
            return False
        if clear_boxes:
            self.clear_all_boxes()
        self.current_frame_index = self.frame_numbers.index(frame_num)
        self.load_current_frame()
        self.mark_workspace_dirty()
        return True

    def get_mousewheel_delta(self, event):
        if hasattr(event, "delta") and event.delta:
            return -1 if event.delta > 0 else 1
        if getattr(event, "num", None) in (4, 5):
            return -1 if event.num == 4 else 1
        return 0

    def widget_is_descendant(self, widget, ancestor):
        while widget is not None:
            if widget == ancestor:
                return True
            widget = getattr(widget, "master", None)
        return False

    def scroll_preview_canvas(self, event):
        delta = self.get_mousewheel_delta(event)
        if delta:
            self.scroll_canvas.yview_scroll(delta, "units")
            return "break"
        return None

    def jump_to_bookmark(self):
        frame_num = self.bookmark_combo.get().strip() if self.bookmark_combo is not None else ""
        if not frame_num:
            return
        if not self.set_current_frame_by_num(frame_num):
            messagebox.showwarning("警告", f"书签帧 {frame_num} 不存在")

    def toggle_current_bookmark(self):
        frame_num = self.get_current_frame_num()
        if frame_num is None:
            return

        if frame_num in self.bookmarked_frames:
            self.bookmarked_frames.remove(frame_num)
            self.status_label.config(text=f"已取消收藏帧 {frame_num}")
        else:
            self.bookmarked_frames.add(frame_num)
            self.status_label.config(text=f"已收藏帧 {frame_num}")

        self.mark_workspace_dirty()
        self.update_bookmark_controls()

    def jump_relative_bookmark(self, direction):
        current_frame = self.get_current_frame_num()
        if current_frame is None:
            return
        bookmarks = self.get_sorted_bookmarks()
        if not bookmarks:
            messagebox.showinfo("提示", "当前没有收藏的帧")
            return

        if current_frame in bookmarks:
            current_index = bookmarks.index(current_frame)
            target_index = (current_index + direction) % len(bookmarks)
        else:
            target_index = 0 if direction > 0 else len(bookmarks) - 1
        self.set_current_frame_by_num(bookmarks[target_index])

    def open_settings_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        content = tk.Frame(dialog, padx=12, pady=12)
        content.pack(fill=tk.BOTH, expand=True)

        tk.Label(content, text="输入文件名模板", font=("Arial", 10, "bold")).pack(anchor=tk.W)
        tk.Label(
            content,
            text="每行一个模板。使用 {number} 表示帧号，使用 * 表示任意文本。",
            justify=tk.LEFT,
            fg="gray",
        ).pack(anchor=tk.W, pady=(4, 8))

        pattern_text = tk.Text(content, width=42, height=8)
        pattern_text.pack(fill=tk.BOTH, expand=True)
        pattern_text.insert("1.0", self.get_input_pattern_text())

        tk.Label(
            content,
            text="示例: frame{number}.exr\n示例: *.{number}.exr",
            justify=tk.LEFT,
            fg="gray",
        ).pack(anchor=tk.W, pady=(8, 0))

        zoom_frame = tk.Frame(content)
        zoom_frame.pack(fill=tk.X, pady=(12, 0))
        tk.Label(zoom_frame, text="最大放大倍率", font=("Arial", 10, "bold")).pack(anchor=tk.W)
        tk.Label(
            zoom_frame,
            text="滚轮缩放上限，默认 5.0。",
            justify=tk.LEFT,
            fg="gray",
        ).pack(anchor=tk.W, pady=(4, 6))
        max_zoom_var = tk.StringVar(value=f"{self.config.max_zoom:g}")
        max_zoom_entry = tk.Entry(zoom_frame, width=12, textvariable=max_zoom_var)
        max_zoom_entry.pack(anchor=tk.W)

        def save_settings():
            patterns = [line.strip() for line in pattern_text.get("1.0", tk.END).splitlines() if line.strip()]
            if not patterns:
                messagebox.showwarning("警告", "请至少保留一个输入文件名模板", parent=dialog)
                return
            if any("{number}" not in pattern for pattern in patterns):
                messagebox.showwarning("警告", "每个模板都必须包含 {number} 占位符", parent=dialog)
                return
            try:
                max_zoom = float(max_zoom_var.get().strip())
            except ValueError:
                messagebox.showwarning("警告", "最大放大倍率必须是数字", parent=dialog)
                return
            if max_zoom < 1.0:
                messagebox.showwarning("警告", "最大放大倍率不能小于 1.0", parent=dialog)
                return

            self.config.save_input_filename_patterns(patterns)
            self.config.save_max_zoom(max_zoom)
            self.backend.update_input_filename_patterns(patterns)
            self.max_zoom = self.config.max_zoom
            if self.zoom_level > self.max_zoom:
                self.zoom_level = self.max_zoom
                for method in self.methods:
                    if method in self.method_images:
                        self.display_image_on_canvas(method)
                self.redraw_all_rectangles()
            self.status_label.config(text=f"输入模板和最大放大倍率已更新，当前上限 {self.max_zoom:g}x")

            if self.input_sources:
                self.scan_methods_and_frames()

            dialog.destroy()

        button_frame = tk.Frame(content)
        button_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.RIGHT, padx=(6, 0))
        tk.Button(button_frame, text="保存", command=save_settings, width=10).pack(side=tk.RIGHT)

    def create_remote_dialog(self, title, path_label, confirm_text, on_submit):
        presets = self.get_server_presets()
        if not presets:
            messagebox.showwarning("警告", "config 中没有可用的服务器预设")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text="服务器").grid(row=0, column=0, sticky=tk.W, padx=10, pady=6)
        preset_var = tk.StringVar(value=presets[0]["label"])
        preset_combo = ttk.Combobox(
            dialog,
            textvariable=preset_var,
            values=[preset["label"] for preset in presets],
            state="readonly",
            width=33,
        )
        preset_combo.grid(row=0, column=1, padx=10, pady=6)

        readonly_fields = [
            ("地址", "host"),
            ("端口", "port"),
            ("账号", "username"),
        ]
        readonly_vars = {}

        for row, (label, key) in enumerate(readonly_fields, start=1):
            tk.Label(dialog, text=label).grid(row=row, column=0, sticky=tk.W, padx=10, pady=6)
            var = tk.StringVar()
            entry = tk.Entry(dialog, width=36, textvariable=var, state="readonly")
            entry.grid(row=row, column=1, padx=10, pady=6)
            readonly_vars[key] = var

        tk.Label(dialog, text="密码").grid(row=4, column=0, sticky=tk.W, padx=10, pady=6)
        password_var = tk.StringVar()
        password_entry = tk.Entry(dialog, width=36, textvariable=password_var, show="*", state="readonly")
        password_entry.grid(row=4, column=1, padx=10, pady=6)

        tk.Label(dialog, text=path_label).grid(row=5, column=0, sticky=tk.W, padx=10, pady=6)
        path_entry = tk.Entry(dialog, width=36)
        path_entry.grid(row=5, column=1, padx=10, pady=6)

        def apply_preset(*_args):
            selected = next((preset for preset in presets if preset["label"] == preset_var.get()), presets[0])
            readonly_vars["host"].set(selected["host"])
            readonly_vars["port"].set(selected["port"])
            readonly_vars["username"].set(selected["username"])
            password_var.set(selected["password"])

        apply_preset()
        preset_combo.bind("<<ComboboxSelected>>", apply_preset)

        def submit():
            selected = next((preset for preset in presets if preset["label"] == preset_var.get()), presets[0])
            remote_path = path_entry.get().strip()
            if not remote_path:
                messagebox.showwarning("警告", f"请填写{path_label}", parent=dialog)
                return
            if not remote_path.startswith("/"):
                messagebox.showwarning("警告", "远程路径必须是绝对路径，并以 / 开头", parent=dialog)
                return

            remote_source = {
                "type": "sftp",
                "host": selected["host"],
                "port": int(selected["port"]),
                "username": selected["username"],
                "password": selected["password"],
                "path": remote_path,
                "server_key": selected["key"],
                "server_label": selected["label"],
            }
            on_submit(dialog, remote_source)

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=6, column=0, columnspan=2, pady=(8, 12))
        tk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=6)
        tk.Button(button_frame, text=confirm_text, command=submit, width=10).pack(side=tk.LEFT, padx=6)
    
    @staticmethod
    @staticmethod
    def load_exr_image(file_path):
        """加载EXR图片并转换为PIL Image"""
        try:
            # 检查文件是否存在
            if not os.path.exists(file_path):
                raise ValueError(f"文件不存在: {file_path}")
            
            print(f"正在加载: {file_path}")
            
            # 使用OpenCV读取EXR
            img_data = cv2.imread(file_path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
            
            if img_data is None:
                raise ValueError(f"cv2.imread返回None，无法读取文件。\n"
                               f"文件路径: {file_path}\n"
                               f"请确认:\n"
                               f"1. 文件确实是有效的EXR格式\n"
                               f"2. 环境变量OPENCV_IO_ENABLE_OPENEXR=1已设置\n"
                               f"3. OpenCV版本支持EXR")
            
            print(f"  - 成功读取，shape: {img_data.shape}, dtype: {img_data.dtype}")
            
            # 转换BGR到RGB
            if len(img_data.shape) == 3:
                img_data = cv2.cvtColor(img_data, cv2.COLOR_BGR2RGB)
            
            # Tone mapping (简单的gamma校正)
            img_data = np.clip(img_data, 0, None)
            img_data = np.power(img_data, 1.0/2.2)  # Gamma correction
            img_data = np.clip(img_data * 255, 0, 255).astype(np.uint8)
            
            # 转换为PIL Image
            return Image.fromarray(img_data)
        
        except Exception as e:
            raise Exception(f"加载EXR失败: {str(e)}\n文件路径: {file_path}")
    
    @staticmethod
    def load_image(file_path):
        """通用图片加载方法，根据扩展名选择加载方式"""
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.exr':
            return MultiMethodCropperGUI.load_exr_image(file_path)
        elif ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff']:
            # 直接使用PIL加载常规图片格式
            return Image.open(file_path).convert('RGB')
        else:
            raise ValueError(f"不支持的图片格式: {ext}")
    
    @staticmethod
    def load_image_bytes(file_name, data):
        """Load image content from bytes for remote sources."""
        ext = os.path.splitext(file_name)[1].lower()
        
        if ext == '.exr':
            array = np.frombuffer(data, dtype=np.uint8)
            img_data = cv2.imdecode(array, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
            if img_data is None:
                raise ValueError(f"无法解码远程EXR文件: {file_name}")
            if len(img_data.shape) == 3:
                img_data = cv2.cvtColor(img_data, cv2.COLOR_BGR2RGB)
            img_data = np.clip(img_data, 0, None)
            img_data = np.power(img_data, 1.0 / 2.2)
            img_data = np.clip(img_data * 255, 0, 255).astype(np.uint8)
            return Image.fromarray(img_data)
        elif ext in ['.png', '.jpg', '.jpeg', '.bmp', '.tiff']:
            return Image.open(io.BytesIO(data)).convert('RGB')
        else:
            raise ValueError(f"不支持的图片格式: {ext}")
    
    def _on_mousewheel(self, event):
        """处理鼠标滚轮事件：默认滚动，Ctrl + 滚轮缩放图片。"""
        hovered_widget = self.root.winfo_containing(event.x_root, event.y_root)
        if hovered_widget is None:
            return None

        if self.method_filter_canvas is not None and self.widget_is_descendant(hovered_widget, self.method_filter_canvas):
            return self.on_method_filter_mousewheel(event)

        if self.scroll_canvas is not None and self.widget_is_descendant(hovered_widget, self.scroll_canvas):
            if event.state & 0x0004 and self.method_images:
                zoom_factor = 1.1 if self.get_mousewheel_delta(event) < 0 else 0.9
                new_zoom = self.zoom_level * zoom_factor
                new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))

                if new_zoom != self.zoom_level:
                    self.zoom_level = new_zoom
                    for method in self.methods:
                        if method in self.method_images:
                            self.display_image_on_canvas(method)
                    self.redraw_all_rectangles()
                    self.status_label.config(text=f"缩放: {self.zoom_level:.1f}x")
                return "break"
            return self.scroll_preview_canvas(event)

        return None
    
    def reset_zoom(self):
        """重置缩放到1:1并居中"""
        self.zoom_level = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0
        for method in self.methods:
            if method in self.method_images:
                self.display_image_on_canvas(method)
        # 重绘裁剪框
        self.redraw_all_rectangles()
        self.status_label.config(text="缩放已重置到1:1")

    def on_method_filter_canvas_configure(self, event):
        if self.method_filter_canvas is not None and self.method_filter_canvas_window is not None:
            self.method_filter_canvas.itemconfigure(self.method_filter_canvas_window, width=event.width)

    def on_method_filter_mousewheel(self, event):
        if self.method_filter_canvas is None:
            return None
        delta = 0
        if hasattr(event, "delta") and event.delta:
            delta = -1 if event.delta > 0 else 1
        elif getattr(event, "num", None) in (4, 5):
            delta = -1 if event.num == 4 else 1
        if delta:
            self.method_filter_canvas.yview_scroll(delta, "units")
            return "break"
        return None

    def get_method_entry(self, method):
        return self.method_entries.get(method)

    def is_source_method(self, method):
        entry = self.get_method_entry(method)
        return bool(entry) and entry.get("type") == "source"

    def shorten_text(self, text, max_chars):
        text = str(text)
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return text[: max_chars - 3] + "..."

    def get_method_list_label(self, method):
        entry = self.get_method_entry(method) or {}
        tags = []
        if entry.get("origin") == "clone":
            tags.append("克隆")
        if entry.get("type") == "errormap":
            tags.append("差分")
        if not tags:
            return self.shorten_text(method, 24)
        return self.shorten_text(f"{method} [{' / '.join(tags)}]", 24)

    def make_unique_session_method_name(self, base_name):
        candidate = str(base_name).strip() or "method"
        if candidate not in self.all_methods:
            return candidate

        index = 2
        while True:
            candidate_with_index = f"{candidate}_{index}"
            if candidate_with_index not in self.all_methods:
                return candidate_with_index
            index += 1

    def build_source_method_entry(self, source, method_path, origin="scan"):
        return {
            "type": "source",
            "origin": origin,
            "source": dict(source),
            "path": str(method_path),
        }

    def build_errormap_method_entry(self, method_a, method_b, origin="errormap"):
        return {
            "type": "errormap",
            "origin": origin,
            "parents": (method_a, method_b),
        }

    def rebuild_method_source_maps(self):
        self.method_paths = {}
        self.method_sources = {}
        for method in self.all_methods:
            entry = self.method_entries.get(method)
            if not entry or entry.get("type") != "source":
                continue
            self.method_paths[method] = str(entry.get("path", ""))
            source = entry.get("source")
            if source:
                self.method_sources[method] = dict(source)

    def remember_new_method_ui_state(self, method, selected=True, offset="0"):
        self.method_ui_defaults[method] = {
            "selected": selected,
            "offset": str(offset),
        }

    def add_method_entry(self, method_name, entry, *, selected=True, offset="0"):
        self.method_entries[method_name] = entry
        self.all_methods.append(method_name)
        self.remember_new_method_ui_state(method_name, selected=selected, offset=offset)
        self.rebuild_method_source_maps()

    def clone_method(self, method):
        entry = self.get_method_entry(method)
        if not entry:
            return

        clone_name = self.make_unique_session_method_name(f"{method}_clone")
        offset_var = self.method_offset_vars.get(method)
        offset_value = offset_var.get().strip() if offset_var is not None else "0"

        if entry.get("type") == "source":
            clone_entry = self.build_source_method_entry(entry.get("source", {}), entry.get("path", ""), origin="clone")
        else:
            parent_a, parent_b = entry.get("parents", (None, None))
            if not parent_a or not parent_b:
                messagebox.showwarning("警告", f"方法 {method} 没有可克隆的差分来源")
                return
            clone_entry = self.build_errormap_method_entry(parent_a, parent_b, origin="clone")

        selected = self.method_filter_vars.get(method).get() if method in self.method_filter_vars else True
        self.add_method_entry(clone_name, clone_entry, selected=selected, offset=offset_value or "0")
        self.rebuild_method_filter_ui()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self.mark_workspace_dirty()
        self.status_label.config(text=f"已克隆方法: {method} -> {clone_name}")

    def collect_dependent_methods(self, root_method):
        to_remove = {root_method}
        changed = True
        while changed:
            changed = False
            for method, entry in self.method_entries.items():
                if method in to_remove or entry.get("type") != "errormap":
                    continue
                parents = entry.get("parents", ())
                if any(parent in to_remove for parent in parents):
                    to_remove.add(method)
                    changed = True
        return [method for method in self.all_methods if method in to_remove]

    def remove_method(self, method):
        if method not in self.method_entries:
            return

        methods_to_remove = self.collect_dependent_methods(method)
        if not methods_to_remove:
            return

        message = f"确定从列表中移除 {method} 吗？"
        dependent_methods = [name for name in methods_to_remove if name != method]
        if dependent_methods:
            message += "\n\n以下差分方法也会一起移除:\n" + "\n".join(dependent_methods)

        if not messagebox.askyesno("确认移除", message):
            return

        for method_name in methods_to_remove:
            self.method_entries.pop(method_name, None)
            self.method_ui_defaults.pop(method_name, None)
            self.method_filter_vars.pop(method_name, None)
            self.method_offset_vars.pop(method_name, None)
            if method_name in self.all_methods:
                self.all_methods.remove(method_name)
            if method_name in self.methods:
                self.methods.remove(method_name)

        self.rebuild_method_source_maps()
        self.rebuild_method_filter_ui()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self.mark_workspace_dirty()
        self.status_label.config(text=f"已移除方法: {', '.join(methods_to_remove)}")

    def open_errormap_dialog(self):
        candidate_methods = [method for method in self.all_methods if self.is_source_method(method)]
        if len(candidate_methods) < 2:
            messagebox.showwarning("警告", "至少需要两个可读取源图的方法才能生成差分方法")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("生成差分方法")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        tk.Label(dialog, text="方法 A").grid(row=0, column=0, sticky=tk.W, padx=10, pady=(12, 6))
        tk.Label(dialog, text="方法 B").grid(row=1, column=0, sticky=tk.W, padx=10, pady=6)

        method_a_var = tk.StringVar(value=candidate_methods[0])
        method_b_var = tk.StringVar(value=candidate_methods[1])
        ttk.Combobox(dialog, textvariable=method_a_var, values=candidate_methods, width=42, state="readonly").grid(
            row=0, column=1, padx=10, pady=(12, 6)
        )
        ttk.Combobox(dialog, textvariable=method_b_var, values=candidate_methods, width=42, state="readonly").grid(
            row=1, column=1, padx=10, pady=6
        )

        def submit_errormap():
            method_a = method_a_var.get().strip()
            method_b = method_b_var.get().strip()
            if not method_a or not method_b:
                messagebox.showwarning("警告", "请选择两个方法", parent=dialog)
                return
            if method_a == method_b:
                messagebox.showwarning("警告", "请选择两个不同的方法", parent=dialog)
                return

            self.add_errormap_method(method_a, method_b)
            dialog.destroy()

        button_frame = tk.Frame(dialog)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(8, 12))
        tk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=6)
        tk.Button(button_frame, text="添加差分方法", command=submit_errormap, bg="#5C6BC0", fg="white", width=14).pack(
            side=tk.LEFT, padx=6
        )

    def add_errormap_method(self, method_a, method_b):
        method_name = self.make_unique_session_method_name(f"{method_a}_vs_{method_b}_errormap")
        self.add_method_entry(method_name, self.build_errormap_method_entry(method_a, method_b), selected=True, offset="0")
        self.rebuild_method_filter_ui()
        self.refresh_visible_methods(reload_frame=bool(self.frame_numbers))
        self.mark_workspace_dirty()
        self.status_label.config(text=f"已添加差分方法: {method_name}")

    def get_source_frame_image_entry(self, source, frame_num):
        return self.backend.get_frame_image_entry({"__source__": source}, "__source__", frame_num)

    def load_source_frame_image(self, source, frame_num):
        return self.backend.load_method_frame_image({"__source__": source}, "__source__", frame_num)

    def get_method_render_frame_num(self, method, logical_frame_num):
        return self.get_method_frame_num(method, logical_frame_num)

    def get_method_title(self, method, logical_frame_num):
        entry = self.get_method_entry(method) or {}
        method_title = self.get_method_list_label(method)
        render_frame_num = self.get_method_render_frame_num(method, logical_frame_num)
        method_offset = self.get_method_frame_offset(method)
        details = []

        if method_offset != 0:
            if render_frame_num is None:
                details.append(f"偏移 {method_offset:+d}")
            else:
                details.append(f"偏移 {method_offset:+d} -> {render_frame_num}")

        if entry.get("type") == "errormap":
            parent_frames = []
            parent_render_frame = render_frame_num if render_frame_num is not None else logical_frame_num
            for parent in entry.get("parents", ()):
                actual_parent_frame = self.get_method_frame_num(parent, parent_render_frame)
                parent_frames.append(f"{parent}:{actual_parent_frame if actual_parent_frame is not None else '-'}")
            if parent_frames:
                details.append("差分 " + " vs ".join(parent_frames))

        if not details:
            return self.shorten_text(method_title, 32)
        return self.shorten_text(f"{method_title} ({'; '.join(details)})", 32)

    def rebuild_method_filter_ui(self):
        if self.method_filter_content is None:
            return

        for widget in self.method_filter_content.winfo_children():
            widget.destroy()

        self.is_updating_method_filter_controls = True
        previous_selected = {method: var.get() for method, var in self.method_filter_vars.items()}
        previous_offsets = {method: var.get() for method, var in self.method_offset_vars.items()}
        self.method_filter_vars = {}
        self.method_offset_vars = {}

        if not self.all_methods:
            self.is_updating_method_filter_controls = False
            tk.Label(self.method_filter_content, text="扫描后将在这里列出方法", fg="gray").pack(anchor=tk.W, pady=4)
            self.set_method_filter_pending(False)
            self.update_method_filter_summary()
            return

        for method in self.all_methods:
            default_state = self.method_ui_defaults.pop(method, None)
            selected_value = previous_selected.get(method, default_state["selected"] if default_state else True)
            offset_value = previous_offsets.get(method, default_state["offset"] if default_state else "0")

            var = tk.BooleanVar(value=selected_value)
            var.trace_add("write", self.on_method_filter_control_changed)
            offset_var = tk.StringVar(value=offset_value)
            offset_var.trace_add("write", self.on_method_filter_control_changed)
            self.method_filter_vars[method] = var
            self.method_offset_vars[method] = offset_var

            row_frame = tk.Frame(self.method_filter_content, pady=2)
            row_frame.pack(fill=tk.X, anchor=tk.W)

            title_frame = tk.Frame(row_frame)
            title_frame.pack(fill=tk.X, anchor=tk.W)

            check = tk.Checkbutton(
                title_frame,
                text=self.get_method_list_label(method),
                variable=var,
                anchor="w",
            )
            check.pack(side=tk.LEFT, fill=tk.X, expand=True, anchor=tk.W)
            check.bind("<MouseWheel>", self.on_method_filter_mousewheel)

            control_frame = tk.Frame(row_frame)
            control_frame.pack(fill=tk.X, anchor=tk.W, padx=(28, 0), pady=(2, 0))

            tk.Label(control_frame, text="偏移", fg="gray").pack(side=tk.LEFT)
            offset_entry = tk.Entry(control_frame, width=5, textvariable=offset_var, justify=tk.RIGHT)
            offset_entry.pack(side=tk.LEFT, padx=(6, 0))
            offset_entry.bind("<MouseWheel>", self.on_method_filter_mousewheel)

            action_frame = tk.Frame(control_frame)
            action_frame.pack(side=tk.RIGHT)
            tk.Button(action_frame, text="移除", width=4, command=lambda m=method: self.remove_method(m)).pack(side=tk.RIGHT)
            tk.Button(action_frame, text="克隆", width=4, command=lambda m=method: self.clone_method(m)).pack(side=tk.RIGHT, padx=(0, 4))

        self.is_updating_method_filter_controls = False
        self.set_method_filter_pending(False)
        self.update_method_filter_summary()

    def update_method_filter_summary(self):
        selected_count = sum(1 for var in self.method_filter_vars.values() if var.get())
        prefix = "待应用" if self.method_filter_pending_changes else "显示"
        if self.methods_summary_label is not None:
            self.methods_summary_label.config(text=f"{prefix} {selected_count} / {len(self.all_methods)}")

    def set_method_filter_pending(self, pending):
        self.method_filter_pending_changes = pending
        if self.method_filter_apply_button is not None:
            self.method_filter_apply_button.config(state=tk.NORMAL if pending else tk.DISABLED)
        self.update_method_filter_summary()

    def on_method_filter_control_changed(self, *_args):
        if self.is_updating_method_filter_controls:
            return
        self.set_method_filter_pending(True)

    def validate_method_offsets(self):
        normalized_offsets = {}
        for method, offset_var in self.method_offset_vars.items():
            raw_value = offset_var.get().strip()
            if not raw_value:
                normalized_offsets[method] = 0
                continue
            try:
                normalized_offsets[method] = int(raw_value)
            except ValueError:
                messagebox.showwarning("警告", f"方法 {method} 的偏移必须是整数")
                return None

        self.is_updating_method_filter_controls = True
        try:
            for method, offset in normalized_offsets.items():
                self.method_offset_vars[method].set(str(offset))
        finally:
            self.is_updating_method_filter_controls = False

        return normalized_offsets

    def get_method_frame_offset(self, method):
        offset_var = self.method_offset_vars.get(method)
        if offset_var is None:
            return 0
        try:
            return int(offset_var.get().strip() or "0")
        except ValueError:
            return 0

    def get_method_frame_num(self, method, logical_frame_num):
        offset = self.get_method_frame_offset(method)
        if offset == 0:
            return logical_frame_num

        target_frame_num = int(logical_frame_num) + offset
        if target_frame_num < 0:
            return None
        return str(target_frame_num).zfill(len(logical_frame_num))

    def update_method_status(self):
        self.update_method_filter_summary()
        self.status_label.config(
            text=f"方法列表 {len(self.all_methods)} 个，当前显示 {len(self.methods)} 个，{len(self.frame_numbers)} 帧"
        )

    def refresh_visible_methods(self, reload_frame=True):
        if self.method_filter_vars:
            normalized_offsets = self.validate_method_offsets()
            if normalized_offsets is None:
                return
        self.methods = [method for method in self.all_methods if self.method_filter_vars.get(method, tk.BooleanVar()).get()]
        self.set_method_filter_pending(False)
        self.update_method_status()
        if reload_frame and self.frame_numbers:
            self.load_current_frame()
        elif not self.methods:
            self.preview_canvas.delete("all")
        self.mark_workspace_dirty()

    def on_method_filter_changed(self):
        self.set_method_filter_pending(True)

    def set_all_method_filters(self, selected):
        self.is_updating_method_filter_controls = True
        for var in self.method_filter_vars.values():
            var.set(selected)
        self.is_updating_method_filter_controls = False
        self.set_method_filter_pending(True)
        
    def setup_ui(self):
        # 顶部控制面板
        control_frame = tk.Frame(self.root, pady=10, padx=10)
        control_frame.pack(side=tk.TOP, fill=tk.X)
        
        # 文件夹选择
        tk.Button(control_frame, text="添加输入文件夹", command=self.select_input_folder, 
                  bg="#4CAF50", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="添加远程输入", command=self.open_remote_input_dialog,
                  bg="#607D8B", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        self.input_label = tk.Label(control_frame, text="未选择文件夹", fg="gray")
        self.input_label.pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="清空输入", command=self.clear_input_folders,
                  bg="#9E9E9E", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(control_frame, text="设置", command=self.open_settings_dialog,
              bg="#795548", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        
        tk.Button(control_frame, text="选择输出文件夹", command=self.select_output_folder,
                  bg="#2196F3", fg="white", padx=10).pack(side=tk.LEFT, padx=20)
        tk.Button(control_frame, text="选择远程输出", command=self.open_remote_output_dialog,
                  bg="#455A64", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        self.output_label = tk.Label(control_frame, text="未选择文件夹", fg="gray")
        self.output_label.pack(side=tk.LEFT, padx=5)
        
        # 缩放控制
        zoom_frame = tk.Frame(control_frame)
        zoom_frame.pack(side=tk.LEFT, padx=20)
        tk.Label(zoom_frame, text="缩放(Ctrl+滚轮):").pack(side=tk.LEFT)
        tk.Button(zoom_frame, text="重置1:1", command=self.reset_zoom, bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=5)

        size_frame = tk.Frame(control_frame)
        size_frame.pack(side=tk.LEFT, padx=10)
        tk.Label(size_frame, text="预览大小:").pack(side=tk.LEFT)
        size_scale = tk.Scale(
            size_frame,
            from_=self.method_view_size_min,
            to=self.method_view_size_max,
            orient=tk.HORIZONTAL,
            resolution=20,
            showvalue=False,
            variable=self.method_view_size_var,
            command=self.on_method_view_scale_changed,
            length=180,
        )
        size_scale.pack(side=tk.LEFT, padx=(4, 6))
        self.method_view_size_value_label = tk.Label(size_frame, width=7, anchor=tk.W)
        self.method_view_size_value_label.pack(side=tk.LEFT)
        self.update_method_view_size_label(self.method_view_size)

        workspace_frame = tk.Frame(self.root, padx=10)
        workspace_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        tk.Button(workspace_frame, text="保存工程", command=self.save_workspace,
              bg="#00897B", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(workspace_frame, text="工程另存", command=self.save_workspace_as,
              bg="#26A69A", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        tk.Button(workspace_frame, text="导入工程", command=self.load_workspace,
              bg="#546E7A", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        self.workspace_label = tk.Label(workspace_frame, text="工程: 未保存", fg="gray")
        self.workspace_label.pack(side=tk.LEFT, padx=10)
        self.update_workspace_label()
        
        # 主容器
        main_container = tk.Frame(self.root)
        main_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # 左侧：图片预览区域
        left_frame = tk.Frame(main_container)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # 图片网格容器（使用Canvas+Scrollbar支持滚动）
        canvas_container = tk.Frame(left_frame)
        canvas_container.pack(fill=tk.BOTH, expand=True)
        
        # 创建可滚动的frame
        self.scroll_canvas = tk.Canvas(canvas_container, bg="gray")
        scrollbar = tk.Scrollbar(canvas_container, orient="vertical", command=self.scroll_canvas.yview)
        self.scrollable_frame = tk.Frame(self.scroll_canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.scroll_canvas.configure(scrollregion=self.scroll_canvas.bbox("all"))
        )
        
        # 绑定鼠标滚轮到滚动
        self.scroll_canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        self.scroll_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.scroll_canvas.configure(yscrollcommand=scrollbar.set)
        
        self.scroll_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # 帧导航
        nav_frame = tk.Frame(left_frame)
        nav_frame.pack(fill=tk.X, pady=5)
        
        tk.Button(nav_frame, text="◀ 上一帧", command=self.prev_frame).pack(side=tk.LEFT, padx=5)
        tk.Button(nav_frame, text="下一帧 ▶", command=self.next_frame).pack(side=tk.LEFT, padx=5)
        self.frame_info_label = tk.Label(nav_frame, text="帧: 0 / 0", font=("Arial", 10, "bold"))
        self.frame_info_label.pack(side=tk.LEFT, padx=10)
        
        tk.Label(nav_frame, text="跳转到帧号:").pack(side=tk.LEFT, padx=(20, 5))
        self.frame_jump_entry = tk.Entry(nav_frame, width=10)
        self.frame_jump_entry.pack(side=tk.LEFT, padx=5)
        tk.Button(nav_frame, text="跳转", command=self.jump_to_frame).pack(side=tk.LEFT, padx=5)

        bookmark_frame = tk.Frame(left_frame)
        bookmark_frame.pack(fill=tk.X, pady=(0, 5))
        self.bookmark_toggle_button = tk.Button(bookmark_frame, text="收藏当前帧", command=self.toggle_current_bookmark)
        self.bookmark_toggle_button.pack(side=tk.LEFT, padx=5)
        tk.Button(bookmark_frame, text="上一书签", command=lambda: self.jump_relative_bookmark(-1)).pack(side=tk.LEFT, padx=5)
        tk.Button(bookmark_frame, text="下一书签", command=lambda: self.jump_relative_bookmark(1)).pack(side=tk.LEFT, padx=5)
        tk.Label(bookmark_frame, text="书签:").pack(side=tk.LEFT, padx=(12, 4))
        self.bookmark_combo = ttk.Combobox(bookmark_frame, state="readonly", width=12)
        self.bookmark_combo.pack(side=tk.LEFT)
        tk.Button(bookmark_frame, text="跳转书签", command=self.jump_to_bookmark).pack(side=tk.LEFT, padx=5)
        self.update_bookmark_controls()
        
        # 右侧：信息和控制面板
        right_frame = tk.Frame(main_container, width=320)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)
        
        # 方法列表
        methods_frame = tk.LabelFrame(right_frame, text="检测到的方法", padx=10, pady=5)
        methods_frame.pack(fill=tk.X, pady=5)

        methods_toolbar = tk.Frame(methods_frame)
        methods_toolbar.pack(fill=tk.X, pady=(0, 2))
        tk.Button(methods_toolbar, text="全选", command=lambda: self.set_all_method_filters(True), width=6).pack(side=tk.LEFT)
        tk.Button(methods_toolbar, text="全不选", command=lambda: self.set_all_method_filters(False), width=6).pack(side=tk.LEFT, padx=(6, 0))
        self.method_filter_apply_button = tk.Button(methods_toolbar, text="确定", command=self.refresh_visible_methods, width=6, state=tk.DISABLED)
        self.method_filter_apply_button.pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(methods_toolbar, text="生成差分", command=self.open_errormap_dialog, width=8).pack(side=tk.LEFT, padx=(6, 0))
        self.methods_summary_label = tk.Label(methods_frame, text="显示 0 / 0", fg="gray", anchor=tk.W)
        self.methods_summary_label.pack(fill=tk.X, pady=(0, 4))

        methods_list_container = tk.Frame(methods_frame, height=220)
        methods_list_container.pack(fill=tk.BOTH, expand=True)
        methods_list_container.pack_propagate(False)

        self.method_filter_canvas = tk.Canvas(methods_list_container, highlightthickness=0, height=150)
        self.method_filter_scrollbar = tk.Scrollbar(
            methods_list_container,
            orient=tk.VERTICAL,
            command=self.method_filter_canvas.yview,
            width=16,
        )
        self.method_filter_content = tk.Frame(self.method_filter_canvas)
        self.method_filter_content.bind(
            "<Configure>",
            lambda e: self.method_filter_canvas.configure(scrollregion=self.method_filter_canvas.bbox("all"))
        )
        self.method_filter_canvas_window = self.method_filter_canvas.create_window(
            (0, 0), window=self.method_filter_content, anchor="nw"
        )
        self.method_filter_canvas.configure(yscrollcommand=self.method_filter_scrollbar.set)
        self.method_filter_canvas.bind("<Configure>", self.on_method_filter_canvas_configure)
        self.method_filter_canvas.bind("<MouseWheel>", self.on_method_filter_mousewheel)
        self.method_filter_content.bind("<MouseWheel>", self.on_method_filter_mousewheel)

        self.method_filter_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.method_filter_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rebuild_method_filter_ui()
        
        # 裁剪信息
        info_frame = tk.LabelFrame(right_frame, text="当前裁剪框", padx=10, pady=5)
        info_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(info_frame, text="起始坐标 (x, y):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.coord_label = tk.Label(info_frame, text="(-, -)", font=("Arial", 10, "bold"))
        self.coord_label.grid(row=0, column=1, sticky=tk.W, pady=2)
        
        tk.Label(info_frame, text="裁剪尺寸 (w × h):").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.size_label = tk.Label(info_frame, text="- × -", font=("Arial", 10, "bold"))
        self.size_label.grid(row=1, column=1, sticky=tk.W, pady=2)
        
        tk.Label(info_frame, text="结束坐标:").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.end_coord_label = tk.Label(info_frame, text="(-, -)", font=("Arial", 9))
        self.end_coord_label.grid(row=2, column=1, sticky=tk.W, pady=2)
        
        tk.Button(info_frame, text="✓ 添加到裁剪列表", command=self.add_crop_box,
                  bg="#2196F3", fg="white", font=("Arial", 10, "bold")).grid(row=3, column=0, columnspan=2, pady=(10, 0), sticky=tk.EW)
        
        # 已添加的裁剪框列表
        boxes_frame = tk.LabelFrame(right_frame, text="裁剪框列表", padx=10, pady=5)
        boxes_frame.pack(fill=tk.X, pady=5)  # 不用expand
        
        self.boxes_listbox = tk.Listbox(boxes_frame, height=4)  # 减小高度
        self.boxes_listbox.pack(fill=tk.BOTH)
        self.boxes_listbox.bind("<Double-Button-1>", self.remove_selected_box)
        
        tk.Label(boxes_frame, text="双击删除选中的裁剪框", fg="gray", font=("Arial", 8)).pack()
        tk.Button(boxes_frame, text="清空所有裁剪框", command=self.clear_all_boxes,
                  bg="#f44336", fg="white").pack(fill=tk.X, pady=(5, 0))
        
        # 手动输入坐标
        manual_frame = tk.LabelFrame(right_frame, text="手动输入坐标", padx=10, pady=5)
        manual_frame.pack(fill=tk.X, pady=5)
        
        tk.Label(manual_frame, text="X:").grid(row=0, column=0, sticky=tk.W)
        self.x_entry = tk.Entry(manual_frame, width=8)
        self.x_entry.grid(row=0, column=1, padx=2)
        
        tk.Label(manual_frame, text="Y:").grid(row=0, column=2, sticky=tk.W, padx=(10, 0))
        self.y_entry = tk.Entry(manual_frame, width=8)
        self.y_entry.grid(row=0, column=3, padx=2)
        
        tk.Label(manual_frame, text="宽度:").grid(row=1, column=0, sticky=tk.W, pady=(5, 0))
        self.w_entry = tk.Entry(manual_frame, width=8)
        self.w_entry.grid(row=1, column=1, padx=2, pady=(5, 0))
        
        tk.Label(manual_frame, text="高度:").grid(row=1, column=2, sticky=tk.W, padx=(10, 0), pady=(5, 0))
        self.h_entry = tk.Entry(manual_frame, width=8)
        self.h_entry.grid(row=1, column=3, padx=2, pady=(5, 0))
        
        tk.Button(manual_frame, text="应用坐标", command=self.apply_manual_coords,
                  bg="#FF9800", fg="white").grid(row=2, column=0, columnspan=4, pady=(10, 0), sticky=tk.EW)
        
        # 预览区域（减小高度）
        preview_frame = tk.LabelFrame(right_frame, text="裁剪预览", padx=10, pady=5)
        preview_frame.pack(fill=tk.X, pady=5)  # 不用expand
        
        self.preview_canvas = tk.Canvas(preview_frame, bg="gray", width=260, height=200)  # 减小尺寸
        self.preview_canvas.pack()
        
        # 操作按钮（确保始终可见）
        action_frame = tk.Frame(right_frame)
        action_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
        
        tk.Button(action_frame, text="🗸 批量裁剪当前帧", command=self.crop_current_frame,
                  bg="#4CAF50", fg="white", font=("Arial", 11, "bold")).pack(fill=tk.X, pady=5)
        
        tk.Button(action_frame, text="批量裁剪所有帧", command=self.batch_crop_all,
                  bg="#2196F3", fg="white").pack(fill=tk.X, pady=2)
        
        # 状态栏
        self.status_label = tk.Label(self.root, text="就绪", relief=tk.SUNKEN, anchor=tk.W)
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)
        
    def select_input_folder(self):
        folder = filedialog.askdirectory(title="选择方法文件夹或包含方法文件夹的根目录")
        if folder:
            self.input_folder = folder
            if folder not in self.input_folders:
                self.input_folders.append(folder)
                self.input_sources.append({
                    "type": "local",
                    "path": folder
                })
            self.scan_methods_and_frames()
    
    def open_remote_input_dialog_with_presets(self):
        if not self.backend.is_remote_available:
            messagebox.showwarning("警告", "未安装 paramiko，暂时无法使用远程 SFTP 输入")
            return

        def submit_remote(dialog, remote_source):
            self.input_sources.append(remote_source)
            dialog.destroy()
            self.scan_methods_and_frames()

        self.create_remote_dialog(
            title="添加远程 SFTP 输入",
            path_label="远程路径",
            confirm_text="添加",
            on_submit=submit_remote,
        )

    def open_remote_input_dialog(self):
        """Open a dialog to add an SFTP input source."""
        return self.open_remote_input_dialog_with_presets()
        if not self.backend.is_remote_available:
            messagebox.showwarning("警告", "未安装 paramiko，暂时无法使用远程 SFTP 输入")
            return
        
        dialog = tk.Toplevel(self.root)
        dialog.title("添加远程 SFTP 输入")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        
        fields = [
            ("地址", "host", ""),
            ("端口", "port", "22"),
            ("账号", "username", ""),
            ("密码", "password", ""),
            ("远程路径", "remote_path", "")
        ]
        entries = {}
        
        for row, (label, key, default_value) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=row, column=0, sticky=tk.W, padx=10, pady=6)
            entry = tk.Entry(dialog, width=36, show="*" if key == "password" else "")
            entry.grid(row=row, column=1, padx=10, pady=6)
            if default_value:
                entry.insert(0, default_value)
            entries[key] = entry
        
        def submit_remote():
            host = entries["host"].get().strip()
            username = entries["username"].get().strip()
            password = entries["password"].get()
            remote_path = entries["remote_path"].get().strip()
            port_text = entries["port"].get().strip() or "22"
            
            if not host or not username or not remote_path:
                messagebox.showwarning("警告", "请填写地址、账号和远程路径", parent=dialog)
                return
            if not remote_path.startswith("/"):
                messagebox.showwarning("警告", "远程路径必须是绝对路径，并以 / 开头", parent=dialog)
                return
            
            try:
                port = int(port_text)
            except ValueError:
                messagebox.showwarning("警告", "端口必须是整数", parent=dialog)
                return
            
            remote_source = {
                "type": "sftp",
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "path": remote_path
            }
            
            self.input_sources.append(remote_source)
            dialog.destroy()
            self.scan_methods_and_frames()
        
        button_frame = tk.Frame(dialog)
        button_frame.grid(row=len(fields), column=0, columnspan=2, pady=(8, 12))
        tk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=6)
        tk.Button(button_frame, text="添加", command=submit_remote, bg="#607D8B", fg="white", width=10).pack(side=tk.LEFT, padx=6)
    
    def get_remote_connection_key(self, source):
        return self.backend.storage.get_remote_connection_key(source)
    
    def get_sftp_client(self, source):
        return self.backend.storage.get_sftp_client(source)
    
    def close_remote_connection(self, connection_key):
        self.backend.storage.close_remote_connection(connection_key)
    
    def close_all_remote_connections(self):
        self.backend.storage.close_all_remote_connections()
    
    def has_output_target(self):
        return self.backend.crop.has_output_target(self.output_target)
    
    def ensure_remote_dir(self, sftp, remote_dir):
        self.backend.storage.ensure_remote_dir(sftp, remote_dir)
    
    def join_output_path(self, *parts):
        return self.backend.storage.join_path(self.output_target, *parts)
    
    def save_output_image(self, img, target_path):
        if not self.output_target:
            raise RuntimeError("未配置输出目标")
        self.backend.crop.save_output_image(img, target_path, self.output_target)

    def clear_workspace_state(self, clear_output=True, preserve_workspace_path=False):
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
        self.method_filter_vars = {}
        self.method_offset_vars = {}
        self.frame_numbers = []
        self.current_frame_index = 0
        self.bookmarked_frames.clear()
        self.close_all_remote_connections()

        if clear_output:
            self.output_target = None
            self.output_folder = str(self.config.default_output_dir)

        for img in self.method_images.values():
            try:
                img.close()
            except Exception:
                pass

        self.method_images.clear()
        self.display_images.clear()
        self.photo_images.clear()
        self.canvases.clear()
        self.rect_ids.clear()
        self.current_rect_ids.clear()
        self.crop_boxes.clear()
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        self.zoom_level = 1.0
        self.pan_offset_x = 0
        self.pan_offset_y = 0

        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.preview_canvas.delete("all")
        self.rebuild_method_filter_ui()
        self.rebuild_crop_boxes_list()
        self.update_bookmark_controls()
        self.update_output_label()
        self.input_label.config(text="未选择文件夹", fg="gray")
        self.frame_info_label.config(text="帧: 0 / 0")
        self.coord_label.config(text="(-, -)")
        self.size_label.config(text="- × -")
        self.end_coord_label.config(text="(-, -)")

        if not preserve_workspace_path:
            self.set_workspace_file_path(None)
    
    def on_close(self):
        """Clean up connections before closing the app."""
        if not self.prompt_save_workspace_if_dirty("退出软件"):
            return
        self.backend.close()
        self.root.destroy()
    
    def clear_input_folders(self):
        """Clear selected input folders and reset related UI state."""
        if not self.is_restoring_workspace and not self.prompt_save_workspace_if_dirty("清空输入"):
            return
        self.clear_workspace_state(clear_output=False, preserve_workspace_path=True)
        self.mark_workspace_dirty()
        self.status_label.config(text="已清空输入文件夹")
            
    def select_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹")
        if folder:
            self.output_folder = folder
            self.output_target = {
                "type": "local",
                "path": folder
            }
            self.update_output_label()
            self.mark_workspace_dirty()
    
    def open_remote_output_dialog_with_presets(self):
        if not self.backend.is_remote_available:
            messagebox.showwarning("警告", "未安装 paramiko，暂时无法使用远程 SFTP 输出")
            return

        def submit_remote_output(dialog, remote_source):
            self.output_target = remote_source
            try:
                sftp = self.get_sftp_client(self.output_target)
                self.ensure_remote_dir(sftp, self.output_target["path"])
            except Exception as exc:
                self.output_target = None
                messagebox.showerror("错误", f"远程输出路径不可用: {exc}", parent=dialog)
                return

            self.output_folder = self.output_target["path"]
            self.update_output_label()
            self.mark_workspace_dirty()
            dialog.destroy()

        self.create_remote_dialog(
            title="设置远程 SFTP 输出",
            path_label="远程输出路径",
            confirm_text="确定",
            on_submit=submit_remote_output,
        )

    def open_remote_output_dialog(self):
        """Open a dialog to configure an SFTP output target."""
        return self.open_remote_output_dialog_with_presets()
        if not self.backend.is_remote_available:
            messagebox.showwarning("警告", "未安装 paramiko，暂时无法使用远程 SFTP 输出")
            return
        
        dialog = tk.Toplevel(self.root)
        dialog.title("设置远程 SFTP 输出")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        
        fields = [
            ("地址", "host", ""),
            ("端口", "port", "22"),
            ("账号", "username", ""),
            ("密码", "password", ""),
            ("远程输出路径", "remote_path", "")
        ]
        entries = {}
        
        for row, (label, key, default_value) in enumerate(fields):
            tk.Label(dialog, text=label).grid(row=row, column=0, sticky=tk.W, padx=10, pady=6)
            entry = tk.Entry(dialog, width=36, show="*" if key == "password" else "")
            entry.grid(row=row, column=1, padx=10, pady=6)
            if default_value:
                entry.insert(0, default_value)
            entries[key] = entry
        
        def submit_remote_output():
            host = entries["host"].get().strip()
            username = entries["username"].get().strip()
            password = entries["password"].get()
            remote_path = entries["remote_path"].get().strip()
            port_text = entries["port"].get().strip() or "22"
            
            if not host or not username or not remote_path:
                messagebox.showwarning("警告", "请填写地址、账号和远程输出路径", parent=dialog)
                return
            if not remote_path.startswith("/"):
                messagebox.showwarning("警告", "远程输出路径必须是绝对路径，并以 / 开头", parent=dialog)
                return
            
            try:
                port = int(port_text)
            except ValueError:
                messagebox.showwarning("警告", "端口必须是整数", parent=dialog)
                return
            
            self.output_target = {
                "type": "sftp",
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "path": remote_path
            }
            try:
                sftp = self.get_sftp_client(self.output_target)
                self.ensure_remote_dir(sftp, remote_path)
            except Exception as exc:
                self.output_target = None
                messagebox.showerror("错误", f"远程输出路径不可用: {exc}", parent=dialog)
                return
            self.output_folder = remote_path
            self.output_label.config(text=f"{host}:{remote_path}", fg="black")
            dialog.destroy()
        
        button_frame = tk.Frame(dialog)
        button_frame.grid(row=len(fields), column=0, columnspan=2, pady=(8, 12))
        tk.Button(button_frame, text="取消", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=6)
        tk.Button(button_frame, text="确定", command=submit_remote_output, bg="#455A64", fg="white", width=10).pack(side=tk.LEFT, padx=6)
    
    def local_folder_has_frames(self, folder_path):
        return self.backend.scan.local_folder_has_frames(folder_path)
    
    def remote_folder_has_frames(self, source):
        return self.backend.scan.remote_folder_has_frames(source)
    
    def list_remote_entries(self, source):
        return self.backend.scan.list_remote_entries(source)
    
    def remote_is_dir(self, source):
        return self.backend.scan.remote_is_dir(source)
    
    def build_child_source(self, source, child_name):
        return self.backend.scan.build_child_source(source, child_name)
    
    def source_has_frames(self, source):
        return self.backend.scan.source_has_frames(source, self.last_scan_errors)
    
    def get_source_display_name(self, source):
        """Human-readable label for an input source."""
        if source["type"] == "local":
            return os.path.basename(source["path"])
        server_name = source.get("server_label", source["host"])
        return f"{server_name}:{source['path']}"
    
    def get_output_display_name(self):
        return self.backend.crop.get_output_display_name(self.output_target)
    
    def make_unique_method_name(self, base_name, folder_path, used_names):
        return self.backend.scan.make_unique_method_name(base_name, folder_path, used_names)
    
    def collect_method_folders(self):
        method_entries, self.last_scan_errors = self.backend.scan.collect_method_folders(self.input_sources)
        return method_entries
    
    def list_method_frame_files(self, source):
        return self.backend.scan.list_method_frame_files(source)
    
    def get_frame_image_entry(self, method, frame_num):
        entry = self.get_method_entry(method)
        if not entry or entry.get("type") != "source":
            return None
        return self.get_source_frame_image_entry(entry.get("source", {}), frame_num)
    
    def load_method_frame_image(self, method, frame_num):
        entry = self.get_method_entry(method)
        if not entry:
            raise ValueError(f"unknown method: {method}")

        render_frame_num = self.get_method_render_frame_num(method, frame_num)
        if render_frame_num is None:
            raise ValueError(f"方法 {method} 的偏移导致帧号超出范围")

        if entry.get("type") == "source":
            image_entry = self.get_source_frame_image_entry(entry.get("source", {}), render_frame_num)
            if image_entry is None:
                raise ValueError(f"方法 {method} 缺少帧 {render_frame_num}")
            image = self.load_source_frame_image(entry.get("source", {}), render_frame_num)
            if image is None:
                raise ValueError(f"方法 {method} 无法读取帧 {render_frame_num}")
            return image

        parent_a, parent_b = entry.get("parents", (None, None))
        if not parent_a or not parent_b:
            raise ValueError(f"差分方法 {method} 缺少来源方法")

        first_image = None
        second_image = None
        try:
            first_image = self.load_method_frame_image(parent_a, render_frame_num)
            second_image = self.load_method_frame_image(parent_b, render_frame_num)
            return self.backend.crop.create_absolute_error_map_image(first_image, second_image)
        finally:
            if first_image is not None:
                first_image.close()
            if second_image is not None:
                second_image.close()
             
    def scan_methods_and_frames(self):
        """扫描子文件夹和帧序列"""
        if not self.input_sources:
            return
        
        result = self.backend.scan.scan(self.input_sources)
        self.last_scan_errors = result.errors
        
        if not result.methods:
            message = "未找到符合当前输入模板的本地/远程方法路径"
            message += f"\n\n当前模板: {self.get_input_pattern_summary()}"
            if self.last_scan_errors:
                message += "\n\n连接错误:\n" + "\n".join(self.last_scan_errors[:3])
            messagebox.showwarning("警告", message)
            return
        
        self.apply_scan_result_preserving_methods(result)

        self.rebuild_method_filter_ui()
        self.refresh_visible_methods(reload_frame=False)
        methods_with_frames = self.methods_with_frames
        
        if not self.frame_numbers:
            messagebox.showwarning(
                "警告", 
                f"在已选择的文件夹中都未找到符合当前模板的文件\n"
                f"当前模板: {self.get_input_pattern_summary()}\n"
                f"已扫描的方法: {', '.join(self.methods)}"
            )
            return
        
        self.input_label.config(
            text=f"已打开 {len(self.input_sources)} 个输入",
            fg="black"
        )
        self.status_label.config(
            text=f"方法列表 {len(self.all_methods)} 个，当前显示 {len(self.methods)} 个，{len(self.frame_numbers)} 帧 "
                 f"(有帧的方法: {', '.join(methods_with_frames)})"
        )
        
        # 加载第一帧
        self.current_frame_index = 0
        self.load_current_frame()
        self.mark_workspace_dirty()
        
    def load_current_frame(self):
        """加载当前帧的所有方法的图片"""
        if not self.frame_numbers:
            return
        
        frame_num = self.frame_numbers[self.current_frame_index]
        
        # 显式关闭旧图片对象
        for img in self.method_images.values():
            try:
                img.close()
            except:
                pass
        
        # 清空现有的canvas
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        
        self.canvases.clear()
        self.method_images.clear()
        self.display_images.clear()
        self.photo_images.clear()
        self.rect_ids.clear()
        
        # 触发垃圾回收
        import gc
        gc.collect()

        bookmark_mark = " ★" if frame_num in self.bookmarked_frames else ""
        self.frame_info_label.config(
            text=f"帧: {self.current_frame_index + 1} / {len(self.frame_numbers)} (帧号: {frame_num}){bookmark_mark}"
        )
        self.update_bookmark_controls()

        if not self.methods:
            self.preview_canvas.delete("all")
            tk.Label(
                self.scrollable_frame,
                text="当前没有选中的方法，请在右侧勾选要显示的子文件夹",
                fg="gray",
                pady=20,
            ).pack()
            return
        
        target_width = self.method_view_size
        target_height = self.method_view_size
        available_width = max(self.scroll_canvas.winfo_width() - 40, target_width)
        cols_per_row = max(1, min(len(self.methods), available_width // (target_width + 16)))
        
        # 为每个方法创建canvas
        for i, method in enumerate(self.methods):
            # 计算当前方法在网格中的位置
            row = i // cols_per_row  # 行号 (0 或 1)
            col = i % cols_per_row   # 列号
            
            # 创建包含标签和canvas的Frame
            method_frame = tk.Frame(
                self.scrollable_frame,
                bd=2,
                relief=tk.RIDGE,
                width=target_width + 12,
                height=target_height + 44,
            )
            method_frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            method_frame.grid_propagate(False)

            method_title = self.get_method_title(method, frame_num)
            
            # 方法标签
            tk.Label(method_frame, text=method_title, font=("Arial", 11, "bold"), 
                    bg="#E3F2FD", pady=5).pack(fill=tk.X)
            
            try:
                # 加载图片（自动识别格式或实时生成差分图）
                img = self.load_method_frame_image(method, frame_num)
                
                self.method_images[method] = img
                
                # 创建canvas
                canvas = tk.Canvas(
                    method_frame,
                    bg="black",
                    width=target_width,
                    height=target_height,
                    cursor="cross",
                )
                canvas.pack(fill=tk.BOTH, expand=True, pady=5)
                self.canvases[method] = canvas
                
                # 绑定鼠标事件
                canvas.bind("<ButtonPress-1>", lambda e, m=method: self.on_mouse_press(e, m))
                canvas.bind("<B1-Motion>", lambda e, m=method: self.on_mouse_drag(e, m))
                canvas.bind("<ButtonRelease-1>", lambda e, m=method: self.on_mouse_release(e, m))
                
                # 绑定右键拖动（平移）
                canvas.bind("<ButtonPress-3>", lambda e, m=method: self.on_pan_start(e, m))
                canvas.bind("<B3-Motion>", lambda e, m=method: self.on_pan_drag(e, m))
                canvas.bind("<ButtonRelease-3>", lambda e, m=method: self.on_pan_end(e, m))
                
                # 显示图片
                self.display_image_on_canvas(method)
                
            except Exception as e:
                tk.Label(method_frame, text=f"加载失败: {str(e)}", 
                        fg="red").pack()
        
        # 配置网格权重，使其能自动调整大小
        for c in range(cols_per_row):
            self.scrollable_frame.grid_columnconfigure(c, weight=1)
        total_rows = (len(self.methods) + cols_per_row - 1) // cols_per_row
        for r in range(total_rows):
            self.scrollable_frame.grid_rowconfigure(r, weight=1)
        
        # 如果有选区，重新绘制
        if self.crop_start_x is not None:
            self.root.after(100, self.redraw_all_rectangles)
            self.update_preview()
            
    def display_image_on_canvas(self, method):
        """在canvas上显示图片"""
        if method not in self.method_images or method not in self.canvases:
            return
        
        canvas = self.canvases[method]
        img = self.method_images[method]
        
        # 等待canvas尺寸更新
        canvas.update_idletasks()
        canvas_width = canvas.winfo_width()
        canvas_height = canvas.winfo_height()  # 使用实际canvas高度
        
        # 如果canvas还没有尺寸，使用默认值
        if canvas_height <= 1:
            canvas_height = self.method_view_size

        if canvas_width <= 1:
            canvas_width = self.method_view_size
        
        if canvas_width <= 1:
            self.root.after(100, lambda: self.display_image_on_canvas(method))
            return
        
        # 计算基础缩放比例（适应canvas）
        img_width, img_height = img.size
        scale_x = canvas_width / img_width
        scale_y = canvas_height / img_height
        base_scale = min(scale_x, scale_y, 1.0)
        
        # 应用缩放级别
        self.scale_factor = base_scale * self.zoom_level
        
        new_width = int(img_width * self.scale_factor)
        new_height = int(img_height * self.scale_factor)
        
        display_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        self.display_images[method] = display_img
        
        photo = ImageTk.PhotoImage(display_img)
        self.photo_images[method] = photo
        
        canvas.delete("all")
        
        # 居中显示 + 平移偏移
        x_offset = (canvas_width - new_width) // 2 + self.pan_offset_x
        y_offset = (canvas_height - new_height) // 2 + self.pan_offset_y
        
        canvas.create_image(x_offset, y_offset, anchor=tk.NW, image=photo, tags="image")
        canvas.image_offset = (x_offset, y_offset)

    def update_method_view_size_label(self, size_value):
        if self.method_view_size_value_label is not None:
            self.method_view_size_value_label.config(text=f"{size_value}px")

    def on_method_view_scale_changed(self, value):
        size_value = int(round(float(value)))
        self.update_method_view_size_label(size_value)
        if self.method_view_resize_job is not None:
            self.root.after_cancel(self.method_view_resize_job)
        self.method_view_resize_job = self.root.after(120, lambda: self.apply_method_view_size(size_value))

    def apply_method_view_size(self, size_value):
        self.method_view_resize_job = None
        size_value = max(self.method_view_size_min, min(self.method_view_size_max, size_value))
        if size_value == self.method_view_size:
            return
        self.method_view_size = size_value
        if self.frame_numbers:
            self.load_current_frame()
        self.mark_workspace_dirty()
        self.status_label.config(
            text=f"预览大小已调整为 {self.method_view_size}px"
        )
        
    def canvas_to_image_coords(self, canvas_x, canvas_y, method):
        """将画布坐标转换为原始图片坐标"""
        canvas = self.canvases.get(method)
        if not canvas or not hasattr(canvas, 'image_offset'):
            return None, None
        
        img = self.method_images.get(method)
        if not img:
            return None, None
        
        x_offset, y_offset = canvas.image_offset
        
        display_x = canvas_x - x_offset
        display_y = canvas_y - y_offset
        
        orig_x = int(display_x / self.scale_factor)
        orig_y = int(display_y / self.scale_factor)
        
        orig_x = max(0, min(orig_x, img.width))
        orig_y = max(0, min(orig_y, img.height))
        
        return orig_x, orig_y
        
    def image_to_canvas_coords(self, img_x, img_y, method):
        """将原始图片坐标转换为画布坐标"""
        canvas = self.canvases.get(method)
        if not canvas or not hasattr(canvas, 'image_offset'):
            return None, None
        
        x_offset, y_offset = canvas.image_offset
        
        canvas_x = int(img_x * self.scale_factor) + x_offset
        canvas_y = int(img_y * self.scale_factor) + y_offset
        
        return canvas_x, canvas_y
        
    def on_pan_start(self, event, method):
        """开始平移拖动"""
        self.is_panning = True
        self.pan_start_x = event.x
        self.pan_start_y = event.y
        # 改变鼠标指针
        self.canvases[method].config(cursor="fleur")
    
    def on_pan_drag(self, event, method):
        """平移拖动中"""
        if not self.is_panning:
            return
        
        dx = event.x - self.pan_start_x
        dy = event.y - self.pan_start_y
        
        self.pan_offset_x += dx
        self.pan_offset_y += dy
        
        self.pan_start_x = event.x
        self.pan_start_y = event.y
        
        # 重新显示所有图片
        for m in self.methods:
            if m in self.method_images:
                self.display_image_on_canvas(m)
        
        # 始终重绘裁剪框（已保存+当前）
        self.redraw_all_rectangles()
    
    def on_pan_end(self, event, method):
        """结束平移拖动"""
        self.is_panning = False
        self.canvases[method].config(cursor="cross")
    
    def on_mouse_press(self, event, method):
        # 如果正在平移，不处理选区
        if self.is_panning:
            return
        
        self.is_dragging = True
        self.active_canvas = method
        
        orig_x, orig_y = self.canvas_to_image_coords(event.x, event.y, method)
        
        if orig_x is not None:
            self.crop_start_x = orig_x
            self.crop_start_y = orig_y
            self.crop_end_x = orig_x
            self.crop_end_y = orig_y
            
    def on_mouse_drag(self, event, method):
        if not self.is_dragging or self.active_canvas != method:
            return
        
        orig_x, orig_y = self.canvas_to_image_coords(event.x, event.y, method)
        
        if orig_x is not None:
            # 检查是否按住 Shift 键
            if event.state & 0x0001:  # Shift 键被按下
                # 计算正方形：取宽高中的最大值
                width = abs(orig_x - self.crop_start_x)
                height = abs(orig_y - self.crop_start_y)
                size = max(width, height)
                
                # 根据拖动方向设置终点
                if orig_x >= self.crop_start_x:
                    self.crop_end_x = self.crop_start_x + size
                else:
                    self.crop_end_x = self.crop_start_x - size
                
                if orig_y >= self.crop_start_y:
                    self.crop_end_y = self.crop_start_y + size
                else:
                    self.crop_end_y = self.crop_start_y - size
            else:
                # 正常的矩形绘制
                self.crop_end_x = orig_x
                self.crop_end_y = orig_y
            
            self.redraw_all_rectangles()
            
    def on_mouse_release(self, event, method):
        if not self.is_dragging:
            return
        
        self.is_dragging = False
        
        if self.crop_start_x is not None and self.crop_end_x is not None:
            # 确保起始点在左上角
            x1 = min(self.crop_start_x, self.crop_end_x)
            y1 = min(self.crop_start_y, self.crop_end_y)
            x2 = max(self.crop_start_x, self.crop_end_x)
            y2 = max(self.crop_start_y, self.crop_end_y)
            
            self.crop_start_x, self.crop_start_y = x1, y1
            self.crop_end_x, self.crop_end_y = x2, y2
            
            self.redraw_all_rectangles()
            self.update_preview()
            
    def redraw_all_rectangles(self):
        """在所有canvas上绘制裁剪矩形（已保存+当前）"""
        for method, canvas in self.canvases.items():
            # 删除当前绘制的矩形
            if method in self.current_rect_ids:
                canvas.delete(self.current_rect_ids[method])
            
            # 重绘已保存的所有矩形
            if method in self.rect_ids:
                for rect_id in self.rect_ids[method]:
                    canvas.delete(rect_id)
            self.rect_ids[method] = []
            
            # 绘制已保存的框
            for idx, (x1, y1, x2, y2) in enumerate(self.crop_boxes):
                canvas_x1, canvas_y1 = self.image_to_canvas_coords(x1, y1, method)
                canvas_x2, canvas_y2 = self.image_to_canvas_coords(x2, y2, method)
                
                if canvas_x1 is not None:
                    color = self.box_colors[idx % len(self.box_colors)]
                    rect_id = canvas.create_rectangle(
                        canvas_x1, canvas_y1, canvas_x2, canvas_y2,
                        outline=color, width=3
                    )
                    self.rect_ids[method].append(rect_id)
            
            # 绘制当前正在绘制的框
            if self.crop_start_x is not None and self.crop_end_x is not None:
                canvas_x1, canvas_y1 = self.image_to_canvas_coords(
                    self.crop_start_x, self.crop_start_y, method
                )
                canvas_x2, canvas_y2 = self.image_to_canvas_coords(
                    self.crop_end_x, self.crop_end_y, method
                )
                
                if canvas_x1 is not None:
                    rect_id = canvas.create_rectangle(
                        canvas_x1, canvas_y1, canvas_x2, canvas_y2,
                        outline="yellow", width=2, dash=(5, 5)
                    )
                    self.current_rect_ids[method] = rect_id
        
        # 更新当前框信息标签
        if self.crop_start_x is not None:
            width = abs(self.crop_end_x - self.crop_start_x)
            height = abs(self.crop_end_y - self.crop_start_y)
            
            self.coord_label.config(text=f"({self.crop_start_x}, {self.crop_start_y})")
            self.size_label.config(text=f"{width} × {height}")
            self.end_coord_label.config(text=f"({self.crop_end_x}, {self.crop_end_y})")
            
            # 更新输入框
            self.x_entry.delete(0, tk.END)
            self.x_entry.insert(0, str(self.crop_start_x))
            self.y_entry.delete(0, tk.END)
            self.y_entry.insert(0, str(self.crop_start_y))
            self.w_entry.delete(0, tk.END)
            self.w_entry.insert(0, str(width))
            self.h_entry.delete(0, tk.END)
            self.h_entry.insert(0, str(height))
        else:
            # 清空输入框
            self.x_entry.delete(0, tk.END)
            self.y_entry.delete(0, tk.END)
            self.w_entry.delete(0, tk.END)
            self.h_entry.delete(0, tk.END)
        
    def update_preview(self):
        """更新裁剪预览"""
        if not self.method_images or self.crop_start_x is None:
            return
        
        try:
            # 使用第一个方法的图片作为预览
            first_method = next((method for method in self.methods if method in self.method_images), None)
            if first_method is None:
                return
            if first_method not in self.method_images:
                return
            
            img = self.method_images[first_method]
            
            x1 = min(self.crop_start_x, self.crop_end_x)
            y1 = min(self.crop_start_y, self.crop_end_y)
            x2 = max(self.crop_start_x, self.crop_end_x)
            y2 = max(self.crop_start_y, self.crop_end_y)
            
            if x2 - x1 < 5 or y2 - y1 < 5:
                return
            
            cropped = img.crop((x1, y1, x2, y2))
            
            # 缩放以适应预览区域（260x200）
            preview_width = 260
            preview_height = 200
            cropped.thumbnail((preview_width, preview_height), Image.Resampling.LANCZOS)
            
            preview_photo = ImageTk.PhotoImage(cropped)
            
            self.preview_canvas.delete("all")
            # 使用正确的中心点坐标
            self.preview_canvas.create_image(
                preview_width // 2, preview_height // 2, image=preview_photo, anchor=tk.CENTER
            )
            self.preview_canvas.preview_image = preview_photo
            
        except Exception as e:
            print(f"预览更新失败: {e}")
            
    def apply_manual_coords(self):
        """应用手动输入的坐标"""
        try:
            x = int(self.x_entry.get())
            y = int(self.y_entry.get())
            w = int(self.w_entry.get())
            h = int(self.h_entry.get())
            
            if not self.method_images:
                messagebox.showwarning("警告", "请先加载图片")
                return
            
            # 检查坐标是否有效
            if x < 0 or y < 0 or w <= 0 or h <= 0:
                messagebox.showwarning("警告", "坐标和尺寸必须为正数")
                return
            
            # 检查是否超出范围（使用第一张图片）
            first_method = next((method for method in self.methods if method in self.method_images), None)
            if first_method is None:
                messagebox.showwarning("警告", "当前没有可用图片")
                return
            img = self.method_images[first_method]
            
            if x + w > img.width or y + h > img.height:
                messagebox.showwarning("警告", "裁剪区域超出图片范围")
                return
            
            self.crop_start_x = x
            self.crop_start_y = y
            self.crop_end_x = x + w
            self.crop_end_y = y + h
            
            self.redraw_all_rectangles()
            self.update_preview()
            
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
            
    def add_crop_box(self):
        """添加当前裁剪框到列表"""
        if self.crop_start_x is None:
            messagebox.showwarning("警告", "请先绘制一个裁剪框")
            return
        
        x1 = min(self.crop_start_x, self.crop_end_x)
        y1 = min(self.crop_start_y, self.crop_end_y)
        x2 = max(self.crop_start_x, self.crop_end_x)
        y2 = max(self.crop_start_y, self.crop_end_y)
        
        if x2 - x1 < 1 or y2 - y1 < 1:
            messagebox.showwarning("警告", "裁剪框太小")
            return
        
        self.crop_boxes.append((x1, y1, x2, y2))
        box_idx = len(self.crop_boxes)
        
        # 更新列表显示
        self.boxes_listbox.insert(tk.END, f"框{box_idx}: ({x1}, {y1}) -> ({x2}, {y2}) [{x2-x1}×{y2-y1}]")
        
        # 清除当前绘制状态（但不清空坐标）
        self.current_rect_ids.clear()
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        
        # 立即重绘所有框，使虚线框变成实线
        self.redraw_all_rectangles()
        
        self.coord_label.config(text="(-, -)")
        self.size_label.config(text="- × -")
        self.end_coord_label.config(text="(-, -)")
        self.preview_canvas.delete("all")
        self.mark_workspace_dirty()
        
        self.status_label.config(text=f"已添加裁剪框 {box_idx}")
    
    def remove_selected_box(self, event=None):
        """删除选中的裁剪框"""
        selection = self.boxes_listbox.curselection()
        if not selection:
            return
        
        idx = selection[0]
        if idx >= len(self.crop_boxes):
            return
        
        # 删除裁剪框
        self.crop_boxes.pop(idx)
        
        # 删除所有canvas上对应的矩形
        for method, canvas in self.canvases.items():
            if method in self.rect_ids and idx < len(self.rect_ids[method]):
                canvas.delete(self.rect_ids[method][idx])
                self.rect_ids[method].pop(idx)
        
        # 更新列表
        self.boxes_listbox.delete(idx)
        # 重新编号
        self.boxes_listbox.delete(0, tk.END)
        for i, (x1, y1, x2, y2) in enumerate(self.crop_boxes, 1):
            self.boxes_listbox.insert(tk.END, f"框{i}: ({x1}, {y1}) -> ({x2}, {y2}) [{x2-x1}×{y2-y1}]")
        self.mark_workspace_dirty()
        self.status_label.config(text=f"已删除裁剪框")
    
    def clear_all_boxes(self):
        """清空所有裁剪框"""
        if not self.crop_boxes and self.crop_start_x is None:
            return
        
        # 清除所有保存的框
        for method, canvas in self.canvases.items():
            if method in self.rect_ids:
                for rect_id in self.rect_ids[method]:
                    canvas.delete(rect_id)
            if method in self.current_rect_ids:
                canvas.delete(self.current_rect_ids[method])
        
        self.crop_boxes.clear()
        self.rect_ids.clear()
        self.current_rect_ids.clear()
        self.boxes_listbox.delete(0, tk.END)
        
        # 清除当前绘制的框
        self.crop_start_x = None
        self.crop_start_y = None
        self.crop_end_x = None
        self.crop_end_y = None
        
        self.coord_label.config(text="(-, -)")
        self.size_label.config(text="- × -")
        self.end_coord_label.config(text="(-, -)")
        self.preview_canvas.delete("all")
        self.mark_workspace_dirty()
        
        self.status_label.config(text="已清空所有裁剪框")
        
    def prev_frame(self):
        """上一帧"""
        if not self.frame_numbers:
            return

        target_index = (self.current_frame_index - 1) % len(self.frame_numbers)
        self.set_current_frame_by_num(self.frame_numbers[target_index])
        
    def next_frame(self):
        """下一帧"""
        if not self.frame_numbers:
            return

        target_index = (self.current_frame_index + 1) % len(self.frame_numbers)
        self.set_current_frame_by_num(self.frame_numbers[target_index])
        
    def jump_to_frame(self):
        """跳转到指定帧号"""
        try:
            frame_num = self.frame_jump_entry.get().zfill(4)  # 补齐到4位
            
            if frame_num in self.frame_numbers:
                self.set_current_frame_by_num(frame_num)
            else:
                messagebox.showwarning("警告", f"帧号 {frame_num} 不存在")
        except Exception as e:
            messagebox.showerror("错误", f"无效的帧号: {str(e)}")
            
    def crop_current_frame(self):
        """裁剪当前帧的所有方法图片（使用所有裁剪框）"""
        if not self.crop_boxes:
            messagebox.showwarning("警告", "请先添加至少一个裁剪框")
            return
        
        if not self.has_output_target():
            messagebox.showwarning("警告", "请先选择输出文件夹")
            return
        
        frame_num = self.frame_numbers[self.current_frame_index]
        success_count, collage_data = self.backend.crop.crop_loaded_images(
            frame_num,
            self.methods,
            self.method_images,
            self.crop_boxes,
            self.output_target,
            self.box_colors,
        )
        
        messagebox.showinfo("完成", f"当前帧批量裁剪完成！\n成功: {success_count} 个方法\n每个方法裁剪了 {len(self.crop_boxes)} 个区域\n输出位置: {self.get_output_display_name()}")
        self.status_label.config(text=f"完成！成功处理 {success_count} 个方法")
    
        if collage_data:
            self.save_current_frame_collage(frame_num, collage_data)
            for item in collage_data:
                item["full"].close()
                for crop_img in item["crops"]:
                    crop_img.close()
    
    def create_visualization_map_image(self, img):
        return self.backend.crop.create_visualization_map_image(img, self.crop_boxes, self.box_colors)
    
    def resize_for_collage(self, img, max_width, max_height):
        return self.backend.crop.resize_for_collage(img, max_width, max_height)
    
    
    def save_current_frame_collage(self, frame_num, collage_data):
        if not self.output_target:
            return
        self.backend.crop.save_current_frame_collage(
            frame_num,
            collage_data,
            self.output_target,
            self.crop_boxes,
        )
    
    def save_visualization_map(self, img, output_folder, frame_num):
        """生成并保存裁剪框可视化标记图（仅框本身）"""
        # 创建图片副本用于绘制
        vis_img = self.create_visualization_map_image(img)
        
        # 绘制每个裁剪框（只绘制框，不绘制数字）
            
            # 绘制矩形框
        
        # 保存可视化图
        vis_path = self.join_output_path(output_folder, f"frame{frame_num}_boxes_map.png")
        self.save_output_image(vis_img, vis_path)
        vis_img.close()
        print(f"已保存可视化标记图: {vis_path}")
        
    def batch_crop_all(self):
        """批量裁剪所有帧（使用所有裁剪框）"""
        if not self.crop_boxes:
            messagebox.showwarning("警告", "请先添加至少一个裁剪框")
            return
        
        if not self.has_output_target():
            messagebox.showwarning("警告", "请先选择输出文件夹")
            return
        
        total_images = len(self.methods) * len(self.frame_numbers)
        
        result = messagebox.askyesno(
            "确认批量裁剪",
            f"即将处理:\n"
            f"- {len(self.methods)} 个方法\n"
            f"- {len(self.frame_numbers)} 帧\n"
            f"- 共 {total_images} 张图片\n"
            f"- 每张图片裁剪 {len(self.crop_boxes)} 个区域\n"
            f"- 总共生成 {total_images * len(self.crop_boxes)} 个裁剪图\n"
            f"输出到: {self.get_output_display_name()}\n\n"
            f"确定继续吗？"
        )
        
        if not result:
            return
        
        success_count = 0
        fail_count = 0
        total = 0
        
        import gc  # 导入垃圾回收模块
        
        for method in self.methods:
            # 创建输出子文件夹
            output_method_folder = self.join_output_path(self.output_target["path"], method)
            
            for frame_num in self.frame_numbers:
                total += 1
                img = None  # 初始化为None
                try:
                    # 更新进度（开始处理）
                    progress_text = f"处理中... {total}/{total_images} ({method} - 帧{frame_num})"
                    self.status_label.config(text=progress_text)
                    self.root.update()

                    img = self.load_method_frame_image(method, frame_num)
                    
                    # 裁剪并保存每个裁剪框
                    for box_idx, (x1, y1, x2, y2) in enumerate(self.crop_boxes, 1):
                        # 检查尺寸
                        if x2 > img.width or y2 > img.height:
                            continue
                        
                        cropped = img.crop((x1, y1, x2, y2))
                        
                        # 保存为PNG格式
                        output_path = self.join_output_path(output_method_folder, f"frame{frame_num}_box{box_idx}.png")
                        self.save_output_image(cropped, output_path)
                    
                    # 生成可视化标记图
                    self.save_visualization_map(img, output_method_folder, frame_num)
                    
                    success_count += 1
                    
                except Exception as e:
                    print(f"处理 {method} 帧 {frame_num} 失败: {e}")
                    fail_count += 1
                
                finally:
                    # 显式释放图片对象
                    if img is not None:
                        img.close()
                        del img
                    
                    # 每10张图片触发一次垃圾回收
                    if total % 10 == 0:
                        gc.collect()
        
        messagebox.showinfo(
            "完成",
            f"批量裁剪完成！\n"
            f"成功: {success_count} 张\n"
            f"失败: {fail_count} 张\n"
            f"输出位置: {self.get_output_display_name()}"
        )
        
        self.status_label.config(text=f"完成！成功 {success_count} 张，失败 {fail_count} 张")
        
    def validate_crop_settings(self):
        """验证裁剪设置"""
        if not self.methods:
            messagebox.showwarning("警告", "请先选择输入文件夹")
            return False
        
        if self.crop_start_x is None or self.crop_end_x is None:
            messagebox.showwarning("警告", "请先选择裁剪区域")
            return False
        
        return True


def main():
    root = tk.Tk()
    app = MultiMethodCropperGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
