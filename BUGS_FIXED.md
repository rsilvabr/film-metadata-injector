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
| #8 | LOW | ✅ **FIXED in Round 3** — `cached_results` reuses EXIF reads |
| #9 | LOW | ✅ **FIXED in Round 3** — `ThreadPoolExecutor` in analysis phase |
| #10 | LOW | Added .yml to METADATA_FILENAMES |
| #11 | LOW | Safe temp file cleanup |
| #12 | LOW | Use removesuffix instead of replace |
| #13 | LOW | Sanitize newlines in arg file |
| #14 | LOW | Strip quotes from INI values |
| #15 | LOW | error_exit returns NoReturn |
| #16 | LOW | ✅ **FIXED in Round 3** — Removed dead `recursive` parameter |

*Note: Bugs #8, #9, #16 were documented as fixed in Round 1 but were never actually implemented until Round 3 (commit 8571210). See Round 3 section for details.*

### Round 2

| Bug | Severity | Fixed |
|-----|----------|-------|
| A | CRITICAL | Keywords operators (+=) no longer get double = |
| B | HIGH | scanner_info always extracted from UserComment first |
| C | HIGH | Keywords list properly serialized from ExifTool JSON |

---

### Round 3 — Actual Implementation (Commit 8571210)

Bugs #8, #9, and #16 were previously documented as fixed in Round 1 but were never actually implemented in the code. They were properly fixed in this round, along with additional improvements identified during code review.

| Bug | Severity | What was actually implemented |
|-----|----------|-------------------------------|
| #17 | CRITICAL | `apply_exif_commands` now runs inside `ThreadPoolExecutor` workers |
| #18 | HIGH | `cached_results` dict stores commands from analysis; apply reuses cache without re-reading EXIF |
| #19 | HIGH | Analysis phase now uses `ThreadPoolExecutor` (parallel like apply) |
| #20 | HIGH | `DATE_PATTERN` accepts subseconds and timezone offsets; `parse_date` tries 4 formats; `is_scanner_trash` returns `False` for unparseable dates (conservative) |
| #21 | MEDIUM | Removed dead `recursive` parameter from `process_folder()` |
| #22 | MEDIUM | `current_keywords.split(", ")` with exact element comparison instead of substring check |
| #23 | MEDIUM | `isinstance(v, list)` is now generic for all EXIF tags, not just Keywords |
| #24 | LOW | `os.walk()` replaces `rglob("*")` for folder discovery |
| #25 | LOW | Backup validation: re-creates if JSON is broken or too short |
| #26 | LOW | `-q` only passed when NOT in verbose/debug mode |
| #27 | LOW | Added warning log when `parse_date()` fails to parse a date |

---

## Round 4 — External Audit Fixes

This round addresses bugs found during a real-world integration audit using ExifTool 12.76 and actual scanned images with Noritsu scanner EXIF data.

### Bug #28: `DateTimeDigitized` logic used wrong tag name for read and write
**Severity:** CRITICAL  
**Location:** `get_exif_data()`, `build_exif_commands()`

**What it did:**
ExifTool exposes the EXIF `DateTimeDigitized` tag as `CreateDate`. The script read the JSON key `DateTimeDigitized` (which ExifTool never emits for EXIF) and wrote `-DateTimeDigitized=`, which targeted XMP instead of EXIF. This caused:
- The "preserve real scan date" rule to never fire.
- `scan_date` to be written to XMP only, leaving EXIF `CreateDate` empty.
- Contradictory metadata when both EXIF and XMP dates existed.

**Fix:**
- Read `EXIF:CreateDate` explicitly.
- Write `-EXIF:CreateDate=` for the EXIF tag.
- Synchronize `-XMP-exif:DateTimeDigitized=` so both stores stay consistent.

---

### Bug #29: `--restore` silently failed after folder rename/move
**Severity:** HIGH  
**Location:** `restore_from_backup()`

**What it did:**
The backup JSON stores `SourceFile` as the absolute path at backup time. ExifTool's `-j=` import skips entries whose `SourceFile` does not match the target image. After renaming the folder, the import did nothing but exited 0, and the script still counted the image as restored.

**Fix:**
- Before importing, load the backup JSON, rewrite `SourceFile` to the current image path, and pass this temporary JSON to ExifTool.
- Also parse ExifTool's stdout to confirm the image was actually updated/unchanged.

---

### Bug #30: Restore was merge-overwrite, not rollback
**Severity:** HIGH  
**Location:** `restore_from_backup()`, documentation

**What it did:**
Importing a JSON backup only overwrites tags present in the backup. Tags created later (for example, the XMP `DateTimeDigitized` written due to Bug #28) survived the restore.

**Fix:**
- Documented honestly that restore is a merge-overwrite, not a complete rollback.
- Removed the invalid `-all:all` argument from restore commands.

---

### Bug #31: EXIF read errors were treated as "no metadata"
**Severity:** MEDIUM  
**Location:** `get_exif_data()`, `process_one_image()`

**What it did:**
`get_exif_data()` returned `{}` on timeout/error. `build_exif_commands` then treated the image as having no metadata and wrote values without the scanner-trash protections.

**Fix:**
- `get_exif_data()` now returns `None` on failure.
- `process_one_image()` skips the image and logs a warning.

---

### Bug #32: Keyword deduplication ignored XMP `Subject`
**Severity:** MEDIUM  
**Location:** `build_exif_commands()`

**What it did:**
The duplicate check only looked at `Keywords` (IPTC). Files with `XMP-dc:Subject` but no IPTC keywords received duplicate film keywords.

**Fix:**
- Collect existing keywords from both `IPTC:Keywords` and `XMP:Subject`.
- Added `--dedup-mode preserve|normalize`:
  - `preserve`: only appends if the film is absent from both lists.
  - `normalize` (default): rewrites both lists to a unified, deduplicated set, adding the film if missing.

---

### Bug #33: Fixed 60-second timeout was too short for large TIFFs
**Severity:** MEDIUM  
**Location:** all `run_exiftool_with_args_file()` callers

**What it did:**
TIFF scans of 200–500 MB on slow storage could exceed the hard-coded 60s timeout, causing failed writes or backup operations.

**Fix:**
- Default timeout scales with the largest file: `60s + 1s per MB`.
- Added `--timeout` CLI flag to override the calculated value.

---

### Bug #34: `-all:all` was an invalid ExifTool option in restore context
**Severity:** LOW  
**Location:** `restore_from_backup()`, README

**What it did:**
`-all:all` produced the ExifTool warning `Ignored superfluous tag name or invalid option: -all:all`. The restore worked only because `-j=` does the work alone.

**Fix:**
- Removed `-all:all` from the script and updated manual restore examples in README.

---

### Bug #35: README claimed Python 3.8+ but code used `removesuffix()`
**Severity:** LOW  
**Location:** README.md

**Fix:**
- Updated requirement to Python 3.9+.

---

### Bug #36: `0000:00:00` scanner dates were moved to `DateTimeDigitized`
**Severity:** LOW  
**Location:** `build_exif_commands()`

**Fix:**
- All-zero dates are now treated as garbage and discarded, not moved to history.

---

### Bug #37: Re-runs produced misleading warnings when real `DateTimeOriginal` existed
**Severity:** LOW  
**Location:** `build_exif_commands()`

**Fix:**
- Changed log level from `warning` to `info` and clarified the message.

---

### Bug #38: Newlines in `notes` broke idempotence
**Severity:** LOW  
**Location:** `build_exif_commands()`

**What it did:**
`notes` was sanitized to spaces only when writing to the arg file, but the comparison with `current_uc` used the raw value, so every re-run rewrote `UserComment`.

**Fix:**
- Normalize newlines to spaces before building and comparing the new `UserComment`.

---

### Bug #39: YAML 1.1 interpreted `dev: NO` / `Off` as booleans
**Severity:** LOW  
**Location:** `parse_yaml()`

**Fix:**
- Detect boolean values that came from YAML 1.1 aliases and convert them back to the original strings `"NO"` / `"YES"`.

---

### Bug #40: INI inline comments corrupted values
**Severity:** LOW  
**Location:** `parse_ini()`

**What it did:**
`iso=400 ; comment` was parsed as `iso=400 ; comment` (value included the comment text).

**Fix:**
- Strip inline comments starting with ` ;` or ` #` after the key/value has been identified, while preserving standalone comment lines.

---

### Bug #41: Tags used for decisions were ambiguous between EXIF and XMP
**Severity:** LOW  
**Location:** `get_exif_data()`

**Fix:**
- Request decision tags with explicit group prefixes (`-EXIF:Make`, `-EXIF:DateTimeOriginal`, `-IPTC:Keywords`, `-XMP-dc:Subject`, etc.) so values come from the intended metadata family.

---

### Bug #42: `to_exif_datetime` was defined before `parse_date`
**Severity:** LOW  
**Location:** module level

**Fix:**
- Reordered definitions so `parse_date` is defined before `to_exif_datetime` uses it.

---

### Bug #43: `parse_date` regex did not accept ISO `T` separator or compact timezone
**Severity:** LOW  
**Location:** `parse_date()`

**Fix:**
- Updated `DATE_PATTERN` to accept `T` as date/time separator and timezone offsets with or without colon.
- Added `%Y-%m-%dT%H:%M:%S` and `%Y-%m-%dT%H:%M:%S.%f` to `parse_date` formats.

---

### Bug #44: `get_exif_data` ignored calculated timeout during analysis
**Severity:** MEDIUM  
**Location:** `get_exif_data()`, `process_one_image()`, `process_folder()`

**Fix:**
- Added `timeout` parameter to `get_exif_data` and `process_one_image`.
- `process_folder` now passes the calculated timeout to the analysis phase.

---

### Bug #45: Keyword normalization produced comma-separated single keywords
**Severity:** MEDIUM  
**Location:** `build_exif_commands()` keyword handling

**Fix:**
- In `normalize` mode, clear each keyword family and re-add keywords one at a time with `-IPTC:Keywords=` and `-XMP-dc:Subject=`, preventing ExifTool from concatenating them into a single comma-separated keyword.

---

### Bug #46: YAML 1.1 boolean fix was unreliable
**Severity:** LOW  
**Location:** `parse_yaml()`

**Fix:**
- Replaced the post-load boolean detection with a custom `SafeLoader` subclass that registers `tag:yaml.org,2002:bool` to return the raw string value, preserving `NO`, `OFF`, `YES`, `ON` literally.

---

### Bug #47: INI inline-comment stripping truncated values containing `#`
**Severity:** MEDIUM  
**Location:** `parse_ini()`

**What it did:**
The fix for inline comments stripped both ` ;` and ` #`. Because film notes commonly contain `#` (e.g., `notes=Test roll #3 half-frame`), the value was silently truncated to `Test roll`.

**Fix:**
- Removed `#` from inline-comment stripping. Only ` ;` (classic INI convention) is treated as an inline comment delimiter.

---

### Bug #48: XMP `DateTimeDigitized` was not read or compared during sync
**Severity:** MEDIUM  
**Location:** `get_exif_data()`, `build_exif_commands()`

**What it did:**
The script synchronized `EXIF:CreateDate` with `XMP-exif:DateTimeDigitized`, but it never read the existing XMP value. The dry-run table showed the current XMP `Subject`/keywords instead. As a result, if `EXIF:CreateDate` already matched `scan_date` but the XMP value diverged (e.g., legacy garbage), the XMP value was never corrected.

**Fix:**
- Added `-XMP-exif:DateTimeDigitized` to the explicit read tags.
- The "current" value for sync commands now uses the real XMP `DateTimeDigitized`.
- `scan_date` logic rewrites both EXIF and XMP if either differs from the target value.

---

### Bug #49: No built-in way to clean up legacy XMP DateTimeDigitized garbage
**Severity:** LOW  
**Location:** CLI / `build_exif_commands()`

**What it did:**
Files processed by older script versions could carry an incorrect `XMP-exif:DateTimeDigitized` value. Restore is merge-overwrite and therefore did not remove it, and the normal sync logic only rewrote the tag when it differed from `scan_date`.

**Fix:**
- Added `--cleanup-xmp-dtd` flag. When set, the script removes `XMP-exif:DateTimeDigitized` from all images in the processed folders. Users can then re-run normal injection with `scan_date` in the YAML to repopulate both EXIF and XMP correctly.

---

## Summary

### Round 4

| Bug | Severity | Fixed |
|-----|----------|-------|
| #28 | CRITICAL | `CreateDate` used for EXIF DateTimeDigitized; XMP synchronized |
| #29 | HIGH | Restore rewrites `SourceFile` before importing |
| #30 | HIGH | Restore documented as merge-overwrite; `-all:all` removed |
| #31 | MEDIUM | `get_exif_data()` returns `None` on failure; image skipped |
| #32 | MEDIUM | Keyword dedup checks IPTC + XMP; `--dedup-mode` added |
| #33 | MEDIUM | Auto-scaling timeout + `--timeout` flag |
| #34 | LOW | Removed invalid `-all:all` from restore |
| #35 | LOW | README requires Python 3.9+ |
| #36 | LOW | All-zero dates discarded instead of moved |
| #37 | LOW | Real-date message downgraded from warning to info |
| #38 | LOW | Newlines normalized before UserComment comparison |
| #39 | LOW | YAML 1.1 boolean aliases converted back to strings |
| #40 | LOW | INI inline comments stripped correctly |
| #41 | LOW | Explicit group prefixes on decision tags |
| #42 | LOW | `parse_date` defined before `to_exif_datetime` |
| #43 | LOW | `DATE_PATTERN` accepts `T` separator and compact timezone |
| #44 | MEDIUM | Timeout propagated to EXIF read/analysis phase |
| #45 | MEDIUM | Keyword normalization avoids comma-separated single keyword |
| #46 | LOW | Custom YAML SafeLoader keeps YES/NO/ON/OFF as strings |
| #47 | MEDIUM | INI inline-comment stripping no longer truncates `#` in values |
| #48 | MEDIUM | XMP DateTimeDigitized read explicitly and compared during sync |
| #49 | LOW | Added `--cleanup-xmp-dtd` flag for legacy cleanup |

**Note on Bug #4:** The semantic change from "invalid date = garbage" to "invalid date = unknown" prevents accidental overwrites of real dates in non-standard formats. A warning is now logged when this happens, so users know the date was skipped intentionally.
