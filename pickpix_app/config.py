from __future__ import annotations

import ast
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "title": "PickPix Multi Method Cropper",
        "geometry": "1600x1000",
        "max_zoom": 5.0,
    },
    "input": {
        "filename_patterns": [
            "frame{number}.exr",
            "frame{number}.png",
            "*.{number}.exr",
            "*.{number}.png",
        ],
    },
    "servers": {
        "server_1": {
            "label": "Server 1",
            "host": "127.0.0.1",
            "port": 22,
            "username": "user1",
            "password": "change_me_1",
        },
        "server_2": {
            "label": "Server 2",
            "host": "127.0.0.2",
            "port": 22,
            "username": "user2",
            "password": "change_me_2",
        },
    },
    "paths": {
        "project_root": ".",
        "config_dir": "config",
        "frontend_dir": "pickpix_app/frontend",
        "backend_dir": "pickpix_app/backend",
        "default_output_dir": "output",
    },
}


def _simple_yaml_load(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, result)]

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        if not _:
            continue

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()

        current = stack[-1][1]
        parsed_value = value.strip()
        if parsed_value == "":
            child: dict[str, Any] = {}
            current[key] = child
            stack.append((indent, child))
            continue

        if (parsed_value.startswith('"') and parsed_value.endswith('"')) or (
            parsed_value.startswith("'") and parsed_value.endswith("'")
        ):
            parsed_value = parsed_value[1:-1]
        elif parsed_value.startswith("[") and parsed_value.endswith("]"):
            try:
                literal = ast.literal_eval(parsed_value)
                if isinstance(literal, list):
                    parsed_value = literal
            except (SyntaxError, ValueError):
                pass

        current[key] = parsed_value

    return result


def _simple_yaml_dump(data: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_simple_yaml_dump(value, indent + 2).rstrip("\n"))
        elif isinstance(value, list):
            lines.append(f"{prefix}{key}: {value}")
        else:
            lines.append(f"{prefix}{key}: {value}")
    return "\n".join(lines) + "\n"


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


class AppConfig:
    def __init__(self, config_path: str | Path | None = None) -> None:
        self.project_root = self._resolve_runtime_root()
        self.config_path = Path(config_path) if config_path else self.project_root / "config" / "paths.yaml"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_default_config()
        self.data = self._load()

    def _resolve_runtime_root(self) -> Path:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def _ensure_default_config(self) -> None:
        if self.config_path.exists():
            return
        if yaml is not None:
            content = yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False, allow_unicode=True)
        else:
            content = _simple_yaml_dump(DEFAULT_CONFIG)
        self.config_path.write_text(content, encoding="utf-8")

    def _load(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.config_path.exists():
            loaded = self.load_yaml_file(self.config_path)
            if isinstance(loaded, dict):
                raw = loaded
        return _merge_dict(DEFAULT_CONFIG, raw)

    @staticmethod
    def load_yaml_file(file_path: str | Path) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {}

        text = path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text) if yaml is not None else _simple_yaml_load(text)
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def save_yaml_file(file_path: str | Path, data: dict[str, Any]) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if yaml is not None:
            content = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        else:
            content = _simple_yaml_dump(data)
        path.write_text(content, encoding="utf-8")

    @property
    def title(self) -> str:
        return str(self.data["app"]["title"])

    @property
    def geometry(self) -> str:
        return str(self.data["app"]["geometry"])

    @staticmethod
    def _normalize_max_zoom(value: Any) -> float:
        try:
            max_zoom = float(value)
        except (TypeError, ValueError):
            max_zoom = float(DEFAULT_CONFIG["app"]["max_zoom"])
        return max(1.0, max_zoom)

    @staticmethod
    def _normalize_input_patterns(patterns: Any) -> list[str]:
        if isinstance(patterns, str):
            values = patterns.splitlines()
        elif isinstance(patterns, list):
            values = patterns
        else:
            values = []

        normalized: list[str] = []
        for value in values:
            text = str(value).strip()
            if text:
                normalized.append(text)

        return normalized or list(DEFAULT_CONFIG["input"]["filename_patterns"])

    def _write(self) -> None:
        self.save_yaml_file(self.config_path, self.data)

    @property
    def server_presets(self) -> dict[str, dict[str, Any]]:
        presets = self.data.get("servers", {})
        return presets if isinstance(presets, dict) else {}

    def _get_servers_store(self) -> dict[str, dict[str, Any]]:
        servers = self.data.setdefault("servers", {})
        if not isinstance(servers, dict):
            servers = {}
            self.data["servers"] = servers
        return servers

    @staticmethod
    def _normalize_server_port(value: Any) -> int:
        try:
            port = int(value)
        except (TypeError, ValueError):
            port = 22
        return max(1, min(65535, port))

    @staticmethod
    def _make_server_key(label: str, existing_keys: set[str], preferred_key: str | None = None) -> str:
        if preferred_key:
            normalized = re.sub(r"[^a-z0-9_]+", "_", preferred_key.strip().lower()).strip("_")
            if normalized and normalized not in existing_keys:
                return normalized

        base = re.sub(r"[^a-z0-9_]+", "_", label.strip().lower()).strip("_")
        if not base:
            base = "server"
        candidate = base
        index = 2
        while candidate in existing_keys:
            candidate = f"{base}_{index}"
            index += 1
        return candidate

    def normalize_server_preset(self, data: dict[str, Any]) -> dict[str, Any]:
        label = str(data.get("label", "")).strip()
        host = str(data.get("host", "")).strip()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        port = self._normalize_server_port(data.get("port", 22))

        if not label:
            raise ValueError("服务器名称不能为空")
        if not host:
            raise ValueError("服务器地址不能为空")
        if not username:
            raise ValueError("用户名不能为空")

        return {
            "label": label,
            "host": host,
            "port": port,
            "username": username,
            "password": password,
        }

    def list_server_presets(self) -> list[dict[str, Any]]:
        presets: list[dict[str, Any]] = []
        for key, preset in self.server_presets.items():
            if not isinstance(preset, dict):
                continue
            presets.append(
                {
                    "key": str(key),
                    "label": str(preset.get("label", key)),
                    "host": str(preset.get("host", "")),
                    "port": self._normalize_server_port(preset.get("port", 22)),
                    "username": str(preset.get("username", "")),
                    "password": str(preset.get("password", "")),
                }
            )
        return presets

    def get_server_preset(self, server_key: str) -> dict[str, Any] | None:
        preset = self.server_presets.get(server_key)
        if not isinstance(preset, dict):
            return None
        normalized = self.normalize_server_preset({
            "label": preset.get("label", server_key),
            "host": preset.get("host", ""),
            "port": preset.get("port", 22),
            "username": preset.get("username", ""),
            "password": preset.get("password", ""),
        })
        normalized["key"] = str(server_key)
        return normalized

    def save_server_preset(self, data: dict[str, Any], server_key: str | None = None) -> dict[str, Any]:
        normalized = self.normalize_server_preset(data)
        servers = self._get_servers_store()

        key = str(server_key or data.get("key", "")).strip()
        if key and key not in servers:
            key = ""

        if not key:
            existing_keys = set(servers.keys())
            key = self._make_server_key(normalized["label"], existing_keys, str(data.get("key", "")).strip() or None)

        servers[key] = normalized
        self._write()
        return {"key": key, **normalized}

    def delete_server_preset(self, server_key: str) -> bool:
        servers = self._get_servers_store()
        if server_key not in servers:
            return False
        servers.pop(server_key, None)
        self._write()
        return True

    def build_remote_target(self, server_key: str, remote_path: str) -> dict[str, Any] | None:
        preset = self.get_server_preset(server_key)
        if preset is None:
            return None
        return {
            "type": "sftp",
            "host": preset["host"],
            "port": int(preset["port"]),
            "username": preset["username"],
            "password": preset["password"],
            "path": str(remote_path),
            "server_key": preset["key"],
            "server_label": preset["label"],
        }

    @property
    def input_filename_patterns(self) -> list[str]:
        input_config = self.data.get("input", {})
        if not isinstance(input_config, dict):
            return list(DEFAULT_CONFIG["input"]["filename_patterns"])
        return self._normalize_input_patterns(input_config.get("filename_patterns"))

    def save_input_filename_patterns(self, patterns: list[str]) -> None:
        normalized = self._normalize_input_patterns(patterns)
        input_config = self.data.setdefault("input", {})
        if not isinstance(input_config, dict):
            input_config = {}
            self.data["input"] = input_config
        input_config["filename_patterns"] = normalized
        self._write()

    @property
    def max_zoom(self) -> float:
        app_config = self.data.get("app", {})
        if not isinstance(app_config, dict):
            return float(DEFAULT_CONFIG["app"]["max_zoom"])
        return self._normalize_max_zoom(app_config.get("max_zoom"))

    def save_max_zoom(self, max_zoom: float) -> None:
        app_config = self.data.setdefault("app", {})
        if not isinstance(app_config, dict):
            app_config = {}
            self.data["app"] = app_config
        app_config["max_zoom"] = self._normalize_max_zoom(max_zoom)
        self._write()

    def resolve_path(self, key: str) -> Path:
        relative = Path(self.data["paths"][key])
        if relative.is_absolute():
            return relative
        return (self.project_root / relative).resolve()

    @property
    def default_output_dir(self) -> Path:
        path = self.resolve_path("default_output_dir")
        path.mkdir(parents=True, exist_ok=True)
        return path
