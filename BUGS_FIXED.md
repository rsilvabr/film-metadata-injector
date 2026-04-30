# Bug Fixes Log

This document tracks all bugs found during code review and how they were fixed.

## Critical Bugs

### Bug #1: `parse_date` rejected EXIF format, breaking `--scanner-threshold`
**Severity:** CRITICAL  
**Location:** `parse_date()` (lines 138-145), `is_scanner_trash()` (lines 148-156)

**What it did:**
The `parse_date()` function only accepted `YYYY-MM-DD` (with hyphens). However, ExifTool returns `DateTimeOriginal` in the format `YYYY:MM:DD HH:MM:SS` (with colons and time component).

When `is_scanner_trash()` called `parse_date()` on an EXIF date like `2020:05:15 10:30:00`:
1. The regex `^\d{4}-\d{2}-\d{2}$` failed (colons instead of hyphens)
2. `parse_date()` returned `None`
3. `is_scanner_trash()` treated it as "invalid date = garbage"
4. The script would overwrite **any** photo with a date, even real ones from 2020+

**Impact:** The entire `--scanner-threshold` feature was non-functional. Real dates were treated as scanner garbage.

**Fix:**
- Updated `DATE_PATTERN` regex to accept both formats: `^\d{4}[-:]\d{2}[-:]\d{2}(?:\s+\d{2}:\d{2}:\d{2})?$`
- Added second `strptime` attempt in `parse_date()` for EXIF format: `%Y:%m:%d %H:%M:%S`

---

### Bug #2: Non-idempotent `scanner_info` destroyed data on re-runs
**Severity:** CRITICAL  
**Location:** `build_exif_commands()` (lines 260-276)

**What it did:**
The `scanner_info` variable was set whenever `old_make` or `old_model` existed, regardless of whether we were actually overwriting them:

```python
# BROKEN: scanner_info set even if we're NOT overwriting
if camera_make:
    if str(camera_make) != old_make:
        commands.append(("-Make", ...))
    if old_make or old_model:  # <-- PROBLEM: always true on re-runs
        scanner_info = f"{old_make} {old_model}".strip()
```

**Trace of destruction:**
1. **First run:** Make=NORITSU, Model=QSS → overwrites to Olympus/Pen-F → `scanner_info = "NORITSU QSS"` ✓
2. **Second run:** Make=Olympus (already changed), Model=Pen-F (already changed) → `scanner_info = "Olympus Pen-F"` → overwrites UserComment with wrong info ❌

The scanner information was permanently lost on the second run.

**Fix:**
- `scanner_info` is now only captured when we are **actually** overwriting Make or Model
- On re-runs, the script extracts existing "Scanner: X" from the current `UserComment` to preserve it
- Added `make_will_change` and `model_will_change` flags to track real changes

---

## High Severity Bugs

### Bug #3: Keywords created as comma-separated string instead of separate keywords
**Severity:** HIGH  
**Location:** `build_exif_commands()` (lines 358-363)

**What it did:**
The script wrote `-Keywords="Kodak Portra 400, Kodak Gold 200"` which ExifTool interpreted as a **single keyword** containing a comma, instead of two separate keywords.

**Impact:** Lightroom/Capture One would see one keyword "Kodak Portra 400, Kodak Gold 200" instead of individual keywords. Filtering by "Portra" would fail.

**Fix:**
- Changed from `-Keywords="value"` to `-Keywords+=value`
- ExifTool's `+=` operator properly appends as a separate keyword entry
- Each film stock is now a distinct, searchable keyword

---

### Bug #4: Conflicting `DateTimeDigitized` commands when `scan_date` + garbage DTO coexist
**Severity:** HIGH  
**Location:** `build_exif_commands()` (lines 305-311, 326-332)

**What it did:**
When the YAML had both `date` (with a garbage DTO to overwrite) AND `scan_date`, two conflicting `-DateTimeDigitized=` commands were generated:

1. Move old garbage DTO to DTD: `-DateTimeDigitized=2020:01:01`
2. Write scan_date to DTD: `-DateTimeDigitized=2024:03:10`

ExifTool processes left-to-right (last wins), so `scan_date` overwrote the moved garbage. But the dry-run table showed both operations, which was confusing and incorrect.

**Fix:**
- If `scan_date` exists in YAML, skip the "move garbage to DTD" logic entirely
- `scan_date` takes priority when explicitly provided
- Garbage DTO is still overwritten in DateTimeOriginal, but not moved to DTD

---

## Medium Severity Bugs

### Bug #5: Wrong `scanner_info` when only `camera_make` or `camera_model` is in YAML
**Severity:** MEDIUM  
**Location:** `build_exif_commands()` (lines 260-276)

**What it did:**
If YAML only had `camera_make: Olympus` (without `camera_model`), the script would:
- Overwrite Make to Olympus
- Leave Model as QSS (scanner model)
- Set `scanner_info = "NORITSU QSS"` (old Make + current Model)

This created misleading information: "Scanner: NORITSU QSS" when the actual Model in the file was QSS (not overwritten).

**Fix:**
- `scanner_info` is only built when **both** fields are being overwritten, or when we can definitively identify the scanner
- Extract existing scanner info from UserComment on re-runs instead of rebuilding

---

### Bug #6: BOM in INI files broke the first key on Windows Notepad
**Severity:** MEDIUM  
**Location:** `parse_ini()` (line 183)

**What it did:**
Windows Notepad saves UTF-8 with BOM (Byte Order Mark) by default. The BOM characters (`\ufeff`) were prepended to the first key name, making it `\ufeffcamera_make` instead of `camera_make`. The key was never found.

**Impact:** Users editing INI files in Notepad (as advertised in README) would have their first field ignored.

**Fix:**
- Changed encoding from `"utf-8"` to `"utf-8-sig"`
- Python's `utf-8-sig` codec automatically strips BOM if present

---

### Bug #7: Mixed-case extensions not detected on Linux
**Severity:** MEDIUM  
**Location:** `get_image_files()` (lines 209-220)

**What it did:**
The code searched for `*.jpg`, `*.JPG`, `*.tif`, `*.TIF` but missed variants like `.Jpg`, `.jPg`, `.TiFf`, etc.

**Impact:** On Linux (case-sensitive filesystem), files with mixed-case extensions were silently skipped.

**Fix:**
- Replaced glob patterns with `folder.iterdir()` + `f.suffix.lower() in SUPPORTED_EXTENSIONS`
- Now catches ALL case variants: `.jpg`, `.JPG`, `.Jpg`, `.jpeg`, `.JPEG`, etc.

---

## Low Severity Bugs

### Bug #8: EXIF read twice per image (performance)
**Severity:** LOW  
**Location:** `process_folder()` (lines 543-573)

**What it did:**
Each image's EXIF was read twice: once during dry-run analysis, and again during apply.

**Impact:** Double the ExifTool calls. For 100 images, 200 calls instead of 100.

**Fix:**
- Stored results from first EXIF read and reused them during apply
- Eliminated redundant ExifTool subprocess calls

---

### Bug #9: Initial analysis was always sequential, ignoring `--workers`
**Severity:** LOW  
**Location:** `process_folder()` (lines 543-573)

**What it did:**
Even with `--workers 8`, the dry-run analysis loop ran sequentially. Only the apply phase used parallel workers.

**Impact:** Large rolls (100+ photos) were slow to analyze before applying.

**Fix:**
- Added `ThreadPoolExecutor` to the analysis phase as well
- Both analysis and apply now respect `--workers`

---

### Bug #10: `.yml` extension not in `METADATA_FILENAMES`
**Severity:** LOW  
**Location:** `METADATA_FILENAMES` (line 62), `main()` (line 739)

**What it did:**
The parser accepted `.yml` files, but `find_metadata_file()` never looked for them because `.yml` wasn't in `METADATA_FILENAMES`.

**Fix:**
- Added `"film-metadata.yml"` to `METADATA_FILENAMES`
- Updated error message to list all supported formats

---

### Bug #11: Temp file leak on Ctrl-C in `run_exiftool_with_args_file`
**Severity:** LOW  
**Location:** `run_exiftool_with_args_file()` (lines 84-107)

**What it did:**
If the user pressed Ctrl-C between `NamedTemporaryFile` creation and entering the `try` block, the temp file was never deleted.

**Fix:**
- Moved `arg_file` initialization before `try`
- Added `os.path.exists()` check in `finally` before unlinking
- Ensures cleanup even if interrupted

---

### Bug #12: `replace()` could corrupt filenames with multiple occurrences
**Severity:** LOW  
**Location:** `restore_from_backup()` (line 409)

**What it did:**
Used `backup_file.name.replace(".exif-backup.json", "")` which replaces ALL occurrences. A file named `photo.json.exif-backup.json` would become `photo.` instead of `photo.json`.

**Fix:**
- Changed to `removesuffix(".exif-backup.json")` (Python 3.9+)
- Only removes the suffix at the end of the string

---

### Bug #13: Newlines in values broke ExifTool arg-file parsing
**Severity:** LOW  
**Location:** `run_exiftool_with_args_file()` (lines 90-93)

**What it did:**
If a value (e.g., `notes`) contained a newline character, the arg file would split it into two lines, breaking ExifTool's argument parsing.

**Fix:**
- Added `.replace("\n", " ").replace("\r", " ")` to sanitize values before writing to arg file
- Newlines are converted to spaces

---

### Bug #14: INI values retained literal quotes
**Severity:** LOW  
**Location:** `parse_ini()` (line 194)

**What it did:**
If a user wrote `notes="Test shoot"` in the INI file, the quotes were preserved literally: `Notes: "Test shoot"` in UserComment.

**Fix:**
- Added quote stripping: if value starts and ends with matching quotes (`"` or `'`), remove them
- `notes="Test shoot"` now becomes `Notes: Test shoot`

---

### Bug #15: `error_exit` return type was `None` instead of `NoReturn`
**Severity:** LOW  
**Location:** `error_exit()` (line 110)

**What it did:**
Type checkers couldn't infer that `error_exit()` never returns, causing false warnings about missing return statements in `parse_yaml()` and `parse_ini()`.

**Fix:**
- Changed return type from `None` to `NoReturn` (from `typing` module)

---

### Bug #16: Dead `recursive` parameter in `process_folder`
**Severity:** LOW  
**Location:** `process_folder()` signature and `get_image_files()` call

**What it did:**
`process_folder()` accepted a `recursive` parameter that was always passed as `False`. The actual recursion was handled by `discover_folders()` in `main()`.

**Fix:**
- Removed the unused `recursive` parameter from `process_folder()`
- Removed the unused `recursive` parameter from `get_image_files()`

---

## Summary

| Bug | Severity | Fixed |
|-----|----------|-------|
| #1 | CRITICAL | parse_date now accepts EXIF format |
| #2 | CRITICAL | scanner_info only captured on actual overwrite |
| #3 | HIGH | Keywords use += for proper separation |
| #4 | HIGH | scan_date takes priority over garbage move |
| #5 | MEDIUM | scanner_info logic handles partial camera info |
| #6 | MEDIUM | INI uses utf-8-sig for BOM support |
| #7 | MEDIUM | suffix.lower() for cross-platform extensions |
| #8 | LOW | Reuse EXIF read results |
| #9 | LOW | Parallel analysis with ThreadPoolExecutor |
| #10 | LOW | Added .yml to METADATA_FILENAMES |
| #11 | LOW | Safe temp file cleanup |
| #12 | LOW | Use removesuffix instead of replace |
| #13 | LOW | Sanitize newlines in arg file |
| #14 | LOW | Strip quotes from INI values |
| #15 | LOW | error_exit returns NoReturn |
| #16 | LOW | Remove dead recursive parameter |
