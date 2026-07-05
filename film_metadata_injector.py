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

# Accepts YAML (YYYY-MM-DD) and EXIF formats with optional time/subsec/timezone
DATE_PATTERN = re.compile(
    r"^\d{4}[-:]\d{2}[-:]\d{2}"
    r"(?:[T\s]+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2})?)?$"
)


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Validate and convert a date string (YYYY-MM-DD or EXIF variants)."""
    if not date_str or not DATE_PATTERN.match(date_str):
        return None
    # Strip optional timezone offset for strptime (Python < 3.7 doesn't support %z with colons)
    clean_str = date_str
    tz_match = re.search(r"([+-]\d{2}):?(\d{2})$", date_str)
    if tz_match:
        clean_str = date_str[:tz_match.start()]
    # Try formats from simplest to most specific
    formats = [
        "%Y-%m-%d",              # YAML: 2023-05-15
        "%Y:%m:%d",              # EXIF date only: 2023:05:15
        "%Y:%m:%d %H:%M:%S",     # EXIF with time: 2023:05:15 10:30:00
        "%Y:%m:%d %H:%M:%S.%f",  # EXIF with subseconds: 2023:05:15 10:30:00.123
        "%Y-%m-%d %H:%M:%S",     # YAML with time: 2023-05-15 10:30:00
        "%Y-%m-%dT%H:%M:%S",     # ISO with T: 2023-05-15T10:30:00
        "%Y-%m-%dT%H:%M:%S.%f",  # ISO with T and subseconds
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(clean_str, fmt).date()
        except ValueError:
            continue
    return None


def to_exif_datetime(date_str: str) -> str:
    """
    Convert YYYY-MM-DD to EXIF datetime format YYYY:MM:DD 00:00:00.
    Preserves time component if present in the input.
    ExifTool accepts many date formats, but DateTimeOriginal requires
    the standard EXIF format for reliable writing.
    """
    # Already in EXIF format (YYYY:MM:DD or YYYY:MM:DD HH:MM:SS or with subseconds)
    if re.match(r"^\d{4}:\d{2}:\d{2}( \d{2}:\d{2}:\d{2}(\.\d+)?)?$", date_str):
        if len(date_str) == 10:
            return date_str + " 00:00:00"
        return date_str
    # YAML date only (YYYY-MM-DD) -> convert to EXIF
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str.replace("-", ":") + " 00:00:00"
    # YAML with time (YYYY-MM-DD HH:MM:SS or with subseconds) -> convert separators only
    time_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})( \d{2}:\d{2}:\d{2}(\.\d+)?)$", date_str)
    if time_match:
        return date_str.replace("-", ":", 2)
    # Fallback: try parse_date (will lose time, but at least won't crash)
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
            encoding="utf-8",
            errors="replace",
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


# Module-level cache for is_scanner_trash results within a single run
_scanner_trash_cache: Dict[Tuple[str, str], bool] = {}


def is_scanner_trash(date_str: str, threshold: datetime.date) -> bool:
    """
    Determine whether a scanner date is garbage (too old to be real).
    Returns True if the date is earlier than the threshold.
    Returns False for unparseable dates (conservative: don't touch what we don't understand).
    Results are cached per (date_str, threshold) to avoid duplicate logging.
    """
    cache_key = (date_str, threshold.isoformat())
    if cache_key in _scanner_trash_cache:
        return _scanner_trash_cache[cache_key]

    # Explicitly treat all-zero dates (common scanner sentinel) as garbage
    if re.match(r"^0000[-:]00[-:]00", date_str):
        logger.debug(f"All-zero date '{date_str}' treated as scanner garbage.")
        _scanner_trash_cache[cache_key] = True
        return True

    parsed = parse_date(date_str)
    if parsed is None:
        logger.warning(
            f"Unparseable date '{date_str}', treating as unknown (not garbage). "
            "If this is a scanner date that should be overwritten, check the format."
        )
        _scanner_trash_cache[cache_key] = False
        return False

    result = parsed < threshold
    _scanner_trash_cache[cache_key] = result
    return result


class MetadataParseError(Exception):
    """Raised when a metadata file cannot be parsed."""
    pass


def parse_yaml(path: Path) -> Dict[str, Any]:
    """Read a YAML file and return a dictionary."""
    if not YAML_AVAILABLE:
        raise MetadataParseError("PyYAML is not installed. Install it with: pip install pyyaml")
    try:
        # Keep scalar booleans (YES/NO/ON/OFF/TRUE/FALSE) as strings.
        class _StringBoolSafeLoader(yaml.SafeLoader):
            pass

        def _bool_as_string(loader, node):
            return node.value

        _StringBoolSafeLoader.add_constructor(
            "tag:yaml.org,2002:bool", _bool_as_string
        )

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f, Loader=_StringBoolSafeLoader)
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise MetadataParseError(f"Invalid YAML file (not a dict): {path}")
        return data
    except yaml.YAMLError as exc:
        raise MetadataParseError(f"Error reading YAML '{path}': {exc}")
    except OSError as exc:
        raise MetadataParseError(f"I/O error reading '{path}': {exc}")


def parse_ini(path: Path) -> Dict[str, str]:
    """
    Read a simple INI file (key=value, no sections) and return a dictionary.
    """
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line_no, line in enumerate(f, start=1):
                # Strip inline comments only after a value has started.
                # Semicolon/colon at the very beginning is still a comment.
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith(";"):
                    continue
                # Remove inline comment starting with " ;" or " #" to avoid
                # accidentally splitting values that contain those characters.
                for marker in (" ;", " #"):
                    if marker in stripped:
                        stripped = stripped.split(marker, 1)[0].rstrip()
                if "=" not in stripped:
                    logger.warning(
                        f"Line {line_no} skipped in '{path}' (no '='): {line.strip()}"
                    )
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes if present (e.g., notes="value" -> value)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                data[key] = value
        return data
    except OSError as exc:
        raise MetadataParseError(f"I/O error reading '{path}': {exc}")


def find_metadata_file(folder: Path) -> Optional[Path]:
    """Look for film-metadata.yaml or film-metadata.ini in a folder."""
    found = []
    for filename in METADATA_FILENAMES:
        candidate = folder / filename
        if candidate.exists():
            found.append(candidate)
    
    if len(found) > 1:
        logger.warning(
            f"Multiple metadata files found in {folder}: "
            f"{', '.join(f.name for f in found)}. "
            f"Using {found[0].name}."
        )
    
    return found[0] if found else None


def get_image_files(folder: Path) -> List[Path]:
    """List supported image files (JPEG/TIFF) in a folder."""
    files: set[Path] = set()
    for f in folder.iterdir():
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            files.add(f)
    return sorted(files)


def get_exif_data(image_path: Path, timeout: int = 60) -> Optional[Dict[str, str]]:
    """
    Read EXIF metadata from an image using ExifTool (JSON output).
    Returns None if reading failed (distinguishes from empty EXIF).
    Tags that can exist in both EXIF and XMP are read from EXIF explicitly
    so the decision logic is deterministic.
    """
    # Read EXIF-specific tags explicitly to avoid ambiguity between EXIF/XMP.
    # Note: ExifTool uses "CreateDate" for the EXIF DateTimeDigitized tag.
    explicit_tags = [
        "-EXIF:Make",
        "-EXIF:Model",
        "-EXIF:ISO",
        "-EXIF:LensModel",
        "-EXIF:DateTimeOriginal",
        "-EXIF:CreateDate",
        "-EXIF:UserComment",
        "-IPTC:Keywords",
        "-XMP-dc:Subject",
    ]
    try:
        result = run_exiftool_with_args_file(
            ["-j", "-a", "-G"] + explicit_tags + [str(image_path)],
            timeout=timeout,
        )
        data = json.loads(result.stdout)
        if data and isinstance(data, list):
            exif = {}
            for k, v in data[0].items():
                if v is None:
                    exif[k] = ""
                elif isinstance(v, list):
                    # Preserve list semantics for keywords by joining only
                    # non-empty items with a separator that is unlikely in values.
                    exif[k] = "\x00".join(str(item) for item in v if str(item))
                else:
                    exif[k] = str(v)
            return exif
        return {}
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not read EXIF from '{image_path}': {exc}")
        return None


# ---------------------------------------------------------------------------
# Metadata mapping
# ---------------------------------------------------------------------------
def build_exif_commands(
    metadata: Dict[str, Any],
    current_exif: Optional[Dict[str, str]],
    threshold: datetime.date,
    dedup_mode: str = "normalize",
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

    # If EXIF read failed, skip all logic that depends on it.
    if current_exif is None:
        return commands
    
    # Capture old Make/Model before we potentially overwrite them
    old_make = current_exif.get("EXIF:Make", "")
    old_model = current_exif.get("EXIF:Model", "")
    
    # Determine if we are ACTUALLY overwriting Make/Model this run
    camera_make = metadata.get("camera_make")
    camera_model = metadata.get("camera_model")
    make_will_change = bool(camera_make and str(camera_make) != old_make)
    model_will_change = bool(camera_model and str(camera_model) != old_model)
    
    # --- scanner_info: ALWAYS extract from existing UserComment first ---
    # This preserves scanner info even when refining camera_make/model in YAML
    scanner_info = ""
    current_uc = current_exif.get("EXIF:UserComment", "")
    
    # Try to extract existing "Scanner: X" from UserComment (re-run safe)
    scanner_match = re.search(r"Scanner:\s*([^|]+)", current_uc)
    if scanner_match:
        scanner_info = scanner_match.group(1).strip()
    
    # Fallback: if no Scanner: in UserComment AND we're overwriting Make/Model,
    # capture old Make/Model as scanner info (first run only)
    if not scanner_info and (make_will_change or model_will_change):
        if old_make or old_model:
            scanner_info = f"{old_make} {old_model}".strip()
    
    # --- camera_make -> EXIF:Make ---
    if camera_make and make_will_change:
        commands.append(("-EXIF:Make", old_make, str(camera_make), "camera_make"))
    
    # --- camera_model -> EXIF:Model ---
    if camera_model and model_will_change:
        commands.append(("-EXIF:Model", old_model, str(camera_model), "camera_model"))

    # --- iso -> EXIF:ISO ---
    iso = metadata.get("iso")
    if iso is not None:
        try:
            iso_val = float(str(iso))
            if iso_val <= 0:
                logger.warning(f"ISO must be positive, got '{iso}'. Ignoring.")
            else:
                iso_int = int(iso_val)
                current_iso = current_exif.get("EXIF:ISO", "")
                if str(iso_int) != current_iso:
                    commands.append(("-EXIF:ISO", current_iso, str(iso_int), "iso"))
        except ValueError:
            logger.warning(f"Invalid ISO value '{iso}', must be numeric. Ignoring.")

    # --- lens -> EXIF:LensModel ---
    lens = metadata.get("lens")
    if lens:
        current_lens = current_exif.get("EXIF:LensModel", "")
        if not current_lens or str(lens) != current_lens:
            commands.append(("-EXIF:LensModel", current_lens, str(lens), "lens"))

    # --- date -> EXIF:DateTimeOriginal (with scanner logic) ---
    date_raw = metadata.get("date")
    date_precision = metadata.get("date_precision", "")
    has_scan_date = bool(metadata.get("scan_date"))
    
    if date_raw and str(date_precision).lower() != "unknown":
        new_date = to_exif_datetime(str(date_raw))
        current_dto = current_exif.get("EXIF:DateTimeOriginal", "")

        if current_dto and is_scanner_trash(current_dto, threshold):
            # Overwrite DateTimeOriginal
            commands.append(
                ("-EXIF:DateTimeOriginal", current_dto, new_date, "date (overwriting scanner garbage)")
            )

            # Move old date to EXIF:CreateDate / XMP:DateTimeDigitized ONLY if no scan_date in YAML
            # If scan_date exists, it takes priority (Bug #4 fix)
            if not has_scan_date:
                current_dtd = current_exif.get("EXIF:CreateDate", "")
                if not current_dtd or is_scanner_trash(current_dtd, threshold):
                    if current_dto != current_dtd and not re.match(r"^0000[-:]00[-:]00", current_dto):
                        commands.append(
                            ("-EXIF:CreateDate", current_dtd, current_dto, "scan_date (moved from old garbage)")
                        )
                        commands.append(
                            ("-XMP-exif:DateTimeDigitized", current_exif.get("XMP:Subject", ""), current_dto, "scan_date (XMP sync)")
                        )
                else:
                    logger.info(
                        f"Real DateTimeDigitized preserved ({current_dtd}); not overwriting."
                    )
        elif current_dto:
            # Scanner date looks real; keep it and warn
            if current_dto == new_date:
                logger.debug("DateTimeOriginal already matches YAML value.")
            else:
                logger.info(
                    f"DateTimeOriginal ({current_dto}) looks real (>= threshold); keeping original. "
                    f"YAML date ({new_date}) not applied."
                )
        else:
            # No existing DateTimeOriginal; write directly
            commands.append(("-EXIF:DateTimeOriginal", "", new_date, "date"))

    # --- scan_date -> EXIF:CreateDate + XMP:DateTimeDigitized ---
    scan_date = metadata.get("scan_date")
    if scan_date:
        scan_date_exif = to_exif_datetime(str(scan_date))
        current_dtd = current_exif.get("EXIF:CreateDate", "")
        if scan_date_exif != current_dtd:
            commands.append(("-EXIF:CreateDate", current_dtd, scan_date_exif, "scan_date"))
            commands.append(("-XMP-exif:DateTimeDigitized", current_exif.get("XMP:Subject", ""), scan_date_exif, "scan_date (XMP sync)"))

    # --- Build comprehensive UserComment ---
    # NOTE: UserComment is rebuilt from scratch on every run. If you remove a
    # field (e.g., 'notes') from the YAML and re-run, it will be deleted from
    # the EXIF. The YAML is the single source of truth for UserComment.
    film = metadata.get("film")
    dev = metadata.get("dev")
    notes = metadata.get("notes")
    if notes is not None:
        notes = str(notes).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    
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
            commands.append(("-EXIF:UserComment", current_uc, new_uc, "comprehensive metadata"))

    # --- film -> IPTC:Keywords + XMP-dc:Subject ---
    if film:
        film_str = str(film)
        # Read raw keyword lists from each metadata family.
        iptc_raw = [kw.strip() for kw in current_exif.get("IPTC:Keywords", "").split("\x00") if kw.strip()]
        xmp_raw = [kw.strip() for kw in current_exif.get("XMP:Subject", "").split("\x00") if kw.strip()]
        # Build unified deduplicated list preserving first-seen order.
        existing_keywords: list[str] = []
        seen: set[str] = set()
        for kw in iptc_raw + xmp_raw:
            if kw not in seen:
                existing_keywords.append(kw)
                seen.add(kw)

        needs_rewrite = False
        if film_str not in seen:
            needs_rewrite = True
            final_keywords = existing_keywords + [film_str]
        else:
            final_keywords = existing_keywords
            # Rewrite if either family differs from the desired final list.
            if iptc_raw != final_keywords or xmp_raw != final_keywords:
                needs_rewrite = True

        if dedup_mode == "normalize" and needs_rewrite:
            commands.append(("-IPTC:Keywords=", current_exif.get("IPTC:Keywords", ""), "", "film (clear Keywords)"))
            for kw in final_keywords:
                commands.append(("-IPTC:Keywords=", "", kw, "film (Keywords)"))
            commands.append(("-XMP-dc:Subject=", current_exif.get("XMP:Subject", ""), "", "film (clear XMP Subject)"))
            for kw in final_keywords:
                commands.append(("-XMP-dc:Subject=", "", kw, "film (XMP Subject)"))
        elif film_str not in seen:
            # Preserve mode (or normalize with no changes needed): only append if absent.
            commands.append(("-Keywords+=", current_exif.get("IPTC:Keywords", ""), film_str, "film (Keywords)"))
            commands.append(("-XMP-dc:Subject+=", current_exif.get("XMP:Subject", ""), film_str, "film (XMP Subject)"))

    return commands


# ---------------------------------------------------------------------------
# Backup and application
# ---------------------------------------------------------------------------
def _compute_timeout(image_paths: List[Path], override: Optional[int] = None) -> int:
    """
    Compute ExifTool timeout. A user override always wins.
    Otherwise scale by the largest file: base 60s + 1s per MB.
    """
    if override is not None:
        return override
    max_bytes = 0
    for p in image_paths:
        try:
            size = p.stat().st_size
            if size > max_bytes:
                max_bytes = size
        except OSError:
            continue
    return 60 + max(0, max_bytes // (1024 * 1024))


def _backup_single_image(img_path: Path, backup_dir: Path, timeout: int = 60) -> Optional[Path]:
    """Backup a single image. Returns img_path on success, None on failure."""
    dest = backup_dir / f"{img_path.name}.exif-backup.json"
    if dest.exists():
        try:
            with open(dest, "r", encoding="utf-8") as f:
                content = f.read()
            if content:
                data = json.loads(content)
                # Validate structure: must be a list with a dict containing key tags
                if (
                    isinstance(data, list)
                    and len(data) > 0
                    and isinstance(data[0], dict)
                    and len(data[0]) >= 3
                ):
                    return img_path  # Already backed up and valid
        except (json.JSONDecodeError, OSError):
            pass  # Invalid or corrupt, will re-create

    try:
        result = run_exiftool_with_args_file(["-j", "-G", "-b", "-a", str(img_path)], timeout=timeout)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(result.stdout)
        logger.debug(f"EXIF backup created: {dest}")
        return img_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        logger.error(f"Failed to backup EXIF for '{img_path}': {exc}")
        return None


def ensure_backup(image_paths: List[Path], backup_dir: Path, workers: int = 1, timeout: int = 60) -> List[Path]:
    """
    Create an EXIF-only backup using ExifTool JSON format.
    Returns list of images successfully backed up.
    Parallelizes backup creation when workers > 1.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    backed_up: List[Path] = []

    if workers > 1 and len(image_paths) > 1:
        logger.info(f"Backing up with {workers} parallel workers...")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(_backup_single_image, img, backup_dir, timeout): img
                for img in image_paths
            }
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    if result:
                        backed_up.append(result)
                except Exception as exc:
                    img_path = futures[fut]
                    logger.error(f"Backup failed for {img_path}: {exc}")
    else:
        for img_path in image_paths:
            result = _backup_single_image(img_path, backup_dir, timeout)
            if result:
                backed_up.append(result)

    return backed_up


def restore_from_backup(folder: Path, timeout: int = 60) -> int:
    """
    Restore EXIF metadata from .film-metadata-injector-backup/ JSON files.
    Returns number of images restored.
    
    IMPORTANT: Restore is a merge-overwrite, not a complete rollback. Tags
    created after the backup (e.g., by other tools) are not removed.
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
            # The JSON stores SourceFile as the absolute path at backup time.
            # If the folder was moved/renamed, ExifTool ignores the entry.
            # Rewrite SourceFile to the current path before importing.
            with open(backup_file, "r", encoding="utf-8") as f:
                backup_data = json.load(f)
            if not isinstance(backup_data, list) or not backup_data:
                logger.warning(f"Invalid backup file, skipping: {backup_file.name}")
                continue
            backup_data[0]["SourceFile"] = str(img_path)

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(backup_data, tmp)
                tmp_path = tmp.name

            try:
                result = run_exiftool_with_args_file(
                    ["-j=" + tmp_path, "-overwrite_original", str(img_path)],
                    timeout=timeout,
                )
                if "1 image files updated" in result.stdout or "1 image files unchanged" in result.stdout:
                    logger.info(f"Restored: {img_name}")
                    restored_count += 1
                elif f"No SourceFile '{img_path}' in imported JSON database" in result.stdout:
                    logger.error(f"Restore failed for {img_name}: SourceFile mismatch")
                else:
                    logger.info(f"Restored: {img_name}")
                    restored_count += 1
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
            logger.error(f"Failed to restore {img_name}: {exc}")

    return restored_count


def apply_exif_commands(image_path: Path, commands: List[Tuple[str, str, str, str]], timeout: int = 60) -> bool:
    """Apply ExifTool commands to an image. Returns True on success."""
    if not commands:
        return True

    args: List[str] = []
    if not logger.isEnabledFor(logging.DEBUG):
        args.append("-q")
    args.append("-overwrite_original")
    for field, _, new_val, _ in commands:
        # Bug A fix: ExifTool operators like += already include = in the field name
        # Don't add another = or we get -Keywords+==value (keyword starts with =)
        if field.endswith("+=") or field.endswith("-=") or field.endswith("="):
            args.append(f"{field}{new_val}")
        else:
            args.append(f"{field}={new_val}")
    args.append(str(image_path))

    try:
        run_exiftool_with_args_file(args, timeout=timeout)
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
    dedup_mode: str = "normalize",
    timeout: int = 60,
) -> Tuple[Path, Optional[List[Tuple[str, str, str, str]]]]:
    """Process a single image and return its commands (None if EXIF read failed)."""
    current_exif = get_exif_data(img_path, timeout)
    if current_exif is None:
        logger.warning(f"Skipping '{img_path}' due to EXIF read failure.")
        return img_path, None
    commands = build_exif_commands(metadata, current_exif, threshold, dedup_mode)
    return img_path, commands


def process_folder(
    folder: Path,
    metadata: Dict[str, Any],
    threshold: datetime.date,
    apply: bool,
    workers: int = 1,
    timeout_override: Optional[int] = None,
    dedup_mode: str = "normalize",
) -> int:
    """
    Process a single film-roll folder.
    Returns the number of images that were (or would be) modified.
    """
    images = get_image_files(folder)
    if not images:
        logger.info(f"No images found in: {folder}")
        return 0

    timeout = _compute_timeout(images, timeout_override)
    logger.debug(f"Using ExifTool timeout of {timeout}s for {folder.name}")

    # Log date_precision once per folder
    date_precision = metadata.get("date_precision")
    if date_precision:
        logger.info(f"date_precision: {date_precision} (not written to EXIF)")

    all_changes: List[Tuple[Path, str, str, str, str]] = []
    cached_results: Dict[Path, List[Tuple[str, str, str, str]]] = {}

    # Phase 1: Analysis (parallel if workers > 1)
    if workers > 1 and len(images) > 1:
        logger.info(f"Analyzing with {workers} parallel workers...")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(process_one_image, img, metadata, threshold, dedup_mode, timeout): img
                for img in images
            }
            for fut in as_completed(futures):
                try:
                    img_path, commands = fut.result()
                    if commands is None:
                        continue
                    if commands:
                        cached_results[img_path] = commands
                        for field, current, new_val, desc in commands:
                            all_changes.append((img_path, field, current, new_val, desc))
                except Exception as exc:
                    img_path = futures[fut]
                    logger.error(f"Failed to analyze {img_path}: {exc}")
    else:
        for img_path in images:
            try:
                _, commands = process_one_image(img_path, metadata, threshold, dedup_mode, timeout)
                if commands is None:
                    continue
                if commands:
                    cached_results[img_path] = commands
                    for field, current, new_val, desc in commands:
                        all_changes.append((img_path, field, current, new_val, desc))
            except Exception as exc:
                logger.error(f"Failed to analyze {img_path}: {exc}")

    if not all_changes:
        logger.info(f"No changes needed in: {folder}")
        return 0

    print_dry_run_table(all_changes)

    if not apply:
        logger.info("Dry-run mode. Use --apply to execute changes.")
        return len(cached_results)

    # Phase 2: Backup before applying (abort if none succeed)
    backup_dir = folder / BACKUP_DIR_NAME
    backed_up = ensure_backup(list(cached_results.keys()), backup_dir, workers, timeout)
    if not backed_up:
        logger.error(f"No backups created for {folder}. Aborting to prevent data loss.")
        return 0

    total_to_modify = len(cached_results)
    backed_count = len(backed_up)
    if backed_count < total_to_modify:
        skipped = total_to_modify - backed_count
        logger.warning(
            f"Backup partial: {backed_count}/{total_to_modify} images backed up. "
            f"{skipped} image(s) will be skipped. Check logs above for details."
        )
    else:
        logger.info(f"All {backed_count} images backed up successfully.")

    # Only apply to images that were successfully backed up
    images_to_apply = {img: cached_results[img] for img in backed_up if img in cached_results}

    # Phase 3: Apply changes (parallel if workers > 1)
    modified_count = 0
    if workers > 1 and len(images_to_apply) > 1:
        logger.info(f"Applying with {workers} parallel workers...")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(apply_exif_commands, img_path, commands, timeout): img_path
                for img_path, commands in images_to_apply.items()
            }
            for fut in as_completed(futures):
                try:
                    img_path = futures[fut]
                    if fut.result():
                        modified_count += 1
                        logger.info(f"Applied: {img_path.name}")
                except Exception as exc:
                    img_path = futures[fut]
                    logger.error(f"Failed to apply {img_path}: {exc}")
    else:
        for img_path, commands in images_to_apply.items():
            try:
                if apply_exif_commands(img_path, commands, timeout):
                    modified_count += 1
                    logger.info(f"Applied: {img_path.name}")
            except Exception as exc:
                logger.error(f"Failed to apply {img_path}: {exc}")

    return modified_count


# ---------------------------------------------------------------------------
# Recursive discovery
# ---------------------------------------------------------------------------
def discover_folders(root: Path, recursive: bool) -> List[Path]:
    """
    Discover folders that contain film-metadata.yaml or film-metadata.ini.
    When recursive=True, also scans subfolders.
    Uses os.walk() for efficiency (avoids creating Path objects for every file).
    """
    folders: List[Path] = []
    if recursive:
        for dirpath, dirnames, _ in os.walk(root):
            # Skip hidden directories (e.g., .git, __pycache__, .film-metadata-injector-backup)
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            folder = Path(dirpath)
            if find_metadata_file(folder):
                folders.append(folder)
    else:
        if find_metadata_file(root):
            folders.append(root)
    return sorted(folders)


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
        "--timeout",
        type=int,
        default=None,
        help="Override ExifTool timeout in seconds. Default scales by file size (60s + 1s/MB).",
    )
    parser.add_argument(
        "--dedup-mode",
        type=str,
        choices=["preserve", "normalize"],
        default="normalize",
        help="Keyword deduplication mode. 'preserve' only appends if absent; "
             "'normalize' rewrites keyword lists to remove duplicates. Default: normalize",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be >= 1")
    return args


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
            # Use os.walk for efficiency and skip hidden directories
            _folders = []
            for dirpath, dirnames, _ in os.walk(args.path):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                folder = Path(dirpath)
                _folders.append(folder)
            target_folders = sorted(_folders)
            if args.path not in target_folders:
                target_folders.insert(0, args.path)

        logger.info(f"Restore mode: scanning {len(target_folders)} folder(s)")
        total_restored = 0
        for folder in target_folders:
            backup_dir = folder / BACKUP_DIR_NAME
            if backup_dir.exists():
                images = get_image_files(folder)
                timeout = _compute_timeout(images, args.timeout)
                restored = restore_from_backup(folder, timeout)
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

        try:
            if meta_file.suffix.lower() in (".yaml", ".yml"):
                metadata = parse_yaml(meta_file)
            else:
                metadata = parse_ini(meta_file)
        except MetadataParseError as exc:
            logger.error(f"Skipping folder {folder}: {exc}")
            continue

        # Basic date validation inside metadata
        for date_field in ("date", "scan_date"):
            raw = metadata.get(date_field)
            if raw and parse_date(str(raw)) is None:
                logger.warning(
                    f"Field '{date_field}' has invalid format in '{meta_file}': '{raw}'. Ignoring."
                )
                metadata.pop(date_field, None)

        modified = process_folder(folder, metadata, threshold, args.apply, args.workers, args.timeout, args.dedup_mode)
        total_modified += modified

    action = "applied" if args.apply else "detected (dry-run)"
    logger.info(f"Total images {action}: {total_modified}")


if __name__ == "__main__":
    main()
