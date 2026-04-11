from __future__ import annotations

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

        current[key] = parsed_value

    return result


def _simple_yaml_dump(data: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(_simple_yaml_dump(value, indent + 2).rstrip("\n"))
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
        self.project_root = Path(__file__).resolve().parent.parent
        self.config_path = Path(config_path) if config_path else self.project_root / "config" / "paths.yaml"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_default_config()
        self.data = self._load()

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
            text = self.config_path.read_text(encoding="utf-8")
            loaded = yaml.safe_load(text) if yaml is not None else _simple_yaml_load(text)
            if isinstance(loaded, dict):
                raw = loaded
        return _merge_dict(DEFAULT_CONFIG, raw)

    @property
    def title(self) -> str:
        return str(self.data["app"]["title"])

    @property
    def geometry(self) -> str:
        return str(self.data["app"]["geometry"])

    @property
    def server_presets(self) -> dict[str, dict[str, Any]]:
        presets = self.data.get("servers", {})
        return presets if isinstance(presets, dict) else {}

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
