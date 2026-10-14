import copy
import json
import os
import random
import threading
import time
import uuid
from pathlib import Path

from .files import TAG_FORMATS, archive_files, directory, fingerprint, integer, normalize_tags, overlaps, result_paths, scan_images, tag_content, write_result
from .models import MODELS, ModelManager


DEFAULT_PROMPT = "Describe the image using concise English visual tags for image-training captions. Include the visible subject, count, appearance, clothing, pose, action, objects, surroundings, composition, viewpoint, lighting, colors, and visual style when clearly observable. Use specific descriptive phrases. Avoid guesses about identity, personality, exact location, or unseen details. Avoid duplicates and subjective praise. Return only comma-separated tags."
DEFAULTS = {"model": "qwen3-vl-4b", "model_path": "", "tag_format": "txt", "separate": False, "prompt": DEFAULT_PROMPT, "min_tags": 20, "trigger": ""}
BUSY = {"running", "pausing", "cancelling"}


class JobManager:
    def __init__(self, root, model):
        self.root = Path(root).resolve()
        self.state = self.root / ".state"
        self.records = self.state / "jobs"
        self.records.mkdir(parents=True, exist_ok=True)
        self.model = model
        self.lock = threading.RLock()
        self.operation_lock = threading.RLock()
        self.jobs = {}
        self.thread = None
        self.active = None
        self.stop = threading.Event()
        self.load_errors = []
        for location in self.records.glob("*.json"):
            try:
                job = json.loads(location.read_text(encoding="utf-8"))
                if job["status"] in BUSY:
                    job["status"] = "paused"
                    self.log(job, "服务重启，任务已恢复为暂停状态")
                for item in job["items"]:
                    if item["status"] == "generating":
                        item["status"] = "pending"
                self.jobs[job["id"]] = job
                self.save(job)
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.load_errors.append(f"{location.name}: {error}")

    def save(self, job):
        job["updated"] = time.time()
        location = self.records / (job["id"] + ".json")
        temporary = location.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(job, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, location)

    def log(self, job, message, level="info"):
        entries = job.setdefault("logs", [])
        sequence = job.get("log_sequence", 0) + 1
        job["log_sequence"] = sequence
        entries.append({"id": sequence, "time": time.time(), "level": level, "message": str(message)[:2000]})
        del entries[:-500]

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("任务不存在")
            job = copy.deepcopy(self.jobs[job_id])
            job["done"] = sum(item["status"] == "done" for item in job["items"])
            job["failed"] = sum(item["status"] == "failed" for item in job["items"])
            job["total"] = len(job["items"])
            job.setdefault("logs", [])
            return job

    def list(self):
        with self.lock:
            result = []
            for job in sorted(self.jobs.values(), key=lambda entry: entry["created"], reverse=True):
                summary = self.get(job["id"])
                summary.pop("items")
                summary.pop("logs", None)
                result.append(summary)
            return result

    def validate_paths(self, source, output=None):
        source = directory(source, True)
        protected = [self.root / name for name in (".state", "models", "neontag", "templates", "static", "docs", "tests")]
        if any(overlaps(source, path) for path in protected):
            raise ValueError("图片目录不能与项目内部目录重叠")
        if output is not None:
            output = directory(output)
            if overlaps(source, output):
                raise ValueError("输入与输出目录不能重叠")
            if output == self.root or output in self.root.parents or any(overlaps(output, path) for path in protected):
                raise ValueError("输出目录不能与项目内部目录重叠")
        return source, output

    def settings(self, values):
        if not isinstance(values, dict):
            raise ValueError("设置格式错误")
        result = {**DEFAULTS, **{key: values[key] for key in (*DEFAULTS, "input", "output") if key in values}}
        source, output = self.validate_paths(result.get("input"), result.get("output"))
        if output is None:
            raise ValueError("请输入输出文件夹路径")
        result.update(input=str(source), output=str(output))
        if result["model"] == "local":
            checked = ModelManager(self.root).check_local(result.get("model_path"))
            result["model_path"] = checked["path"]
            if overlaps(source, Path(checked["path"])) or overlaps(output, Path(checked["path"])):
                raise ValueError("图片和输出目录不能与本地模型目录重叠")
        elif result["model"] not in MODELS:
            raise ValueError("请选择有效模型")
        if result["tag_format"] not in TAG_FORMATS or not isinstance(result["separate"], bool):
            raise ValueError("标签格式或存放方式无效")
        result["min_tags"] = integer(result["min_tags"], 1, 150, "最少标签数量")
        if not isinstance(result["prompt"], str) or not result["prompt"].strip() or len(result["prompt"]) > 12000:
            raise ValueError("提示词长度应为 1–12000 个字符")
        if not isinstance(result["trigger"], str) or len(result["trigger"]) > 200 or any(character in result["trigger"] for character in "\n\r"):
            raise ValueError("触发词格式错误")
        return result

    def create(self, values, kind, selected=None, sample_count=2):
        with self.operation_lock, self.lock:
            settings = self.settings(values)
            if kind not in {"bulk", "test"}:
                raise ValueError("任务类型错误")
            if self.active:
                raise ValueError("请先暂停或取消当前任务")
            sources = scan_images(settings["input"])
            if selected is not None:
                if not isinstance(selected, list) or not selected or any(not isinstance(name, str) for name in selected):
                    raise ValueError("请至少选择一张图片")
                known = {path.name for path in sources}
                if not set(selected) <= known:
                    raise ValueError("所选图片已变化，请重新读取目录")
                sources = [path for path in sources if path.name in selected]
            if not sources:
                raise ValueError("文件夹中没有图片")
            if kind == "test":
                count = integer(sample_count, 1, 50, "测试数量")
                sources = random.sample(sources, min(count, len(sources)))
            stems = [path.stem.casefold() for path in sources]
            if len(stems) != len(set(stems)):
                raise ValueError("存在同名不同格式的图片，请先统一重命名")
            job_id = uuid.uuid4().hex
            output = self.state / "tests" / job_id if kind == "test" else Path(settings["output"])
            if kind == "bulk":
                for old in self.jobs.values():
                    if old["kind"] == "bulk" and overlaps(output, Path(old["output"])):
                        raise ValueError("输出目录已有任务，请继续该任务或使用新目录")
                if output.exists() and any(output.iterdir()):
                    raise ValueError("输出目录必须为空，请选择新目录")
            items = [{"id": uuid.uuid4().hex, "name": path.name, "source": str(path), "source_hash": fingerprint(path), "status": "pending", "tags": "", "error": "", "files": [], "hashes": [], "owned": False} for path in sources]
            job = {"id": job_id, "kind": kind, "status": "preview" if kind == "test" else "ready", "created": time.time(), "settings": settings, "output": str(output), "items": items, "error": ""}
            self.jobs[job_id] = job
            self.log(job, f"创建{'测试' if kind == 'test' else '批量'}任务，共 {len(items)} 张图片")
            self.save(job)
            return self.get(job_id)

    def verify_output(self, job):
        output = Path(job["output"])
        if not output.exists():
            return
        if not output.is_dir():
            raise ValueError("输出路径已被替换为文件")
        allowed = set()
        for item in job["items"]:
            if item["owned"]:
                allowed.update(result_paths(item["source"], output, job["settings"]["tag_format"], job["settings"]["separate"]))
        permitted_dirs = {output / "images", output / "labels"} if job["settings"]["separate"] else set()
        for path in output.rglob("*"):
            if path.is_symlink() or (path.is_file() and path not in allowed) or (path.is_dir() and path not in permitted_dirs):
                raise ValueError("输出目录有其他文件，请移走后继续")

    def start(self, job_id):
        with self.operation_lock, self.lock:
            self.get(job_id)
            job = self.jobs[job_id]
            if self.active:
                raise ValueError("已有任务正在运行")
            self.verify_output(job)
            for item in job["items"]:
                valid = item["status"] == "done" and len(item["files"]) == 2 and len(item["hashes"]) == 2
                if valid:
                    try:
                        valid = all(Path(path).is_file() and fingerprint(path) == digest for path, digest in zip(item["files"], item["hashes"]))
                    except OSError:
                        valid = False
                if not valid:
                    item.update(status="pending", error="")
            job.update(status="running", error="")
            pending = sum(item["status"] != "done" for item in job["items"])
            self.log(job, f"开始任务：待处理 {pending} 张，跳过完整结果 {len(job['items']) - pending} 张")
            reference = job["settings"]["model_path"] if job["settings"]["model"] == "local" else job["settings"]["model"]
            self.log(job, "准备模型：" + reference)
            self.active = job_id
            self.stop.clear()
            self.save(job)
            self.thread = threading.Thread(target=self.run, args=(job_id,), daemon=True)
            self.thread.start()
            return self.get(job_id)

    def run(self, job_id):
        job = self.jobs[job_id]
        settings = job["settings"]
        try:
            for item in job["items"]:
                if self.stop.is_set():
                    break
                if item["status"] == "done":
                    continue
                with self.lock:
                    item["status"] = "generating"
                    self.log(job, "正在标注：" + item["name"])
                    self.save(job)
                try:
                    source = Path(item["source"])
                    if not source.is_file() or fingerprint(source) != item["source_hash"]:
                        raise ValueError("输入图片已变化，请新建任务")
                    tags = None
                    for attempt in range(3):
                        prompt = settings["prompt"]
                        if attempt:
                            prompt += "\nThe previous response had too few distinct visual tags. Describe more observable details."
                            with self.lock:
                                self.log(job, f"标签数量不足，重试 {attempt}/2：{item['name']}", "warning")
                                self.save(job)
                        reference = "local:" + settings["model_path"] if settings["model"] == "local" else settings["model"]
                        raw = self.model.generate(reference, source, prompt, settings["min_tags"], self.stop)
                        if self.stop.is_set():
                            break
                        try:
                            tags = normalize_tags(raw, settings["trigger"], settings["min_tags"])
                            break
                        except ValueError:
                            if attempt == 2:
                                raise
                    if self.stop.is_set():
                        with self.lock:
                            item["status"] = "pending"
                            self.save(job)
                        break
                    if fingerprint(source) != item["source_hash"]:
                        raise ValueError("推理期间输入图片发生变化，请新建任务")
                    with self.lock:
                        owned = item["owned"]
                        targets = result_paths(source, job["output"], settings["tag_format"], settings["separate"])
                        if not owned and any(path.exists() for path in targets):
                            raise ValueError("输出文件已存在，请移走冲突文件")
                        item["owned"] = True
                        self.save(job)
                        files = write_result(source, job["output"], tags, settings["tag_format"], settings["separate"], owned=owned)
                        item.update(status="done", tags=tags, error="", files=files, hashes=[fingerprint(path) for path in files])
                        self.log(job, "标注完成：" + item["name"])
                        self.save(job)
                except Exception as error:
                    with self.lock:
                        item.update(status="pending" if self.stop.is_set() else "failed", error="" if self.stop.is_set() else str(error))
                        self.log(job, f"{'已中断' if self.stop.is_set() else '标注失败'}：{item['name']} · {error}", "warning" if self.stop.is_set() else "error")
                        self.save(job)
            with self.lock:
                if self.stop.is_set():
                    job["status"] = "cancelled" if job["status"] == "cancelling" else "paused"
                else:
                    job["status"] = "completed_with_errors" if any(item["status"] == "failed" for item in job["items"]) else "completed"
                completed = sum(item["status"] == "done" for item in job["items"])
                failed = sum(item["status"] == "failed" for item in job["items"])
                self.log(job, f"任务{'已取消，可继续' if job['status'] == 'cancelled' else '已暂停' if job['status'] == 'paused' else '完成'}：成功 {completed} 张，失败 {failed} 张")
                self.save(job)
        except Exception as error:
            with self.lock:
                job.update(status="paused", error=str(error))
                self.log(job, "任务异常：" + str(error), "error")
                self.save(job)
        finally:
            with self.lock:
                self.active = None

    def control(self, job_id, action):
        with self.lock:
            self.get(job_id)
            if action not in {"pause", "cancel"}:
                raise ValueError("操作无效")
            job = self.jobs[job_id]
            if self.active == job_id:
                job["status"] = "pausing" if action == "pause" else "cancelling"
                self.stop.set()
            elif job["status"] not in {"completed", "completed_with_errors"}:
                job["status"] = "paused" if action == "pause" else "cancelled"
            self.log(job, "收到暂停请求" if action == "pause" else "收到取消请求，已完成结果保留")
            self.save(job)
            return self.get(job_id)

    def edit(self, job_id, item_id, tags):
        with self.operation_lock, self.lock:
            self.get(job_id)
            job = self.jobs[job_id]
            if self.active == job_id:
                raise ValueError("请先暂停任务再修改标签")
            item = next((entry for entry in job["items"] if entry["id"] == item_id), None)
            if not item or item["status"] != "done":
                raise ValueError("此图片尚未生成标签")
            if not isinstance(tags, str) or len(tags) > 20000:
                raise ValueError("标签长度无效")
            normalized = normalize_tags(tags, job["settings"]["trigger"], job["settings"]["min_tags"])
            tag_path = Path(item["files"][1])
            if tag_path.is_symlink() or not tag_path.is_file():
                raise ValueError("标签文件已变化，请继续任务重建")
            temporary = Path(job["output"]).parent / (".neontag-" + uuid.uuid4().hex + ".tmp")
            content = tag_content(normalized, job["settings"]["tag_format"])
            try:
                with temporary.open("x", encoding="utf-8", newline="") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, tag_path)
            finally:
                temporary.unlink(missing_ok=True)
            item.update(tags=normalized, hashes=[fingerprint(path) for path in item["files"]])
            self.log(job, "已保存人工标签：" + item["name"])
            self.save(job)
            return self.get(job_id)

    def archive(self, job_id, name, archive_format):
        with self.operation_lock, self.lock:
            self.get(job_id)
            job = self.jobs[job_id]
            if self.active == job_id:
                raise ValueError("请先暂停或完成任务再压缩")
            self.verify_output(job)
            files = []
            for item in job["items"]:
                if item["status"] == "done":
                    if any(not Path(path).is_file() or fingerprint(path) != digest for path, digest in zip(item["files"], item["hashes"])):
                        raise ValueError("输出文件已变化，请继续任务校验后再压缩")
                    files.extend(item["files"])
            path = archive_files(job["output"], files, name, archive_format, self.state)
            archive = {"id": uuid.uuid4().hex, "name": path.name, "path": str(path), "size": path.stat().st_size, "format": archive_format}
            job.setdefault("archives", []).append(archive)
            self.log(job, "压缩完成：" + path.name)
            self.save(job)
            return copy.deepcopy(archive)

    def check_preprocess(self, source):
        source, _ = self.validate_paths(source)
        with self.lock:
            for job in self.jobs.values():
                if overlaps(source, Path(job["settings"]["input"])) and job["status"] not in {"completed", "completed_with_errors"}:
                    raise ValueError("此图片目录有未完成任务，请完成任务或改用其他目录")
