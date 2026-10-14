import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from neontag.files import archive_files, normalize_tags, preprocess, scan_images, write_result


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.source.mkdir()
        self.state = self.root / ".state"
        self.state.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def image(self, name, size=(200, 100), mode="RGB"):
        target = self.source / name
        Image.new(mode, size).save(target)
        return target

    def test_resize_renames_and_backs_up_originals(self):
        original = self.image("old.png", (200, 100))
        before = original.read_bytes()
        result = preprocess(self.source, self.state, 1024, "scene", "jpg")
        self.assertEqual([path.name for path in scan_images(self.source)], ["scene_000001.jpg"])
        with Image.open(self.source / "scene_000001.jpg") as image:
            self.assertEqual(image.size, (1024, 512))
        self.assertEqual((Path(result["backup"]) / "old.png").read_bytes(), before)

    def test_preprocess_transparency_and_name_cycle(self):
        self.image("image_000001.png", (40, 80), "RGBA")
        self.image("other.jpg", (80, 40))
        preprocess(self.source, self.state, 64, "image", "png")
        self.assertEqual(len(scan_images(self.source)), 2)
        with Image.open(self.source / "image_000001.png") as image:
            self.assertEqual(image.size, (32, 64))

    def test_corrupt_image_does_not_modify_other_originals(self):
        original = self.image("a.jpg")
        before = original.read_bytes()
        (self.source / "b.png").write_bytes(b"broken")
        with self.assertRaises(ValueError):
            preprocess(self.source, self.state, 1024, "image", "jpg")
        self.assertEqual(original.read_bytes(), before)
        self.assertFalse((self.source / "image_000001.jpg").exists())

    def test_invalid_prefix_cannot_escape_directory(self):
        self.image("a.png")
        with self.assertRaises(ValueError):
            preprocess(self.source, self.state, 1024, "../escape", "jpg")

    def test_tags_deduplicate_without_counting_trigger(self):
        self.assertEqual(normalize_tags("Tags: Cat, grass, cat, blue sky", "mycat", 3), "mycat, Cat, grass, blue sky")
        with self.assertRaises(ValueError):
            normalize_tags("cat, cat", "trigger", 2)

    def test_output_contains_only_image_and_tag(self):
        original = self.image("one.png")
        output = self.root / "output"
        files = write_result(original, output, "cat, sky", "txt", False)
        self.assertEqual({path.name for path in output.iterdir()}, {"one.png", "one.txt"})
        self.assertEqual(Path(files[1]).read_text(encoding="utf-8"), "cat, sky\n")

    def test_separate_output_and_zip_are_clean(self):
        original = self.image("one.png")
        output = self.root / "output"
        files = write_result(original, output, "cat, sky", "json", True)
        (output / "unrelated.txt").write_text("private")
        archive = archive_files(output, files, "dataset", "zip", self.state)
        self.assertEqual(archive, self.root / "dataset.zip")
        with zipfile.ZipFile(archive) as handle:
            self.assertEqual(set(handle.namelist()), {"output/images/one.png", "output/labels/one.json"})
        with self.assertRaises(ValueError):
            archive_files(output, files, "dataset", "zip", self.state)

    def test_output_collision_is_never_overwritten(self):
        original = self.image("one.png")
        output = self.root / "output"
        write_result(original, output, "old", "txt", False)
        with self.assertRaises(ValueError):
            write_result(original, output, "new", "txt", False)
        self.assertEqual((output / "one.txt").read_text(encoding="utf-8"), "old\n")

    def test_competing_file_created_during_write_is_preserved(self):
        original = self.image("one.png")
        output = self.root / "output"
        competing = output / "one.txt"
        real_open = Path.open

        def racing_open(path, mode="r", *args, **kwargs):
            if path == competing and mode == "x":
                path.write_bytes(b"someone else")
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", racing_open), self.assertRaises(FileExistsError):
            write_result(original, output, "cat", "txt", False)
        self.assertEqual(competing.read_bytes(), b"someone else")

    def test_competing_preprocess_target_is_preserved(self):
        self.image("one.png")
        target = self.source / "image_000001.jpg"
        real_open = Path.open

        def racing_open(path, mode="r", *args, **kwargs):
            if path == target and mode == "xb":
                path.write_bytes(b"someone else")
            return real_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", racing_open), self.assertRaises(FileExistsError):
            preprocess(self.source, self.state)
        self.assertEqual(target.read_bytes(), b"someone else")
        self.assertTrue((self.source / "one.png").exists())
