import gc
import json
import re
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path


MODELS = {
    "qwen3-vl-4b": {"name": "Qwen3-VL-4B-Instruct", "repo": "Qwen/Qwen3-VL-4B-Instruct"},
    "qwen3-vl-8b": {"name": "Qwen3-VL-8B-Instruct", "repo": "Qwen/Qwen3-VL-8B-Instruct"},
}
QWEN_FAMILIES = {"qwen2_vl", "qwen2_5_vl", "qwen3_vl"}


class ModelManager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.inference_lock = threading.Lock()
        self.states = {key: {"status": "missing", "message": "未下载", "logs": deque(maxlen=30)} for key in MODELS}
        self.download_thread = None
        self.loaded_id = None
        self.model = None
        self.processor = None

    def path(self, model_id):
        if isinstance(model_id, str) and model_id.startswith("local:"):
            value = model_id[len("local:"):].strip()
            if not value:
                raise ValueError("请输入本地模型路径")
            return Path(value).expanduser().resolve()
        if model_id not in MODELS:
            raise ValueError("请选择有效模型")
        return self.root / "models" / MODELS[model_id]["name"]

    def downloaded(self, model_id):
        folder = self.path(model_id)
        try:
            config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
            if config.get("model_type") not in QWEN_FAMILIES:
                return False
            required = ["tokenizer_config.json", "tokenizer.json", "preprocessor_config.json"]
            if any(not (folder / name).is_file() or (folder / name).stat().st_size == 0 for name in required):
                return False
            index = folder / "model.safetensors.index.json"
            weights = list(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values()) if index.exists() else ["model.safetensors"]
            return bool(weights) and all(Path(name).name == name and (folder / name).is_file() and (folder / name).stat().st_size > 0 for name in weights)
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def check_local(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请输入本地模型路径")
        folder = Path(value.strip()).expanduser().resolve()
        model_id = "local:" + str(folder)
        try:
            config = json.loads((folder / "config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("本地模型目录缺少有效的 config.json") from None
        family = config.get("model_type")
        if family not in QWEN_FAMILIES:
            raise ValueError("仅支持 Qwen2-VL、Qwen2.5-VL 和 Qwen3-VL 视觉语言模型")
        if config.get("quantization_config"):
            raise ValueError("暂不支持预量化模型，请使用原始 BF16/FP16 权重")
        if not self.downloaded(model_id):
            raise ValueError("本地模型不完整，请检查处理器、词表和全部 safetensors 权重")
        with self.lock:
            state = self.states.setdefault(model_id, {"status": "downloaded", "message": "本地模型已就绪", "logs": deque(maxlen=30)})
            return {"id": model_id, "name": folder.name, "path": str(folder), "family": family, "downloaded": True, "status": "ready" if self.loaded_id == model_id else state["status"], "message": state["message"], "logs": list(state["logs"])}

    def set_state(self, model_id, status, message):
        with self.lock:
            self.states.setdefault(model_id, {"logs": deque(maxlen=30)}).update(status=status, message=message)

    def catalogue(self):
        with self.lock:
            result = []
            for model_id, details in MODELS.items():
                state = self.states[model_id]
                present = self.downloaded(model_id)
                status = state["status"]
                if status not in {"downloading", "loading", "error"}:
                    status = "ready" if self.loaded_id == model_id else "downloaded" if present else "missing"
                result.append({"id": model_id, **details, "path": str(self.path(model_id)), "downloaded": present, "status": status, "message": state["message"] if status in {"downloading", "loading", "error"} else {"ready": "已加载", "downloaded": "已下载", "missing": "未下载"}[status], "logs": list(state["logs"])})
            return result

    def ensure(self, model_id):
        if isinstance(model_id, str) and model_id.startswith("local:"):
            self.check_local(str(self.path(model_id)))
            return
        folder = self.path(model_id)
        with self.lock:
            if self.downloaded(model_id):
                self.set_state(model_id, "downloaded", "已下载")
                return
            if self.states[model_id]["status"] == "downloading":
                return
            if self.download_thread and self.download_thread.is_alive():
                raise ValueError("另一个模型正在下载，请等待完成")
            folder.mkdir(parents=True, exist_ok=True)
            self.states[model_id]["logs"].clear()
            self.set_state(model_id, "downloading", "正在从 ModelScope 下载")
            self.download_thread = threading.Thread(target=self.download, args=(model_id,), daemon=True)
            self.download_thread.start()

    def download(self, model_id):
        script = "from modelscope import snapshot_download; snapshot_download(model_id=" + repr(MODELS[model_id]["repo"]) + ", local_dir=" + repr(str(self.path(model_id))) + ")"
        try:
            process = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
            for line in process.stdout:
                clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()[-600:]
                if clean:
                    with self.lock:
                        self.states[model_id]["logs"].append(clean)
                        self.states[model_id]["message"] = clean
            code = process.wait()
            if code or not self.downloaded(model_id):
                raise ValueError("模型下载未完成，请检查网络、磁盘及下载日志后重试")
            self.set_state(model_id, "downloaded", "下载完成")
        except Exception as error:
            self.set_state(model_id, "error", str(error))

    def load(self, model_id):
        if self.loaded_id == model_id and self.model is not None:
            return
        if not self.downloaded(model_id):
            raise ValueError("模型尚未下载完成，请先检查并下载模型")
        self.set_state(model_id, "loading", "正在加载模型")
        try:
            import torch
            from transformers import AutoProcessor, Qwen2VLForConditionalGeneration, Qwen2_5_VLForConditionalGeneration, Qwen3VLForConditionalGeneration

            self.model = None
            self.processor = None
            self.loaded_id = None
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16 if torch.cuda.is_available() else torch.float32
            folder = str(self.path(model_id))
            config = json.loads((Path(folder) / "config.json").read_text(encoding="utf-8"))
            family = config["model_type"]
            classes = {"qwen2_vl": Qwen2VLForConditionalGeneration, "qwen2_5_vl": Qwen2_5_VLForConditionalGeneration, "qwen3_vl": Qwen3VLForConditionalGeneration}
            patch = 32 if family == "qwen3_vl" else 28
            self.processor = AutoProcessor.from_pretrained(folder, local_files_only=True, min_pixels=256 * patch * patch, max_pixels=1280 * patch * patch)
            self.model = classes[family].from_pretrained(folder, dtype=dtype, device_map="auto" if torch.cuda.is_available() else "cpu", local_files_only=True, attn_implementation="sdpa")
            self.model.eval()
            self.loaded_id = model_id
            self.set_state(model_id, "ready", "已加载")
        except Exception as error:
            self.model = None
            self.processor = None
            self.loaded_id = None
            gc.collect()
            self.set_state(model_id, "error", str(error))
            raise RuntimeError("模型加载失败：" + str(error)) from error

    def generate(self, model_id, path, prompt, minimum, stop):
        import torch
        from PIL import Image, ImageOps
        from transformers import StoppingCriteria, StoppingCriteriaList

        class RequestedStop(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return stop.is_set()

        with self.inference_lock:
            self.load(model_id)
            if stop.is_set():
                return ""
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
            instruction = prompt + f"\nReturn at least {minimum} distinct visual tags, separated by English commas. Output only the tags. No headings, explanations, numbering, or invented details."
            messages = [{"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": instruction}]}]
            inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt").to(self.model.device)
            with torch.inference_mode():
                generated = self.model.generate(**inputs, max_new_tokens=min(2048, max(384, minimum * 12)), do_sample=False, stopping_criteria=StoppingCriteriaList([RequestedStop()]))
            trimmed = generated[:, inputs["input_ids"].shape[1]:]
            return self.processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]
