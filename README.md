# Film Metadata Injector

CLI tool that injects analog film metadata—read from `film-metadata.yaml` or `film-metadata.ini` files—directly into the EXIF/IPTC/XMP of scanned photos (JPEG/TIFF).

## The Problem

Film scanners (Nikon Coolscan, Epson, Plustek, etc.) often write nonsensical dates into the `DateTimeOriginal` field of scanned images. A photo shot in 2022 may appear in Lightroom as `2001:01:01` because the scanner's internal clock was never set.

This script fixes that intelligently:

- **Detects scanner garbage dates** (default: before 2015)
- **Overwrites with the real exposure date** (from the film roll)
- **Moves the garbage date to `DateTimeDigitized`**, preserving history
- **Injects camera, film stock, ISO, lens, development process, and notes** into EXIF

## Expected Folder Structure

```
📁 Session_2023-05-15/
   ├── film-metadata.yaml
   ├── photo_001.jpg
   ├── photo_002.tif
   └── ...
```

One folder = one film roll. The `film-metadata.yaml` (or `.ini`) inside the folder describes metadata shared by every photo in that folder.

## Metadata File Format

### YAML (`film-metadata.yaml`)

```yaml
camera_make: Nikon
camera_model: F3
film: Kodak Portra 400
iso: 400
date: 2022-08-15          # Exposure date (the roll)
date_precision: roll      # roll | exact | unknown
lens: 50mm f/1.4
dev: C-41
scan_date: 2024-03-10     # Optional: when scanned
notes: "Test shoot at the park"
```

### INI (`film-metadata.ini`) — simple fallback

```ini
camera_make=Nikon
camera_model=F3
film=Kodak Portra 400
iso=400
date=2022-08-15
date_precision=roll
lens=50mm f/1.4
dev=C-41
scan_date=2024-03-10
notes=Test shoot at the park
```

See the `examples/` folder for ready-to-use templates.

### YAML vs INI — Which to use?

| Feature | YAML | INI |
|---------|------|-----|
| Readability | Better for complex data | Simple `key=value` |
| Comments | `# comment` | `; comment` |
| Speed to write | Slower (indentation) | Faster (any text editor) |
| Use case | When you need structure | Quick edits in Notepad |

**INI is recommended** for quick edits — just open Notepad and type `key=value`.  
**YAML** is better if you need advanced features (lists, nested data) in the future.

## Field Mapping

| YAML/INI Field | EXIF/IPTC Destination | Behavior |
|----------------|-----------------------|----------|
| `camera_make` | `EXIF:Make` | Overwrite if different. Old scanner Make/Model saved in `UserComment`. |
| `camera_model` | `EXIF:Model` | Overwrite if different. Old scanner Make/Model saved in `UserComment`. |
| `film` | `EXIF:UserComment` + `IPTC:Keywords` (flat) | Part of comprehensive `UserComment` string |
| `date` | `EXIF:DateTimeOriginal` | **Special scanner logic** (see below) |
| `scan_date` | `EXIF:DateTimeDigitized` | Overwrite if different |
| `lens` | `EXIF:LensModel` | Overwrite if different |
| `iso` | `EXIF:ISO` | Overwrite if different |
| `dev`, `notes` | `EXIF:UserComment` | Part of comprehensive `UserComment` string |
| `date_precision` | **Not written** | Logged only for reference |

### Scanner Date Logic

This is the most important behavior of the script:

1. If the image already has a `DateTimeOriginal` and it is **before the threshold** (default: `2015-01-01`), we treat it as **scanner garbage**.
2. In that case:
   - Overwrite `DateTimeOriginal` with the date from the YAML file.
   - Move the old (garbage) date to `DateTimeDigitized` **only if** `DateTimeDigitized` is empty or also garbage.
   - If `DateTimeDigitized` already exists and is `>= threshold` (a real scan date), **we preserve it** and do not touch it.
3. If the scanner's `DateTimeOriginal` is `>= threshold`, or if there is no `date` in the YAML: **keep the original** and log a warning.
4. If `date` is missing or `date_precision: unknown`: **do not touch** `DateTimeOriginal`.

### Comprehensive UserComment Format

`Make` and `Model` are overwritten with camera info from the YAML because Lightroom/Capture One filter by these fields—you want to search for "Olympus", not "Noritsu". But nothing is lost:

The old scanner info is preserved in `UserComment` as part of a single comprehensive string:

```
Film: Kodak Gold 200 | Scanner: NORITSU KOKI QSS-32_33 | Dev: C-41 | Notes: Cycling trip - half frame
```

This makes it easy to read all metadata at a glance while keeping Make/Model clean for software filters.

### Special Characters in Paths

The script handles paths with brackets `[ ]`, Japanese characters, spaces, and other special characters correctly by passing all arguments to ExifTool via a temporary argument file (`-@` flag). This prevents ExifTool from interpreting brackets as wildcards and ensures Unicode paths work on Windows.

Tested with paths like:
```
E:\film\230309_FilmBW_fomapan100_fx39_梅_野村さん小林さん
```

## Installation

### 1. Install ExifTool

The script depends on [ExifTool](https://exiftool.org/) by Phil Harvey.

- **Windows**: Download the installer from https://exiftool.org/
- **macOS**: `brew install exiftool`
- **Linux**: `sudo apt install libimage-exiftool-perl`

Verify it is on your PATH:

```bash
exiftool -ver
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Or manually:

```bash
pip install pyyaml rich
```

## Usage

### Dry-run (default) — always run this first

```bash
python film_metadata_injector.py /path/to/folder
```

Shows a table of every change that **would** be made without modifying any files.

### Apply changes

```bash
python film_metadata_injector.py /path/to/folder --apply
```

### Additional Options

```bash
python film_metadata_injector.py /path/to/folder \
  --apply \
  --recursive \
  --workers 4 \
  --scanner-threshold 2015-01-01 \
  --verbose
```

| Flag | Description |
|------|-------------|
| `--apply` | **Required** to write EXIF. Without it, dry-run only. |
| `--recursive` | Recursively process subfolders; each folder with its own metadata file. |
| `--workers` | Number of parallel workers for EXIF writing. Default: `1` (sequential). Use `4-8` for faster processing on SSDs. |
| `--scanner-threshold` | Date threshold for treating scanner dates as garbage. Default: `2015-01-01`. |
| `--verbose` | Enable debug logging. |

### Recursive Processing

With `--recursive`, the script scans every subfolder. Each folder that contains a `film-metadata.yaml` or `.ini` is treated as an **independent roll**.

```
📁 Projects/
   ├── 📁 Roll_01/
   │   ├── film-metadata.yaml   # Roll 01 metadata
   │   ├── photo_01.jpg
   │   └── photo_02.jpg
   └── 📁 Roll_02/
       ├── film-metadata.yaml   # Roll 02 metadata
       ├── photo_03.jpg
       └── photo_04.jpg
```

Folders **without** a metadata file are skipped.

## Parallel Processing

By default, the script processes images sequentially (`--workers 1`). For large rolls (100+ photos), parallel processing significantly speeds up the EXIF writing:

```bash
# Process with 4 parallel workers (good for SSDs)
python film_metadata_injector.py /path/to/folder --apply --workers 4

# Process with 8 workers (fast NVMe SSDs)
python film_metadata_injector.py /path/to/folder --apply --workers 8
```

**Recommendations:**
- **HDD**: Use `--workers 1-2` (sequential access is faster)
- **SATA SSD**: Use `--workers 4`
- **NVMe SSD**: Use `--workers 8-16`

The script uses Python's `ThreadPoolExecutor` (same approach as `jxl-photo`), spawning multiple ExifTool processes in parallel. Each worker handles one image at a time.

## Automatic Backup (EXIF-only)

Before any write operation, the script automatically creates a hidden folder `.film-metadata-injector-backup/` inside the photo folder and saves **only the EXIF metadata** as JSON files (not the entire image). This keeps backups tiny (a few KB per photo instead of multi-MB copies).

### Restore from backup

If something goes wrong, restore the original EXIF with ExifTool:

```bash
# Restore single image
exiftool -j=.film-metadata-injector-backup/photo_001.jpg.exif-backup.json -all:all photo_001.jpg

# Restore all images in a folder
for file in .film-metadata-injector-backup/*.json; do
  img=$(basename "$file" .exif-backup.json)
  exiftool -j="$file" -all:all "$img"
done
```

## Supported Formats

- JPEG (`.jpg`, `.jpeg`)
- TIFF (`.tif`, `.tiff`)

Other formats are skipped with a warning.

## Requirements

- Python 3.8+
- ExifTool installed and on PATH
- `pyyaml` (to read `.yaml`)
- `rich` (optional, for colored dry-run tables)

## Example Dry-run Output

```
### Dry-run: Detected changes

| File        | Field            | Current    | New            | Description                   |
|-------------|------------------|------------|----------------|-------------------------------|
| photo_01.jpg| DateTimeOriginal | 2001:01:01 | 2022:08:15     | date (overwriting scanner garbage) |
| photo_01.jpg| DateTimeDigitized|            | 2001:01:01     | scan_date (moved from old garbage) |
| photo_01.jpg| Make             |            | Nikon          | camera_make                   |
| photo_01.jpg| Model            |            | F3             | camera_model                  |
| photo_01.jpg| ISO              |            | 400            | iso                           |
| photo_01.jpg| Keywords         |            | Kodak Portra 400| film (Keywords)              |
| photo_01.jpg| UserComment      |            | Film: Kodak    | comprehensive metadata        |
|             |                  |            | Portra 400 |    |                               |
|             |                  |            | Scanner:       |                               |
|             |                  |            | NORITSU KOKI   |                               |
|             |                  |            | QSS-32_33 |    |                               |
|             |                  |            | Dev: C-41 |    |                               |
```

## Roadmap / Future Ideas

- [ ] Interactive wizard (similar to `convert_tiff.py`)
- [ ] Hierarchical keywords (`Film>Kodak>Portra 400`)
- [ ] Metadata inheritance between folders (child inherits from parent)
- [ ] Lightroom Classic integration (read `.lrcat` catalog)
- [ ] Support for DNG and other scanner RAW formats

## License

MIT License — see [LICENSE](LICENSE).
