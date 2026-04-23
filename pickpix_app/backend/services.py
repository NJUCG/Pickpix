from __future__ import annotations

import gc
import glob
import io
import os
import posixpath
import re
import stat
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import paramiko
except ImportError:
    paramiko = None


SourceConfig = dict[str, object]
OutputTarget = dict[str, object]


@dataclass
class ScanResult:
    methods: list[str]
    method_paths: dict[str, str]
    method_sources: dict[str, SourceConfig]
    frame_numbers: list[str]
    methods_with_frames: list[str]
    errors: list[str]


class InputFilenameMatcher:
    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns: list[str] = []
        self.compiled_patterns: list[re.Pattern[str]] = []
        self.set_patterns(patterns or ["frame{number}.exr", "frame{number}.png"])

    def set_patterns(self, patterns: list[str]) -> None:
        self.patterns = [str(pattern).strip() for pattern in patterns if str(pattern).strip()]
        if not self.patterns:
            self.patterns = ["frame{number}.exr", "frame{number}.png"]
        self.compiled_patterns = [self._compile_pattern(pattern) for pattern in self.patterns]

    def _compile_pattern(self, pattern: str) -> re.Pattern[str]:
        regex_parts: list[str] = []
        index = 0

        while index < len(pattern):
            if pattern.startswith("{number}", index):
                regex_parts.append(r"(?P<number>\d+)")
                index += len("{number}")
                continue
            if pattern[index] == "*":
                regex_parts.append(r".+?")
                index += 1
                continue

            regex_parts.append(re.escape(pattern[index]))
            index += 1

        return re.compile("^" + "".join(regex_parts) + "$", re.IGNORECASE)

    def parse_file_name(self, file_name: str) -> tuple[str, str] | None:
        for pattern in self.compiled_patterns:
            match = pattern.match(file_name)
            if match:
                frame_number = match.group("number")
                extension = os.path.splitext(file_name)[1].lstrip(".").lower()
                return frame_number, extension
        return None

    def matches(self, file_name: str) -> bool:
        return self.parse_file_name(file_name) is not None


class RemoteStorageService:
    def __init__(self) -> None:
        self.remote_clients: dict[tuple[str, int, str, str], dict[str, object]] = {}

    @property
    def is_remote_available(self) -> bool:
        return paramiko is not None

    def get_remote_connection_key(self, source: SourceConfig) -> tuple[str, int, str, str]:
        return (
            str(source.get("host", "")),
            int(source.get("port", 22)),
            str(source.get("username", "")),
            str(source.get("password", "")),
        )

    def get_sftp_client(self, source: SourceConfig):
        if paramiko is None:
            raise RuntimeError("paramiko is not installed, SFTP is unavailable")

        connection_key = self.get_remote_connection_key(source)
        cached = self.remote_clients.get(connection_key)
        if cached:
            transport = cached.get("transport")
            if transport is not None and transport.is_active():
                return cached["sftp"]
            self.close_remote_connection(connection_key)

        transport = paramiko.Transport((str(source["host"]), int(source.get("port", 22))))
        transport.connect(username=str(source["username"]), password=str(source.get("password", "")))
        sftp = paramiko.SFTPClient.from_transport(transport)
        self.remote_clients[connection_key] = {"transport": transport, "sftp": sftp}
        return sftp

    def close_remote_connection(self, connection_key: tuple[str, int, str, str]) -> None:
        cached = self.remote_clients.pop(connection_key, None)
        if not cached:
            return
        try:
            cached["sftp"].close()
        except Exception:
            pass
        try:
            cached["transport"].close()
        except Exception:
            pass

    def close_all_remote_connections(self) -> None:
        for connection_key in list(self.remote_clients.keys()):
            self.close_remote_connection(connection_key)

    def ensure_remote_dir(self, sftp, remote_dir: str) -> None:
        remote_dir = remote_dir.rstrip("/")
        if not remote_dir:
            return

        parts = remote_dir.split("/")
        current = "/" if remote_dir.startswith("/") else ""

        for part in parts:
            if not part:
                continue
            if current in ("", "/"):
                current = f"/{part}" if current == "/" else part
            else:
                current = posixpath.join(current, part)
            try:
                sftp.stat(current)
            except IOError:
                sftp.mkdir(current)

    def join_path(self, target: OutputTarget | SourceConfig | None, *parts: str) -> str:
        if not parts:
            return ""

        target_type = target.get("type") if target else "local"
        if target_type == "local":
            return os.path.join(*parts)

        cleaned: list[str] = []
        for idx, part in enumerate(parts):
            if not part:
                continue
            normalized = str(part).replace("\\", "/")
            if idx == 0:
                cleaned.append("/" if normalized == "/" else normalized.rstrip("/"))
            else:
                cleaned.append(normalized.strip("/"))

        if not cleaned:
            return ""

        base = cleaned[0]
        for part in cleaned[1:]:
            base = f"/{part}" if base == "/" else posixpath.join(base, part)
        return base


class ImageService:
    def load_exr_image(self, file_path: str) -> Image.Image:
        if not os.path.exists(file_path):
            raise ValueError(f"file does not exist: {file_path}")

        img_data = cv2.imread(file_path, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
        if img_data is None:
            raise ValueError(f"cv2.imread returned None: {file_path}")

        if len(img_data.shape) == 3:
            img_data = cv2.cvtColor(img_data, cv2.COLOR_BGR2RGB)

        img_data = np.clip(img_data, 0, None)
        img_data = np.power(img_data, 1.0 / 2.2)
        img_data = np.clip(img_data * 255, 0, 255).astype(np.uint8)
        return Image.fromarray(img_data)

    def load_image(self, file_path: str) -> Image.Image:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".exr":
            return self.load_exr_image(file_path)
        if ext in [".png", ".jpg", ".jpeg", ".bmp", ".tiff"]:
            return Image.open(file_path).convert("RGB")
        raise ValueError(f"unsupported image format: {ext}")

    def load_image_bytes(self, file_name: str, data: bytes) -> Image.Image:
        ext = os.path.splitext(file_name)[1].lower()
        if ext == ".exr":
            array = np.frombuffer(data, dtype=np.uint8)
            img_data = cv2.imdecode(array, cv2.IMREAD_ANYCOLOR | cv2.IMREAD_ANYDEPTH)
            if img_data is None:
                raise ValueError(f"failed to decode remote EXR file: {file_name}")
            if len(img_data.shape) == 3:
                img_data = cv2.cvtColor(img_data, cv2.COLOR_BGR2RGB)
            img_data = np.clip(img_data, 0, None)
            img_data = np.power(img_data, 1.0 / 2.2)
            img_data = np.clip(img_data * 255, 0, 255).astype(np.uint8)
            return Image.fromarray(img_data)
        if ext in [".png", ".jpg", ".jpeg", ".bmp", ".tiff"]:
            return Image.open(io.BytesIO(data)).convert("RGB")
        raise ValueError(f"unsupported image format: {ext}")


class ScanService:
    def __init__(self, storage: RemoteStorageService, matcher: InputFilenameMatcher) -> None:
        self.storage = storage
        self.matcher = matcher

    def local_folder_has_frames(self, folder_path: str) -> bool:
        try:
            return any(
                os.path.isfile(os.path.join(folder_path, name)) and self.matcher.matches(name)
                for name in os.listdir(folder_path)
            )
        except OSError:
            return False

    def list_remote_entries(self, source: SourceConfig) -> list[str]:
        sftp = self.storage.get_sftp_client(source)
        return sftp.listdir(str(source["path"]))

    def remote_folder_has_frames(self, source: SourceConfig) -> bool:
        return any(self.matcher.matches(name) for name in self.list_remote_entries(source))

    def remote_is_dir(self, source: SourceConfig) -> bool:
        sftp = self.storage.get_sftp_client(source)
        mode = sftp.stat(str(source["path"])).st_mode
        return stat.S_ISDIR(mode)

    def build_child_source(self, source: SourceConfig, child_name: str) -> SourceConfig:
        child_source = dict(source)
        if source["type"] == "local":
            child_source["path"] = os.path.join(str(source["path"]), child_name)
        else:
            child_source["path"] = posixpath.join(str(source["path"]), child_name)
        return child_source

    def source_has_frames(self, source: SourceConfig, errors: list[str]) -> bool:
        if source["type"] == "local":
            return self.local_folder_has_frames(str(source["path"]))
        if source["type"] == "sftp":
            try:
                return self.remote_folder_has_frames(source)
            except Exception as exc:
                errors.append(f"{source['host']}:{source['path']} - {exc}")
                return False
        return False

    def make_unique_method_name(self, base_name: str, folder_path: str, used_names: set[str]) -> str:
        normalized_path = folder_path.rstrip("/\\")
        base_name = base_name or os.path.basename(normalized_path) or normalized_path
        parent_name = os.path.basename(os.path.dirname(normalized_path)) or "root"
        grandparent_name = os.path.basename(os.path.dirname(os.path.dirname(normalized_path))) or "root"
        candidate = f"{grandparent_name}_{parent_name}_{base_name}"
        if candidate not in used_names:
            return candidate

        index = 2
        while True:
            candidate = f"{grandparent_name}_{parent_name}_{base_name}_{index}"
            if candidate not in used_names:
                return candidate
            index += 1

    def collect_method_folders(self, input_sources: list[SourceConfig]) -> tuple[list[tuple[str, SourceConfig]], list[str]]:
        method_entries: list[tuple[str, SourceConfig]] = []
        errors: list[str] = []
        seen_sources: set[tuple[str, str, int, str]] = set()

        for source in input_sources:
            if source["type"] == "local" and not os.path.isdir(str(source["path"])):
                continue

            source_key = (
                str(source["type"]),
                str(source.get("host", "")),
                int(source.get("port", 22)),
                str(source["path"]),
            )
            if source_key in seen_sources:
                continue

            if self.source_has_frames(source, errors):
                method_entries.append((os.path.basename(str(source["path"])), source))
                seen_sources.add(source_key)
                continue

            if source["type"] == "local":
                child_names = sorted(os.listdir(str(source["path"])))
            else:
                try:
                    child_names = sorted(self.list_remote_entries(source))
                except Exception as exc:
                    errors.append(f"{source['host']}:{source['path']} - {exc}")
                    continue

            for name in child_names:
                child_source = self.build_child_source(source, name)
                child_key = (
                    str(child_source["type"]),
                    str(child_source.get("host", "")),
                    int(child_source.get("port", 22)),
                    str(child_source["path"]),
                )

                if source["type"] == "local":
                    if child_key in seen_sources or not os.path.isdir(str(child_source["path"])):
                        continue
                else:
                    if child_key in seen_sources:
                        continue
                    try:
                        if not self.remote_is_dir(child_source):
                            continue
                    except Exception as exc:
                        errors.append(f"{source['host']}:{child_source['path']} - {exc}")
                        continue

                if self.source_has_frames(child_source, errors):
                    method_entries.append((name, child_source))
                    seen_sources.add(child_key)

        return method_entries, errors

    def list_method_frame_files(self, source: SourceConfig) -> list[str]:
        if source["type"] == "local":
            try:
                return sorted(
                    os.path.join(str(source["path"]), name)
                    for name in os.listdir(str(source["path"]))
                    if os.path.isfile(os.path.join(str(source["path"]), name)) and self.matcher.matches(name)
                )
            except OSError:
                return []

        return [
            posixpath.join(str(source["path"]), name)
            for name in self.list_remote_entries(source)
            if self.matcher.matches(name)
        ]

    def scan(self, input_sources: list[SourceConfig]) -> ScanResult:
        method_entries, errors = self.collect_method_folders(input_sources)

        methods: list[str] = []
        method_paths: dict[str, str] = {}
        method_sources: dict[str, SourceConfig] = {}
        methods_with_frames: list[str] = []
        frame_numbers: set[str] = set()
        used_names: set[str] = set()

        for base_name, source in method_entries:
            method_name = self.make_unique_method_name(base_name, str(source["path"]), used_names)
            used_names.add(method_name)
            methods.append(method_name)
            method_paths[method_name] = str(source["path"])
            method_sources[method_name] = source

        for method in methods:
            source = method_sources[method]
            files = self.list_method_frame_files(source)
            if files:
                methods_with_frames.append(method)
            for file_path in files:
                basename = os.path.basename(file_path) if source["type"] == "local" else posixpath.basename(file_path)
                matched = self.matcher.parse_file_name(basename)
                if matched:
                    frame_numbers.add(matched[0])

        return ScanResult(
            methods=methods,
            method_paths=method_paths,
            method_sources=method_sources,
            frame_numbers=sorted(frame_numbers),
            methods_with_frames=methods_with_frames,
            errors=errors,
        )


class CropService:
    def __init__(self, storage: RemoteStorageService) -> None:
        self.storage = storage

    def has_output_target(self, output_target: OutputTarget | None) -> bool:
        return output_target is not None and bool(output_target.get("path"))

    def get_output_display_name(self, output_target: OutputTarget | None) -> str:
        if not output_target:
            return ""
        if output_target["type"] == "local":
            return str(output_target["path"])
        server_name = output_target.get("server_label", output_target["host"])
        return f"{server_name}:{output_target['path']}"

    def save_output_image(self, img: Image.Image, target_path: str, output_target: OutputTarget) -> None:
        if output_target["type"] == "local":
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            img.save(target_path, "PNG")
            return

        sftp = self.storage.get_sftp_client(output_target)
        self.storage.ensure_remote_dir(sftp, posixpath.dirname(target_path))
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        with sftp.open(target_path, "wb") as remote_file:
            remote_file.write(buffer.getvalue())

    def create_visualization_map_image(
        self,
        img: Image.Image,
        crop_boxes: list[tuple[int, int, int, int]],
        box_colors: list[str],
    ) -> Image.Image:
        vis_img = img.copy()
        draw = ImageDraw.Draw(vis_img)
        for idx, (x1, y1, x2, y2) in enumerate(crop_boxes, 1):
            color = box_colors[(idx - 1) % len(box_colors)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=4)
        return vis_img

    def create_absolute_error_map_image(self, base_img: Image.Image, compare_img: Image.Image) -> Image.Image:
        if base_img.size != compare_img.size:
            raise ValueError(
                f"image size mismatch: {base_img.width}x{base_img.height} vs {compare_img.width}x{compare_img.height}"
            )

        base_array = np.asarray(base_img.convert("RGB"), dtype=np.int16)
        compare_array = np.asarray(compare_img.convert("RGB"), dtype=np.int16)
        diff_array = np.abs(base_array - compare_array).astype(np.uint8)
        return Image.fromarray(diff_array, mode="RGB")

    def resize_for_collage(self, img: Image.Image, max_width: int, max_height: int) -> Image.Image:
        copy_img = img.copy()
        copy_img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        return copy_img

    def save_visualization_map(
        self,
        img: Image.Image,
        output_folder: str,
        frame_num: str,
        output_target: OutputTarget,
        crop_boxes: list[tuple[int, int, int, int]],
        box_colors: list[str],
    ) -> None:
        vis_img = self.create_visualization_map_image(img, crop_boxes, box_colors)
        vis_path = self.storage.join_path(output_target, output_folder, f"frame{frame_num}_boxes_map.png")
        self.save_output_image(vis_img, vis_path, output_target)
        vis_img.close()

    def save_current_frame_collage(
        self,
        frame_num: str,
        collage_data: list[dict[str, object]],
        output_target: OutputTarget,
        crop_boxes: list[tuple[int, int, int, int]],
    ) -> None:
        if not collage_data:
            return

        font = ImageFont.load_default()
        padding = 24
        header_gap = 12
        header_height = 18
        image_gap = 0
        title_gap = 10
        label_gap = 4
        label_height = 14
        separator_gap = 20
        separator_height = 4
        section_title_height = 16
        max_canvas_width = 2200
        max_canvas_height = 1800

        method_count = max(1, len(collage_data))
        section_count = len(crop_boxes) + 1
        base_full_thumb_size = (320, 220)
        base_crop_thumb_size = (220, 220)
        base_width = padding * 2 + max(method_count * base_full_thumb_size[0], method_count * base_crop_thumb_size[0])
        base_height = (
            padding * 2
            + (section_title_height + title_gap + base_full_thumb_size[1] + label_gap + label_height)
            + len(crop_boxes) * (section_title_height + title_gap + base_crop_thumb_size[1] + label_gap + label_height)
            + (section_count - 1) * (separator_gap + separator_height)
        )
        scale = min(1.0, max_canvas_width / base_width, max_canvas_height / base_height)
        scale = max(scale, 0.35)

        padding = max(16, int(padding * max(scale, 0.6)))
        title_gap = max(8, int(title_gap * max(scale, 0.6)))
        label_gap = max(3, int(label_gap * max(scale, 0.6)))
        separator_gap = max(12, int(separator_gap * max(scale, 0.6)))
        separator_height = max(2, int(separator_height * max(scale, 0.6)))
        full_thumb_size = (max(140, int(base_full_thumb_size[0] * scale)), max(96, int(base_full_thumb_size[1] * scale)))
        crop_thumb_size = (max(96, int(base_crop_thumb_size[0] * scale)), max(96, int(base_crop_thumb_size[1] * scale)))

        sections = [("Full Images", [(item["method"], item["full"]) for item in collage_data], full_thumb_size)]
        for box_idx in range(len(crop_boxes)):
            sections.append(
                (
                    f"Box {box_idx + 1}",
                    [(item["method"], item["crops"][box_idx]) for item in collage_data if box_idx < len(item["crops"])],
                    crop_thumb_size,
                )
            )

        prepared_sections: list[dict[str, object]] = []
        canvas_width = 0

        for title, items, thumb_size in sections:
            thumbs = [(method, self.resize_for_collage(image, thumb_size[0], thumb_size[1])) for method, image in items]
            strip_width = sum(thumb.width for _, thumb in thumbs)
            if len(thumbs) > 1:
                strip_width += image_gap * (len(thumbs) - 1)
            max_thumb_height = max((thumb.height for _, thumb in thumbs), default=0)
            strip_height = max_thumb_height + label_gap + label_height
            canvas_width = max(canvas_width, strip_width)
            prepared_sections.append(
                {
                    "title": title,
                    "thumbs": thumbs,
                    "height": section_title_height + title_gap + strip_height,
                    "strip_height": strip_height,
                    "max_thumb_height": max_thumb_height,
                }
            )

        canvas_width += padding * 2
        canvas_height = padding * 2 + header_height + header_gap
        canvas_height += sum(section["height"] for section in prepared_sections)
        canvas_height += separator_gap * (len(prepared_sections) - 1)
        canvas_height += separator_height * (len(prepared_sections) - 1)

        collage = Image.new("RGB", (canvas_width, canvas_height), "white")
        draw = ImageDraw.Draw(collage)
        current_y = padding
        draw.text((padding, current_y), f"Frame {frame_num}", fill="black", font=font)
        current_y += header_height + header_gap

        for section_idx, section in enumerate(prepared_sections):
            draw.text((padding, current_y), str(section["title"]), fill="black", font=font)
            current_y += section_title_height + title_gap

            current_x = padding
            for method, thumb in section["thumbs"]:
                collage.paste(thumb, (current_x, current_y))
                label_box = draw.textbbox((0, 0), str(method), font=font)
                label_width = label_box[2] - label_box[0]
                label_x = current_x + max(0, (thumb.width - label_width) // 2)
                label_y = current_y + int(section["max_thumb_height"]) + label_gap
                draw.text((label_x, label_y), str(method), fill="black", font=font)
                thumb.close()
                current_x += thumb.width + image_gap

            current_y += int(section["strip_height"])

            if section_idx < len(prepared_sections) - 1:
                current_y += separator_gap // 2
                draw.rectangle([padding, current_y, canvas_width - padding, current_y + separator_height], fill="#C8C8C8")
                current_y += separator_height + separator_gap // 2

        collage_path = self.storage.join_path(output_target, str(output_target["path"]), f"frame{frame_num}_summary.png")
        self.save_output_image(collage, collage_path, output_target)
        collage.close()

    def crop_loaded_images(
        self,
        frame_num: str,
        methods: list[str],
        method_images: dict[str, Image.Image],
        crop_boxes: list[tuple[int, int, int, int]],
        output_target: OutputTarget,
        box_colors: list[str],
    ) -> tuple[int, list[dict[str, object]]]:
        success_count = 0
        collage_data: list[dict[str, object]] = []

        for method in methods:
            img = method_images.get(method)
            if img is None:
                continue

            output_method_folder = self.storage.join_path(output_target, str(output_target["path"]), method)
            cropped_images: list[Image.Image] = []

            for box_idx, (x1, y1, x2, y2) in enumerate(crop_boxes, 1):
                cropped = img.crop((x1, y1, x2, y2))
                cropped_images.append(cropped.copy())
                output_path = self.storage.join_path(output_target, output_method_folder, f"frame{frame_num}_box{box_idx}.png")
                self.save_output_image(cropped, output_path, output_target)

            self.save_visualization_map(img, output_method_folder, frame_num, output_target, crop_boxes, box_colors)
            collage_data.append(
                {
                    "method": method,
                    "full": self.create_visualization_map_image(img, crop_boxes, box_colors),
                    "crops": cropped_images,
                }
            )
            success_count += 1

        return success_count, collage_data

    def batch_crop_all(
        self,
        methods: list[str],
        frame_numbers: list[str],
        method_sources: dict[str, SourceConfig],
        crop_boxes: list[tuple[int, int, int, int]],
        output_target: OutputTarget,
        box_colors: list[str],
        load_method_frame_image: Callable[[str, str], Image.Image | None],
        get_frame_image_entry: Callable[[str, str], str | None],
        progress_callback: Callable[[int, int, str, str], None] | None = None,
    ) -> tuple[int, int]:
        success_count = 0
        fail_count = 0
        total = 0
        total_images = len(methods) * len(frame_numbers)

        for method in methods:
            output_method_folder = self.storage.join_path(output_target, str(output_target["path"]), method)

            for frame_num in frame_numbers:
                total += 1
                img = None
                try:
                    if progress_callback:
                        progress_callback(total, total_images, method, frame_num)

                    img_path = get_frame_image_entry(method, frame_num)
                    if img_path is None:
                        fail_count += 1
                        continue

                    img = load_method_frame_image(method, frame_num)
                    if img is None:
                        fail_count += 1
                        continue

                    for box_idx, (x1, y1, x2, y2) in enumerate(crop_boxes, 1):
                        if x2 > img.width or y2 > img.height:
                            continue
                        cropped = img.crop((x1, y1, x2, y2))
                        output_path = self.storage.join_path(output_target, output_method_folder, f"frame{frame_num}_box{box_idx}.png")
                        self.save_output_image(cropped, output_path, output_target)

                    self.save_visualization_map(img, output_method_folder, frame_num, output_target, crop_boxes, box_colors)
                    success_count += 1
                except Exception:
                    fail_count += 1
                finally:
                    if img is not None:
                        img.close()
                    if total % 10 == 0:
                        gc.collect()

        return success_count, fail_count


class PickPixBackend:
    def __init__(self, input_filename_patterns: list[str] | None = None) -> None:
        self.storage = RemoteStorageService()
        self.image = ImageService()
        self.matcher = InputFilenameMatcher(input_filename_patterns)
        self.scan = ScanService(self.storage, self.matcher)
        self.crop = CropService(self.storage)

    def update_input_filename_patterns(self, patterns: list[str]) -> None:
        self.matcher.set_patterns(patterns)

    @property
    def is_remote_available(self) -> bool:
        return self.storage.is_remote_available

    def close(self) -> None:
        self.storage.close_all_remote_connections()

    def get_frame_image_entry(
        self,
        method_sources: dict[str, SourceConfig],
        method: str,
        frame_num: str,
    ) -> str | None:
        source = method_sources.get(method)
        if not source:
            return None

        files = self.scan.list_method_frame_files(source)
        preferred_matches: dict[str, str] = {}

        for file_path in files:
            file_name = os.path.basename(file_path) if source["type"] == "local" else posixpath.basename(file_path)
            matched = self.matcher.parse_file_name(file_name)
            if not matched or matched[0] != frame_num:
                continue
            preferred_matches.setdefault(matched[1], file_path)

        for extension in ["exr", "png"]:
            if extension in preferred_matches:
                return preferred_matches[extension]

        return next(iter(preferred_matches.values()), None)

    def load_method_frame_image(
        self,
        method_sources: dict[str, SourceConfig],
        method: str,
        frame_num: str,
    ) -> Image.Image | None:
        source = method_sources.get(method)
        image_entry = self.get_frame_image_entry(method_sources, method, frame_num)
        if not source or not image_entry:
            return None

        if source["type"] == "local":
            return self.image.load_image(image_entry)

        sftp = self.storage.get_sftp_client(source)
        with sftp.open(image_entry, "rb") as remote_file:
            data = remote_file.read()
        return self.image.load_image_bytes(image_entry, data)
