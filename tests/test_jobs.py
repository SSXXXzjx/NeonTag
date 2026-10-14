import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from neontag.jobs import JobManager


class ControlledModel:
    def __init__(self):
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.fail = False

    def generate(self, model_id, path, prompt, minimum, stop):
        self.calls.append(Path(path).name)
        self.entered.set()
        if self.block:
            self.release.wait(3)
        if self.fail:
            raise RuntimeError("inference failed")
        return "cat, sky, grass"


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.source.mkdir()
        for name in ("a.png", "b.png", "c.png"):
            Image.new("RGB", (30, 20)).save(self.source / name)
        self.model = ControlledModel()
        self.manager = JobManager(self.root, self.model)
        self.settings = {"input": str(self.source), "output": str(self.root / "output"), "model": "qwen3-vl-4b", "min_tags": 3}

    def tearDown(self):
        self.model.release.set()
        if self.manager.thread:
            self.manager.thread.join(5)
        self.temp.cleanup()

    def wait(self, manager=None):
        manager = manager or self.manager
        if manager.thread:
            manager.thread.join(5)
            self.assertFalse(manager.thread.is_alive())

    def test_test_preview_waits_for_confirmation_and_uses_isolated_output(self):
        job = self.manager.create(self.settings, "test", sample_count=2)
        self.assertEqual(len(job["items"]), 2)
        self.assertEqual(self.model.calls, [])
        self.assertFalse((self.root / "output").exists())
        self.manager.start(job["id"])
        self.wait()
        result = self.manager.get(job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(Path(result["output"]).is_relative_to(self.root / ".state" / "tests"))
        self.assertFalse((self.root / "output").exists())

    def test_cancelled_job_can_resume_without_repeating_completed_images(self):
        job = self.manager.create(self.settings, "bulk")
        self.model.block = True
        self.manager.start(job["id"])
        self.assertTrue(self.model.entered.wait(3))
        self.manager.control(job["id"], "cancel")
        self.model.release.set()
        self.wait()
        self.assertEqual(self.manager.get(job["id"])["status"], "cancelled")
        self.model.block = False
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(self.manager.get(job["id"])["done"], 3)
        calls = len(self.model.calls)
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(len(self.model.calls), calls)

    def test_restart_recovers_running_job_as_paused(self):
        job = self.manager.create(self.settings, "bulk")
        location = self.root / ".state" / "jobs" / (job["id"] + ".json")
        saved = json.loads(location.read_text(encoding="utf-8"))
        saved["status"] = "running"
        location.write_text(json.dumps(saved), encoding="utf-8")
        recovered = JobManager(self.root, self.model)
        self.assertEqual(recovered.get(job["id"])["status"], "paused")
        recovered.start(job["id"])
        self.wait(recovered)
        self.assertEqual(recovered.get(job["id"])["done"], 3)

    def test_missing_output_is_regenerated_on_resume(self):
        job = self.manager.create(self.settings, "bulk")
        self.manager.start(job["id"])
        self.wait()
        (self.root / "output" / "a.txt").unlink()
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(self.model.calls.count("a.png"), 2)
        self.assertEqual(self.model.calls.count("b.png"), 1)

    def test_changed_input_is_not_silently_used_by_old_job(self):
        job = self.manager.create(self.settings, "bulk")
        Image.new("RGB", (40, 40), "red").save(self.source / "a.png")
        self.manager.start(job["id"])
        self.wait()
        result = self.manager.get(job["id"])
        self.assertEqual(result["failed"], 1)
        self.assertIn("变化", result["items"][0]["error"])

    def test_failed_items_can_be_retried(self):
        job = self.manager.create(self.settings, "bulk")
        self.model.fail = True
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(self.manager.get(job["id"])["failed"], 3)
        self.model.fail = False
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(self.manager.get(job["id"])["done"], 3)

    def test_duplicate_stems_are_rejected_before_output(self):
        Image.new("RGB", (20, 20)).save(self.source / "a.jpg")
        with self.assertRaises(ValueError):
            self.manager.create(self.settings, "bulk")
        self.assertFalse((self.root / "output").exists())

    def test_overlapping_paths_and_dirty_output_are_rejected(self):
        for target in (self.source, self.source / "output", self.root, self.root / "models"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.manager.create({**self.settings, "output": str(target)}, "bulk")
        output = self.root / "output"
        output.mkdir()
        (output / "config.json").write_text("{}")
        with self.assertRaises(ValueError):
            self.manager.create(self.settings, "bulk")

    def test_subset_and_edited_tags_survive_restart(self):
        job = self.manager.create(self.settings, "bulk", selected=["b.png"])
        self.manager.start(job["id"])
        self.wait()
        item = self.manager.get(job["id"])["items"][0]
        self.manager.edit(job["id"], item["id"], "dog, sky, grass")
        recovered = JobManager(self.root, self.model)
        recovered.start(job["id"])
        self.wait(recovered)
        self.assertEqual(self.model.calls, ["b.png"])
        self.assertEqual((self.root / "output" / "b.txt").read_text(encoding="utf-8"), "dog, sky, grass\n")

    def test_preprocess_is_blocked_for_unfinished_source_jobs(self):
        job = self.manager.create(self.settings, "bulk")
        with self.assertRaises(ValueError):
            self.manager.check_preprocess(self.source)
        self.manager.start(job["id"])
        self.wait()
        self.manager.check_preprocess(self.source)

    def test_failed_tag_edit_preserves_previous_complete_label(self):
        job = self.manager.create(self.settings, "bulk", selected=["b.png"])
        self.manager.start(job["id"])
        self.wait()
        item = self.manager.get(job["id"])["items"][0]
        tag = self.root / "output" / "b.txt"
        before = tag.read_bytes()
        with patch("neontag.jobs.os.replace", side_effect=OSError("disk failure")), self.assertRaises(OSError):
            self.manager.edit(job["id"], item["id"], "dog, sky, grass")
        self.assertEqual(tag.read_bytes(), before)
        self.assertEqual(self.manager.get(job["id"])["items"][0]["tags"], "cat, sky, grass")

    def test_live_logs_include_generation_success_and_recovery(self):
        job = self.manager.create(self.settings, "bulk")
        self.model.block = True
        self.manager.start(job["id"])
        self.assertTrue(self.model.entered.wait(3))
        running = self.manager.get(job["id"])
        self.assertTrue(any("a.png" in entry["message"] for entry in running["logs"]))
        self.assertTrue(all("time" in entry and "level" in entry for entry in running["logs"]))
        self.manager.control(job["id"], "pause")
        self.model.release.set()
        self.wait()
        self.model.block = False
        self.manager.start(job["id"])
        self.wait()
        recovered = JobManager(self.root, self.model)
        logs = recovered.get(job["id"])["logs"]
        self.assertTrue(any("完成" in entry["message"] for entry in logs))
        self.assertEqual([entry["id"] for entry in logs], sorted({entry["id"] for entry in logs}))
        self.assertNotIn("logs", recovered.list()[0])

    def test_local_model_path_is_pinned_in_job_settings(self):
        folder = self.root / "local-model"
        folder.mkdir()
        (folder / "config.json").write_text(json.dumps({"model_type": "qwen2_5_vl"}))
        for name in ("tokenizer.json", "tokenizer_config.json", "preprocessor_config.json"):
            (folder / name).write_text("{}")
        (folder / "model.safetensors").write_bytes(b"weight")
        job = self.manager.create({**self.settings, "model": "local", "model_path": str(folder)}, "bulk")
        self.assertEqual(job["settings"]["model_path"], str(folder.resolve()))
        self.manager.start(job["id"])
        self.wait()
        self.assertEqual(self.manager.get(job["id"])["done"], 3)

    def test_failure_reason_is_in_live_log(self):
        self.model.fail = True
        job = self.manager.create(self.settings, "bulk")
        self.manager.start(job["id"])
        self.wait()
        logs = self.manager.get(job["id"])["logs"]
        self.assertTrue(any(entry["level"] == "error" and "inference failed" in entry["message"] for entry in logs))
