import json
import tempfile
import unittest
from pathlib import Path

from neontag.models import ModelManager


class ModelTests(unittest.TestCase):
    def local_fixture(self, folder, family="qwen3_vl"):
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps({"model_type": family}))
        for name in ("tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"):
            (folder / name).write_text("{}")
        (folder / "model.safetensors").write_bytes(b"weight")

    def test_local_qwen_families_are_checked_without_downloading(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ModelManager(Path(temporary))
            for family in ("qwen2_vl", "qwen2_5_vl", "qwen3_vl"):
                folder = Path(temporary) / family
                self.local_fixture(folder, family)
                result = manager.check_local(str(folder))
                self.assertEqual(result["family"], family)
                self.assertEqual(manager.path(result["id"]), folder.resolve())
                self.assertTrue(manager.downloaded(result["id"]))

    def test_local_text_model_and_incomplete_model_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ModelManager(Path(temporary))
            folder = Path(temporary) / "text"
            self.local_fixture(folder, "qwen3")
            with self.assertRaises(ValueError):
                manager.check_local(str(folder))
            (folder / "config.json").write_text(json.dumps({"model_type": "qwen3_vl"}))
            (folder / "model.safetensors").unlink()
            with self.assertRaises(ValueError):
                manager.check_local(str(folder))

    def test_missing_shard_does_not_count_as_downloaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ModelManager(Path(temporary))
            folder = manager.path("qwen3-vl-4b")
            folder.mkdir(parents=True)
            (folder / "config.json").write_text(json.dumps({"model_type": "qwen3_vl"}))
            for name in ("tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"):
                (folder / name).write_text("{}")
            (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors"}}))
            (folder / "model-1.safetensors").write_bytes(b"weight")
            self.assertFalse(manager.downloaded("qwen3-vl-4b"))
            (folder / "model-2.safetensors").write_bytes(b"weight")
            self.assertTrue(manager.downloaded("qwen3-vl-4b"))

    def test_unknown_model_cannot_be_used_as_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = ModelManager(Path(temporary))
            with self.assertRaises(ValueError):
                manager.path("../outside")
