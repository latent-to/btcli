import shutil
import subprocess
import sys
from typing import Optional

_GIT_AVAILABLE: Optional[bool] = None


def _check_git() -> bool:
    """Check if git is available on the system."""
    global _GIT_AVAILABLE
    if _GIT_AVAILABLE is None:
        _GIT_AVAILABLE = shutil.which("git") is not None
    return _GIT_AVAILABLE

from rich import box
from rich.table import Table

from bittensor_cli.src.bittensor.utils import (
    console,
    err_console,
)
from bittensor_cli.src.commands.extensions.manifest import (
    ExtensionManifest,
    get_extensions_dir,
    get_installed_extensions,
    get_extension_by_name,
)
from bittensor_cli.src.commands.extensions.templates import (
    EXTENSION_YAML_TEMPLATE,
    MAIN_PY_TEMPLATE,
    TEST_TEMPLATE,
)


async def ext_add(repo_url: str) -> None:
    """Clone a git repository into ~/.bittensor/extensions/ and validate it."""
    ext_dir = get_extensions_dir()

    # Derive directory name from repo URL
    repo_name = repo_url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    target = ext_dir / repo_name
    if target.exists():
        err_console.print(
            f"[red]Error:[/red] Directory '{repo_name}' already exists. "
            f"Use [bold]btcli ext update {repo_name}[/bold] to update it."
        )
        return

    if not _check_git():
        err_console.print(
            "[red]Error:[/red] git is not installed. "
            "Please install git and try again."
        )
        return

    console.print(f"Cloning [bold]{repo_url}[/bold] ...")
    result = subprocess.run(
        ["git", "clone", repo_url, str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err_console.print(f"[red]Error:[/red] git clone failed:\n{result.stderr}")
        return

    # Validate extension.yaml exists
    try:
        manifest = ExtensionManifest.from_yaml(target)
    except (FileNotFoundError, ValueError) as e:
        err_console.print(f"[red]Error:[/red] {e}")
        err_console.print("Removing cloned directory.")
        shutil.rmtree(target, ignore_errors=True)
        return

    # Install dependencies if specified
    if manifest.dependencies:
        console.print("Installing dependencies ...")
        dep_result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + manifest.dependencies,
            capture_output=True,
            text=True,
        )
        if dep_result.returncode != 0:
            err_console.print(
                f"[yellow]Warning:[/yellow] Some dependencies failed to install:\n"
                f"{dep_result.stderr}"
            )

    console.print(
        f"[green]Successfully installed extension "
        f"[bold]{manifest.name}[/bold] v{manifest.version}[/green]"
    )


async def ext_update(name: Optional[str] = None) -> None:
    """Update extension(s) by pulling latest changes from git."""
    if name:
        try:
            path, manifest = get_extension_by_name(name)
        except FileNotFoundError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            return
        extensions = [(path, manifest)]
    else:
        extensions = get_installed_extensions()
        if not extensions:
            console.print("No extensions installed.")
            return

    if not _check_git():
        err_console.print(
            "[red]Error:[/red] git is not installed. "
            "Please install git and try again."
        )
        return

    for path, manifest in extensions:
        console.print(f"Updating [bold]{manifest.name}[/bold] ...")
        result = subprocess.run(
            ["git", "-C", str(path), "pull"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            err_console.print(
                f"[red]Error:[/red] Failed to update {manifest.name}:\n{result.stderr}"
            )
        else:
            console.print(f"[green]Updated {manifest.name}[/green]")


def ext_remove(name: str) -> None:
    """Remove an installed extension."""
    try:
        path, manifest = get_extension_by_name(name)
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        return

    shutil.rmtree(path)
    console.print(f"[green]Removed extension [bold]{manifest.name}[/bold][/green]")


def ext_list() -> None:
    """List all installed extensions."""
    extensions = get_installed_extensions()
    if not extensions:
        console.print("No extensions installed.")
        return

    table = Table(
        title="Installed Extensions",
        box=box.ROUNDED,
        show_lines=True,
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Version")
    table.add_column("Description")
    table.add_column("Entry Point")
    table.add_column("Path", style="dim")

    for path, manifest in extensions:
        table.add_row(
            manifest.name,
            manifest.version,
            manifest.description,
            manifest.entry_point,
            str(path),
        )

    console.print(table)


def ext_create(name: str) -> None:
    """Generate boilerplate for a new extension."""
    ext_dir = get_extensions_dir()
    target = ext_dir / name

    if target.exists():
        err_console.print(
            f"[red]Error:[/red] Directory '{name}' already exists in extensions."
        )
        return

    target.mkdir(parents=True)
    tests_dir = target / "tests"
    tests_dir.mkdir()

    # Write extension.yaml
    (target / "extension.yaml").write_text(EXTENSION_YAML_TEMPLATE.format(name=name))

    # Write main.py
    (target / "main.py").write_text(MAIN_PY_TEMPLATE.format(name=name))

    # Write test file
    safe_name = name.replace("-", "_").replace(" ", "_")
    (tests_dir / f"test_{safe_name}.py").write_text(
        TEST_TEMPLATE.format(safe_name=safe_name)
    )

    console.print(
        f"[green]Created extension boilerplate at [bold]{target}[/bold][/green]"
    )
    console.print(
        f"  Edit [bold]{target / 'extension.yaml'}[/bold] to configure your extension."
    )


async def ext_test(name: Optional[str] = None) -> None:
    """Run tests for extension(s)."""
    if name:
        try:
            path, manifest = get_extension_by_name(name)
        except FileNotFoundError as e:
            err_console.print(f"[red]Error:[/red] {e}")
            return
        extensions = [(path, manifest)]
    else:
        extensions = get_installed_extensions()
        if not extensions:
            console.print("No extensions installed.")
            return

    for path, manifest in extensions:
        tests_dir = path / "tests"
        if not tests_dir.exists():
            console.print(
                f"[yellow]Skipping {manifest.name}: no tests/ directory[/yellow]"
            )
            continue

        console.print(f"Running tests for [bold]{manifest.name}[/bold] ...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-v"],
            cwd=str(path),
        )
        if result.returncode == 0:
            console.print(f"[green]{manifest.name}: all tests passed[/green]")
        else:
            err_console.print(f"[red]{manifest.name}: tests failed[/red]")


async def ext_run(name: str, args: Optional[list[str]] = None) -> None:
    """Run an extension's entry point."""
    try:
        path, manifest = get_extension_by_name(name)
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {e}")
        return

    entry_point = path / manifest.entry_point
    if not entry_point.exists():
        err_console.print(
            f"[red]Error:[/red] Entry point '{manifest.entry_point}' "
            f"not found in {path}"
        )
        return

    cmd = [sys.executable, str(entry_point)] + (args or [])
    result = subprocess.run(cmd, cwd=str(path))
    raise SystemExit(result.returncode)
