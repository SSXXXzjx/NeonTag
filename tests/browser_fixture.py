import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from waitress import serve

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neontag.web import create_app


class BrowserModel:
    def catalogue(self):
        return [{"id": "qwen3-vl-4b", "name": "Qwen3-VL-4B-Instruct", "status": "downloaded", "downloaded": True, "message": "浏览器测试替身", "logs": []}]

    def ensure(self, model_id):
        pass

    def generate(self, model_id, path, prompt, minimum, stop):
        stop.wait(1.5)
        return "cat, green grass, blue sky, daylight, outdoor, landscape, trees, clouds, wide view, soft shadows, warm light, summer, natural colors, horizon, white fur, standing, centered subject, full body, depth of field, peaceful scene, whiskers, paws"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="neontag-browser-") as temporary:
        root = Path(temporary)
        source = root / "input"
        source.mkdir()
        for index, color in enumerate(("#354d28", "#394348", "#63513c"), 1):
            image = Image.new("RGB", (600, 400), color)
            draw = ImageDraw.Draw(image)
            draw.ellipse((200, 100, 400, 300), fill="#c1ff57")
            draw.text((20, 20), f"NeonTag test image {index}", fill="white")
            image.save(source / f"sample_{index}.png")
        print("INPUT=" + str(source), flush=True)
        print("OUTPUT=" + str(root / "output"), flush=True)
        serve(create_app(root, BrowserModel()), host="127.0.0.1", port=8081, threads=4)
