# Bug Fixes Log

This document tracks all bugs found during code review and how they were fixed.

> **⚠️ IMPORTANT NOTE:** Three bugs documented below as "Fixed" (#8, #9, #16) were **documented but never actually implemented in the code**. They remain active bugs in the current codebase and need to be addressed. See the updated entries below for details.

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
**Location:** `process_folder()` (lines 570-611)

**What it did:**
Each image's EXIF was read twice: once during dry-run analysis, and again during apply.

**Impact:** Double the ExifTool calls. For 100 images, 200 calls instead of 100.

**Status: NOT FIXED IN CODE** ❌
- The fix was documented here but never implemented in `film_metadata_injector.py`
- `process_folder()` still calls `get_exif_data()` in both the analysis loop (line 572) and the apply phase (lines 540, 611)
- The results from the first read are never stored or reused

**Planned Fix:**
- Store `current_exif` + `commands` in a dictionary during analysis
- Reuse cached results during apply phase instead of reading EXIF again

---

### Bug #9: Initial analysis was always sequential, ignoring `--workers`
**Severity:** LOW  
**Location:** `process_folder()` (lines 570-577)

**What it did:**
Even with `--workers 8`, the dry-run analysis loop ran sequentially. Only the apply phase used parallel workers.

**Impact:** Large rolls (100+ photos) were slow to analyze before applying.

**Status: NOT FIXED IN CODE** ❌
- The fix was documented here but never implemented in `film_metadata_injector.py`
- The analysis loop (lines 570-577) still uses a plain `for` loop without any ThreadPoolExecutor
- The comment even admits it: "Dry-run analysis: can be parallel too, but keep it simple"

**Planned Fix:**
- Add `ThreadPoolExecutor` to the analysis phase, matching the apply phase pattern
- Cache EXIF results to avoid double-reading (see Bug #8)

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
**Location:** `process_folder()` signature (line 550) and `main()` call (line 767)

**What it did:**
`process_folder()` accepted a `recursive` parameter that was never used inside the function. The actual recursion was handled by `discover_folders()` in `main()`.

**Status: NOT FIXED IN CODE** ❌
- The fix was documented here but never implemented in `film_metadata_injector.py`
- The `recursive` parameter still exists in the function signature and is still passed from `main()`

**Planned Fix:**
- Remove the unused `recursive` parameter from `process_folder()` signature
- Remove `args.recursive` from the call in `main()`

---

## Round 2 — Follow-up Review

### Bug A: Keywords generated with literal `=` at start (`=Kodak Portra 400`)
**Severity:** CRITICAL  
**Location:** `build_exif_commands()` (line 391), `apply_exif_commands()` (line 464)

**What it did:**
The fix for Bug #3 introduced a worse bug. The tuple was `("-Keywords+=", ..., film_str, ...)`, and `apply_exif_commands()` formatted it as `f"{field}={new_val}"`, producing `-Keywords+==Kodak Portra 400` (double `=`).

ExifTool interpreted this as a keyword literally starting with `=`: `=Kodak Portra 400`. Lightroom/Capture One would never filter by "Kodak" because the keyword began with `=`.

**Impact:** All keywords were corrupted with a leading `=` character.

**Fix:**
- `apply_exif_commands()` now detects fields ending with `+=` or `-=` (ExifTool operators)
- For these operators, appends `{field}{value}` (no extra `=`)
- For normal fields, keeps `{field}={value}`

---

### Bug B: `scanner_info` still destroyed when refining camera_make/model
**Severity:** HIGH  
**Location:** `build_exif_commands()` (lines 280-293)

**What it did:**
The fix for Bug #2 only worked when Make/Model were identical between runs. But if you refined the YAML (e.g., "Olympus" → "Olympus Corporation"):

1. **Run 1:** Make=NORITSU → `scanner_info = "NORITSU QSS"` ✓
2. **Refine YAML:** camera_make="Olympus Corporation" (was "Olympus")
3. **Run 2:** Make=Olympus (from Run 1), YAML=Olympus Corporation → `make_will_change=True` → `scanner_info = "Olympus QSS"` ❌

The scanner info was replaced with the camera's own name because `old_make` was already the camera.

**Fix:**
- ALWAYS try to extract "Scanner: X" from existing UserComment first (re-run safe)
- Only fall back to `old_make + old_model` if no "Scanner:" found in UserComment AND we're overwriting

---

### Bug C: False positives/negatives in Keywords duplicate check
**Severity:** HIGH  
**Location:** `get_exif_data()` (line 239), `build_exif_commands()` (line 389)

**What it did:**
When ExifTool returned multiple Keywords in JSON, it came as a list: `["Foo", "Bar"]`. `get_exif_data()` did `str(v)`, turning it into `"['Foo', 'Bar']"`.

Two problems:
1. **False negative:** If existing keyword was `"Tri-X"` and new is `"Tri-X 400"`, `"Tri-X 400" not in "['Tri-X']"` → adds duplicate
2. **False positive:** If existing was `"Kodak Portra 400"` and new is `"Portra"`, `"Portra" in "['Kodak Portra 400']"` → skips legitimate new keyword

**Fix:**
- `get_exif_data()` now treats Keywords specially: if it's a list, joins with `", "` → `"Foo, Bar"`
- The substring check `"Tri-X" in "Foo, Bar"` works correctly for exact matches

---

## Summary

### Round 1

| Bug | Severity | Fixed |
|-----|----------|-------|
| #1 | CRITICAL | parse_date now accepts EXIF format |
| #2 | CRITICAL | scanner_info only captured on actual overwrite |
| #3 | HIGH | Keywords use += for proper separation |
| #4 | HIGH | scan_date takes priority over garbage move |
| #5 | MEDIUM | scanner_info handles partial camera info |
| #6 | MEDIUM | INI uses utf-8-sig for Windows Notepad BOM |
| #7 | MEDIUM | suffix.lower() for cross-platform extensions |
| #8 | LOW | ⚠️ **NOT FIXED** — Documented but never implemented |
| #9 | LOW | ⚠️ **NOT FIXED** — Documented but never implemented |
| #10 | LOW | Added .yml to METADATA_FILENAMES |
| #11 | LOW | Safe temp file cleanup |
| #12 | LOW | Use removesuffix instead of replace |
| #13 | LOW | Sanitize newlines in arg file |
| #14 | LOW | Strip quotes from INI values |
| #15 | LOW | error_exit returns NoReturn |
| #16 | LOW | ⚠️ **NOT FIXED** — Documented but never implemented |

### Round 2

| Bug | Severity | Fixed |
|-----|----------|-------|
| A | CRITICAL | Keywords operators (+=) no longer get double = |
| B | HIGH | scanner_info always extracted from UserComment first |
| C | HIGH | Keywords list properly serialized from ExifTool JSON |
