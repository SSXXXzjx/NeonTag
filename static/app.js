"use strict";

const byId = (id) => document.getElementById(id);
const statusNames = {preview: "待确认", ready: "待开始", running: "标注中", pausing: "正在暂停", cancelling: "正在取消", paused: "已暂停", cancelled: "已取消 · 可继续", completed: "已完成", completed_with_errors: "完成 · 有失败", pending: "待标注", generating: "生成中", done: "完成", failed: "失败"};
const activeStatuses = ["running", "pausing", "cancelling"];
const state = {images: [], selected: new Set(), scannedInput: "", jobs: [], job: null, activeId: null, expanded: new Set(), cache: new Map(), itemPages: new Map(), models: [], localModel: null, defaultPrompt: "", sourceMode: "input", preview: null, page: 0, reviewJobId: null, reviewIndex: 0, reviewDirty: false, reviewVersion: "", polling: false, processing: false};
const pendingButtons = new Set();
let toastTimer;

function toast(message, error = false) {
  const target = byId("toast"); const parent = document.activeElement.closest("dialog[open]") || document.body; parent.append(target);
  target.textContent = message; target.className = error ? "error" : ""; target.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { target.hidden = true; }, error ? 10000 : 5000);
}

async function api(path, data, method = "POST") {
  const response = await fetch(path, data === undefined ? {} : {method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  let result;
  try { result = await response.json(); } catch { throw new Error("服务器返回无效响应"); }
  if (!response.ok) throw new Error(result.error || "请求失败");
  return result;
}

async function busy(button, work) {
  if (pendingButtons.has(button)) return;
  pendingButtons.add(button); button.disabled = true;
  const text = button.textContent; button.textContent = "处理中…";
  try { await work(); } catch (error) { toast(error.message, true); }
  finally { pendingButtons.delete(button); button.textContent = text; button.disabled = false; updateControls(); }
}

function normalizedPath(value) {
  const path = value.trim().replaceAll("\\", "/").replace(/\/+$/, "");
  return /^[a-z]:\//i.test(path) ? path.toLowerCase() : path;
}

function sameSettings(current, saved) {
  return Object.keys(current).every((key) => ["input", "output", "model_path"].includes(key) ? normalizedPath(current[key] || "") === normalizedPath(saved[key] || "") : current[key] === saved[key]);
}

function settings() {
  const values = {model: byId("model").value, model_path: byId("model").value === "local" ? byId("modelPath").value.trim() : "", input: byId("input").value.trim(), output: byId("output").value.trim(), tag_format: byId("tagFormat").value, separate: byId("separate").checked, prompt: byId("prompt").value.trim(), min_tags: Number(byId("minTags").value), trigger: byId("triggerEnabled").checked ? byId("trigger").value.trim() : ""};
  if (!values.input || !values.output) throw new Error("请输入图片目录和输出目录");
  if (!values.prompt) throw new Error("请输入提示词");
  if (!Number.isInteger(values.min_tags) || values.min_tags < 1 || values.min_tags > 150) throw new Error("最少标签数量范围为 1–150");
  if (values.model === "local" && !values.model_path) throw new Error("请输入本地模型路径");
  return values;
}

function selectedNames() {
  if (normalizedPath(state.scannedInput) !== normalizedPath(byId("input").value)) throw new Error("请先读取当前图片目录");
  if (!state.selected.size) throw new Error("请至少选择一张图片");
  return state.selected.size === state.images.length ? null : [...state.selected];
}

function confirmation(title, text) {
  byId("confirmTitle").textContent = title; byId("confirmBody").textContent = text;
  const dialog = byId("confirmDialog"); dialog.returnValue = "cancel"; dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue === "confirm"), {once: true}));
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function badge(status) {
  const element = node("span", "badge", statusNames[status] || status); element.dataset.status = status; return element;
}

function modelName(job) {
  return job.settings.model === "local" ? job.settings.model_path.replaceAll("\\", "/").split("/").pop() : job.settings.model === "qwen3-vl-4b" ? "Qwen3-VL · 4B" : "Qwen3-VL · 8B";
}

function jobName(job) { return `${job.kind === "test" ? "测试" : "批量标注"} · ${job.id.slice(0, 6)}`; }

function chosenModel() {
  if (byId("model").value === "local") return state.localModel && normalizedPath(state.localModel.path) === normalizedPath(byId("modelPath").value) ? state.localModel : null;
  return state.models.find((entry) => entry.id === byId("model").value);
}

function modelReady() { const model = chosenModel(); return Boolean(model && model.downloaded && !["downloading", "loading"].includes(model.status)); }

function showModels(models) {
  state.models = models;
  const local = byId("model").value === "local"; byId("localModelBox").hidden = !local;
  const model = chosenModel();
  const names = {missing: "未下载", downloading: "下载中", downloaded: "已就绪", loading: "加载中", ready: "已加载", error: "错误"};
  byId("modelBadge").textContent = model ? names[model.status] || model.status : "待检查";
  byId("modelBadge").classList.toggle("error", Boolean(model && model.status === "error"));
  byId("modelMessage").textContent = model ? model.message : local ? "支持 Qwen2 / 2.5 / 3 VL 标准权重" : "选择模型后检查";
  byId("modelLogsBox").hidden = !model || !model.logs.length;
  byId("modelLogs").textContent = model ? model.logs.join("\n") : "";
  if (!pendingButtons.has(byId("ensureModel"))) byId("ensureModel").textContent = local ? "检查本地模型" : "检查 / 下载";
  updateControls();
}

async function checkModel() {
  if (byId("model").value === "local") {
    const requestedPath = byId("modelPath").value;
    const model = await api("/api/models/local", {path: requestedPath.trim()});
    if (byId("model").value !== "local" || byId("modelPath").value !== requestedPath) return;
    state.localModel = model; byId("modelPath").value = model.path;
  }
  else state.models = await api("/api/models/ensure", {model: byId("model").value});
  showModels(state.models);
}

function updateControls() {
  const active = Boolean(state.activeId); const job = state.job; const ownActive = job && activeStatuses.includes(job.status);
  byId("startBulk").disabled = active || !modelReady() || state.processing;
  byId("previewTest").disabled = active || state.processing;
  byId("preprocess").disabled = active || state.processing;
  byId("ensureModel").disabled = active || Boolean(chosenModel() && ["downloading", "loading"].includes(chosenModel().status));
  byId("pauseJob").disabled = !job || job.status !== "running";
  byId("cancelJob").disabled = !job || !["running", "paused", "ready", "preview", "pausing"].includes(job.status);
  byId("resumeJob").disabled = !job || active || state.processing;
  byId("restoreJob").disabled = !job;
  byId("archive").disabled = !job || ownActive || !job.done || state.processing;
  byId("confirmTest").disabled = active || !modelReady() || state.processing;
  byId("openSource").disabled = !state.images.length;
  byId("model").disabled = active; byId("modelPath").disabled = active;
  if (byId("reviewDialog").open) {
    const review = state.cache.get(state.reviewJobId); const item = review && review.items[state.reviewIndex];
    byId("saveReview").disabled = !item || item.status !== "done" || activeStatuses.includes(review.status);
  }
  for (const button of pendingButtons) button.disabled = true;
}

function selectionSummary() {
  byId("imageCount").textContent = `${state.images.length} 张`;
  byId("selectionCount").textContent = state.images.length ? `已选择 ${state.selected.size} / ${state.images.length} 张` : "尚未读取";
  byId("startBulk").textContent = state.selected.size === state.images.length ? "开始全部打标签 ↗" : `开始标注 ${state.selected.size} 张 ↗`;
  updateControls();
}

async function scan() {
  const result = await api("/api/scan", {input: byId("input").value.trim()});
  state.images = result.images; state.selected = new Set(result.images.map((image) => image.name));
  state.scannedInput = result.input; byId("input").value = result.input; selectionSummary();
  if (!result.total) toast("此目录没有图片", true); else toast(`已读取 ${result.total} 张图片，默认全选`);
}

function drawGallery() {
  const test = state.sourceMode === "test"; const images = test ? state.preview.items : state.images;
  byId("sourceTitle").textContent = test ? "测试图片 · 确认后开始" : "图片选择";
  byId("allSelectRow").hidden = test; byId("confirmTest").hidden = !test;
  byId("selectAll").checked = state.selected.size === state.images.length;
  byId("selectAll").indeterminate = state.selected.size > 0 && state.selected.size < state.images.length;
  const pages = Math.max(1, Math.ceil(images.length / 24)); state.page = Math.min(state.page, pages - 1);
  const gallery = byId("gallery"); gallery.replaceChildren();
  for (const image of images.slice(state.page * 24, (state.page + 1) * 24)) {
    const card = node("figure", "image-card"); const photo = node("img"); photo.src = image.url; photo.alt = image.name; photo.loading = "lazy"; card.append(photo, node("figcaption", "", image.name));
    if (!test) {
      const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = state.selected.has(image.name); checkbox.setAttribute("aria-label", `选择 ${image.name}`); card.classList.toggle("selected", checkbox.checked);
      const toggle = () => { checkbox.checked ? state.selected.add(image.name) : state.selected.delete(image.name); card.classList.toggle("selected", checkbox.checked); selectionSummary(); byId("selectAll").checked = state.selected.size === state.images.length; byId("selectAll").indeterminate = state.selected.size > 0 && state.selected.size < state.images.length; };
      checkbox.addEventListener("change", toggle); card.addEventListener("click", (event) => { if (event.target !== checkbox) { checkbox.checked = !checkbox.checked; toggle(); } }); card.append(checkbox);
    }
    gallery.append(card);
  }
  byId("pageInfo").textContent = `${state.page + 1} / ${pages}`; byId("previousPage").disabled = state.page === 0; byId("nextPage").disabled = state.page >= pages - 1;
}

function rememberJob(job) {
  state.cache.set(job.id, job);
  const summary = {...job}; delete summary.items; delete summary.logs;
  const index = state.jobs.findIndex((entry) => entry.id === job.id);
  if (index < 0) state.jobs.unshift(summary); else state.jobs[index] = summary;
  if (state.job && state.job.id === job.id) state.job = job;
  state.activeId = (state.jobs.find((entry) => activeStatuses.includes(entry.status)) || {}).id || null;
}

function setJob(job) {
  rememberJob(job); state.job = job;
  byId("currentTask").textContent = `${jobName(job)} · ${statusNames[job.status]}`; byId("exportJob").textContent = jobName(job);
  renderTasks(); drawLogs(); drawArchives(job.archives || []); updateControls();
}

async function selectJob(jobId) { const job = await api(`/api/jobs/${jobId}`); setJob(job); return job; }

function renderTasks() {
  const target = byId("taskList"); const scrollTop = target.scrollTop; const focused = document.activeElement && document.activeElement.dataset.focusKey;
  target.replaceChildren(); byId("taskCount").textContent = state.jobs.length;
  const filter = byId("taskFilter").value; const jobs = state.jobs.filter((job) => filter === "all" || job.kind === filter);
  if (!jobs.length) { const empty = node("div", "empty-state"); empty.append(node("span", "empty-icon", "▤"), node("strong", "", "暂无任务"), node("span", "", "读取图片后开始标注")); target.append(empty); }
  for (const job of jobs) {
    const expanded = state.expanded.has(job.id); const card = node("article", "task-card"); card.classList.toggle("selected", Boolean(state.job && state.job.id === job.id));
    const header = node("button", "task-header"); header.type = "button"; header.setAttribute("aria-expanded", expanded); header.setAttribute("aria-label", `${expanded ? "收起" : "展开"} ${jobName(job)}`); header.dataset.focusKey = job.id;
    const info = node("div", "task-info"); const title = node("div", "task-title"); title.append(node("strong", "", jobName(job)), badge(job.status));
    const date = new Date(job.created * 1000).toLocaleString("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}); info.append(title, node("span", "task-subtitle", `${modelName(job)} · ${date} · ${job.output}`));
    const completion = node("div", "task-progress"); const progress = node("progress"); progress.max = job.total; progress.value = job.done; completion.append(node("span", "", `${job.done} / ${job.total}${job.failed ? ` · 失败 ${job.failed}` : ""}`), progress);
    header.append(node("span", "task-chevron", expanded ? "⌄" : "›"), info, completion);
    header.addEventListener("click", () => busy(header, async () => { expanded ? state.expanded.delete(job.id) : state.expanded.add(job.id); await selectJob(job.id); })); card.append(header);
    const detail = state.cache.get(job.id);
    if (expanded && detail) {
      const body = node("div", "task-details"); const heading = node("div", "task-detail-heading"); heading.append(node("span", "", `${detail.total} 张图片 · 点击查看结果`));
      if (detail.status === "preview") { const start = node("button", "text-button", "确认测试图片 →"); start.type = "button"; start.addEventListener("click", () => { state.preview = detail; state.sourceMode = "test"; state.page = 0; drawGallery(); byId("sourceDialog").showModal(); }); heading.append(start); }
      body.append(heading);
      const grid = node("div", "task-images"); const pages = Math.max(1, Math.ceil(detail.items.length / 24)); const page = Math.min(state.itemPages.get(job.id) || 0, pages - 1);
      detail.items.slice(page * 24, (page + 1) * 24).forEach((item, offset) => {
        const button = node("button", "task-image"); button.type = "button"; button.setAttribute("aria-label", `查看 ${item.name}`); button.dataset.focusKey = item.id;
        const image = node("img"); image.src = item.url; image.alt = item.name; image.loading = "lazy";
        const text = node("span", "task-image-info"); text.append(node("span", "", item.name), badge(item.status)); button.append(image, text); button.addEventListener("click", () => openReview(job.id, page * 24 + offset)); grid.append(button);
      }); body.append(grid);
      if (pages > 1) {
        const pager = node("div", "pager field-gap");
        for (const [label, delta] of [["上一页", -1], ["下一页", 1]]) { const button = node("button", "text-button", label); button.type = "button"; button.disabled = page + delta < 0 || page + delta >= pages; button.addEventListener("click", () => { state.itemPages.set(job.id, page + delta); renderTasks(); }); pager.append(button); }
        pager.insertBefore(node("span", "", `${page + 1} / ${pages}`), pager.lastChild); body.append(pager);
      }
      if (detail.error) body.append(node("div", "error-text task-error", detail.error)); card.append(body);
    }
    target.append(card);
  }
  target.scrollTop = scrollTop;
  if (focused) { const element = [...target.querySelectorAll("[data-focus-key]")].find((entry) => entry.dataset.focusKey === focused); if (element) element.focus({preventScroll: true}); }
}

function drawLogs() {
  const target = byId("liveLogs"); const previous = target.scrollTop; target.replaceChildren(); const entries = state.job ? state.job.logs || [] : [];
  byId("logCount").textContent = `${entries.length} 条`;
  if (!entries.length) target.append(node("div", "muted", state.job ? "此任务暂无日志" : "等待任务"));
  for (const entry of entries) { const row = node("div", "log-line"); row.dataset.level = entry.level; row.append(node("time", "log-time", new Date(entry.time * 1000).toLocaleTimeString("zh-CN", {hour12: false})), node("span", "", entry.message)); target.append(row); }
  const active = state.jobs.find((entry) => entry.id === state.activeId);
  if (active && state.job && active.id === state.job.id) { const model = active.settings.model === "local" ? state.localModel : state.models.find((entry) => entry.id === active.settings.model); if (model && ["loading", "error"].includes(model.status)) target.append(node("div", model.status === "error" ? "error-text" : "muted", model.message)); }
  target.scrollTop = byId("followLogs").checked ? target.scrollHeight : previous;
}

function fileSize(bytes) { return bytes < 1024 ? `${bytes} B` : bytes < 1024 ** 2 ? `${(bytes / 1024).toFixed(1)} KB` : bytes < 1024 ** 3 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${(bytes / 1024 ** 3).toFixed(1)} GB`; }

function archiveIcon(name) {
  const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  for (const [key, value] of Object.entries({viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": "1.6", "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true"})) icon.setAttribute(key, value);
  const shape = document.createElementNS("http://www.w3.org/2000/svg", "path"); shape.setAttribute("d", name === "download" ? "M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4" : "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 0v6h6M9 3v2m0 2v2m0 2v2m-1 3h2v3H8v-3Z"); icon.append(shape); return icon;
}

function drawArchives(archives) {
  const list = byId("archiveList"); list.replaceChildren();
  for (const archive of [...archives].reverse()) { const card = node("div", "archive-card"); const mark = node("div", "archive-mark"); mark.append(archiveIcon("archive")); const info = node("div", "archive-info"); info.append(node("strong", "", archive.name), node("span", "", `${archive.format.toUpperCase()} · ${fileSize(archive.size)}`)); const link = node("a", "primary archive-download"); link.href = archive.download_url; link.download = archive.name; link.setAttribute("aria-label", `下载 ${archive.name}`); link.append(archiveIcon("download"), document.createTextNode("下载")); card.append(mark, info, link); list.append(card); }
}

async function refreshJobs() {
  state.jobs = await api("/api/jobs"); state.activeId = (state.jobs.find((job) => activeStatuses.includes(job.status)) || {}).id || null;
  const wanted = new Set(state.expanded); if (state.job) wanted.add(state.job.id); if (state.reviewJobId) wanted.add(state.reviewJobId);
  const results = await Promise.all([...wanted].map((id) => api(`/api/jobs/${id}`))); for (const job of results) rememberJob(job);
  renderTasks(); if (state.job) { byId("currentTask").textContent = `${jobName(state.job)} · ${statusNames[state.job.status]}`; drawLogs(); }
  if (byId("reviewDialog").open) renderReview(false); updateControls();
}

function restoreSettings(job) {
  const values = job.settings; byId("model").value = values.model; byId("modelPath").value = values.model_path || "";
  byId("input").value = values.input; byId("output").value = values.output; byId("tagFormat").value = values.tag_format;
  byId("separate").checked = values.separate; byId("prompt").value = values.prompt; byId("minTags").value = values.min_tags;
  byId("trigger").value = values.trigger; byId("triggerEnabled").checked = Boolean(values.trigger); byId("trigger").disabled = !values.trigger; showModels(state.models);
}

async function openReview(jobId, index) {
  try { const job = await api(`/api/jobs/${jobId}`); rememberJob(job); state.reviewJobId = jobId; state.reviewIndex = index; state.reviewDirty = false; state.reviewVersion = ""; renderReview(true); if (!byId("reviewDialog").open) byId("reviewDialog").showModal(); } catch (error) { toast(error.message, true); }
}

function renderReview(force) {
  const job = state.cache.get(state.reviewJobId); if (!job) return; const item = job.items[state.reviewIndex]; if (!item) return;
  byId("reviewTitle").textContent = item.name; const version = `${job.id}/${item.id}`;
  if (force || state.reviewVersion !== version) { byId("reviewImage").src = item.url; byId("reviewImage").alt = item.name; }
  if (force || !state.reviewDirty) byId("reviewTags").value = item.tags || "";
  state.reviewVersion = version; byId("reviewStatus").textContent = statusNames[item.status]; byId("reviewStatus").dataset.status = item.status;
  byId("reviewError").hidden = !item.error; byId("reviewError").textContent = item.error;
  const active = activeStatuses.includes(job.status); byId("reviewTags").readOnly = item.status !== "done" || active;
  byId("reviewTags").placeholder = item.status === "generating" ? "正在生成，完成后自动显示…" : item.status === "failed" ? "标注失败，请查看错误或日志" : "等待标注";
  byId("reviewHint").textContent = active ? "实时更新 · 暂停后可编辑" : item.status === "done" ? "可编辑并保存" : "尚无结果";
  byId("saveReview").disabled = item.status !== "done" || active;
  byId("reviewPageInfo").textContent = `${state.reviewIndex + 1} / ${job.items.length}`;
  byId("reviewPrevious").disabled = state.reviewIndex === 0; byId("reviewNext").disabled = state.reviewIndex === job.items.length - 1;
}

async function leaveReview() { return !state.reviewDirty || confirmation("放弃未保存修改", "当前图片的标签修改尚未保存。"); }

function bind(buttonId, work) { byId(buttonId).addEventListener("click", () => busy(byId(buttonId), work)); }

bind("scan", scan);
bind("ensureModel", checkModel);
byId("model").addEventListener("change", async () => { showModels(state.models); if (byId("model").value !== "local") { try { await checkModel(); } catch (error) { toast(error.message, true); } } });
byId("modelPath").addEventListener("input", () => { state.localModel = null; showModels(state.models); });
byId("triggerEnabled").addEventListener("change", () => { byId("trigger").disabled = !byId("triggerEnabled").checked; });
byId("resetPrompt").addEventListener("click", () => { byId("prompt").value = state.defaultPrompt; });
byId("expandPrompt").addEventListener("click", () => { byId("expandedPrompt").value = byId("prompt").value; byId("promptDialog").showModal(); });
byId("closePrompt").addEventListener("click", () => { byId("prompt").value = byId("expandedPrompt").value; byId("promptDialog").close(); });
for (const [tabId, panelId] of [["basicTab", "basicSettings"], ["rulesTab", "ruleSettings"], ["imageTab", "imageSettings"]]) byId(tabId).addEventListener("click", () => { for (const [otherTab, otherPanel] of [["basicTab", "basicSettings"], ["rulesTab", "ruleSettings"], ["imageTab", "imageSettings"]]) { byId(otherTab).classList.toggle("active", otherTab === tabId); byId(otherTab).setAttribute("aria-selected", otherTab === tabId); byId(otherPanel).hidden = otherPanel !== panelId; } });
byId("settingsToggle").addEventListener("click", () => { const hidden = byId("workspace").classList.toggle("settings-hidden"); byId("settingsToggle").textContent = hidden ? "展开设置" : "收起设置"; byId("settingsToggle").setAttribute("aria-expanded", !hidden); });
function theme(value) { document.documentElement.dataset.theme = value; byId("themeToggle").textContent = value === "light" ? "☾" : "☀"; byId("themeToggle").setAttribute("aria-label", value === "light" ? "切换夜间主题" : "切换日间主题"); }
byId("themeToggle").addEventListener("click", () => { const value = document.documentElement.dataset.theme === "dark" ? "light" : "dark"; theme(value); try { localStorage.setItem("neontag-theme", value); } catch {} });
byId("expandLogs").addEventListener("click", () => { byId("content").classList.remove("logs-collapsed"); const expanded = byId("content").classList.toggle("logs-expanded"); byId("expandLogs").textContent = expanded ? "还原 ↙" : "放大 ↗"; byId("expandLogs").setAttribute("aria-expanded", expanded); byId("collapseLogs").setAttribute("aria-expanded", true); byId("collapseLogs").textContent = "−"; });
byId("collapseLogs").addEventListener("click", () => { const collapsed = byId("content").classList.toggle("logs-collapsed"); byId("collapseLogs").textContent = collapsed ? "+" : "−"; byId("collapseLogs").setAttribute("aria-expanded", !collapsed); });
byId("taskFilter").addEventListener("change", renderTasks); bind("refreshJobs", refreshJobs);
byId("openSource").addEventListener("click", () => { state.sourceMode = "input"; state.page = 0; drawGallery(); byId("sourceDialog").showModal(); });
byId("closeSource").addEventListener("click", () => byId("sourceDialog").close());
byId("selectAll").addEventListener("change", () => { state.selected = byId("selectAll").checked ? new Set(state.images.map((item) => item.name)) : new Set(); selectionSummary(); drawGallery(); });
byId("previousPage").addEventListener("click", () => { state.page--; drawGallery(); }); byId("nextPage").addEventListener("click", () => { state.page++; drawGallery(); });
bind("preprocess", async () => {
  if (!await confirmation("缩放并重命名", `处理当前目录全部图片，先备份原图再替换。\n长边 ${byId("longEdge").value}px · ${byId("prefix").value}_000001.${byId("imageFormat").value}`)) return;
  state.processing = true; updateControls();
  try { const result = await api("/api/preprocess", {input: byId("input").value.trim(), long_edge: Number(byId("longEdge").value), prefix: byId("prefix").value, format: byId("imageFormat").value}); await scan(); toast(`已处理 ${result.count} 张，原图已备份`); } finally { state.processing = false; }
});
bind("previewTest", async () => { const job = await api("/api/jobs", {kind: "test", settings: settings(), selected: selectedNames(), sample_count: Number(byId("sampleCount").value)}); state.preview = job; state.sourceMode = "test"; state.page = 0; state.expanded.add(job.id); setJob(job); drawGallery(); byId("sourceDialog").showModal(); byId("testBadge").textContent = "待确认"; });
bind("confirmTest", async () => { const job = state.preview; if (!job) throw new Error("请先抽取测试图片"); if (!sameSettings(settings(), job.settings)) throw new Error("设置已变化，请重新抽取测试图片"); setJob(await api(`/api/jobs/${job.id}/start`, {})); byId("sourceDialog").close(); byId("testBadge").textContent = "测试中"; });
bind("startBulk", async () => { const values = settings(); const selected = selectedNames(); if (!await confirmation("开始批量标注", `${state.selected.size} 张图片 · ${values.tag_format.toUpperCase()}\n输出：${values.output}`)) return; const job = await api("/api/jobs", {kind: "bulk", settings: values, selected}); state.expanded.add(job.id); setJob(job); setJob(await api(`/api/jobs/${job.id}/start`, {})); });
bind("pauseJob", async () => setJob(await api(`/api/jobs/${state.job.id}/control`, {action: "pause"}))); bind("cancelJob", async () => setJob(await api(`/api/jobs/${state.job.id}/control`, {action: "cancel"}))); bind("resumeJob", async () => { const job = state.job; setJob(await api(`/api/jobs/${job.id}/start`, {})); });
byId("restoreJob").addEventListener("click", () => { restoreSettings(state.job); toast("已载入任务设置，新建任务请使用新的输出目录"); });
byId("reviewTags").addEventListener("input", () => { state.reviewDirty = true; });
byId("closeReview").addEventListener("click", async () => { if (await leaveReview()) byId("reviewDialog").close(); });
byId("reviewDialog").addEventListener("cancel", (event) => { if (state.reviewDirty) { event.preventDefault(); leaveReview().then((leave) => { if (leave) byId("reviewDialog").close(); }); } });
byId("reviewDialog").addEventListener("close", () => { state.reviewJobId = null; state.reviewDirty = false; });
for (const [buttonId, delta] of [["reviewPrevious", -1], ["reviewNext", 1]]) byId(buttonId).addEventListener("click", async () => { if (await leaveReview()) { state.reviewIndex += delta; state.reviewDirty = false; renderReview(true); } });
bind("saveReview", async () => {
  const job = state.cache.get(state.reviewJobId); const item = job.items[state.reviewIndex]; const submittedTags = byId("reviewTags").value;
  const updated = await api(`/api/jobs/${job.id}/items/${item.id}`, {tags: submittedTags}, "PATCH"); rememberJob(updated);
  const current = state.cache.get(state.reviewJobId); const currentItem = current && current.items[state.reviewIndex];
  if (current && current.id === job.id && currentItem && currentItem.id === item.id && byId("reviewTags").value === submittedTags) state.reviewDirty = false;
  renderReview(false); if (state.job && state.job.id === job.id) setJob(updated); toast("标签已保存");
});
bind("archive", async () => { const id = state.job.id; await api(`/api/jobs/${id}/archive`, {name: byId("archiveName").value.trim(), format: byId("archiveFormat").value}); if (state.job.id === id) await selectJob(id); toast("压缩完成，可点击下载"); });

async function poll() {
  if (state.polling) return; state.polling = true;
  try {
    const previous = state.activeId; showModels(await api("/api/models"));
    if (state.localModel && state.activeId) { try { state.localModel = await api("/api/models/local", {path: state.localModel.path}); } catch {} }
    await refreshJobs();
    if (previous && previous !== state.activeId) { const job = state.jobs.find((entry) => entry.id === previous); if (job) { toast(`${jobName(job)} · ${statusNames[job.status]}`, Boolean(job.failed)); if (job.kind === "test") byId("testBadge").textContent = statusNames[job.status]; } }
    byId("connectionDot").className = "connected"; byId("connectionText").textContent = "已连接";
  } catch { byId("connectionDot").className = ""; byId("connectionText").textContent = "连接中断"; }
  finally { state.polling = false; }
}

async function init() {
  try { theme(localStorage.getItem("neontag-theme") === "light" ? "light" : "dark"); } catch { theme("dark"); }
  if (window.innerWidth < 700) { byId("workspace").classList.add("settings-hidden"); byId("settingsToggle").textContent = "展开设置"; byId("settingsToggle").setAttribute("aria-expanded", false); }
  try {
    const config = await api("/api/config"); state.defaultPrompt = config.defaults.prompt; byId("prompt").value = state.defaultPrompt; showModels(config.models);
    try { const saved = JSON.parse(localStorage.getItem("neontag-inputs") || "{}"); for (const id of ["input", "output", "modelPath"]) byId(id).value = saved[id] || ""; } catch {}
    for (const id of ["input", "output", "modelPath"]) byId(id).addEventListener("change", () => { try { localStorage.setItem("neontag-inputs", JSON.stringify({input: byId("input").value, output: byId("output").value, modelPath: byId("modelPath").value})); } catch {} });
    await refreshJobs(); const chosen = state.jobs.find((job) => job.id === state.activeId) || state.jobs[0]; if (chosen) { state.expanded.add(chosen.id); await selectJob(chosen.id); }
    if (config.recovery_errors.length) toast(`任务记录异常：${config.recovery_errors.join("；")}`, true);
    byId("connectionDot").className = "connected"; byId("connectionText").textContent = "已连接";
  } catch (error) { toast(error.message, true); }
  updateControls(); setInterval(poll, 1000);
}

init();
