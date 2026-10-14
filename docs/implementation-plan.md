# NeonTag Implementation Plan

**Goal:** 实现可下载本地 Qwen3-VL 模型、测试审核、批量标注及恢复的单目录 Linux 工具。

**Architecture:** 原生页面调用 Flask JSON API。图片处理、模型加载、持久任务各自独立，只有一个推理线程；Waitress 服务浏览和控制请求。

**Tech Stack:** Python 3.10–3.13、Flask、Waitress、Pillow、PyTorch、Transformers >=4.57、ModelScope。

**Spec:** `docs/design.md`

## Constraints

- `python main.py`，`0.0.0.0:8080`。
- 预设 4B / 8B 下载进 `models`，另可输入本地 Qwen2 / 2.5 / 3 VL 标准权重路径。
- 正式输出只有图片和标签；备份、预览测试及任务记录在 `.state`。
- 输出不覆盖他人的文件，路径不能与输入或内部目录重叠。
- 默认所有图片、TXT、合放、1024 长边、随机两张。

## Tasks

- [x] 图片与标签：先用真实临时图片测试尺寸、透明背景、备份、命名冲突、失败回滚、标签数量及触发词。实现 `neontag/files.py` 的 scan_images、preprocess、normalize_tags、write_result、archive_files。运行 `python -m unittest discover -s tests -v`。
- [x] 任务与模型：先测试预览不生成、取消继续、重启恢复、失败重试、输出缺失重做及输入变化拒绝。实现 `neontag/models.py` 的 ModelManager 和 `neontag/jobs.py` 的 JobManager；外部推理在测试中注入可控函数，磁盘任务及输出使用真实文件。
- [x] 服务与页面：测试 Flask 的扫描、预览、开始、控制、审核修改、压缩及路径校验。实现 `neontag/web.py`、`templates/index.html`、`static/app.js`、`static/style.css` 和 main.py。
- [x] 验证与交付：运行单元/集成测试、Python 编译检查、JS 语法检查和浏览器交互检查。补充 requirements.txt、README.md 及精确的部署步骤，记录 GPU 推理验证限制。

## Verification result

当前验证记录见 `docs/verification.md`。GPU 推理和 ModelScope 大模型下载未在本机执行，需 Linux 服务器有完整依赖和足够资源。

## 工作台优化

- [x] 本地 Qwen VL 目录验证、模型类选择及任务路径固定。
- [x] 日间主题及浏览器主题记忆。
- [x] 实时任务日志、失败原因、恢复日志及持久化。
- [x] 固定窗口布局、任务优先、折叠设置 / 测试及可放大日志。
- [x] 任务展开分页图片、生成中查看结果及左右审核弹窗。
- [x] 本地路径规范化和异步保存竞态回归测试。
