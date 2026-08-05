#!/usr/bin/env python3
"""
Film Metadata Injector - Interactive Menu
Guided wizard wrapper around film_metadata_injector.py, in the style of
jxl_photo.py: numbered menus, step-by-step wizard, persistent settings,
named presets, and a dry-run-first workflow.

Run:  python film_metadata_menu.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
INJECTOR = SCRIPT_DIR / "film_metadata_injector.py"
CONFIG_NAME = ".film_metadata_injector_config.json"

sys.path.insert(0, str(SCRIPT_DIR))
try:
    import film_metadata_injector as fmi
    FMI_AVAILABLE = True
except Exception:
    fmi = None  # type: ignore[assignment]
    FMI_AVAILABLE = False

try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
except ImportError:
    sys.exit("This menu requires 'rich'. Install it with: pip install rich")

console = Console()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class MenuConfig:
    default_workers: int = 4
    default_threshold: str = "2015-01-01"
    default_dedup: str = "normalize"

    last_path: Optional[str] = None
    last_recursive: Optional[bool] = None
    last_workers: Optional[int] = None
    last_threshold: Optional[str] = None
    last_dedup: Optional[str] = None
    last_timeout: Optional[int] = None
    last_cleanup: Optional[bool] = None

    presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)


class ConfigManager:
    def __init__(self) -> None:
        self.config_path = self._get_config_path()
        self.config = MenuConfig()
        self._load()

    def _get_config_path(self) -> Path:
        script_config = SCRIPT_DIR / CONFIG_NAME
        if script_config.exists():
            return script_config
        if platform.system() == "Windows":
            base = Path(os.environ.get("USERPROFILE", str(Path.home())))
        else:
            base = Path.home()
        return base / CONFIG_NAME

    def _load(self) -> None:
        if not self.config_path.exists():
            return
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            valid = {k: v for k, v in data.items() if k in MenuConfig.__dataclass_fields__}
            self.config = MenuConfig(**valid)
        except Exception as exc:
            console.print(f"[yellow]Warning: corrupted config file ({exc}). Using defaults.[/yellow]")
            self.config = MenuConfig()

    def save(self) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self.config), f, indent=2, ensure_ascii=False)
        except OSError as exc:
            console.print(f"[red]Error saving config: {exc}[/red]")

    def session_snapshot(self) -> Dict[str, Any]:
        return {k: getattr(self.config, k)
                for k in MenuConfig.__dataclass_fields__ if k.startswith("last_")}

    def apply_session(self, session: Dict[str, Any]) -> None:
        for k, v in session.items():
            if k.startswith("last_") and k in MenuConfig.__dataclass_fields__:
                setattr(self.config, k, v)
        self.save()


# ---------------------------------------------------------------------------
# Prompt helpers (defaults shown in brackets; Enter accepts)
# ---------------------------------------------------------------------------
def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    raw = console.input(f"[bold cyan]{prompt}{suffix}:[/bold cyan] ").strip()
    raw = raw.strip('"').strip("'")
    return raw if raw else (default or "")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = console.input(f"[bold cyan]{prompt} [{hint}]:[/bold cyan] ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "s", "sim", "1", "true")


def ask_int(prompt: str, default: int, minimum: int = 1) -> int:
    while True:
        raw = ask(prompt, str(default))
        try:
            value = int(raw)
            if value < minimum:
                raise ValueError
            return value
        except ValueError:
            console.print(f"[red]Enter an integer >= {minimum}.[/red]")


def ask_choice(prompt: str, choices: List[str], default: str) -> str:
    label = "/".join(f"[{c}]" if c == default else c for c in choices)
    while True:
        raw = console.input(f"[bold cyan]{prompt} {label}:[/bold cyan] ").strip().lower()
        if not raw:
            return default
        matches = [c for c in choices if c.startswith(raw)]
        if len(matches) == 1:
            return matches[0]
        console.print(f"[red]Choose one of: {', '.join(choices)}[/red]")


def pause() -> None:
    console.input("[dim]Press Enter to continue...[/dim]")


# ---------------------------------------------------------------------------
# Dependency banner
# ---------------------------------------------------------------------------
def check_dependencies() -> bool:
    status: List[str] = []
    ok = True

    try:
        result = subprocess.run(
            ["exiftool", "-ver"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            status.append(f"[green][OK] exiftool {result.stdout.strip()}[/green]")
        else:
            raise RuntimeError
    except Exception:
        status.append("[red][X] exiftool[/red]")
        ok = False

    if importlib.util.find_spec("yaml") is not None:
        status.append("[green][OK] pyyaml[/green]")
    else:
        status.append("[yellow][!] pyyaml (YAML disabled)[/yellow]")

    status.append("[green][OK] rich[/green]")

    injector_ok = INJECTOR.exists() and FMI_AVAILABLE
    status.append(
        "[green][OK] film_metadata_injector.py[/green]"
        if injector_ok else "[red][X] film_metadata_injector.py[/red]"
    )
    ok = ok and injector_ok

    console.print(Panel(" | ".join(status), title="Film Metadata Injector - Environment", box=box.ROUNDED))
    if not ok:
        console.print("[red]Missing required dependencies. Fix the items above and restart.[/red]")
    return ok


# ---------------------------------------------------------------------------
# Run execution
# ---------------------------------------------------------------------------
def build_args(params: Dict[str, Any], apply: bool) -> List[str]:
    args = [str(params["path"])]
    if apply:
        args.append("--apply")
    if params.get("recursive"):
        args.append("--recursive")
    if params.get("workers") and params["workers"] != 1:
        args += ["--workers", str(params["workers"])]
    if params.get("threshold"):
        args += ["--scanner-threshold", str(params["threshold"])]
    if params.get("dedup") and params["dedup"] != "normalize":
        args += ["--dedup-mode", params["dedup"]]
    if params.get("timeout"):
        args += ["--timeout", str(params["timeout"])]
    if params.get("cleanup"):
        args.append("--cleanup-xmp-dtd")
    return args


def run_injector(params: Dict[str, Any], apply: bool) -> int:
    args = build_args(params, apply)
    cmd = [sys.executable, str(INJECTOR)] + args
    console.print(f"\n[dim]$ python film_metadata_injector.py {' '.join(args)}[/dim]\n")
    return subprocess.call(cmd, cwd=str(SCRIPT_DIR))


def save_session(cfg: ConfigManager, params: Dict[str, Any]) -> None:
    c = cfg.config
    c.last_path = str(params["path"])
    c.last_recursive = params.get("recursive")
    c.last_workers = params.get("workers")
    c.last_threshold = params.get("threshold")
    c.last_dedup = params.get("dedup")
    c.last_timeout = params.get("timeout")
    c.last_cleanup = params.get("cleanup")
    cfg.save()


# ---------------------------------------------------------------------------
# Wizard: New run
# ---------------------------------------------------------------------------
def wizard_new_run(cfg: ConfigManager, preset: Optional[Dict[str, Any]] = None) -> None:
    c = cfg.config
    src = preset or {}

    console.print(Panel("Step 1/4 - Folder", box=box.ROUNDED))
    while True:
        path_str = ask("Folder with photos (drag & drop works)",
                       src.get("last_path") or c.last_path or "")
        folder = Path(path_str).expanduser()
        if folder.is_dir():
            break
        console.print("[red]Folder not found. Try again.[/red]")

    recursive = ask_yes_no("Process subfolders recursively?",
                           src.get("last_recursive") if src.get("last_recursive") is not None
                           else (c.last_recursive or False))

    # Metadata presence check
    if FMI_AVAILABLE:
        found = fmi.discover_folders(folder, recursive)
        if not found:
            console.print("[yellow]No film-metadata file found there.[/yellow]")
            if ask_yes_no("Create one now?", True):
                wizard_create_metadata(folder)
                found = fmi.discover_folders(folder, recursive)
            if not found:
                console.print("[red]Still no metadata file - nothing to do.[/red]")
                pause()
                return
        else:
            console.print(f"[green]Found {len(found)} folder(s) with metadata.[/green]")

    console.print(Panel("Step 2/4 - Options", box=box.ROUNDED))
    workers = ask_int("Parallel workers",
                      src.get("last_workers") or c.last_workers or c.default_workers)
    threshold = ask("Scanner-garbage date threshold (YYYY-MM-DD)",
                    src.get("last_threshold") or c.last_threshold or c.default_threshold)
    if FMI_AVAILABLE and fmi.parse_date(threshold) is None:
        console.print(f"[red]Invalid date, using default {c.default_threshold}.[/red]")
        threshold = c.default_threshold
    dedup = ask_choice("Keyword dedup mode", ["normalize", "preserve"],
                       src.get("last_dedup") or c.last_dedup or c.default_dedup)
    # "auto" has to be typeable: ask() returns the default on an empty answer, so
    # once a timeout is stored there would otherwise be no way back to auto.
    timeout_raw = ask("ExifTool timeout in seconds ('auto' to scale by file size)",
                      str(src.get("last_timeout") or c.last_timeout or "auto"))
    timeout = int(timeout_raw) if timeout_raw.isdigit() and int(timeout_raw) > 0 else None
    cleanup = ask_yes_no("Cleanup legacy XMP DateTimeDigitized?",
                         src.get("last_cleanup") or False)

    params: Dict[str, Any] = {
        "path": folder, "recursive": recursive, "workers": workers,
        "threshold": threshold, "dedup": dedup, "timeout": timeout,
        "cleanup": cleanup,
    }

    console.print(Panel("Step 3/4 - Summary", box=box.ROUNDED))
    table = Table(box=box.SIMPLE, show_header=False)
    table.add_column(style="bold")
    table.add_column()
    for label, value in [
        ("Folder", str(folder)),
        ("Recursive", "yes" if recursive else "no"),
        ("Workers", str(workers)),
        ("Threshold", threshold),
        ("Dedup mode", dedup),
        ("Timeout", f"{timeout}s" if timeout else "auto (60s + 1s/MB)"),
        ("Cleanup XMP DTD", "yes" if cleanup else "no"),
    ]:
        table.add_row(label, value)
    console.print(table)

    console.print(Panel("Step 4/4 - Execute", box=box.ROUNDED))
    if not ask_yes_no("Run dry-run now?", True):
        return

    rc = run_injector(params, apply=False)
    save_session(cfg, params)

    if rc != 0:
        console.print(f"[red]Dry-run exited with code {rc}.[/red]")
        pause()
        return

    console.print()
    if ask_yes_no("Apply these changes now? (backup is automatic)", False):
        console.print("[bold]Type YES to confirm writing EXIF:[/bold] ", end="")
        if console.input().strip() == "YES":
            rc = run_injector(params, apply=True)
            if rc == 0:
                console.print("[green]Done.[/green]")
            else:
                console.print(f"[red]Apply finished with failures (exit {rc}). "
                              "Check the log above; backups are in .film-metadata-injector-backup/.[/red]")
        else:
            console.print("[yellow]Apply cancelled.[/yellow]")

    maybe_save_preset(cfg)
    pause()


def maybe_save_preset(cfg: ConfigManager) -> None:
    if ask_yes_no("Save this run as a preset?", False):
        name = ask("Preset name")
        if name:
            cfg.config.presets[name] = cfg.session_snapshot()
            cfg.save()
            console.print(f"[green]Preset '{name}' saved.[/green]")


# ---------------------------------------------------------------------------
# Wizard: Create metadata file
# ---------------------------------------------------------------------------
FIELDS = [
    ("camera_make",    "Camera make",            "Nikon, Canon, Olympus, Pentax, Leica..."),
    ("camera_model",   "Camera model",           "F3, AE-1, Pen-F, K1000, M6..."),
    ("film",           "Film stock",             "Kodak Portra 400, Ilford HP5 Plus 400, Fuji Superia X-TRA 400..."),
    ("iso",            "ISO",                    "100, 200, 400, 800..."),
    ("date",           "Exposure date",          "YYYY-MM-DD (the roll's date)"),
    ("date_precision", "Date precision",         "roll | exact | unknown"),
    ("lens",           "Lens",                   "50mm f/1.4, D.Zuiko 38mm f/1.8..."),
    ("dev",            "Development process",    "C-41, E-6, D-76, HC-110, Xtol..."),
    ("scan_date",      "Scan date",              "YYYY-MM-DD (optional)"),
    ("notes",          "Notes",                  "free text"),
]


def wizard_create_metadata(folder: Optional[Path] = None) -> None:
    console.print(Panel("Create film-metadata file (empty fields are omitted)", box=box.ROUNDED))

    if folder is None:
        while True:
            folder = Path(ask("Target folder")).expanduser()
            if folder.is_dir():
                break
            console.print("[red]Folder not found. Try again.[/red]")

    existing = fmi.find_metadata_file(folder) if FMI_AVAILABLE else None
    if existing:
        console.print(f"[yellow]{existing.name} already exists in this folder.[/yellow]")
        if not ask_yes_no("Overwrite it?", False):
            return

    fmt = ask_choice("Format", ["yaml", "ini"], "yaml")
    filename = folder / f"film-metadata.{fmt}"

    values: Dict[str, str] = {}
    for key, label, hint in FIELDS:
        while True:
            value = ask(f"{label} ({hint})")
            if not value:
                break
            if key in ("date", "scan_date"):
                if FMI_AVAILABLE and fmi.parse_date(value) is None:
                    console.print("[red]Invalid date. Use YYYY-MM-DD (or empty to skip).[/red]")
                    continue
            elif key == "date_precision":
                if value.lower() not in ("roll", "exact", "unknown"):
                    console.print("[red]Use: roll, exact or unknown (or empty to skip).[/red]")
                    continue
                value = value.lower()
            elif key == "iso":
                if not value.isdigit():
                    console.print("[red]ISO must be a number (or empty to skip).[/red]")
                    continue
            values[key] = value
            break

    if not values:
        console.print("[yellow]All fields empty - nothing written.[/yellow]")
        pause()
        return

    lines: List[str] = []
    if fmt == "ini":
        lines.append("; One folder = one film roll")
        for key, _, _ in FIELDS:
            if key in values:
                v = values[key]
                # " ;" starts an inline comment in INI; quote so the reader keeps
                # the whole note instead of truncating it there.
                if " ;" in v:
                    v = '"' + v.replace('"', "'") + '"'
                lines.append(f"{key}={v}")
    else:
        lines.append("# One folder = one film roll")
        for key, _, _ in FIELDS:
            if key in values:
                v = values[key]
                if key == "iso":
                    lines.append(f"{key}: {v}")
                else:
                    # json.dumps produces a valid YAML double-quoted scalar
                    lines.append(f"{key}: {json.dumps(v, ensure_ascii=False)}")

    try:
        filename.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.print(f"[green]Created {filename}[/green]")
    except OSError as exc:
        console.print(f"[red]Failed to write {filename}: {exc}[/red]")
        pause()
        return

    # The user confirmed an overwrite, but picking a different format writes a
    # DIFFERENT filename. Leaving the old file behind means the injector keeps
    # using it (film-metadata.yaml wins over .ini), so everything just typed
    # would be silently ignored.
    if existing and existing.resolve() != filename.resolve():
        try:
            existing.unlink()
            console.print(f"[yellow]Removed the previous {existing.name} (replaced by {filename.name}).[/yellow]")
        except OSError as exc:
            console.print(
                f"[red]Could not remove the old {existing.name}: {exc}. "
                f"Delete it manually - otherwise the injector will keep using it "
                f"instead of {filename.name}.[/red]"
            )

    if not ask_yes_no("Create metadata for another folder?", False):
        return
    wizard_create_metadata()


# ---------------------------------------------------------------------------
# Wizard: Restore
# ---------------------------------------------------------------------------
def wizard_restore(cfg: ConfigManager) -> None:
    console.print(Panel("Restore EXIF from .film-metadata-injector-backup/", box=box.ROUNDED))
    console.print("[dim]Restore is a merge-overwrite, not a full rollback.[/dim]")
    while True:
        folder = Path(ask("Folder to restore", cfg.config.last_path or "")).expanduser()
        if folder.is_dir():
            break
        console.print("[red]Folder not found. Try again.[/red]")

    recursive = ask_yes_no("Restore recursively?", False)
    console.print("[bold]Type YES to confirm restore:[/bold] ", end="")
    if console.input().strip() != "YES":
        console.print("[yellow]Cancelled.[/yellow]")
        pause()
        return

    args = [str(folder), "--restore"]
    if recursive:
        args.append("--recursive")
    cmd = [sys.executable, str(INJECTOR)] + args
    console.print(f"\n[dim]$ python film_metadata_injector.py {' '.join(args)}[/dim]\n")
    rc = subprocess.call(cmd, cwd=str(SCRIPT_DIR))
    if rc != 0:
        console.print(f"[red]Restore exited with code {rc}.[/red]")
    pause()


# ---------------------------------------------------------------------------
# Settings & presets
# ---------------------------------------------------------------------------
def menu_settings(cfg: ConfigManager) -> None:
    c = cfg.config
    while True:
        console.print(Panel(
            f"  1  Default workers          [{c.default_workers}]\n"
            f"  2  Default threshold        [{c.default_threshold}]\n"
            f"  3  Default dedup mode       [{c.default_dedup}]\n"
            f"  4  Reset all settings\n"
            f"  0  Back\n"
            f"[dim]Config file: {cfg.config_path}[/dim]",
            title="Default Settings", box=box.ROUNDED))
        choice = console.input("[bold cyan]>[/bold cyan] ").strip()
        if choice == "1":
            c.default_workers = ask_int("Default workers", c.default_workers)
            cfg.save()
        elif choice == "2":
            t = ask("Default threshold (YYYY-MM-DD)", c.default_threshold)
            if FMI_AVAILABLE and fmi.parse_date(t) is None:
                console.print("[red]Invalid date - not saved.[/red]")
            else:
                c.default_threshold = t
                cfg.save()
        elif choice == "3":
            c.default_dedup = ask_choice("Default dedup mode", ["normalize", "preserve"], c.default_dedup)
            cfg.save()
        elif choice == "4":
            if ask_yes_no("Reset ALL settings and presets?", False):
                cfg.config = MenuConfig()
                cfg.save()
                console.print("[green]Settings reset.[/green]")
                return
        elif choice == "0":
            return


def menu_presets(cfg: ConfigManager) -> None:
    while True:
        presets = cfg.config.presets
        if not presets:
            console.print("[yellow]No presets saved. Run a workflow and save it at the end.[/yellow]")
            pause()
            return
        lines = []
        names = sorted(presets)
        for i, name in enumerate(names, 1):
            p = presets[name]
            lines.append(f"  {i}  {name}  [dim]({p.get('last_path') or '?'})[/dim]")
        lines.append("  d  Delete a preset")
        lines.append("  0  Back")
        console.print(Panel("\n".join(lines), title=f"Presets ({len(names)} saved)", box=box.ROUNDED))
        choice = console.input("[bold cyan]>[/bold cyan] ").strip().lower()
        if choice == "0":
            return
        if choice == "d":
            name = ask("Preset name to delete")
            if cfg.config.presets.pop(name, None) is not None:
                cfg.save()
                console.print(f"[green]Preset '{name}' deleted.[/green]")
            else:
                console.print("[red]Preset not found.[/red]")
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            name = names[int(choice) - 1]
            console.print(f"[green]Running preset '{name}'...[/green]")
            wizard_new_run(cfg, preset=cfg.config.presets[name])
            return
        console.print("[red]Invalid choice.[/red]")


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
def main() -> None:
    cfg = ConfigManager()
    if not check_dependencies():
        sys.exit(1)

    while True:
        last = cfg.config.last_path or "none"
        n_presets = len(cfg.config.presets)
        console.print(Panel(
            f"  1  New run (wizard)\n"
            f"  2  Repeat last run [dim]({last})[/dim]\n"
            f"  3  Create metadata file for a folder\n"
            f"  4  Restore from backup\n"
            f"  5  Edit default settings\n"
            f"  6  Presets ({n_presets} saved)\n"
            f"  7  Check dependencies again\n"
            f"  0  Exit",
            title="Main Menu", box=box.ROUNDED))
        choice = console.input("[bold cyan]>[/bold cyan] ").strip()

        try:
            if choice == "1":
                wizard_new_run(cfg)
            elif choice == "2":
                if not cfg.config.last_path:
                    console.print("[yellow]No previous run saved.[/yellow]")
                else:
                    wizard_new_run(cfg, preset=cfg.session_snapshot())
            elif choice == "3":
                wizard_create_metadata()
            elif choice == "4":
                wizard_restore(cfg)
            elif choice == "5":
                menu_settings(cfg)
            elif choice == "6":
                menu_presets(cfg)
            elif choice == "7":
                check_dependencies()
            elif choice == "0":
                return
        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled - back to menu.[/yellow]")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\nBye.")
        sys.exit(130)
