# 验证记录

2026-10-14，Windows 本地环境；前端为原生 HTML/CSS/JavaScript，后端 Python。模型生成在自动测试和浏览器测试中使用明确的可控替身。

- `python -m unittest discover -s tests -v`：35 项，34 项通过；1 项因 Windows 不允许创建符号链接跳过。
- `node tests/test_frontend.cjs`：设置比较、本地路径规范化、过期路径响应、同图保存时继续编辑及保存时切换图片均通过。
- `node --check static/app.js` 与 `python -m compileall -q main.py neontag tests` 通过。
- 浏览器检查：读取目录、全选批量标注、运行中打开待标注图片、自动更新标签、编辑保存、未保存确认、随机两张预览后开始测试、任务筛选、日间主题、本地模型目录检查、ZIP 文件卡片及下载链接。
- 1280×720、800×600、390×700 页面文档宽高均保持视口大小；设置、任务列表和日志在各自区域内滚动。检查设置收起、日志放大与还原。
- 真实临时输出包含 3 张 PNG 和 3 个 TXT；测试输出单独位于 `.state/tests`；人工修改写入对应 TXT，日志位于任务记录。
- 独立代码审查发现的本地路径匹配及异步标签保存问题已修复并加入前端回归检查。

未执行真实 GPU 模型加载、ModelScope 大模型下载及 Linux RAR 调用；这些需要服务器的完整推理依赖、权重和系统 `rar`。
