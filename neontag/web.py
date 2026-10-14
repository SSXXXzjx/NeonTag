import io
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file
from PIL import Image, ImageOps
from werkzeug.exceptions import HTTPException

from .files import preprocess, scan_images
from .jobs import DEFAULTS, JobManager
from .models import ModelManager


def create_app(root=None, model=None):
    assets = Path(__file__).resolve().parent.parent
    root = Path(root or assets).resolve()
    app = Flask(__name__, template_folder=str(assets / "templates"), static_folder=str(assets / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
    models = model or ModelManager(root)
    jobs = JobManager(root, models)
    app.extensions.update(jobs=jobs, models=models)
    images = {}
    image_lock = threading.Lock()

    def body():
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求内容必须为对象")
        return data

    def register_image(path):
        with image_lock:
            if len(images) >= 20000:
                images.pop(next(iter(images)))
            token = secrets.token_urlsafe(18)
            images[token] = Path(path)
        return "/api/images/" + token

    def job_response(job):
        for item in job["items"]:
            item["url"] = f'/api/jobs/{job["id"]}/images/{item["id"]}'
        job["archives"] = [archive_response(job["id"], archive) for archive in job.get("archives", [])]
        return jsonify(job)

    def archive_response(job_id, archive):
        return {**archive, "download_url": f'/api/jobs/{job_id}/archives/{archive["id"]}/download'}

    def thumbnail(path):
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
                image.thumbnail((640, 640), Image.Resampling.LANCZOS)
                content = io.BytesIO()
                image.save(content, "JPEG", quality=85)
            content.seek(0)
            return send_file(content, mimetype="image/jpeg", max_age=0)
        except (OSError, ValueError):
            abort(404, "图片不存在或无法读取")

    @app.before_request
    def origin_check():
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and (urlsplit(origin).scheme, urlsplit(origin).netloc) != (request.scheme, request.host):
                abort(403, "请求来源无效")

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        if request.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        app.logger.exception("请求处理失败")
        return jsonify(error="操作失败：" + str(error)), 500

    @app.get("/")
    def home():
        return render_template("index.html")

    @app.get("/api/config")
    def config():
        return jsonify(defaults=DEFAULTS, models=models.catalogue(), root=str(root), archive_formats=["zip", "tar.gz", "rar"], recovery_errors=jobs.load_errors)

    @app.get("/api/models")
    def model_list():
        return jsonify(models.catalogue())

    @app.post("/api/models/ensure")
    def ensure_model():
        if jobs.active:
            raise ValueError("请先暂停当前任务再检查模型")
        models.ensure(body().get("model"))
        return jsonify(models.catalogue())

    @app.post("/api/models/local")
    def check_local_model():
        data = body()
        checker = models if isinstance(models, ModelManager) else ModelManager(root)
        return jsonify(checker.check_local(data.get("path")))

    @app.post("/api/scan")
    def scan():
        data = body()
        source, _ = jobs.validate_paths(data.get("input"))
        paths = scan_images(source)
        return jsonify(input=str(source), total=len(paths), images=[{"name": path.name, "url": register_image(path)} for path in paths])

    @app.post("/api/preprocess")
    def process_images():
        data = body()
        with jobs.operation_lock:
            source, _ = jobs.validate_paths(data.get("input"))
            jobs.check_preprocess(source)
            result = preprocess(source, jobs.state, data.get("long_edge", 1024), data.get("prefix", "image"), data.get("format", "jpg"))
        return jsonify(result)

    @app.get("/api/images/<token>")
    def scanned_image(token):
        with image_lock:
            path = images.get(token)
        if not path:
            abort(404)
        return thumbnail(path)

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify(jobs.list())

    @app.post("/api/jobs")
    def new_job():
        data = body()
        return job_response(jobs.create(data.get("settings", {}), data.get("kind"), selected=data.get("selected"), sample_count=data.get("sample_count", 2)))

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        return job_response(jobs.get(job_id))

    @app.post("/api/jobs/<job_id>/start")
    def start_job(job_id):
        body()
        return job_response(jobs.start(job_id))

    @app.post("/api/jobs/<job_id>/control")
    def control_job(job_id):
        return job_response(jobs.control(job_id, body().get("action")))

    @app.patch("/api/jobs/<job_id>/items/<item_id>")
    def edit_item(job_id, item_id):
        return job_response(jobs.edit(job_id, item_id, body().get("tags")))

    @app.post("/api/jobs/<job_id>/archive")
    def archive_job(job_id):
        data = body()
        archive = jobs.archive(job_id, data.get("name", "dataset"), data.get("format", "zip"))
        return jsonify(archive_response(job_id, archive))

    @app.get("/api/jobs/<job_id>/archives/<archive_id>/download")
    def download_archive(job_id, archive_id):
        job = jobs.get(job_id)
        archive = next((entry for entry in job.get("archives", []) if entry["id"] == archive_id), None)
        if not archive:
            abort(404, "压缩包不存在")
        path = Path(archive["path"])
        expected_parent = Path(job["output"]).parent.resolve()
        if path.is_symlink() or not path.is_file() or path.resolve().parent != expected_parent or path.name != archive["name"]:
            abort(404, "压缩包不存在或已移动")
        return send_file(path, as_attachment=True, download_name=archive["name"], conditional=True)

    @app.get("/api/jobs/<job_id>/images/<item_id>")
    def job_image(job_id, item_id):
        job = jobs.get(job_id)
        item = next((entry for entry in job["items"] if entry["id"] == item_id), None)
        if not item:
            abort(404)
        output = Path(item["files"][0]) if item["files"] else None
        return thumbnail(output if output and output.is_file() else item["source"])

    return app
