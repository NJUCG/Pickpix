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
        self.backend = PickPixBackend()
        self.root.title(self.config.title)
        self.root.geometry(self.config.geometry)
        
        # 变量
        self.input_folder = ""
        self.input_folders = []
        self.input_sources = []
        self.output_folder = str(self.config.default_output_dir)
        self.output_target = None
        self.methods = []  # 子文件夹列表（方法名）
        self.method_paths = {}  # {method_name: folder_path}
        self.method_sources = {}  # {method_name: source_config}
        self.last_scan_errors = []
        self.frame_numbers = []  # 帧号列表
        self.current_frame_index = 0
        
        # 每个方法的图片数据
        self.method_images = {}  # {method_name: {frame: PIL.Image}}
        self.display_images = {}  # {method_name: display_image}
        self.photo_images = {}  # {method_name: PhotoImage}
        self.canvases = {}  # {method_name: canvas}
        self.scale_factor = 1.0
        
        # 缩放级别
        self.zoom_level = 1.0  # 缩放倍数
        self.min_zoom = 0.1
        self.max_zoom = 5.0
        
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
        """处理鼠标滚轮事件 - 缩放图片"""
        if not self.method_images:
            return
        
        # 计算缩放因子
        zoom_factor = 1.1 if event.delta > 0 else 0.9
        new_zoom = self.zoom_level * zoom_factor
        
        # 限制缩放范围
        new_zoom = max(self.min_zoom, min(self.max_zoom, new_zoom))
        
        if new_zoom != self.zoom_level:
            self.zoom_level = new_zoom
            # 重新显示所有图片
            for method in self.methods:
                if method in self.method_images:
                    self.display_image_on_canvas(method)
            # 重绘裁剪框
            self.redraw_all_rectangles()
            # 更新状态显示
            self.status_label.config(text=f"缩放: {self.zoom_level:.1f}x")
    
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
        
        tk.Button(control_frame, text="选择输出文件夹", command=self.select_output_folder,
                  bg="#2196F3", fg="white", padx=10).pack(side=tk.LEFT, padx=20)
        tk.Button(control_frame, text="选择远程输出", command=self.open_remote_output_dialog,
                  bg="#455A64", fg="white", padx=10).pack(side=tk.LEFT, padx=5)
        self.output_label = tk.Label(control_frame, text="未选择文件夹", fg="gray")
        self.output_label.pack(side=tk.LEFT, padx=5)
        
        # 缩放控制
        zoom_frame = tk.Frame(control_frame)
        zoom_frame.pack(side=tk.LEFT, padx=20)
        tk.Label(zoom_frame, text="缩放(滚轮):").pack(side=tk.LEFT)
        tk.Button(zoom_frame, text="重置1:1", command=self.reset_zoom, bg="#FF9800", fg="white").pack(side=tk.LEFT, padx=5)
        
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
        
        # 右侧：信息和控制面板
        right_frame = tk.Frame(main_container, width=320)
        right_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right_frame.pack_propagate(False)
        
        # 方法列表
        methods_frame = tk.LabelFrame(right_frame, text="检测到的方法", padx=10, pady=5)
        methods_frame.pack(fill=tk.X, pady=5)
        
        self.methods_listbox = tk.Listbox(methods_frame, height=4)  # 减小高度
        self.methods_listbox.pack(fill=tk.BOTH, expand=True)
        
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
    
    def on_close(self):
        """Clean up connections before closing the app."""
        self.backend.close()
        self.root.destroy()
    
    def clear_input_folders(self):
        """Clear selected input folders and reset related UI state."""
        self.input_folder = ""
        self.input_folders = []
        self.input_sources = []
        self.methods = []
        self.method_paths = {}
        self.method_sources = {}
        self.frame_numbers = []
        self.current_frame_index = 0
        self.close_all_remote_connections()
        for img in self.method_images.values():
            try:
                img.close()
            except:
                pass
        self.method_images.clear()
        self.display_images.clear()
        self.photo_images.clear()
        self.canvases.clear()
        self.rect_ids.clear()
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.preview_canvas.delete("all")
        self.methods_listbox.delete(0, tk.END)
        self.input_label.config(text="未选择文件夹", fg="gray")
        self.frame_info_label.config(text="帧: 0 / 0")
        self.status_label.config(text="已清空输入文件夹")
            
    def select_output_folder(self):
        folder = filedialog.askdirectory(title="选择输出文件夹")
        if folder:
            self.output_folder = folder
            self.output_target = {
                "type": "local",
                "path": folder
            }
            self.output_label.config(text=os.path.basename(folder), fg="black")
    
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
            self.output_label.config(
                text=f"{self.output_target['server_label']}:{self.output_target['path']}",
                fg="black",
            )
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
        return self.backend.get_frame_image_entry(self.method_sources, method, frame_num)
    
    def load_method_frame_image(self, method, frame_num):
        return self.backend.load_method_frame_image(self.method_sources, method, frame_num)
             
    def scan_methods_and_frames(self):
        """扫描子文件夹和帧序列"""
        if not self.input_sources:
            return
        
        result = self.backend.scan.scan(self.input_sources)
        self.last_scan_errors = result.errors
        
        if not result.methods:
            message = "未找到包含 frame*.exr 或 frame*.png 的本地/远程方法路径"
            if self.last_scan_errors:
                message += "\n\n连接错误:\n" + "\n".join(self.last_scan_errors[:3])
            messagebox.showwarning("警告", message)
            return
        
        self.methods = result.methods
        self.method_paths = result.method_paths
        self.method_sources = result.method_sources
        self.frame_numbers = result.frame_numbers
        
        # 更新方法列表
        self.methods_listbox.delete(0, tk.END)
        for method in self.methods:
            self.methods_listbox.insert(tk.END, method)
        
        # 扫描帧号（从所有方法中合并）
        frame_numbers = set()
        methods_with_frames = []
        
        for method in self.methods:
            method_source = self.method_sources[method]
            method_path = self.method_paths[method]
            
            files = self.list_method_frame_files(method_source)
            
            if files:
                methods_with_frames.append(method)
                
            # 提取帧号
            for f in files:
                basename = os.path.basename(f) if method_source["type"] == "local" else posixpath.basename(f)
                match = re.search(r'frame(\d+)\.(exr|png)', basename, re.IGNORECASE)
                if match:
                    frame_numbers.add(match.group(1))
        
        self.frame_numbers = sorted(list(frame_numbers))
        
        if not self.frame_numbers:
            messagebox.showwarning(
                "警告", 
                f"在已选择的文件夹中都未找到 frame*.exr 或 frame*.png 文件\n"
                f"已扫描的方法: {', '.join(self.methods)}"
            )
            return
        
        self.input_label.config(
            text=f"已打开 {len(self.input_sources)} 个输入",
            fg="black"
        )
        self.status_label.config(
            text=f"已加载 {len(self.methods)} 个方法，{len(self.frame_numbers)} 帧 "
                 f"(有帧的方法: {', '.join(methods_with_frames)})"
        )
        
        # 加载第一帧
        self.current_frame_index = 0
        self.load_current_frame()
        
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
        
        # 计算网格布局：每行最多显示的方法数
        cols_per_row = (len(self.methods) + 1) // 2  # 向上取整，分成2行
        
        # 为每个方法创建canvas
        for i, method in enumerate(self.methods):
            # 计算当前方法在网格中的位置
            row = i // cols_per_row  # 行号 (0 或 1)
            col = i % cols_per_row   # 列号
            
            # 创建包含标签和canvas的Frame
            method_frame = tk.Frame(self.scrollable_frame, bd=2, relief=tk.RIDGE)
            method_frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            
            # 方法标签
            tk.Label(method_frame, text=method, font=("Arial", 11, "bold"), 
                    bg="#E3F2FD", pady=5).pack(fill=tk.X)
            
            # 查找图片路径（优先exr，如果不存在则尝试png）
            img_path = self.get_frame_image_entry(method, frame_num)
            
            if img_path is None:
                tk.Label(method_frame, text=f"文件不存在: frame{frame_num}.(exr|png)", 
                        fg="red").pack()
                continue
            
            try:
                # 加载图片（自动识别格式）
                img = self.load_method_frame_image(method, frame_num)
                
                self.method_images[method] = img
                
                # 创建canvas
                canvas = tk.Canvas(method_frame, bg="black", 
                                 height=300, cursor="cross")  # 减小高度以适应两行布局
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
        for r in range(2):
            self.scrollable_frame.grid_rowconfigure(r, weight=1)
        
        # 更新帧信息
        self.frame_info_label.config(
            text=f"帧: {self.current_frame_index + 1} / {len(self.frame_numbers)} (帧号: {frame_num})"
        )
        
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
            canvas_height = 300
        
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
            first_method = self.methods[0]
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
            first_method = self.methods[0]
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
        
        self.status_label.config(text="已清空所有裁剪框")
        
    def prev_frame(self):
        """上一帧"""
        if not self.frame_numbers:
            return
        
        # 清空当前裁剪框选择
        self.clear_all_boxes()
        
        self.current_frame_index = (self.current_frame_index - 1) % len(self.frame_numbers)
        self.load_current_frame()
        
    def next_frame(self):
        """下一帧"""
        if not self.frame_numbers:
            return
        
        # 清空当前裁剪框选择
        self.clear_all_boxes()
        
        self.current_frame_index = (self.current_frame_index + 1) % len(self.frame_numbers)
        self.load_current_frame()
        
    def jump_to_frame(self):
        """跳转到指定帧号"""
        try:
            frame_num = self.frame_jump_entry.get().zfill(4)  # 补齐到4位
            
            if frame_num in self.frame_numbers:
                # 清空当前裁剪框选择
                self.clear_all_boxes()
                
                self.current_frame_index = self.frame_numbers.index(frame_num)
                self.load_current_frame()
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
        success_count = 0
        collage_data = []
        
        for method in self.methods:
            if method not in self.method_images:
                continue
            
            try:
                img = self.method_images[method]
                output_method_folder = self.join_output_path(self.output_target["path"], method)
                cropped_images = []
                
                # 裁剪并保存每个裁剪框
                for box_idx, (x1, y1, x2, y2) in enumerate(self.crop_boxes, 1):
                    cropped = img.crop((x1, y1, x2, y2))
                    cropped_images.append(cropped.copy())
                    
                    # 保存为PNG格式
                    output_path = self.join_output_path(output_method_folder, f"frame{frame_num}_box{box_idx}.png")
                    self.save_output_image(cropped, output_path)
                
                # 生成可视化标记图
                self.save_visualization_map(img, output_method_folder, frame_num)
                collage_data.append({
                    "method": method,
                    "full": self.create_visualization_map_image(img),
                    "crops": cropped_images
                })
                
                success_count += 1
                
            except Exception as e:
                print(f"处理 {method} 帧 {frame_num} 失败: {e}")
        
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
                    self.status_label.config(text=f"处理中... {total}/{total_images} ({method} - 帧{frame_num})")
                    self.root.update()
                    
                    # 查找图片路径（优先exr，如果不存在则尝试png）
                    img_path = self.get_frame_image_entry(method, frame_num)
                    if img_path is None:
                        fail_count += 1
                        continue
                    
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
