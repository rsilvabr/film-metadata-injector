# Film Metadata Configuration Guide

Place a `film-metadata.yaml` (or `film-metadata.ini`) file inside each folder containing scanned photos. One folder = one film roll.

## Quick Start

1. Copy `film-metadata.yaml` from this folder into your photo folder
2. Edit the values to match your film roll
3. Run the injector: `python film_metadata_injector.py /path/to/folder --apply`

## Field Reference

### camera_make
**What it is:** Camera manufacturer (e.g., Nikon, Canon, Olympus)  
**Where it goes:** EXIF:Make  
**Why overwrite:** Lightroom/Capture One filter by Make/Model. You want to search for "Olympus", not "Noritsu" (the scanner).  
**Example:** `Olympus`

### camera_model
**What it is:** Camera model (e.g., F3, AE-1, Pen-F)  
**Where it goes:** EXIF:Model  
**Example:** `Pen-F`

### film
**What it is:** Film stock name (e.g., Kodak Portra 400, Ilford HP5)  
**Where it goes:** 
- EXIF:UserComment (prefixed with "Film: ")
- IPTC:Keywords (flat, searchable in Lightroom)
**Example:** `Kodak Gold 200`

### iso
**What it is:** Film ISO speed  
**Where it goes:** EXIF:ISO  
**Example:** `200`

### date
**What it is:** Date the roll was shot (YYYY-MM-DD)  
**Where it goes:** EXIF:DateTimeOriginal  
**Special behavior:** If the photo already has a DateTimeOriginal that looks like scanner garbage (before 2015), it gets overwritten. The old date moves to DateTimeDigitized.  
**Example:** `2023-03-01`

### date_precision
**What it is:** How sure you are about the date  
**Values:**
- `roll` = all photos in this folder share the same date (most common)
- `exact` = you know the exact date of each photo
- `unknown` = skip DateTimeOriginal entirely (don't touch existing dates)
**Note:** This field is logged but NOT written to EXIF.

### scan_date
**What it is:** When the film was scanned (optional)  
**Where it goes:** EXIF:DateTimeDigitized  
**Example:** `2024-03-10`

### lens
**What it is:** Lens used (optional)  
**Where it goes:** EXIF:LensModel  
**Example:** `D.Zuiko 38mm f/1.8`

### dev
**What it is:** Development process (optional)  
**Where it goes:** Part of UserComment  
**Examples:** `C-41`, `E-6`, `BW`, `FX-39`

### notes
**What it is:** Any additional notes (optional)  
**Where it goes:** Part of UserComment  
**Example:** `Cycling trip - half frame`

## UserComment Format

The script builds a comprehensive UserComment string:

```
Film: Kodak Gold 200 | Scanner: NORITSU KOKI QSS-32_33 | Dev: C-41 | Notes: Cycling trip - half frame
```

This preserves scanner info while keeping Make/Model clean for software filters.

## YAML vs INI

Use **YAML** if you want:
- Better readability
- Future-proofing (supports lists, nested data)

Use **INI** if you want:
- Quick edits in Notepad
- Simple `key=value` syntax

Both files are included in this folder as examples.

## Example Folder Structure

```
📁 Session_2023-05-15/
   ├── film-metadata.yaml
   ├── photo_001.jpg
   ├── photo_002.tif
   └── ...
```

## Tips

- **Always run dry-run first:** `python film_metadata_injector.py /path/to/folder`
- **Backup is automatic:** EXIF metadata is saved as JSON before any changes
- **Restore if needed:** `python film_metadata_injector.py /path/to/folder --restore`
- **Parallel processing:** Use `--workers 4` for faster processing on SSDs
- **Special characters:** The script handles brackets `[ ]`, Japanese characters, and spaces in paths
