import tempfile
import io
import unittest
import zipfile
import json
from pathlib import Path

from PIL import Image

from neontag.web import create_app


class LocalModel:
    def catalogue(self):
        return [{"id": "qwen3-vl-4b", "name": "Qwen3-VL-4B-Instruct", "status": "downloaded", "downloaded": True, "message": "已下载", "logs": []}]

    def ensure(self, model_id):
        if model_id != "qwen3-vl-4b":
            raise ValueError("模型不存在")

    def generate(self, model_id, path, prompt, minimum, stop):
        return "cat, grass, sky"


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.source.mkdir()
        for name in ("a.png", "b.png"):
            Image.new("RGB", (30, 20)).save(self.source / name)
        self.app = create_app(self.root, LocalModel())
        self.client = self.app.test_client()
        self.settings = {"input": str(self.source), "output": str(self.root / "output"), "min_tags": 3}

    def tearDown(self):
        manager = self.app.extensions["jobs"]
        if manager.thread:
            manager.thread.join(5)
        self.temp.cleanup()

    def test_scan_and_thumbnail_return_only_selected_folder_images(self):
        response = self.client.post("/api/scan", json={"input": str(self.source)})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["total"], 2)
        self.assertEqual([entry["name"] for entry in data["images"]], ["a.png", "b.png"])
        image = self.client.get(data["images"][0]["url"])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(image.mimetype, "image/jpeg")
        self.assertEqual(self.client.get("/api/images/unknown").status_code, 404)

    def test_preview_confirm_edit_archive_round_trip(self):
        response = self.client.post("/api/jobs", json={"settings": self.settings, "kind": "test", "sample_count": 2})
        job = response.get_json()
        self.assertEqual(job["status"], "preview")
        self.assertFalse((self.root / "output").exists())
        self.assertEqual(self.client.post(f'/api/jobs/{job["id"]}/start', json={}).status_code, 200)
        self.app.extensions["jobs"].thread.join(5)
        generated = self.client.get(f'/api/jobs/{job["id"]}').get_json()
        self.assertEqual(generated["done"], 2)
        item = generated["items"][0]
        edited = self.client.patch(f'/api/jobs/{job["id"]}/items/{item["id"]}', json={"tags": "dog, sky, grass"})
        self.assertEqual(edited.status_code, 200)
        packed = self.client.post(f'/api/jobs/{job["id"]}/archive', json={"name": "test-data", "format": "zip"})
        self.assertEqual(packed.status_code, 200)
        self.assertTrue(Path(packed.get_json()["path"]).is_file())

    def test_archive_download_returns_zip_and_survives_restart(self):
        job = self.client.post("/api/jobs", json={"settings": self.settings, "kind": "bulk"}).get_json()
        self.client.post(f'/api/jobs/{job["id"]}/start', json={})
        self.app.extensions["jobs"].thread.join(5)
        packed = self.client.post(f'/api/jobs/{job["id"]}/archive', json={"name": "图片数据", "format": "zip"}).get_json()
        self.assertEqual(packed["name"], "图片数据.zip")
        self.assertGreater(packed["size"], 0)
        downloaded = self.client.get(packed["download_url"])
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(downloaded.data)) as archive:
            self.assertEqual(set(archive.namelist()), {"output/a.png", "output/a.txt", "output/b.png", "output/b.txt"})
        downloaded.close()
        restarted = create_app(self.root, LocalModel()).test_client()
        saved = restarted.get(f'/api/jobs/{job["id"]}').get_json()
        self.assertEqual(saved["archives"][0]["download_url"], packed["download_url"])
        download = restarted.get(saved["archives"][0]["download_url"])
        self.assertEqual(download.status_code, 200)
        download.close()
        self.assertEqual(restarted.get(f'/api/jobs/{job["id"]}/archives/unknown/download').status_code, 404)
        Path(packed["path"]).unlink()
        self.assertEqual(restarted.get(packed["download_url"]).status_code, 404)

    def test_download_rejects_archive_replaced_by_symbolic_link(self):
        job = self.client.post("/api/jobs", json={"settings": self.settings, "kind": "bulk"}).get_json()
        self.client.post(f'/api/jobs/{job["id"]}/start', json={})
        self.app.extensions["jobs"].thread.join(5)
        packed = self.client.post(f'/api/jobs/{job["id"]}/archive', json={"name": "dataset", "format": "zip"}).get_json()
        target = Path(packed["path"])
        target.unlink()
        try:
            target.symlink_to(self.source / "a.png")
        except OSError:
            self.skipTest("此系统不允许创建符号链接")
        self.assertEqual(self.client.get(packed["download_url"]).status_code, 404)

    def test_bad_json_and_cross_origin_mutations_are_rejected(self):
        response = self.client.post("/api/scan", data="bad", content_type="application/json")
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/scan", json={"input": str(self.source)}, headers={"Origin": "https://other.example"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/api/scan", json=[]).status_code, 400)

    def test_invalid_output_cannot_create_a_job(self):
        response = self.client.post("/api/jobs", json={"settings": {**self.settings, "output": str(self.source)}, "kind": "bulk"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/jobs").get_json(), [])

    def test_local_model_endpoint_validates_family_and_completeness(self):
        folder = self.root / "local-model"
        folder.mkdir()
        (folder / "config.json").write_text(json.dumps({"model_type": "qwen2_5_vl"}))
        for name in ("tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"):
            (folder / name).write_text("{}")
        (folder / "model.safetensors").write_bytes(b"fixture")
        response = self.client.post("/api/models/local", json={"path": str(folder)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["path"], str(folder.resolve()))
        self.assertEqual(response.get_json()["family"], "qwen2_5_vl")
        (folder / "model.safetensors").unlink()
        self.assertEqual(self.client.post("/api/models/local", json={"path": str(folder)}).status_code, 400)
        self.assertEqual(self.client.post("/api/models/local", json={"path": ""}).status_code, 400)
