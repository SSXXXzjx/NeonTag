import csv
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import uuid
import zipfile
from pathlib import Path

from PIL import Image, ImageOps


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
TAG_FORMATS = {"txt", "json", "csv"}


def directory(value, must_exist=False):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError("请输入文件夹路径")
    path = Path(str(value).strip()).expanduser().resolve()
    if must_exist and not path.is_dir():
        raise ValueError("图片文件夹不存在")
    if path.exists() and not path.is_dir():
        raise ValueError("路径不是文件夹")
    return path


def overlaps(first, second):
    return first == second or first in second.parents or second in first.parents


def safe_name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 100:
        raise ValueError("名称长度应为 1–100 个字符")
    value = value.strip()
    if value in {".", ".."} or re.search(r'[\\/:*?"<>|\x00-\x1f]', value) or value.endswith("."):
        raise ValueError("名称不能包含路径分隔符或特殊字符")
    return value


def integer(value, minimum, maximum, label):
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须是整数") from None
    if str(number) != str(value).strip() or not minimum <= number <= maximum:
        raise ValueError(f"{label}范围为 {minimum}–{maximum}")
    return number


def scan_images(source):
    source = directory(source, True)
    return sorted(
        (path for path in source.iterdir() if path.is_file() and not path.is_symlink() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda path: (path.name.casefold(), path.name),
    )


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preprocess(source, state, long_edge=1024, prefix="image", image_format="jpg"):
    source = directory(source, True)
    state = directory(state)
    if overlaps(source, state):
        raise ValueError("图片目录不能与项目内部数据目录重叠")
    long_edge = integer(long_edge, 32, 8192, "长边")
    prefix = safe_name(prefix)
    image_format = str(image_format).lower()
    if image_format not in {"jpg", "png", "webp"}:
        raise ValueError("图片格式应为 JPG、PNG 或 WEBP")
    sources = scan_images(source)
    if not sources:
        raise ValueError("文件夹中没有图片")
    targets = [source / f"{prefix}_{index:06d}.{image_format}" for index in range(1, len(sources) + 1)]
    if any(target.exists() and target not in sources for target in targets):
        raise ValueError("重命名目标已存在，请调整前缀")
    operation = uuid.uuid4().hex
    stage = state / "staging" / operation
    backup = state / "backups" / operation
    stage.mkdir(parents=True)
    removed = []
    created = []
    try:
        for original, target in zip(sources, targets):
            try:
                with Image.open(original) as opened:
                    image = ImageOps.exif_transpose(opened)
                    ratio = long_edge / max(image.size)
                    size = tuple(max(1, round(dimension * ratio)) for dimension in image.size)
                    image = image.resize(size, Image.Resampling.LANCZOS)
                    if image_format == "jpg":
                        if image.mode in {"RGBA", "LA", "P"}:
                            image = image.convert("RGBA")
                            background = Image.new("RGB", image.size, "white")
                            background.paste(image, mask=image.getchannel("A"))
                            image = background
                        else:
                            image = image.convert("RGB")
                    elif image.mode not in {"RGB", "RGBA", "L", "LA"}:
                        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                    image.save(stage / target.name, quality=95)
            except Exception as error:
                raise ValueError(f"图片处理失败：{original.name} · {error}") from error
        backup.mkdir(parents=True)
        for original in sources:
            shutil.copy2(original, backup / original.name)
        for original in sources:
            original.unlink()
            removed.append(original)
        for target in targets:
            with (stage / target.name).open("rb") as incoming, target.open("xb") as outgoing:
                created.append(target)
                shutil.copyfileobj(incoming, outgoing)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        for original in removed:
            shutil.copy2(backup / original.name, original)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return {"count": len(sources), "backup": str(backup), "source": str(source)}


def normalize_tags(raw, trigger, minimum):
    minimum = integer(minimum, 1, 150, "最少标签数量")
    trigger = str(trigger).strip().strip(",， ")
    if any(character in trigger for character in "\n\r") or len(trigger) > 200:
        raise ValueError("触发词过长或包含换行")
    text = re.sub(r"^\s*(?:tags?|标签)\s*[:：]\s*", "", str(raw), flags=re.I)
    text = text.replace("```", "").replace("，", ",")
    values = []
    seen = {part.strip().casefold() for part in trigger.split(",") if part.strip()}
    for part in re.split(r"[,;\n]", text):
        tag = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", part).strip().strip('"')
        if tag and tag.casefold() not in seen:
            seen.add(tag.casefold())
            values.append(tag)
    if len(values) < minimum:
        raise ValueError(f"标签不足：{len(values)}/{minimum}，请调整提示词或最少数量")
    return ", ".join(([trigger] if trigger else []) + values)


def tag_content(tags, tag_format):
    if tag_format == "txt":
        return tags + "\n"
    if tag_format == "json":
        return json.dumps({"tags": [part.strip() for part in tags.split(",")]}, ensure_ascii=False, indent=2) + "\n"
    if tag_format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(["tags"])
        writer.writerow([tags])
        return stream.getvalue()
    raise ValueError("标签格式应为 TXT、JSON 或 CSV")


def result_paths(original, output, tag_format, separate):
    original, output = Path(original), Path(output)
    image_dir = output / "images" if separate else output
    tag_dir = output / "labels" if separate else output
    return image_dir / original.name, tag_dir / f"{original.stem}.{tag_format}"


def write_result(original, output, tags, tag_format, separate, owned=False):
    original, output = Path(original), Path(output)
    image_target, tag_target = result_paths(original, output, tag_format, separate)
    content = tag_content(tags, tag_format)
    for target in (image_target, tag_target):
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise ValueError("输出文件不能为符号链接")
        if target.exists() and not owned:
            raise ValueError(f"输出文件已存在：{target.name}")
    written = []
    try:
        for target in (image_target, tag_target):
            if owned:
                target.unlink(missing_ok=True)
        with original.open("rb") as incoming, image_target.open("xb") as outgoing:
            written.append(image_target)
            shutil.copyfileobj(incoming, outgoing)
        with tag_target.open("x", encoding="utf-8", newline="") as handle:
            written.append(tag_target)
            handle.write(content)
    except Exception:
        for target in written:
            target.unlink(missing_ok=True)
        raise
    return [str(image_target), str(tag_target)]


def archive_files(output, files, name, archive_format, state):
    output = directory(output, True)
    name = safe_name(name)
    extensions = {"zip": ".zip", "tar.gz": ".tar.gz", "rar": ".rar"}
    if archive_format not in extensions:
        raise ValueError("不支持的压缩格式")
    extension = extensions[archive_format]
    if name.lower().endswith(extension):
        name = name[:-len(extension)]
    safe_name(name)
    destination = output.parent / (name + extension)
    if destination.exists():
        raise ValueError("压缩包已存在，请更换名称")
    members = []
    for value in files:
        path = Path(value).resolve()
        if output not in path.parents or not path.is_file():
            raise ValueError("输出文件缺失或越过输出目录")
        members.append((path, str(Path(output.name) / path.relative_to(output))))
    if not members:
        raise ValueError("没有可压缩的已完成数据")
    stage = Path(state) / "staging" / uuid.uuid4().hex
    stage.mkdir(parents=True)
    packed = stage / ("dataset" + extension)
    try:
        if archive_format == "zip":
            with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as handle:
                for path, member in members:
                    handle.write(path, member)
        elif archive_format == "tar.gz":
            with tarfile.open(packed, "w:gz") as handle:
                for path, member in members:
                    handle.add(path, arcname=member, recursive=False)
        else:
            executable = shutil.which("rar")
            if not executable:
                raise ValueError("服务器未安装 rar，请安装后重试或选择 ZIP")
            for path, member in members:
                target = stage / member
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            result = subprocess.run([executable, "a", "-r", "-idq", str(packed), output.name], cwd=stage, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                raise ValueError("RAR 压缩失败：" + result.stderr[-500:])
        try:
            with packed.open("rb") as incoming, destination.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
        except FileExistsError:
            raise ValueError("压缩包已存在，请更换名称") from None
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return destination
