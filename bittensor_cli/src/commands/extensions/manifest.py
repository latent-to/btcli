from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from yaml import safe_load, safe_dump

from bittensor_cli.src import defaults

EXTENSIONS_DIR = Path(defaults.config.base_path).expanduser() / "extensions"


@dataclass
class ExtensionManifest:
    name: str
    version: str
    description: str
    entry_point: str
    dependencies: list[str] = field(default_factory=list)
    author: Optional[str] = None
    repository: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: Path) -> "ExtensionManifest":
        manifest_file = path / "extension.yaml"
        if not manifest_file.exists():
            raise FileNotFoundError(f"No extension.yaml found in {path}")
        with open(manifest_file, "r") as f:
            data = safe_load(f) or {}

        required = ["name", "version", "description", "entry_point"]
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(
                f"extension.yaml missing required fields: {', '.join(missing)}"
            )
        return cls(
            name=data["name"],
            version=data["version"],
            description=data["description"],
            entry_point=data["entry_point"],
            dependencies=data.get("dependencies", []),
            author=data.get("author"),
            repository=data.get("repository"),
        )

    def to_yaml(self, path: Path) -> None:
        data = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "entry_point": self.entry_point,
        }
        if self.dependencies:
            data["dependencies"] = self.dependencies
        if self.author:
            data["author"] = self.author
        if self.repository:
            data["repository"] = self.repository
        with open(path / "extension.yaml", "w+") as f:
            safe_dump(data, f, default_flow_style=False, sort_keys=False)


def get_extensions_dir() -> Path:
    EXTENSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return EXTENSIONS_DIR


def get_installed_extensions() -> list[tuple[Path, ExtensionManifest]]:
    ext_dir = get_extensions_dir()
    results = []
    for child in sorted(ext_dir.iterdir()):
        if child.is_dir() and (child / "extension.yaml").exists():
            try:
                manifest = ExtensionManifest.from_yaml(child)
                results.append((child, manifest))
            except (ValueError, FileNotFoundError):
                continue
    return results


def get_extension_by_name(name: str) -> tuple[Path, ExtensionManifest]:
    for path, manifest in get_installed_extensions():
        if manifest.name == name or path.name == name:
            return path, manifest
    raise FileNotFoundError(f"Extension '{name}' not found")
