#!/usr/bin/env python3
"""
Regression tests for film_metadata_injector.py.

End-to-end: every test writes real JPEG/TIFF files, runs the script as a
subprocess, and reads the result back with ExifTool. No mocks - the bugs these
cover were all in the seam between this script and ExifTool, which is exactly
what a mock would have hidden.

Requirements: ExifTool on PATH, pyyaml. Rich is optional (menu tests skip
without it).

Run:  python -m unittest discover -s tests -v
"""

import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent
INJECTOR = SCRIPT_DIR / "film_metadata_injector.py"
sys.path.insert(0, str(SCRIPT_DIR))

# A 16x16 baseline JPEG. Embedded so the suite needs no image library.
TINY_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAA0JCgsKCA0LCgsODg0PEyAVExISEyccHhcgLikx"
    "MC4pLSwzOko+MzZGNywtQFdBRkxOUlNSMj5aYVpQYEpRUk//2wBDAQ4ODhMREyYVFSZPNS01"
    "T09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT09PT0//wAARCAAQ"
    "ABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgED"
    "AwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcY"
    "GRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJ"
    "ipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo"
    "6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgEC"
    "BAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl"
    "8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaH"
    "iImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn"
    "6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDPooorjOs//9k="
)


def exiftool_available() -> bool:
    try:
        subprocess.run(["exiftool", "-ver"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


@unittest.skipUnless(exiftool_available(), "ExifTool not on PATH")
class InjectorTestCase(unittest.TestCase):
    """Shared fixtures: a temp roll folder plus ExifTool read/write helpers."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fmi-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def make_image(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(TINY_JPEG)
        return path

    def set_tags(self, path: Path, **tags: str) -> None:
        # Via the script's own -@ argument file: ExifTool cannot resolve a UTF-8
        # path passed straight on argv under a non-UTF-8 Windows code page, which
        # is exactly why the script uses an argument file in the first place.
        import film_metadata_injector as fmi
        args = [f"-{k}={v}" for k, v in tags.items()]
        fmi.run_exiftool_with_args_file(["-overwrite_original", *args, str(path)])

    def read_tags(self, path: Path) -> dict:
        import film_metadata_injector as fmi
        result = fmi.run_exiftool_with_args_file(["-j", "-G", "-a", str(path)])
        return json.loads(result.stdout)[0]

    def write_metadata(self, folder: Path, body: str, name: str = "film-metadata.yaml") -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / name
        target.write_text(body, encoding="utf-8")
        return target

    def run_injector(self, *args: str, encoding: str = "utf-8") -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": encoding}
        return subprocess.run(
            [sys.executable, str(INJECTOR), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=300,
        )


class TestDateConvergence(InjectorTestCase):
    """Bug: a roll older than the scanner threshold never converged and the
    preserved scanner date was overwritten on the second run."""

    def test_pre_threshold_roll_converges_and_keeps_scanner_date(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(
            img,
            **{"EXIF:Make": "NORITSU KOKI", "EXIF:Model": "QSS-32_33",
               "EXIF:DateTimeOriginal": "2001:01:01 00:00:00"},
        )
        # Exposure date is BEFORE the default 2015-01-01 threshold - the normal
        # case for analog film, and the one that used to loop forever.
        self.write_metadata(folder, "camera_make: Nikon\ncamera_model: F3\ndate: 1998-06-01\n")

        self.run_injector(str(folder), "--apply")
        after_first = self.read_tags(img)
        self.assertEqual(after_first["EXIF:DateTimeOriginal"], "1998:06:01 00:00:00")
        self.assertEqual(after_first["EXIF:CreateDate"], "2001:01:01 00:00:00",
                         "first run must park the scanner date in CreateDate")

        self.run_injector(str(folder), "--apply")
        after_second = self.read_tags(img)
        self.assertEqual(after_second["EXIF:CreateDate"], "2001:01:01 00:00:00",
                         "second run must NOT overwrite the preserved scanner date")

        third = self.run_injector(str(folder))
        self.assertIn("No changes needed", third.stdout + third.stderr,
                      "the roll must converge instead of reporting phantom changes")

    def test_post_threshold_roll_is_idempotent(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(img, **{"EXIF:DateTimeOriginal": "2001:01:01 00:00:00"})
        self.write_metadata(folder, "camera_make: Nikon\nfilm: Tri-X 400\ndate: 2023-03-01\n")

        self.run_injector(str(folder), "--apply")
        second = self.run_injector(str(folder))
        self.assertIn("No changes needed", second.stdout + second.stderr)
        self.assertEqual(self.read_tags(img)["EXIF:DateTimeOriginal"], "2023:03:01 00:00:00")


class TestCleanupXmpFlag(InjectorTestCase):
    """Bug: --cleanup-xmp-dtd was appended after the scan_date write, so ExifTool
    applied the deletion last and wiped the tag the run had just set."""

    def test_cleanup_does_not_delete_the_scan_date_it_just_wrote(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(img, **{"XMP-exif:DateTimeDigitized": "2001:01:01 00:00:00"})
        self.write_metadata(folder, "film: Kodak Gold 200\nscan_date: 2024-03-10\n")

        self.run_injector(str(folder), "--apply", "--cleanup-xmp-dtd")
        tags = self.read_tags(img)
        self.assertEqual(tags.get("XMP:DateTimeDigitized"), "2024:03:10 00:00:00",
                         "scan_date must win over the cleanup flag")
        self.assertEqual(tags.get("EXIF:CreateDate"), "2024:03:10 00:00:00",
                         "EXIF and XMP must stay in sync")

        second = self.run_injector(str(folder), "--cleanup-xmp-dtd")
        self.assertIn("No changes needed", second.stdout + second.stderr,
                      "cleanup + scan_date must not fight on every run")

    def test_cleanup_still_removes_a_legacy_value_when_no_scan_date(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(img, **{"XMP-exif:DateTimeDigitized": "2001:01:01 00:00:00"})
        self.write_metadata(folder, "film: Kodak Gold 200\n")

        self.run_injector(str(folder), "--apply", "--cleanup-xmp-dtd")
        self.assertNotIn("XMP:DateTimeDigitized", self.read_tags(img))


class TestScanDateWrites(InjectorTestCase):
    """Bug: a mismatch in one of the two date-digitized tags rewrote both."""

    def test_no_noop_rewrite_of_the_matching_tag(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(img, **{"EXIF:CreateDate": "2024:03:10 00:00:00"})
        self.write_metadata(folder, "film: Kodak Gold 200\nscan_date: 2024-03-10\n")

        result = self.run_injector(str(folder))
        output = result.stdout + result.stderr
        self.assertIn("XMP", output, "the missing XMP tag should still be reported")
        self.assertNotIn("2024:03:10 00:00:00 | 2024:03:10 00:00:00", output.replace("\n", " "))


class TestUnicodeOutput(InjectorTestCase):
    """Bug: a CJK filename raised UnicodeEncodeError from Rich and killed the
    whole run, including the untouched folders left in a --recursive batch."""

    def test_cjk_filename_does_not_abort_a_recursive_batch(self) -> None:
        root = self.tmp / "batch"
        for name, image in (("aaa", "x.jpg"), ("bbb", "写真.jpg"), ("ccc", "z.jpg")):
            folder = root / name
            self.make_image(folder / image)
            self.write_metadata(folder, "camera_make: Olympus\ncamera_model: Pen-F\n")

        # cp1252 is the default Windows console encoding and cannot represent
        # the CJK filename - the condition that used to crash.
        result = self.run_injector(str(root), "--recursive", encoding="cp1252")

        output = result.stdout + result.stderr
        self.assertNotIn("Traceback", output)
        self.assertNotIn("UnicodeEncodeError", output)
        for name in ("aaa", "bbb", "ccc"):
            self.assertIn(name, output, f"folder {name} was never processed")

    def test_cjk_filename_is_written_correctly(self) -> None:
        folder = self.tmp / "梅 [roll]"
        img = self.make_image(folder / "写真.jpg")
        self.write_metadata(folder, "camera_make: Olympus\ncamera_model: Pen-F\n")

        self.run_injector(str(folder), "--apply", encoding="cp1252")
        self.assertEqual(self.read_tags(img)["EXIF:Make"], "Olympus")


class TestRestore(InjectorTestCase):
    """Bugs: the success summary is on stderr (so nothing was ever counted), and
    restore left most of the injected tags in place."""

    def _injected_roll(self) -> tuple:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(
            img,
            **{"EXIF:Make": "NORITSU KOKI", "EXIF:Model": "QSS-32_33",
               "EXIF:DateTimeOriginal": "2001:01:01 00:00:00"},
        )
        before = self.read_tags(img)
        self.write_metadata(folder, (
            "camera_make: Olympus\ncamera_model: Pen-F\nfilm: Kodak Gold 200\n"
            "iso: 200\ndate: 2023-03-01\nlens: D.Zuiko 38mm f/1.8\n"
            "dev: C-41\nscan_date: 2024-03-10\nnotes: trip\n"
        ))
        self.run_injector(str(folder), "--apply")
        return folder, img, before

    def test_restore_reports_the_images_it_restored(self) -> None:
        folder, _, _ = self._injected_roll()
        result = self.run_injector(str(folder), "--restore")
        output = result.stdout + result.stderr
        self.assertIn("Total images restored: 1", output)
        self.assertNotIn("Unexpected ExifTool output", output)

    def test_restore_is_a_full_rollback_of_managed_tags(self) -> None:
        folder, img, before = self._injected_roll()

        injected = self.read_tags(img)
        self.assertEqual(injected["EXIF:Make"], "Olympus")  # sanity: it did inject

        self.run_injector(str(folder), "--restore")
        after = self.read_tags(img)

        import film_metadata_injector as fmi
        for tag in fmi.MANAGED_TAGS:
            self.assertEqual(
                after.get(tag), before.get(tag),
                f"{tag} was not rolled back (before={before.get(tag)!r}, after={after.get(tag)!r})",
            )

    def test_restore_leaves_foreign_tags_alone(self) -> None:
        folder, img, _ = self._injected_roll()
        # A tag this script never manages must survive the rollback.
        self.set_tags(img, **{"IPTC:By-line": "Some Photographer"})

        self.run_injector(str(folder), "--restore")
        self.assertEqual(self.read_tags(img).get("IPTC:By-line"), "Some Photographer")


class TestKeywords(InjectorTestCase):
    def test_dedup_is_case_insensitive(self) -> None:
        folder = self.tmp / "roll"
        img = self.make_image(folder / "r.jpg")
        self.set_tags(img, **{"IPTC:Keywords": "Kodak Portra 400"})
        self.write_metadata(folder, "film: kodak portra 400\n")

        self.run_injector(str(folder), "--apply")
        keywords = self.read_tags(img)["IPTC:Keywords"]
        keywords = keywords if isinstance(keywords, list) else [keywords]
        self.assertEqual(keywords, ["Kodak Portra 400"],
                         "a case variant must not be added as a second keyword")

    def test_preserve_mode_is_idempotent(self) -> None:
        folder = self.tmp / "roll"
        self.make_image(folder / "r.jpg")
        self.write_metadata(folder, "film: Kodak Gold 200\n")

        self.run_injector(str(folder), "--apply", "--dedup-mode", "preserve")
        second = self.run_injector(str(folder), "--dedup-mode", "preserve")
        self.assertIn("No changes needed", second.stdout + second.stderr)


class TestParsing(unittest.TestCase):
    """Pure-function tests: no ExifTool needed."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="fmi-parse-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        import film_metadata_injector as fmi
        self.fmi = fmi

    def test_quoted_ini_value_keeps_its_semicolon(self) -> None:
        path = self.tmp / "film-metadata.ini"
        path.write_text(
            'notes="roll #3 ; half frame"\n'
            "dev=C-41 ; developed at home\n"
            "film=Kodak Gold 200\n",
            encoding="utf-8",
        )
        data = self.fmi.parse_ini(path)
        self.assertEqual(data["notes"], "roll #3 ; half frame")
        self.assertEqual(data["dev"], "C-41", "unquoted values keep the inline-comment rule")
        self.assertEqual(data["film"], "Kodak Gold 200")

    def test_ini_still_handles_the_ordinary_cases(self) -> None:
        path = self.tmp / "film-metadata.ini"
        path.write_text(
            "; comment line\n"
            "camera_make=Olympus\n"
            "notes=roll #3 half frame\n"
            "key=value=with=equals\n"
            "url=http://x.com/a;b\n",
            encoding="utf-8",
        )
        data = self.fmi.parse_ini(path)
        self.assertEqual(data["camera_make"], "Olympus")
        self.assertEqual(data["notes"], "roll #3 half frame")
        self.assertEqual(data["key"], "value=with=equals")
        self.assertEqual(data["url"], "http://x.com/a;b")

    def test_date_parsing(self) -> None:
        self.assertIsNone(self.fmi.parse_date("2023-02-30"))
        self.assertIsNone(self.fmi.parse_date("15/05/2023"))
        self.assertIsNotNone(self.fmi.parse_date("2023-05-15"))
        self.assertEqual(self.fmi.to_exif_datetime("2023-05-15"), "2023:05:15 00:00:00")
        self.assertEqual(
            self.fmi.to_exif_datetime("2023-05-15T10:30:00+09:00"), "2023:05:15 10:30:00"
        )


class TestMenu(unittest.TestCase):
    """Bug: confirming an overwrite while switching format left both files, and
    the injector then silently used the old one."""

    def setUp(self) -> None:
        if importlib.util.find_spec("rich") is None:
            self.skipTest("rich not installed")
        self.tmp = Path(tempfile.mkdtemp(prefix="fmi-menu-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_overwrite_with_a_different_format_replaces_the_old_file(self) -> None:
        import film_metadata_menu as menu

        folder = self.tmp / "roll"
        folder.mkdir()
        (folder / "film-metadata.yaml").write_text("camera_make: OLD\n", encoding="utf-8")

        answers = iter(["y", "ini", "Nikon"] + [""] * 9 + ["n"])
        originals = (menu.ask, menu.ask_yes_no, menu.ask_choice, menu.pause)
        menu.ask = lambda p, d=None: next(answers)
        menu.ask_yes_no = lambda p, d=True: next(answers) in ("y", "yes")
        menu.ask_choice = lambda p, c, d: next(answers)
        menu.pause = lambda: None
        try:
            menu.wizard_create_metadata(folder)
        finally:
            menu.ask, menu.ask_yes_no, menu.ask_choice, menu.pause = originals

        self.assertTrue((folder / "film-metadata.ini").exists())
        self.assertFalse((folder / "film-metadata.yaml").exists(),
                         "the file the user chose to overwrite must be gone")

        import film_metadata_injector as fmi
        self.assertEqual(fmi.find_metadata_file(folder).name, "film-metadata.ini")


if __name__ == "__main__":
    unittest.main(verbosity=2)
