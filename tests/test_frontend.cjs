const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const context = vm.createContext({});
vm.runInContext(source.slice(0, source.indexOf('bind("scan", scan);')), context);
const compare = vm.runInContext("sameSettings", context);
assert.equal(compare({input: "C:/Images/", output: "C:/Dataset", min_tags: 20}, {input: "c:\\Images", output: "C:\\Dataset", min_tags: 20}), true);
assert.equal(compare({input: "/hy-tmp/images", output: "/hy-tmp/output", min_tags: 20}, {input: "/hy-tmp/images/", output: "/hy-tmp/output", min_tags: 20}), true);
assert.equal(compare({input: "/hy-tmp/Images"}, {input: "/hy-tmp/images"}), false);
assert.equal(compare({prompt: "changed", min_tags: 20}, {prompt: "original", min_tags: 20}), false);
assert.equal(compare({min_tags: 21}, {min_tags: 20}), false);
async function interactions() {
  const elements = new Map();
  const callbacks = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {value: "", addEventListener: (event, callback) => callbacks.set(`${id}/${event}`, callback)});
    return elements.get(id);
  };
  const runtime = vm.createContext({document: {getElementById: element}, window: {innerWidth: 1280}, setInterval: () => {}});
  vm.runInContext(source.slice(0, source.lastIndexOf("init();")), runtime);
  vm.runInContext("showModels = () => {}; renderReview = () => {}; setJob = () => {}; toast = () => {}; updateControls = () => {};", runtime);
  element("model").value = "local";
  element("modelPath").value = "~/models/qwen";
  runtime.checkedModel = {path: "/home/user/models/qwen", downloaded: true, status: "downloaded"};
  vm.runInContext("api = async () => checkedModel;", runtime);
  await vm.runInContext("checkModel()", runtime);
  assert.equal(element("modelPath").value, "/home/user/models/qwen");
  assert.equal(vm.runInContext("modelReady()", runtime), true);

  let resolveCheck;
  runtime.pendingCheck = new Promise((resolve) => { resolveCheck = resolve; });
  vm.runInContext("api = () => pendingCheck;", runtime);
  element("modelPath").value = "./first-model";
  const checking = vm.runInContext("checkModel()", runtime);
  element("modelPath").value = "./second-model";
  resolveCheck({path: "/project/first-model", downloaded: true, status: "downloaded"});
  await checking;
  assert.equal(element("modelPath").value, "./second-model");
  assert.equal(vm.runInContext("chosenModel()", runtime), null);

  vm.runInContext('renderReview = () => { const item = state.cache.get(state.reviewJobId).items[state.reviewIndex]; if (!state.reviewDirty) byId("reviewTags").value = item.tags; };', runtime);
  for (const changeImage of [false, true]) {
    let finishSave;
    runtime.saveResponse = new Promise((resolve) => { finishSave = resolve; });
    vm.runInContext('api = () => saveResponse; state.jobs = []; state.cache.clear(); state.job = null; state.reviewJobId = "job"; state.reviewIndex = 0; state.reviewDirty = true; state.cache.set("job", {id: "job", status: "completed", items: [{id: "a", tags: "original A"}, {id: "b", tags: "original B"}]});', runtime);
    element("reviewTags").value = "submitted A";
    const saving = callbacks.get("saveReview/click")();
    if (changeImage) vm.runInContext("state.reviewIndex = 1;", runtime);
    element("reviewTags").value = changeImage ? "unsaved B" : "newer A";
    finishSave({id: "job", status: "completed", items: [{id: "a", tags: "submitted A"}, {id: "b", tags: "original B"}]});
    await saving;
    assert.equal(element("reviewTags").value, changeImage ? "unsaved B" : "newer A");
    assert.equal(vm.runInContext("state.reviewDirty", runtime), true);
  }
  console.log("Frontend checks: 5 settings comparisons, local path normalization, stale model response, and 2 save races passed");
}

interactions().catch((error) => { console.error(error); process.exitCode = 1; });
