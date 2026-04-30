#!/usr/bin/env python3
"""
Film Metadata Injector
Injects analog film metadata (read from text files) into EXIF/IPTC/XMP
of scanned photos (JPEG/TIFF).

Architecture:
    - One folder = one film roll
    - film-metadata.yaml (or .ini) inside the folder defines shared metadata
    - Only JPEG and TIFF are processed; others are skipped with a warning
    - Each folder with metadata is treated as an independent roll
    - No inheritance between parent/child folders
"""

import argparse
import datetime
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

try:
    from rich.console import Console
    from rich.table import Table
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("film_metadata_injector")

if RICH_AVAILABLE:
    console = Console()
else:
    console = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".tif", ".tiff"}
METADATA_FILENAMES = ["film-metadata.yaml", "film-metadata.yml", "film-metadata.ini"]
BACKUP_DIR_NAME = ".film-metadata-injector-backup"
DEFAULT_SCANNER_THRESHOLD = "2015-01-01"

# Accepts both YAML format (YYYY-MM-DD) and EXIF format (YYYY:MM:DD HH:MM:SS)
DATE_PATTERN = re.compile(r"^\d{4}[-:]\d{2}[-:]\d{2}(?:\s+\d{2}:\d{2}:\d{2})?$")


def to_exif_datetime(date_str: str) -> str:
    """
    Convert YYYY-MM-DD to EXIF datetime format YYYY:MM:DD 00:00:00.
    ExifTool accepts many date formats, but DateTimeOriginal requires
    the standard EXIF format for reliable writing.
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return date_str
    return parsed.strftime("%Y:%m:%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def run_exiftool_with_args_file(args: List[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """
    Run ExifTool with arguments passed via -@ file to avoid wildcard issues
    with special characters (brackets, Japanese chars, etc.) in paths.
    Based on jxl-photo bug #5 fix.
    """
    arg_file = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            for arg in args:
                # Sanitize newlines to prevent breaking ExifTool arg-file parsing
                safe_arg = str(arg).replace("\n", " ").replace("\r", " ")
                f.write(safe_arg + "\n")
            arg_file = f.name
        
        result = subprocess.run(
            ["exiftool", "-@", arg_file],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=True,
        )
        return result
    finally:
        if arg_file and os.path.exists(arg_file):
            os.unlink(arg_file)


def error_exit(message: str) -> NoReturn:
    """Log a fatal error and exit."""
    logger.error(message)
    sys.exit(1)


def check_exiftool() -> None:
    """Check that ExifTool is installed and accessible."""
    try:
        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        logger.info(f"ExifTool found: version {result.stdout.strip()}")
    except FileNotFoundError:
        error_exit(
            "ExifTool not found. "
            "Install it from https://exiftool.org/ and make sure it is on PATH."
        )
    except subprocess.CalledProcessError as exc:
        error_exit(f"Error running ExifTool: {exc}")
    except subprocess.TimeoutExpired:
        error_exit("ExifTool did not respond in time.")


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Validate and convert a date string (YYYY-MM-DD or YYYY:MM:DD HH:MM:SS)."""
    if not date_str or not DATE_PATTERN.match(date_str):
        return None
    try:
        # Try YAML format first (YYYY-MM-DD)
        return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        try:
            # Try EXIF format (YYYY:MM:DD HH:MM:SS)
            return datetime.datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S").date()
        except ValueError:
            return None


def is_scanner_trash(date_str: str, threshold: datetime.date) -> bool:
    """
    Determine whether a scanner date is garbage (too old to be real).
    Returns True if the date is earlier than the threshold.
    """
    parsed = parse_date(date_str)
    if parsed is None:
        return True  # Invalid date = garbage
    return parsed < threshold


def parse_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file and return a dictionary."""
    if not YAML_AVAILABLE:
        error_exit("PyYAML is not installed. Install it with: pip install pyyaml")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return {}
        if not isinstance(data, dict):
            error_exit(f"Invalid YAML file (not a dict): {path}")
        return data
    except yaml.YAMLError as exc:
        error_exit(f"Error reading YAML '{path}': {exc}")
    except OSError as exc:
        error_exit(f"I/O error reading '{path}': {exc}")


def parse_ini(path: Path) -> Dict[str, str]:
    """
    Read a simple INI file (key=value, no sections) and return a dictionary.
    """
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    logger.warning(
                        f"Line {line_no} skipped in '{path}' (no '='): {line}"
                    )
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes if present (e.g., notes="value" -> value)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                data[key] = value
        return data
    except OSError as exc:
        error_exit(f"I/O error reading '{path}': {exc}")


def find_metadata_file(folder: Path) -> Optional[Path]:
    """Look for film-metadata.yaml or film-metadata.ini in a folder."""
    for filename in METADATA_FILENAMES:
        candidate = folder / filename
        if candidate.exists():
            return candidate
    return None


def get_image_files(folder: Path) -> List[Path]:
    """List supported image files (JPEG/TIFF) in a folder."""
    files: set[Path] = set()
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.add(f)
    return sorted(files)


def get_exif_data(image_path: Path) -> Dict[str, str]:
    """Read EXIF metadata from an image using ExifTool (JSON output)."""
    try:
        result = run_exiftool_with_args_file(["-j", "-a", str(image_path)], timeout=60)
        data = json.loads(result.stdout)
        if data and isinstance(data, list):
            exif = {}
            for k, v in data[0].items():
                if v is None:
                    exif[k] = ""
                elif k == "Keywords" and isinstance(v, list):
                    # Bug C fix: Keep Keywords as comma-separated string for reliable checking
                    exif[k] = ", ".join(str(item) for item in v)
                else:
                    exif[k] = str(v)
            return exif
        return {}
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not read EXIF from '{image_path}': {exc}")
        return {}


# ---------------------------------------------------------------------------
# Metadata mapping
# ---------------------------------------------------------------------------
def build_exif_commands(
    metadata: Dict[str, Any],
    current_exif: Dict[str, str],
    threshold: datetime.date,
) -> List[Tuple[str, str, str, str]]:
    """
    Build a list of ExifTool commands from the metadata file.
    Returns list of tuples: (exif_field, current_value, new_value, description)
    
    Logic:
    - Make/Model: overwritten with camera info from YAML (searchable in Lightroom)
    - Scanner info: preserved in UserComment (extracted from existing UserComment if re-run)
    - UserComment: comprehensive single string with Film, Scanner, Dev, Notes
    """
    commands: List[Tuple[str, str, str, str]] = []
    
    # Capture old Make/Model before we potentially overwrite them
    old_make = current_exif.get("Make", "")
    old_model = current_exif.get("Model", "")
    
    # Determine if we are ACTUALLY overwriting Make/Model this run
    camera_make = metadata.get("camera_make")
    camera_model = metadata.get("camera_model")
    make_will_change = bool(camera_make and str(camera_make) != old_make)
    model_will_change = bool(camera_model and str(camera_model) != old_model)
    
    # --- scanner_info: ALWAYS extract from existing UserComment first ---
    # This preserves scanner info even when refining camera_make/model in YAML
    scanner_info = ""
    current_uc = current_exif.get("UserComment", "")
    
    # Try to extract existing "Scanner: X" from UserComment (re-run safe)
    if "Scanner:" in current_uc:
        start = current_uc.find("Scanner: ") + len("Scanner: ")
        end = current_uc.find(" |", start)
        if end == -1:
            scanner_info = current_uc[start:]
        else:
            scanner_info = current_uc[start:end]
    
    # Fallback: if no Scanner: in UserComment AND we're overwriting Make/Model,
    # capture old Make/Model as scanner info (first run only)
    if not scanner_info and (make_will_change or model_will_change):
        if old_make or old_model:
            scanner_info = f"{old_make} {old_model}".strip()
    
    # --- camera_make -> EXIF:Make ---
    if camera_make and make_will_change:
        commands.append(("-Make", old_make, str(camera_make), "camera_make"))
    
    # --- camera_model -> EXIF:Model ---
    if camera_model and model_will_change:
        commands.append(("-Model", old_model, str(camera_model), "camera_model"))

    # --- iso -> EXIF:ISO ---
    iso = metadata.get("iso")
    if iso:
        current_iso = current_exif.get("ISO", "")
        if str(iso) != current_iso:
            commands.append(("-ISO", current_iso, str(iso), "iso"))

    # --- lens -> EXIF:LensModel ---
    lens = metadata.get("lens")
    if lens:
        current_lens = current_exif.get("LensModel", "")
        if not current_lens or str(lens) != current_lens:
            commands.append(("-LensModel", current_lens, str(lens), "lens"))

    # --- date -> EXIF:DateTimeOriginal (with scanner logic) ---
    date_raw = metadata.get("date")
    date_precision = metadata.get("date_precision", "")
    has_scan_date = bool(metadata.get("scan_date"))
    
    if date_raw and str(date_precision).lower() != "unknown":
        new_date = to_exif_datetime(str(date_raw))
        current_dto = current_exif.get("DateTimeOriginal", "")

        if current_dto and is_scanner_trash(current_dto, threshold):
            # Overwrite DateTimeOriginal
            commands.append(
                ("-DateTimeOriginal", current_dto, new_date, "date (overwriting scanner garbage)")
            )

            # Move old date to DateTimeDigitized ONLY if no scan_date in YAML
            # If scan_date exists, it takes priority (Bug #4 fix)
            if not has_scan_date:
                current_dtd = current_exif.get("DateTimeDigitized", "")
                if not current_dtd or is_scanner_trash(current_dtd, threshold):
                    if current_dto != current_dtd:
                        commands.append(
                            ("-DateTimeDigitized", current_dtd, current_dto, "scan_date (moved from old garbage)")
                        )
                else:
                    logger.info(
                        f"Real DateTimeDigitized preserved ({current_dtd}); not overwriting."
                    )
        elif current_dto:
            # Scanner date looks real; keep it and warn
            logger.warning(
                f"Scanner DateTimeOriginal ({current_dto}) >= threshold; keeping original. "
                f"YAML date ({new_date}) not applied."
            )
        else:
            # No existing DateTimeOriginal; write directly
            commands.append(("-DateTimeOriginal", "", new_date, "date"))

    # --- scan_date -> EXIF:DateTimeDigitized ---
    scan_date = metadata.get("scan_date")
    if scan_date:
        scan_date_exif = to_exif_datetime(str(scan_date))
        current_dtd = current_exif.get("DateTimeDigitized", "")
        if scan_date_exif != current_dtd:
            commands.append(("-DateTimeDigitized", current_dtd, scan_date_exif, "scan_date"))

    # --- Build comprehensive UserComment ---
    film = metadata.get("film")
    dev = metadata.get("dev")
    notes = metadata.get("notes")
    
    # Build UserComment parts
    uc_parts: List[str] = []
    if film:
        uc_parts.append(f"Film: {film}")
    if scanner_info:
        uc_parts.append(f"Scanner: {scanner_info}")
    if dev:
        uc_parts.append(f"Dev: {dev}")
    if notes:
        uc_parts.append(f"Notes: {notes}")
    
    if uc_parts:
        new_uc = " | ".join(uc_parts)
        # Only update if the new comprehensive string is different
        if new_uc != current_uc:
            commands.append(("-UserComment", current_uc, new_uc, "comprehensive metadata"))

    # --- film -> IPTC:Keywords (Bug #3 fix: use += for proper keyword separation) ---
    if film:
        film_str = str(film)
        current_keywords = current_exif.get("Keywords", "")
        if film_str not in current_keywords:
            # ExifTool: -Keywords+=value adds a keyword (does not create comma-separated string)
            commands.append(("-Keywords+=", current_keywords, film_str, "film (Keywords)"))

    return commands


# ---------------------------------------------------------------------------
# Backup and application
# ---------------------------------------------------------------------------
def ensure_backup(image_paths: List[Path], backup_dir: Path) -> None:
    """
    Create an EXIF-only backup using ExifTool JSON format.
    Much lighter than copying entire image files.
    Restore with: exiftool -j=backup.json -all:all image.jpg
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    for img_path in image_paths:
        dest = backup_dir / f"{img_path.name}.exif-backup.json"
        if not dest.exists():
            try:
                result = run_exiftool_with_args_file(["-j", "-a", str(img_path)], timeout=60)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(result.stdout)
                logger.debug(f"EXIF backup created: {dest}")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                logger.warning(f"Failed to backup EXIF for '{img_path}': {exc}")


def restore_from_backup(folder: Path) -> int:
    """
    Restore EXIF metadata from .film-metadata-injector-backup/ JSON files.
    Returns number of images restored.
    """
    backup_dir = folder / BACKUP_DIR_NAME
    if not backup_dir.exists():
        logger.warning(f"No backup folder found in: {folder}")
        return 0

    backup_files = sorted(backup_dir.glob("*.exif-backup.json"))
    if not backup_files:
        logger.info(f"No backup files found in: {backup_dir}")
        return 0

    restored_count = 0
    for backup_file in backup_files:
        # Extract original filename from backup filename
        # e.g., "photo_001.jpg.exif-backup.json" -> "photo_001.jpg"
        img_name = backup_file.name.removesuffix(".exif-backup.json")
        img_path = folder / img_name

        if not img_path.exists():
            logger.warning(f"Original image not found for backup: {img_name}")
            continue

        try:
            run_exiftool_with_args_file(
                ["-j=" + str(backup_file), "-all:all", "-overwrite_original", str(img_path)],
                timeout=60,
            )
            logger.info(f"Restored: {img_name}")
            restored_count += 1
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            logger.error(f"Failed to restore {img_name}: {exc}")

    return restored_count


def apply_exif_commands(image_path: Path, commands: List[Tuple[str, str, str, str]]) -> bool:
    """Apply ExifTool commands to an image. Returns True on success."""
    if not commands:
        return True

    args: List[str] = ["-q", "-overwrite_original"]
    for field, _, new_val, _ in commands:
        # Bug A fix: ExifTool operators like += already include = in the field name
        # Don't add another = or we get -Keywords+==value (keyword starts with =)
        if field.endswith("+=") or field.endswith("-="):
            args.append(f"{field}{new_val}")
        else:
            args.append(f"{field}={new_val}")
    args.append(str(image_path))

    try:
        run_exiftool_with_args_file(args, timeout=60)
        logger.debug(f"ExifTool OK: {image_path}")
        return True
    except subprocess.CalledProcessError as exc:
        logger.error(f"Failed to write EXIF to '{image_path}': {exc.stderr.strip()}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout writing EXIF to '{image_path}'")
        return False


# ---------------------------------------------------------------------------
# Dry-run and table
# ---------------------------------------------------------------------------
def print_dry_run_table(changes: List[Tuple[Path, str, str, str, str]]) -> None:
    """Print a dry-run table (Rich or plain Markdown fallback)."""
    if not changes:
        logger.info("No changes detected.")
        return

    if RICH_AVAILABLE and console:
        table = Table(title="Dry-run: Detected changes", show_header=True, header_style="bold magenta")
        table.add_column("File", style="cyan", no_wrap=True)
        table.add_column("Field", style="green")
        table.add_column("Current", style="yellow")
        table.add_column("New", style="bright_green")
        table.add_column("Description", style="dim")

        for img_path, field, current, new_val, desc in changes:
            table.add_row(
                str(img_path.name),
                field.lstrip("-"),
                current or "(empty)",
                new_val,
                desc,
            )
        console.print(table)
    else:
        print("\n### Dry-run: Detected changes\n")
        print("| File | Field | Current | New | Description |")
        print("|------|-------|---------|-----|-------------|")
        for img_path, field, current, new_val, desc in changes:
            print(
                f"| {img_path.name} | {field.lstrip('-')} | "
                f"{current or '(empty)'} | {new_val} | {desc} |"
            )
        print()


# ---------------------------------------------------------------------------
# Folder processing
# ---------------------------------------------------------------------------
def process_one_image(
    img_path: Path,
    metadata: Dict[str, Any],
    threshold: datetime.date,
) -> Tuple[Path, List[Tuple[str, str, str, str]]]:
    """Process a single image and return its commands."""
    current_exif = get_exif_data(img_path)
    commands = build_exif_commands(metadata, current_exif, threshold)
    return img_path, commands


def process_folder(
    folder: Path,
    metadata: Dict[str, Any],
    threshold: datetime.date,
    apply: bool,
    recursive: bool,
    workers: int = 1,
) -> int:
    """
    Process a single film-roll folder.
    Returns the number of images that were (or would be) modified.
    """
    images = get_image_files(folder)
    if not images:
        logger.info(f"No images found in: {folder}")
        return 0

    # Log date_precision once per folder
    date_precision = metadata.get("date_precision")
    if date_precision:
        logger.info(f"date_precision: {date_precision} (not written to EXIF)")

    all_changes: List[Tuple[Path, str, str, str, str]] = []
    images_to_modify: List[Path] = []

    # Dry-run analysis: can be parallel too, but keep it simple
    for img_path in images:
        current_exif = get_exif_data(img_path)
        commands = build_exif_commands(metadata, current_exif, threshold)
        if commands:
            images_to_modify.append(img_path)
            for field, current, new_val, desc in commands:
                all_changes.append((img_path, field, current, new_val, desc))

    if not all_changes:
        logger.info(f"No changes needed in: {folder}")
        return 0

    print_dry_run_table(all_changes)

    if not apply:
        logger.info("Dry-run mode. Use --apply to execute changes.")
        return len(images_to_modify)

    # Backup before applying
    backup_dir = folder / BACKUP_DIR_NAME
    ensure_backup(images_to_modify, backup_dir)

    # Apply changes in parallel
    modified_count = 0
    if workers > 1 and len(images_to_modify) > 1:
        logger.info(f"Applying with {workers} parallel workers...")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(process_one_image, img, metadata, threshold): img
                for img in images_to_modify
            }
            for fut in as_completed(futures):
                img_path, commands = fut.result()
                if commands:
                    if apply_exif_commands(img_path, commands):
                        modified_count += 1
                        logger.info(f"Applied: {img_path.name}")
    else:
        # Sequential mode (default or single image)
        for img_path in images_to_modify:
            current_exif = get_exif_data(img_path)
            commands = build_exif_commands(metadata, current_exif, threshold)
            if apply_exif_commands(img_path, commands):
                modified_count += 1
                logger.info(f"Applied: {img_path}")

    return modified_count


# ---------------------------------------------------------------------------
# Recursive discovery
# ---------------------------------------------------------------------------
def discover_folders(root: Path, recursive: bool) -> List[Path]:
    """
    Discover folders that contain film-metadata.yaml or film-metadata.ini.
    When recursive=True, also scans subfolders.
    """
    folders: List[Path] = []
    candidates = [root]
    if recursive:
        candidates = sorted([p for p in root.rglob("*") if p.is_dir()])
        if root not in candidates:
            candidates.insert(0, root)
    else:
        candidates = [root]

    for folder in candidates:
        if find_metadata_file(folder):
            folders.append(folder)

    return folders


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject analog film metadata into EXIF of scanned photos.",
        epilog="Example: python film_metadata_injector.py ./Session_2023-05-15 --apply",
    )
    parser.add_argument("path", type=Path, help="Root folder to process")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively process subfolders (each with its own metadata file)",
    )
    parser.add_argument(
        "--scanner-threshold",
        type=str,
        default=DEFAULT_SCANNER_THRESHOLD,
        help="Date threshold to treat as scanner garbage (YYYY-MM-DD). "
             f"Default: {DEFAULT_SCANNER_THRESHOLD}",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel workers for EXIF writing. "
             "Default: 1 (sequential). Use 4-8 for faster processing on SSDs.",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="Restore EXIF from backups in .film-metadata-injector-backup/ folders",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    args = parse_cli_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Validate path
    if not args.path.exists():
        error_exit(f"Path not found: {args.path}")
    if not args.path.is_dir():
        error_exit(f"Path is not a directory: {args.path}")

    # Validate threshold
    threshold = parse_date(args.scanner_threshold)
    if threshold is None:
        error_exit(
            f"Invalid --scanner-threshold: '{args.scanner_threshold}'. "
            "Use YYYY-MM-DD format."
        )

    # Check dependencies
    check_exiftool()

    # Handle restore mode
    if args.restore:
        target_folders = [args.path]
        if args.recursive:
            target_folders = sorted([p for p in args.path.rglob("*") if p.is_dir()])
            if args.path not in target_folders:
                target_folders.insert(0, args.path)
        
        logger.info(f"Restore mode: scanning {len(target_folders)} folder(s)")
        total_restored = 0
        for folder in target_folders:
            backup_dir = folder / BACKUP_DIR_NAME
            if backup_dir.exists():
                restored = restore_from_backup(folder)
                total_restored += restored
        
        logger.info(f"Total images restored: {total_restored}")
        return

    # Discover folders with metadata
    target_folders = discover_folders(args.path, args.recursive)
    if not target_folders:
        logger.warning(
            f"No metadata file found (looked for: {', '.join(METADATA_FILENAMES)})."
        )
        sys.exit(0)

    logger.info(f"Folders found for processing: {len(target_folders)}")
    total_modified = 0

    for folder in target_folders:
        meta_file = find_metadata_file(folder)
        if meta_file is None:
            continue  # Defensive; should not happen

        logger.info(f"Processing: {folder} (metadata: {meta_file.name})")

        if meta_file.suffix.lower() in (".yaml", ".yml"):
            metadata = parse_yaml(meta_file)
        else:
            metadata = parse_ini(meta_file)

        # Basic date validation inside metadata
        for date_field in ("date", "scan_date"):
            raw = metadata.get(date_field)
            if raw and parse_date(str(raw)) is None:
                logger.warning(
                    f"Field '{date_field}' has invalid format in '{meta_file}': '{raw}'. Ignoring."
                )
                metadata.pop(date_field, None)

        modified = process_folder(folder, metadata, threshold, args.apply, args.recursive, args.workers)
        total_modified += modified

    action = "applied" if args.apply else "detected (dry-run)"
    logger.info(f"Total images {action}: {total_modified}")


if __name__ == "__main__":
    main()
