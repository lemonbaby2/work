const state = {
  user: null,
  appInitialized: false,
  dashboard: null,
  recipe: null,
  catalog: null,
  currentVideoId: "video_0265",
  box: null,
  dragging: false,
  dragMode: null,
  dragHandle: null,
  dragBoxStart: null,
  dragClientStart: null,
  dragCanvasScale: null,
  start: null,
  decisionSecond: -1,
  cameraTimer: null,
  cameraActive: false,
  cameraSingleActive: false,
  cameraWallActive: false,
  recording: false,
  selectedCamera: "0",
  cameraOptions: [],
  connectedCameras: [],
  annotationItems: [],
  annotationDirty: false,
  annotationChangedAt: null,
  annotationSavedAt: null,
  loadedAnnotation: null,
  interpolationStart: null,
  endKeyframeEdited: false,
  trackingBusy: false,
  trackFrameBoxes: new Map(),
  trackTimelines: new Map(),
  latestAutosave: null,
  checkpointPreview: null,
  annotationAutosaveBusy: false,
  annotationFrameCallback: null,
  aiPrelabelTimer: null,
  renderJobTimer: null,
  cloudJobTimer: null,
  annotationRequestController: null,
  algorithms: [],
  latestCvatTaskId: null,
  productionLines: [],
  datasets: [],
  trainingDatasets: [],
  trainingOutputs: [],
  trainingJobTimer: null,
  pcbModels: [],
  loaded: { annotations: false, devices: false, quality: false },
  activeLineId: localStorage.getItem("sop.activeLine") || "pcb"
};

const fallbackSteps = [
  { id: "S01", label: "确认仪表板骨架到位", start_s: 0, end_s: 10, roi: [0.1, 0.1, 0.9, 0.9] },
  { id: "S02", label: "放置前除霜风道", start_s: 10, end_s: 23, roi: [0.1, 0.1, 0.9, 0.9] },
  { id: "S03", label: "风道卡扣压合", start_s: 23, end_s: 35, roi: [0.1, 0.1, 0.9, 0.9] },
  { id: "S04", label: "左侧紧固作业", start_s: 35, end_s: 50, roi: [0.1, 0.1, 0.9, 0.9] },
  { id: "S05", label: "右侧紧固作业", start_s: 50, end_s: 64, roi: [0.1, 0.1, 0.9, 0.9] },
  { id: "S06", label: "完成检查并流转", start_s: 64, end_s: 77.6, roi: [0.1, 0.1, 0.9, 0.9] }
];

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !url.startsWith("/api/auth/login")) showLogin();
    throw new Error(data.message || "请求失败");
  }
  return data;
}

function showLogin(message = "开发者、管理者和普通员工使用各自账号登录。") {
  state.user = null;
  document.body.classList.remove("auth-pending", "authenticated");
  document.body.classList.add("unauthenticated");
  const element = document.getElementById("loginMessage");
  if (element) { element.textContent = message; element.classList.remove("bad"); }
}

function applyUser(user) {
  state.user = user;
  document.body.classList.remove("auth-pending", "unauthenticated");
  document.body.classList.add("authenticated");
  document.getElementById("currentUserName").textContent = user.display_name || user.username;
  document.getElementById("currentUserRole").textContent = user.role_label || user.role;
  document.querySelectorAll(".role-manager").forEach(item => item.classList.toggle("role-hidden", !["admin", "manager"].includes(user.role)));
  document.querySelectorAll(".role-admin").forEach(item => item.classList.toggle("role-hidden", user.role !== "admin"));
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("role-hidden", !(user.views || []).includes(item.dataset.view)));
}

document.getElementById("loginForm").addEventListener("submit", async event => {
  event.preventDefault();
  const button = document.getElementById("loginButton");
  const message = document.getElementById("loginMessage");
  button.disabled = true;
  button.textContent = "正在验证…";
  message.classList.remove("bad");
  try {
    const result = await request("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: document.getElementById("loginUsername").value, password: document.getElementById("loginPassword").value }) });
    applyUser(result.user);
    document.getElementById("loginPassword").value = "";
    if (!state.appInitialized) await init();
    else switchView("overview");
  } catch (error) {
    message.textContent = error.message;
    message.classList.add("bad");
  } finally {
    button.disabled = false;
    button.textContent = "登录";
  }
});

document.getElementById("logoutButton").addEventListener("click", async () => {
  await request("/api/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => null);
  showLogin("已安全退出。请使用岗位账号重新登录。");
});

function showToast(id, message, bad = false) {
  const element = document.getElementById(id);
  element.textContent = message;
  element.style.background = bad ? "#fff0ee" : "#e6f5f0";
  element.style.color = bad ? "#a33a34" : "#08705d";
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 4200);
}

function markAnnotationDirty(dirty = true) {
  state.annotationDirty = dirty;
  if (dirty) state.annotationChangedAt = Date.now();
  const container = document.getElementById("annotationSaveState");
  const label = document.getElementById("annotationSaveLabel");
  if (!container || !label) return;
  container.classList.toggle("dirty", dirty);
  label.textContent = dirty ? "当前区域有未保存修改" : "所有修改已保存";
}

function updateLastSaved(recordedAt = null) {
  state.annotationSavedAt = Date.now();
  const element = document.getElementById("annotationLastSaved");
  if (element) element.textContent = `最近保存：${recordedAt || new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
  markAnnotationDirty(false);
}

function switchView(name) {
  if (state.user && !(state.user.views || []).includes(name)) name = "overview";
  document.querySelectorAll(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  if (name === "decision") refreshDecision(document.getElementById("sopVideo").currentTime, true);
  if (name === "annotation") {
    fitCanvas();
    loadAnnotations();
    loadAnnotationStats();
    loadAnnotationHistory();
    loadAnnotationTracks();
    loadAnnotationAutosave();
    loadAnnotationScope();
    loadAiPrelabelStatus();
    loadCvatIntegration();
    loadCloudStatus();
    state.loaded.annotations = true;
  }
  if (name === "monitor" && !state.loaded.devices) { loadDeviceInventory(); state.loaded.devices = true; }
  if (name === "quality" && !state.loaded.quality) { loadQualityReports(); loadCvatIntegration(); state.loaded.quality = true; }
  if (name === "training") { loadSparkStatus(); loadPcbModels(); }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-jump]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.jump)));

function currentVideoInfo() {
  return state.catalog?.videos.find(video => video.id === state.currentVideoId) || null;
}

function authenticatedMediaUrl(url) {
  if (!url || !String(url).startsWith("media/")) return url;
  return `${url}${String(url).includes("?") ? "&" : "?"}release=20260821-annotation-v3`;
}

function renderCameraOptions(cameras = []) {
  const select = document.getElementById("cameraSelect");
  if (!select) return;
  const items = cameras.filter(item => item?.source);
  state.cameraOptions = items;
  if (!items.length) {
    select.innerHTML = '<option value="">未发现可用摄像头</option>';
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const current = String(state.selectedCamera ?? items[0].camera_id);
  select.innerHTML = items.map(item => {
    const cameraId = String(item.camera_id);
    const label = item.camera_name || `摄像头${cameraId}`;
    const source = item.source || item.model || "未配置";
    return `<option value="${escapeHtml(cameraId)}">${escapeHtml(label)} · ${escapeHtml(source)}</option>`;
  }).join("");
  const hasCurrent = items.some(item => String(item.camera_id) === current);
  state.selectedCamera = hasCurrent ? current : String(items[0].camera_id);
  select.value = state.selectedCamera;
}

function playbackSteps() {
  return currentVideoInfo()?.steps || fallbackSteps;
}

function activeStep(time) {
  const steps = playbackSteps();
  return steps.find(step => time >= Number(step.start_s) && time < Number(step.end_s)) || steps.at(-1);
}

function renderVideoSwitcher() {
  const switcher = document.getElementById("videoSwitcher");
  if (!state.catalog) return;
  switcher.innerHTML = state.catalog.videos.map((video, index) => {
    const shortName = String(video.display_name || video.id).split("｜").at(-1).replace(/\.mp4$/i, "");
    return `<button data-video="${escapeHtml(video.id)}" title="${escapeHtml(video.display_name || video.id)}" class="${video.id === state.currentVideoId ? "active" : ""}">视频${index + 1} · ${escapeHtml(shortName.slice(0, 18))} · ${Number(video.duration_s).toFixed(1)}秒</button>`;
  }).join("");
  switcher.querySelectorAll("button").forEach(button => button.addEventListener("click", () => selectVideo(button.dataset.video)));
}

function selectVideo(videoId) {
  exitCheckpointPreview(false);
  state.currentVideoId = videoId;
  state.decisionSecond = -1;
  const info = currentVideoInfo();
  if (!info) return;
  const video = document.getElementById("sopVideo");
  video.pause();
  video.src = authenticatedMediaUrl(info.presentation_video || info.enhanced_video || info.video);
  video.load();
  const annotVideo = document.getElementById("annotVideo");
  annotVideo.src = authenticatedMediaUrl(info.source_video);
  annotVideo.load();
  state.loadedAnnotation = null;
  state.trackFrameBoxes.clear();
  state.trackTimelines.clear();
  clearInterpolationStart();
  state.box = null;
  markAnnotationDirty(false);
  document.getElementById("annotVideoSelect").value = videoId;
  const cvatSelectedVideo = document.getElementById("cvatSelectedVideo");
  if (cvatSelectedVideo) cvatSelectedVideo.textContent = `${info.display_name || videoId} · ${Number(info.duration_s || 0).toFixed(1)} 秒`;
  document.getElementById("videoAlgorithm").textContent = info.algorithm?.split(" + ").slice(0, 2).join(" + ") || "目标检测 + SOP状态机";
  document.getElementById("videoResolution").textContent = info.presentation_resolution || info.resolution || "1620×720";
  renderVideoSwitcher();
  renderLiveSteps();
  renderEvidence();
  updateVideoStatus(video);
  refreshDecision(0, true);
  loadAnnotations();
  loadAnnotationHistory();
  loadAnnotationTracks();
  loadAnnotationAutosave();
}

function renderLiveSteps() {
  const steps = playbackSteps();
  const list = document.getElementById("liveSteps");
  list.innerHTML = steps.map(step => {
    const start = Number(step.start_s || 0);
    const end = Number(step.end_s || start);
    const duration = Number(step.duration_s ?? end - start);
    return `<li data-step="${escapeHtml(step.id)}" data-start="${start}" data-end="${end}" tabindex="0" role="button" aria-label="跳转到${escapeHtml(step.id)} ${escapeHtml(step.label)}">
      <span><b>${escapeHtml(step.id)}</b> ${escapeHtml(step.label)}</span>
      <small>${formatTime(start)} - ${formatTime(end)} · 持续 ${formatTime(duration)}</small>
    </li>`;
  }).join("");
  list.querySelectorAll("li").forEach(item => {
    const activate = () => jumpToStep(item.dataset.step);
    item.addEventListener("click", activate);
    item.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
  });
}

function formatTime(value) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function renderStepRoi(step) {
  const overlay = document.getElementById("stepRoiOverlay");
  const label = document.getElementById("stepRoiLabel");
  if (!overlay || !label || !step?.roi || step.roi.length !== 4) {
    if (overlay) overlay.hidden = true;
    return;
  }
  const [x1, y1, x2, y2] = step.roi.map(value => Math.max(0, Math.min(1, Number(value))));
  overlay.hidden = false;
  overlay.style.left = `${x1 * 100}%`;
  overlay.style.top = `${y1 * 100}%`;
  overlay.style.width = `${Math.max(0, x2 - x1) * 100}%`;
  overlay.style.height = `${Math.max(0, y2 - y1) * 100}%`;
  label.textContent = `${step.id} ${step.label}`;
}

function jumpToStep(stepId) {
  const step = playbackSteps().find(item => item.id === stepId);
  const video = document.getElementById("sopVideo");
  if (!step || !video) return;
  video.currentTime = Number(step.start_s || 0);
  renderStepRoi(step);
  updateVideoStatus(video);
  video.play().catch(() => {});
}

function updateVideoStatus(video) {
  const steps = playbackSteps();
  const step = activeStep(video.currentTime || 0);
  const currentIndex = steps.findIndex(item => item.id === step.id);
  document.getElementById("liveStepTitle").textContent = `${step.id} ${step.label}`;
  const total = Number.isFinite(video.duration) ? video.duration : Number(currentVideoInfo()?.duration_s || 77.53);
  document.getElementById("liveStepTime").textContent = `${formatTime(video.currentTime || 0)} / ${formatTime(total)}`;
  renderStepRoi(step);
  document.getElementById("videoProgress").style.width = `${Math.min(100, (video.currentTime || 0) / total * 100)}%`;
  document.querySelectorAll("#liveSteps li").forEach((li, index) => {
    li.classList.toggle("active", index === currentIndex);
    li.classList.toggle("done", index < currentIndex);
  });
  refreshDecision(video.currentTime || 0);
}

function renderEvidence() {
  const info = currentVideoInfo();
  const grid = document.getElementById("evidenceGrid");
  if (!info) return;
  const items = info.steps.map((step, index) => ({
    time: Math.round((Number(step.start_s) + Number(step.end_s)) / 2),
    label: `${step.id} ${step.label}`,
    image: info.snapshots?.[index]
  })).filter(item => item.image);
  grid.innerHTML = items.map(item => `<button data-time="${item.time}" aria-label="跳转到${item.label}"><img src="${item.image}" alt="${item.label}"><span>${item.label} · ${item.time}秒</span></button>`).join("");
  grid.querySelectorAll("button").forEach(button => button.addEventListener("click", () => {
    const video = document.getElementById("sopVideo");
    video.currentTime = Number(button.dataset.time);
    video.play();
  }));
}

async function refreshDecision(time, force = false) {
  if (!state.catalog) return;
  const second = Math.floor(time);
  if (!force && second === state.decisionSecond) return;
  state.decisionSecond = second;
  try {
    const decision = await request(`/api/decision?video=${encodeURIComponent(state.currentVideoId)}&time=${encodeURIComponent(time.toFixed(2))}`);
    document.getElementById("decisionRelease").textContent = decision.release;
    document.getElementById("decisionAction").textContent = decision.recommended_action;
    document.getElementById("riskScore").textContent = decision.risk_score;
    document.getElementById("riskLevel").textContent = decision.risk_level;
    document.getElementById("riskRing").style.background = `conic-gradient(#e19a33 ${decision.risk_score}%,#314957 0)`;
    const completeness = Number(decision.evidence_completeness ?? decision.evidence_score ?? 0);
    document.getElementById("evidenceScore").textContent = `${completeness}%`;
    document.getElementById("evidenceMeter").style.width = `${completeness}%`;
    document.getElementById("decisionStep").textContent = `${decision.step.id} ${decision.step.label}`;
    document.getElementById("decisionTime").textContent = `${decision.time_s.toFixed(1)}秒`;
    document.getElementById("decisionReasons").innerHTML = decision.reasons.map(reason => `<li>${reason}</li>`).join("");
    document.getElementById("decisionChain").innerHTML = decision.decision_chain.map((item, index) => `<span>${item}</span>${index < decision.decision_chain.length - 1 ? "<i>→</i>" : ""}`).join("");
    document.getElementById("countRegions").textContent = decision.objects.business_regions;
    document.getElementById("countDynamic").textContent = decision.objects.dynamic;
    document.getElementById("countFastener").textContent = decision.objects.fastener_candidates;
    document.getElementById("truthNotice").textContent = decision.truth_notice;
    const missing = decision.missing_evidence || [];
    document.getElementById("decisionMissing").textContent = missing.length ? `缺少：${missing.join("、")}` : "当前视觉必需项已经看到，仍需质量/MES确认";
    const labels = decision.detected_labels || [];
    document.getElementById("decisionDetectedLabels").innerHTML = labels.length ? labels.slice(0, 12).map(label => `<span>${escapeHtml(label)}</span>`).join("") : "<span>当前关键帧未检测到稳定目标</span>";
    renderVideoDetections(decision);
  } catch (error) {
    document.getElementById("decisionAction").textContent = `决策服务暂不可用：${error.message}`;
    renderVideoDetections(null);
  }
}

function renderVideoDetections(decision) {
  const canvas = document.getElementById("sopDetectionCanvas");
  const video = document.getElementById("sopVideo");
  if (!canvas || !video) return;
  const width = Number(decision?.frame_size?.width || video.videoWidth || 1280);
  const height = Number(decision?.frame_size?.height || video.videoHeight || 720);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, width, height);
  if (!decision) return;
  const items = [...(decision.detections || []), ...(decision.candidates || [])].slice(0, 28);
  context.lineWidth = Math.max(2, width / 640);
  context.font = `${Math.max(14, Math.round(width / 85))}px Microsoft YaHei, sans-serif`;
  items.forEach(item => {
    const box = item.xyxy || item.box_pixels;
    if (!Array.isArray(box) || box.length !== 4) return;
    const label = String(item.label || "候选目标");
    const color = label.includes("ROI") ? "#35a7ff" : (label.includes("操作") || label.includes("取放") || label.includes("复检") ? "#ffb13b" : (label.includes("手部") ? "#3be0a5" : (label.includes("PCB") ? "#26d6e5" : "#f16d8f")));
    const [x1, y1, x2, y2] = box.map(Number);
    context.strokeStyle = color;
    context.strokeRect(x1, y1, Math.max(1, x2 - x1), Math.max(1, y2 - y1));
    const confidence = item.confidence == null ? "" : ` ${Math.round(Number(item.confidence) * 100)}%`;
    const text = `${label}${confidence}`;
    const textWidth = context.measureText(text).width + 12;
    const textHeight = Math.max(22, width / 55);
    const top = Math.max(0, y1 - textHeight);
    context.fillStyle = "rgba(7,16,24,.84)";
    context.fillRect(x1, top, Math.min(textWidth, width - x1), textHeight);
    context.fillStyle = color;
    context.fillText(text, x1 + 6, top + textHeight - 6, Math.max(20, width - x1 - 8));
  });
}

document.querySelectorAll(".decision-review").forEach(button => button.addEventListener("click", async () => {
  const video = document.getElementById("sopVideo");
  try {
    const result = await request("/api/decision/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, time_s: Number(video.currentTime.toFixed(2)), result: button.dataset.result, reviewer: "本地演示用户" }) });
    showToast("decisionToast", result.message);
  } catch (error) { showToast("decisionToast", error.message, true); }
}));

function renderEditor() {
  const steps = state.recipe?.steps || fallbackSteps;
  document.getElementById("stepEditor").innerHTML = steps.map((step, index) => `
    <div class="edit-step" data-index="${index}">
      <b>${step.id}</b>
      <input class="step-label" value="${step.label}" aria-label="${step.id}步骤名称">
      <input class="step-end" type="number" step="0.1" value="${step.end_s}" aria-label="${step.id}结束秒数">
      <div class="step-actions"><button data-move="up" title="上移">↑</button><button data-move="down" title="下移">↓</button></div>
    </div>`).join("");
  document.querySelectorAll("[data-move]").forEach(button => button.addEventListener("click", () => {
    const index = Number(button.closest(".edit-step").dataset.index);
    const target = button.dataset.move === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= steps.length) return;
    [steps[index], steps[target]] = [steps[target], steps[index]];
    steps.forEach((step, i) => step.id = `S${String(i + 1).padStart(2, "0")}`);
    renderEditor();
  }));
}

function editorSteps() {
  const original = state.recipe?.steps || fallbackSteps;
  let start = 0;
  return [...document.querySelectorAll(".edit-step")].map((row, index) => {
    const end = Number(row.querySelector(".step-end").value);
    const source = original[index] || {};
    const step = { ...source, id: `S${String(index + 1).padStart(2, "0")}`, label: row.querySelector(".step-label").value.trim(), start_s: start, end_s: end };
    start = end;
    return step;
  });
}

document.getElementById("addStep").addEventListener("click", () => {
  const steps = editorSteps();
  const start = steps.length ? Number(steps.at(-1).end_s) : 0;
  steps.push({ id: `S${String(steps.length + 1).padStart(2, "0")}`, label: "新步骤", start_s: start, end_s: start + 10, roi: [0.1, 0.1, 0.9, 0.9] });
  state.recipe.steps = steps;
  renderEditor();
});

document.getElementById("saveRecipe").addEventListener("click", async () => {
  try {
    const steps = editorSteps();
    const result = await request("/api/sop/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version: `web-${Date.now()}`, steps }) });
    state.recipe.steps = steps;
    renderEditor();
    showToast("studioToast", result.message);
  } catch (error) { showToast("studioToast", error.message, true); }
});

const annotVideo = document.getElementById("annotVideo");
const canvas = document.getElementById("annotCanvas");
const ctx = canvas.getContext("2d");

function fitCanvas() {
  const rect = annotVideo.getBoundingClientRect();
  canvas.width = Math.max(1, Math.round(rect.width));
  canvas.height = Math.max(1, Math.round(rect.height));
  drawBox();
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: (event.clientX - rect.left) * canvas.width / Math.max(1, rect.width),
    y: (event.clientY - rect.top) * canvas.height / Math.max(1, rect.height),
  };
}

function editableBoxHandles(box) {
  const left = box.x;
  const right = box.x + box.w;
  const top = box.y;
  const bottom = box.y + box.h;
  const middleX = (left + right) / 2;
  const middleY = (top + bottom) / 2;
  return {
    nw: [left, top], n: [middleX, top], ne: [right, top],
    e: [right, middleY], se: [right, bottom], s: [middleX, bottom],
    sw: [left, bottom], w: [left, middleY],
  };
}

function hitEditableBox(point) {
  if (!state.box) return null;
  for (const [handle, [x, y]] of Object.entries(editableBoxHandles(state.box))) {
    if (Math.abs(point.x - x) <= 9 && Math.abs(point.y - y) <= 9) return { mode: "resize", handle };
  }
  const inside = point.x >= state.box.x && point.x <= state.box.x + state.box.w && point.y >= state.box.y && point.y <= state.box.y + state.box.h;
  return inside ? { mode: "move", handle: null } : null;
}

function resizeCursor(handle) {
  if (["nw", "se"].includes(handle)) return "nwse-resize";
  if (["ne", "sw"].includes(handle)) return "nesw-resize";
  if (["n", "s"].includes(handle)) return "ns-resize";
  return "ew-resize";
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
}

function interpolatedTrackItems(frame) {
  const items = [];
  state.trackTimelines.forEach((timeline, trackId) => {
    if (!timeline.length || frame < Number(timeline[0].frame) || frame > Number(timeline.at(-1).frame)) return;
    let low = 0;
    let high = timeline.length - 1;
    while (low <= high) {
      const middle = Math.floor((low + high) / 2);
      const candidateFrame = Number(timeline[middle].frame);
      if (candidateFrame === frame) {
        items.push({ ...timeline[middle], playback_interpolated: false });
        return;
      }
      if (candidateFrame < frame) low = middle + 1;
      else high = middle - 1;
    }
    const before = timeline[Math.max(0, high)];
    const after = timeline[Math.min(timeline.length - 1, low)];
    const span = Number(after.frame) - Number(before.frame);
    if (!before || !after || span <= 0) return;
    const ratio = (frame - Number(before.frame)) / span;
    const box = before.box.map((value, index) => Number(value) + (Number(after.box[index]) - Number(value)) * ratio);
    items.push({ ...before, annotation_id: `playback:${trackId}:${frame}`, frame, box, playback_interpolated: true });
  });
  return items;
}

function playbackAnnotationItems(currentTime, fps) {
  const frame = Math.round(currentTime * fps);
  if (state.checkpointPreview) {
    return (state.checkpointPreview.annotations || []).filter(item => Number(item.frame) === frame);
  }
  const visible = state.annotationItems.filter(item => Math.abs(Number(item.video_time) - currentTime) <= Math.max(0.05, 1.1 / fps));
  const source = document.getElementById("annotSourceFilter")?.value || "all";
  if (["all", "manual"].includes(source)) visible.push(...interpolatedTrackItems(frame));
  const deduplicated = new Map();
  visible.forEach(item => deduplicated.set(item.track_id ? `track:${item.track_id}` : String(item.annotation_id), item));
  return [...deduplicated.values()];
}

function drawBox() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const currentTime = Number(annotVideo.currentTime || 0);
  const fps = Number(currentVideoInfo()?.fps || 30);
  const drawItems = playbackAnnotationItems(currentTime, fps);
  drawItems.forEach(item => {
    const [x1, y1, x2, y2] = item.box;
    const x = x1 * canvas.width;
    const y = y1 * canvas.height;
    const width = (x2 - x1) * canvas.width;
    const height = (y2 - y1) * canvas.height;
    const palette = ["#39e2bc", "#ffbd66", "#6db6ff", "#ef6a62", "#c28cff", "#f28f6b"];
    const paletteIndex = [...new Set(drawItems.map(entry => entry.label))].indexOf(item.label);
    const savedColor = /^#[0-9a-f]{6}$/i.test(item.color || "") ? item.color : null;
    const color = item.review_status === "human_confirmed" ? "#52d6a9" : (item.review_status === "rejected" ? "#ef6a62" : (savedColor || palette[Math.max(0, paletteIndex) % palette.length]));
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, width, height);
    const label = `${item.label}${item.confidence == null ? "" : ` ${Math.round(item.confidence * 100)}%`}`;
    ctx.font = "12px Microsoft YaHei, sans-serif";
    const labelWidth = Math.min(canvas.width - x, ctx.measureText(label).width + 12);
    ctx.fillStyle = color;
    ctx.fillRect(x, Math.max(0, y - 20), labelWidth, 20);
    ctx.fillStyle = "#102231";
    ctx.fillText(label, x + 6, Math.max(14, y - 6));
  });
  if (state.box) {
    ctx.strokeStyle = document.getElementById("annotColor")?.value || "#39e2bc";
    ctx.lineWidth = 3;
    ctx.fillStyle = "rgba(12,143,121,.15)";
    ctx.fillRect(state.box.x, state.box.y, state.box.w, state.box.h);
    ctx.strokeRect(state.box.x, state.box.y, state.box.w, state.box.h);
    ctx.fillStyle = "#ffffff";
    ctx.strokeStyle = "#0c8f79";
    ctx.lineWidth = 2;
    Object.values(editableBoxHandles(state.box)).forEach(([x, y]) => {
      ctx.fillRect(x - 5, y - 5, 10, 10);
      ctx.strokeRect(x - 5, y - 5, 10, 10);
    });
  }
}

function annotationEvidenceDataUrl() {
  const width = annotVideo.videoWidth || canvas.width;
  const height = annotVideo.videoHeight || canvas.height;
  const output = document.createElement("canvas");
  output.width = width;
  output.height = height;
  const outputCtx = output.getContext("2d");
  if (annotVideo.readyState >= 2) outputCtx.drawImage(annotVideo, 0, 0, width, height);
  const scaleX = width / Math.max(1, canvas.width);
  const scaleY = height / Math.max(1, canvas.height);
  state.annotationItems.filter(item => Math.abs(Number(item.video_time) - Number(annotVideo.currentTime || 0)) <= 0.08).forEach(item => {
    const [x1, y1, x2, y2] = item.box;
    const color = item.review_status === "human_confirmed" ? "#52d6a9" : (item.review_status === "rejected" ? "#ef6a62" : (item.color || "#ffbd66"));
    outputCtx.strokeStyle = color;
    outputCtx.lineWidth = Math.max(2, 2 * scaleX);
    outputCtx.strokeRect(x1 * width, y1 * height, (x2 - x1) * width, (y2 - y1) * height);
  });
  if (state.box) {
    outputCtx.strokeStyle = document.getElementById("annotColor")?.value || "#39e2bc";
    outputCtx.lineWidth = Math.max(3, 3 * scaleX);
    outputCtx.strokeRect(state.box.x * scaleX, state.box.y * scaleY, state.box.w * scaleX, state.box.h * scaleY);
  }
  return output.toDataURL("image/jpeg", 0.88);
}

function renderAnnotationRows(items, readOnly = false) {
  const rows = document.getElementById("annotationRows");
  document.getElementById("annotationResultCount").textContent = `${items.length} 条`;
  if (!items.length) {
    rows.innerHTML = '<tr><td colspan="6">当前筛选条件下没有标注，请切换时间、来源或状态。</td></tr>';
    return;
  }
  const statusNames = { pending: "待复核", human_confirmed: "人工确认", needs_correction: "需要修正", rejected: "已驳回" };
  const sourceNames = { prelabel: "检测预标注", candidate: "小目标候选", manual: "人工标注" };
  rows.innerHTML = items.map(item => `<tr>
    <td><b>${Number(item.video_time).toFixed(3)}s</b><small>第 ${item.frame} 帧</small></td>
    <td><b>${escapeHtml(item.region || "未分区")}</b><small>${escapeHtml(item.label)}</small>${item.track_id ? `<small class="track-id">轨迹 ${escapeHtml(item.track_id)}</small>` : ""}</td>
    <td><span class="source-tag ${item.source_kind}">${sourceNames[item.source_kind] || escapeHtml(item.source_kind)}</span><small title="${escapeHtml(item.source)}">${escapeHtml(item.source)}</small></td>
    <td>${item.confidence == null ? "--" : `${(Number(item.confidence) * 100).toFixed(1)}%`}</td>
    <td><span class="review-state ${item.review_status}">${statusNames[item.review_status] || escapeHtml(item.review_status)}</span></td>
    <td>${readOnly ? '<span class="review-state pending">历史只读</span>' : `<div class="row-actions"><button class="ghost" data-load-annotation="${escapeHtml(item.annotation_id)}">载入框</button><button class="ghost" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="human_confirmed">确认</button><button class="ghost danger-action" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="rejected">驳回</button><button class="ghost danger-action" data-delete-annotation="${escapeHtml(item.annotation_id)}" data-delete-source="${escapeHtml(item.source_kind)}">删除</button></div>`}</td>
  </tr>`).join("");
}

async function loadAnnotations() {
  if (!state.catalog) return;
  if (state.checkpointPreview) {
    const items = playbackAnnotationItems(Number(annotVideo.currentTime || 0), Number(currentVideoInfo()?.fps || 30));
    state.annotationItems = items;
    renderAnnotationRows(items, true);
    drawBox();
    return;
  }
  const source = document.getElementById("annotSourceFilter").value;
  const status = document.getElementById("annotStatusFilter").value;
  const time = Number(annotVideo.currentTime || 0);
  document.getElementById("annotFrameTime").textContent = `${time.toFixed(3)} 秒`;
  if (state.annotationRequestController) state.annotationRequestController.abort();
  const controller = new AbortController();
  state.annotationRequestController = controller;
  try {
    const result = await request(`/api/annotations?video=${encodeURIComponent(state.currentVideoId)}&time=${time.toFixed(3)}&source=${encodeURIComponent(source)}&status=${encodeURIComponent(status)}&limit=300`, { signal: controller.signal });
    if (state.annotationRequestController !== controller) return;
    state.annotationItems = result.items || [];
    renderAnnotationRows(state.annotationItems);
    restoreSelectedTrackBox(state.annotationItems);
    drawBox();
  } catch (error) {
    if (error.name === "AbortError") return;
    state.annotationItems = [];
    document.getElementById("annotationRows").innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
    document.getElementById("annotationResultCount").textContent = "读取失败";
  } finally {
    if (state.annotationRequestController === controller) state.annotationRequestController = null;
  }
}

async function loadAnnotationStats() {
  try {
    const stats = await request("/api/annotations/stats");
    document.getElementById("prelabelCount").textContent = Number(stats.prelabels).toLocaleString("zh-CN");
    document.getElementById("candidateCount").textContent = Number(stats.candidates).toLocaleString("zh-CN");
    document.getElementById("manualCount").textContent = Number(stats.manual).toLocaleString("zh-CN");
    document.getElementById("annotationTruth").textContent = stats.truth_boundary;
    const database = stats.database || {};
    const databaseState = document.getElementById("annotationDatabaseState");
    databaseState.textContent = database.ok ? `SQLite WAL 正常 · ${database.annotations} 条` : `数据库异常：${database.message || database.integrity || "未知"}`;
    databaseState.classList.toggle("bad", !database.ok);
  } catch (error) {
    document.getElementById("annotationTruth").textContent = `标注统计暂不可用：${error.message}`;
  }
}

async function loadAiPrelabelStatus() {
  try {
    const job = await request("/api/annotations/prelabel/status");
    const percent = Number(job.progress || 0);
    document.getElementById("aiPrelabelMeter").style.width = `${Math.min(100, Math.max(0, percent))}%`;
    document.getElementById("aiPrelabelProgress").textContent = `${percent.toFixed(2)}%`;
    const statusNames = { not_started: "未启动", queued: "排队中", loading_model: "加载模型", running: "运行中", paused: "已暂停", completed: "已完成", failed: "失败", interrupted: "已中断" };
    document.getElementById("aiPrelabelMessage").textContent = job.message || statusNames[job.status] || job.status;
    const eta = Number(job.eta_seconds || 0);
    const missing = (job.missing_video_ids || []).length;
    document.getElementById("aiPrelabelDetail").textContent = `${statusNames[job.status] || job.status} · ${Number(job.sampled_frames_completed || 0).toLocaleString("zh-CN")}/${Number(job.sampled_frames_total || 0).toLocaleString("zh-CN")} 个采样帧 · ${Number(job.detections_total || 0).toLocaleString("zh-CN")} 个候选框${eta > 0 ? ` · 预计剩余 ${Math.ceil(eta / 60)} 分钟` : ""}${missing ? ` · ${missing} 段源文件缺失，网盘拉回后补跑` : ""}`;
    document.getElementById("startAiPrelabel").disabled = Boolean(job.running);
    document.getElementById("pauseAiPrelabel").disabled = !job.running;
    window.clearTimeout(state.aiPrelabelTimer);
    state.aiPrelabelTimer = job.running ? window.setTimeout(loadAiPrelabelStatus, 5000) : null;
    if (job.status === "completed") loadAnnotationStats();
  } catch (error) {
    document.getElementById("aiPrelabelMessage").textContent = `状态读取失败：${error.message}`;
  }
}

async function controlAiPrelabel(action) {
  const button = action === "pause" ? document.getElementById("pauseAiPrelabel") : document.getElementById("startAiPrelabel");
  button.disabled = true;
  try {
    const result = await request("/api/annotations/prelabel", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    showToast("annotationToast", result.message);
    await loadAiPrelabelStatus();
  } catch (error) {
    showToast("annotationToast", error.message, true);
    button.disabled = false;
  }
}

async function loadAnnotationHistory() {
  const rows = document.getElementById("annotationHistoryRows");
  if (!rows || !state.currentVideoId) return;
  try {
    const history = await request(`/api/annotations/history?video=${encodeURIComponent(state.currentVideoId)}`);
    document.getElementById("annotationHistoryTitle").textContent = `${state.currentVideoId} · 已保存 ${history.save_rounds} 次`;
    const checkpoints = history.checkpoints || [];
    const regions = history.regions || [];
    if (!checkpoints.length && !regions.length) {
      rows.innerHTML = "<span>尚未保存。画框后先保存这个框，完成一批后再保存本次进度。</span>";
      return;
    }
    const checkpointHtml = checkpoints.map(item => `<button class="history-row history-action" data-view-checkpoint="${escapeHtml(item.checkpoint_id)}" type="button"><b>第 ${item.round} 次保存</b><em>点击回溯</em><span>${escapeHtml(item.recorded_at)} · 第 ${item.current_frame} 帧 · ${item.annotation_count} 个框</span><small>${escapeHtml((item.regions || []).join("、") || "尚未分区")}</small></button>`).join("");
    const regionHtml = regions.map(item => `<div class="history-row region-summary"><b>${escapeHtml(item.region)}</b><span>${item.annotation_count} 个已落库框</span><small>最近 ${escapeHtml(item.last_saved_at || "--")}</small></div>`).join("");
    rows.innerHTML = checkpointHtml + regionHtml;
  } catch (error) {
    rows.innerHTML = `<span>保存记录读取失败：${escapeHtml(error.message)}</span>`;
  }
}

async function viewAnnotationCheckpoint(checkpointId) {
  try {
    const result = await request(`/api/annotations/checkpoint?video=${encodeURIComponent(state.currentVideoId)}&checkpoint=${encodeURIComponent(checkpointId)}`);
    const snapshot = result.snapshot;
    state.checkpointPreview = snapshot;
    state.box = null;
    state.loadedAnnotation = null;
    annotVideo.pause();
    const banner = document.getElementById("checkpointPreview");
    banner.hidden = false;
    document.getElementById("checkpointPreviewTitle").textContent = `保存点 ${snapshot.recorded_at} · 第 ${snapshot.current_frame} 帧`;
    document.getElementById("checkpointPreviewDetail").textContent = `只读回溯 · 当时共 ${Number(snapshot.annotation_count || 0)} 个框，不会改动当前数据库`;
    document.getElementById("restoreCheckpointDraft").disabled = !snapshot.draft;
    annotVideo.currentTime = Math.max(0, Number(snapshot.current_time || 0));
    await loadAnnotations();
    document.querySelector(".annot-stage")?.scrollIntoView({ behavior: "smooth", block: "start" });
    showToast("annotationToast", `已打开 ${snapshot.recorded_at} 的只读保存点`);
  } catch (error) {
    showToast("annotationToast", error.message, true);
  }
}

function exitCheckpointPreview(reload = true) {
  if (!state.checkpointPreview) return;
  state.checkpointPreview = null;
  const banner = document.getElementById("checkpointPreview");
  if (banner) banner.hidden = true;
  if (reload) loadAnnotations();
}

function restoreCheckpointDraft() {
  const snapshot = state.checkpointPreview;
  const draft = snapshot?.draft;
  if (!snapshot || !draft) return;
  exitCheckpointPreview(false);
  annotVideo.pause();
  annotVideo.currentTime = Math.max(0, Number(snapshot.current_time || 0));
  if (Array.isArray(draft.box) && draft.box.length === 4) {
    const [x1, y1, x2, y2] = draft.box;
    state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
    document.getElementById("annotLabel").value = draft.label || document.getElementById("annotLabel").value;
    document.getElementById("annotRegion").value = draft.region || document.getElementById("annotRegion").value;
    document.getElementById("annotTrack").value = draft.track_id || "";
    if (/^#[0-9a-f]{6}$/i.test(draft.color || "")) document.getElementById("annotColor").value = draft.color;
    markAnnotationDirty(true);
  }
  document.getElementById("boxStatus").textContent = `已从 ${snapshot.recorded_at} 恢复草稿，保存前不会覆盖当前标注`;
  loadAnnotations();
  drawBox();
}

async function loadAnnotationScope() {
  try {
    const result = await request("/api/annotations/scope");
    const scope = result.scope || {};
    document.getElementById("scopeStationName").value = scope.station_name || "";
    document.getElementById("scopeStationType").value = scope.station_type || "assembly";
    document.getElementById("scopeQualityGoal").value = scope.quality_goal || "";
    document.getElementById("scopeMaterialA").value = scope.material_a || "";
    document.getElementById("scopeMaterialB").value = scope.material_b || "";
    document.getElementById("scopeDistinguish").checked = Boolean(scope.distinguish_materials);
    document.getElementById("scopeRequiredLabels").textContent = (scope.required_labels || []).join("、");
    document.getElementById("scopeExcludedLabels").textContent = (scope.excluded_labels || []).join("、");
    document.getElementById("scopeVerificationPoints").textContent = (scope.verification_points || []).join("、");
    document.getElementById("scopePolicy").textContent = scope.policy || "";
    document.getElementById("scopeStatus").textContent = scope.distinguish_materials ? "按工艺区分物料" : "插件使用通用类别";
  } catch (error) {
    document.getElementById("scopeStatus").textContent = "读取失败";
  }
}

async function loadAnnotationTracks() {
  const rows = document.getElementById("trackRows");
  if (!rows || !state.currentVideoId) return;
  try {
    const result = await request(`/api/annotations/tracks?video=${encodeURIComponent(state.currentVideoId)}`);
    state.trackFrameBoxes.clear();
    state.trackTimelines.clear();
    (result.items || []).forEach(track => {
      const timeline = (track.shapes || []).map(shape => ({
        ...shape,
        track_id: track.track_id,
        label: track.label,
        region: track.region,
        source_kind: "manual",
        review_status: "pending",
      })).sort((left, right) => Number(left.frame) - Number(right.frame));
      state.trackTimelines.set(track.track_id, timeline);
      timeline.forEach(cacheTrackShape);
    });
    if (!result.items?.length) {
      rows.innerHTML = "<span>尚未生成轨迹。设置起始关键帧并到后续帧生成后，这里会显示帧范围。</span>";
      return;
    }
    restoreCachedTrackBox(currentAnnotFrame());
    rows.innerHTML = result.items.map(track => `<div class="track-row"><div><b>${escapeHtml(track.track_id)}</b><span>${escapeHtml(track.label)} · ${escapeHtml(track.region)} · 第 ${track.start_frame}–${track.end_frame} 帧</span><small>已生成 ${track.frame_count} 个框：${escapeHtml((track.frames || []).slice(0, 18).join("、"))}${track.frame_count > 18 ? "…" : ""}</small></div><div class="track-actions"><button class="ghost" data-open-track="${escapeHtml(track.track_id)}" data-track-frame="${track.start_frame}">查看起始帧</button><label>删段 <input type="number" min="${track.start_frame}" max="${track.end_frame}" value="${track.start_frame}" data-segment-start="${escapeHtml(track.track_id)}" aria-label="删除片段起始帧"> - <input type="number" min="${track.start_frame}" max="${track.end_frame}" value="${track.end_frame}" data-segment-end="${escapeHtml(track.track_id)}" aria-label="删除片段结束帧"></label><button class="ghost danger-action" data-delete-track-segment="${escapeHtml(track.track_id)}">删除此段</button><button class="ghost danger-action" data-delete-track="${escapeHtml(track.track_id)}">删除整轨</button></div></div>`).join("");
  } catch (error) {
    rows.innerHTML = `<span>轨迹读取失败：${escapeHtml(error.message)}</span>`;
  }
}

function currentAnnotFrame() {
  return Math.round(Number(annotVideo.currentTime || 0) * Number(currentVideoInfo()?.fps || 30));
}

function normalizedCurrentBox() {
  if (!state.box || state.box.w < 5 || state.box.h < 5) return null;
  return [state.box.x / canvas.width, state.box.y / canvas.height, (state.box.x + state.box.w) / canvas.width, (state.box.y + state.box.h) / canvas.height].map(value => Number(value.toFixed(6)));
}

function trackFrameKey(trackId, frame) {
  return `${trackId}:${Number(frame)}`;
}

function cacheTrackShape(item) {
  if (!item?.track_id || !Array.isArray(item.box) || item.box.length !== 4) return;
  state.trackFrameBoxes.set(trackFrameKey(item.track_id, item.frame), { ...item });
}

function syncEditableBoxToPlaybackFrame(frame) {
  if (state.annotationDirty || state.dragging || annotVideo.paused) return;
  const trackId = document.getElementById("annotTrack")?.value.trim();
  const item = trackId ? interpolatedTrackItems(frame).find(candidate => candidate.track_id === trackId) : null;
  if (!item) {
    state.box = null;
    state.loadedAnnotation = null;
    return;
  }
  const [x1, y1, x2, y2] = item.box;
  state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
  state.loadedAnnotation = item.playback_interpolated ? null : { ...item };
}

function renderAnnotationPlaybackFrame() {
  syncEditableBoxToPlaybackFrame(currentAnnotFrame());
  document.getElementById("annotFrameTime").textContent = `${Number(annotVideo.currentTime).toFixed(3)} 秒`;
  updatePlaybackUi();
  drawBox();
  if (!annotVideo.paused && typeof annotVideo.requestVideoFrameCallback === "function") {
    state.annotationFrameCallback = annotVideo.requestVideoFrameCallback(renderAnnotationPlaybackFrame);
  } else {
    state.annotationFrameCallback = null;
  }
}

function loadEditableBox(item, statusText) {
  if (!item?.box || state.annotationDirty || state.dragging) return false;
  const [x1, y1, x2, y2] = item.box;
  state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
  state.loadedAnnotation = item.annotation_id ? { ...item } : null;
  if (statusText) document.getElementById("boxStatus").textContent = statusText;
  updateInterpolationControls();
  return true;
}

function restoreCachedTrackBox(frame) {
  const trackId = document.getElementById("annotTrack")?.value.trim();
  if (!trackId || state.interpolationStart) return false;
  const item = state.trackFrameBoxes.get(trackFrameKey(trackId, frame));
  return loadEditableBox(item, item ? `第 ${frame} 帧轨迹框已载入，可直接拖动或缩放后保存` : "");
}

function restoreSelectedTrackBox(items) {
  const trackId = document.getElementById("annotTrack")?.value.trim();
  if (!trackId || state.interpolationStart || state.annotationDirty) return false;
  const frame = currentAnnotFrame();
  const item = items.find(candidate => candidate.source_kind === "manual" && candidate.track_id === trackId && Number(candidate.frame) === frame);
  if (!item) return false;
  cacheTrackShape(item);
  return loadEditableBox(item, `第 ${frame} 帧轨迹框已载入，可直接拖动或缩放后保存`);
}

function formatAnnotTime(seconds) {
  const safe = Math.max(0, Number(seconds || 0));
  const minutes = Math.floor(safe / 60);
  const remainder = Math.floor(safe % 60);
  return `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

function updatePlaybackUi() {
  const frame = currentAnnotFrame();
  const totalFrames = Math.max(0, Number(currentVideoInfo()?.frames || Math.round(Number(annotVideo.duration || 0) * Number(currentVideoInfo()?.fps || 30))));
  const timeline = document.getElementById("annotTimeline");
  timeline.max = String(Math.max(1, totalFrames - 1));
  timeline.value = String(Math.min(frame, totalFrames - 1));
  document.getElementById("annotFrameInput").max = String(Math.max(0, totalFrames - 1));
  document.getElementById("annotFrameInput").value = String(frame);
  document.getElementById("annotDuration").textContent = `${formatAnnotTime(annotVideo.currentTime)} / ${formatAnnotTime(annotVideo.duration || currentVideoInfo()?.duration_s)}`;
  const icon = annotVideo.paused ? "▶" : "❚❚";
  document.getElementById("stagePlayback").textContent = icon;
  document.getElementById("toggleAnnotPlayback").textContent = icon;
}

function seekToFrame(frame) {
  annotVideo.pause();
  const fps = Number(currentVideoInfo()?.fps || 30);
  const maxFrame = Math.max(0, Number(currentVideoInfo()?.frames || Math.round(Number(annotVideo.duration || 0) * fps)) - 1);
  const target = Math.min(maxFrame, Math.max(0, Number(frame || 0)));
  if (target !== currentAnnotFrame()) {
    state.loadedAnnotation = null;
    markAnnotationDirty(false);
    const start = state.interpolationStart;
    if (restoreCachedTrackBox(target)) {
      state.endKeyframeEdited = false;
    } else if (start && target > start.frame) {
      const [x1, y1, x2, y2] = start.box;
      state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
      state.endKeyframeEdited = false;
      document.getElementById("boxStatus").textContent = `第 ${target} 帧：可直接自动跟踪；也可拖动或缩放框作为结束位置校正`;
    } else {
      state.box = null;
      state.endKeyframeEdited = false;
    }
    state.annotationItems = [];
    drawBox();
  }
  annotVideo.currentTime = target / fps;
  updateInterpolationControls();
}

function toggleAnnotPlayback() {
  if (annotVideo.paused) annotVideo.play().catch(error => showToast("annotationToast", `无法播放：${error.message}`, true));
  else annotVideo.pause();
}

function clearInterpolationStart() {
  state.interpolationStart = null;
  state.endKeyframeEdited = false;
  const button = document.getElementById("interpolateAnnotation");
  const cancel = document.getElementById("cancelInterpolation");
  if (button) button.disabled = true;
  if (cancel) cancel.disabled = true;
  const panel = document.getElementById("keyframeState");
  if (panel) panel.innerHTML = "<b>尚未设置起始关键帧</b><span>在起始帧画框后设为关键帧，跳到后续帧即可自动跟踪；移动结束框可作为人工校正。</span>";
  updateInterpolationControls();
}

function generatedTrackId(frame) {
  return `TRK-${state.currentVideoId}-${frame}-${Date.now().toString(36).toUpperCase()}`;
}

function updateInterpolationControls() {
  const boxReady = Boolean(normalizedCurrentBox());
  const start = state.interpolationStart;
  const currentFrame = currentAnnotFrame();
  const setStart = document.getElementById("setStartKeyframe");
  const interpolate = document.getElementById("interpolateAnnotation");
  if (setStart) setStart.disabled = !boxReady;
  if (!interpolate) return;
  const endReady = Boolean(start && boxReady && currentFrame > start.frame);
  interpolate.disabled = !endReady || state.trackingBusy;
  interpolate.textContent = state.trackingBusy
    ? "正在读取视频并跟踪…"
    : (!start
      ? "先设置起始关键帧"
      : (currentFrame <= start.frame
        ? "请跳到后续帧"
        : (!boxReady
          ? "请保留或画出目标框"
          : (state.endKeyframeEdited ? "自动跟踪并按结束框校正" : "自动跟踪到当前帧"))));
}

updateInterpolationControls();

function setInterpolationStart() {
  const box = normalizedCurrentBox();
  if (!box) {
    showToast("annotationToast", "请先在起始帧画一个有效的框", true);
    return;
  }
  const frame = currentAnnotFrame();
  const trackInput = document.getElementById("annotTrack");
  const trackId = trackInput.value.trim() || generatedTrackId(frame);
  trackInput.value = trackId;
  state.interpolationStart = {
    frame,
    box,
    label: document.getElementById("annotLabel").value,
    region: document.getElementById("annotRegion").value,
    trackId,
    annotationId: state.loadedAnnotation?.source_kind === "manual" && Number(state.loadedAnnotation.frame) === frame ? state.loadedAnnotation.annotation_id : "",
  };
  state.endKeyframeEdited = false;
  document.getElementById("cancelInterpolation").disabled = false;
  document.getElementById("keyframeState").innerHTML = `<b>起始关键帧：第 ${frame} 帧 · ${escapeHtml(state.interpolationStart.label)}</b><span>轨迹 ${escapeHtml(trackId)} 已建立。跳到后续帧可直接自动跟踪；若移动或缩放结束框，系统会按该关键帧校正轨迹。</span>`;
  document.getElementById("boxStatus").textContent = `轨迹 ${trackId}：已记住第 ${frame} 帧起始框`;
  markAnnotationDirty(false);
  updateInterpolationControls();
}

canvas.addEventListener("pointerdown", event => {
  if (state.checkpointPreview) {
    showToast("annotationToast", "当前是只读保存点，请先返回当前标注再修改");
    return;
  }
  const point = canvasPoint(event);
  const rect = canvas.getBoundingClientRect();
  const hit = hitEditableBox(point);
  state.dragging = true;
  state.start = point;
  state.dragMode = hit?.mode || "draw";
  state.dragHandle = hit?.handle || null;
  state.dragBoxStart = state.box ? { ...state.box } : null;
  state.dragClientStart = { x: event.clientX, y: event.clientY };
  state.dragCanvasScale = { x: canvas.width / Math.max(1, rect.width), y: canvas.height / Math.max(1, rect.height) };
  if (state.dragMode === "draw") state.box = { x: point.x, y: point.y, w: 0, h: 0 };
  markAnnotationDirty(true);
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", event => {
  const point = state.dragging && state.dragClientStart && state.dragCanvasScale
    ? {
      x: state.start.x + (event.clientX - state.dragClientStart.x) * state.dragCanvasScale.x,
      y: state.start.y + (event.clientY - state.dragClientStart.y) * state.dragCanvasScale.y,
    }
    : canvasPoint(event);
  if (!state.dragging) {
    const hit = hitEditableBox(point);
    canvas.style.cursor = hit?.mode === "move" ? "move" : (hit?.mode === "resize" ? resizeCursor(hit.handle) : "crosshair");
    return;
  }
  if (state.dragMode === "move" && state.dragBoxStart) {
    const nextX = clamp(state.dragBoxStart.x + point.x - state.start.x, 0, canvas.width - state.dragBoxStart.w);
    const nextY = clamp(state.dragBoxStart.y + point.y - state.start.y, 0, canvas.height - state.dragBoxStart.h);
    state.box = { ...state.dragBoxStart, x: nextX, y: nextY };
  } else if (state.dragMode === "resize" && state.dragBoxStart) {
    let left = state.dragBoxStart.x;
    let right = state.dragBoxStart.x + state.dragBoxStart.w;
    let top = state.dragBoxStart.y;
    let bottom = state.dragBoxStart.y + state.dragBoxStart.h;
    if (state.dragHandle.includes("w")) left = clamp(point.x, 0, right - 5);
    if (state.dragHandle.includes("e")) right = clamp(point.x, left + 5, canvas.width);
    if (state.dragHandle.includes("n")) top = clamp(point.y, 0, bottom - 5);
    if (state.dragHandle.includes("s")) bottom = clamp(point.y, top + 5, canvas.height);
    state.box = { x: left, y: top, w: right - left, h: bottom - top };
  } else {
    state.box = { x: Math.min(state.start.x, point.x), y: Math.min(state.start.y, point.y), w: Math.abs(point.x - state.start.x), h: Math.abs(point.y - state.start.y) };
  }
  if (state.interpolationStart && currentAnnotFrame() > state.interpolationStart.frame) state.endKeyframeEdited = true;
  drawBox();
  const actionName = state.dragMode === "move" ? "移动" : (state.dragMode === "resize" ? "缩放" : "框选");
  document.getElementById("boxStatus").textContent = `${actionName} ${Math.round(state.box.w)} × ${Math.round(state.box.h)} 像素`;
  updateInterpolationControls();
});
function finishBoxEdit() {
  state.dragging = false;
  state.dragMode = null;
  state.dragHandle = null;
  state.dragBoxStart = null;
  state.dragClientStart = null;
  state.dragCanvasScale = null;
  canvas.style.cursor = "crosshair";
  updateInterpolationControls();
}
canvas.addEventListener("pointerup", finishBoxEdit);
canvas.addEventListener("pointercancel", finishBoxEdit);
canvas.addEventListener("lostpointercapture", finishBoxEdit);
window.addEventListener("pointerup", () => {
  if (state.dragging) finishBoxEdit();
});
document.getElementById("clearBox").addEventListener("click", () => {
  state.box = null;
  state.endKeyframeEdited = false;
  drawBox();
  document.getElementById("boxStatus").textContent = "请在视频上拖动鼠标框选";
  state.loadedAnnotation = null;
  markAnnotationDirty(false);
  updateInterpolationControls();
});
window.addEventListener("resize", fitCanvas);
annotVideo.addEventListener("loadedmetadata", () => { fitCanvas(); updatePlaybackUi(); loadAnnotations(); });
annotVideo.addEventListener("timeupdate", () => {
  document.getElementById("annotFrameTime").textContent = `${Number(annotVideo.currentTime).toFixed(3)} 秒`;
  updatePlaybackUi();
  drawBox();
  updateInterpolationControls();
});
annotVideo.addEventListener("seeked", loadAnnotations);
annotVideo.addEventListener("pause", loadAnnotations);
annotVideo.addEventListener("play", () => {
  if (state.annotationFrameCallback !== null && typeof annotVideo.cancelVideoFrameCallback === "function") {
    annotVideo.cancelVideoFrameCallback(state.annotationFrameCallback);
  }
  renderAnnotationPlaybackFrame();
});
annotVideo.addEventListener("ended", updatePlaybackUi);

document.getElementById("annotVideoSelect").addEventListener("change", event => selectVideo(event.target.value));
document.getElementById("annotSourceFilter").addEventListener("change", loadAnnotations);
document.getElementById("annotStatusFilter").addEventListener("change", loadAnnotations);
document.getElementById("refreshAnnotations").addEventListener("click", loadAnnotations);
document.getElementById("prevFrame").addEventListener("click", () => {
  seekToFrame(currentAnnotFrame() - 1);
});
document.getElementById("nextFrame").addEventListener("click", () => {
  seekToFrame(currentAnnotFrame() + 1);
});
document.getElementById("toggleAnnotPlayback").addEventListener("click", toggleAnnotPlayback);
document.getElementById("stagePlayback").addEventListener("click", toggleAnnotPlayback);
document.getElementById("jumpBack").addEventListener("click", () => seekToFrame(currentAnnotFrame() - Number(document.getElementById("annotJumpStep").value || 30)));
document.getElementById("jumpForward").addEventListener("click", () => seekToFrame(currentAnnotFrame() + Number(document.getElementById("annotJumpStep").value || 30)));
document.getElementById("annotTimeline").addEventListener("input", event => seekToFrame(Number(event.target.value)));
document.getElementById("annotFrameInput").addEventListener("change", event => seekToFrame(Number(event.target.value)));
document.getElementById("setStartKeyframe").addEventListener("click", setInterpolationStart);
document.getElementById("cancelInterpolation").addEventListener("click", () => { clearInterpolationStart(); document.getElementById("boxStatus").textContent = "已取消起始关键帧"; });

document.getElementById("annotationRows").addEventListener("click", async event => {
  const loadButton = event.target.closest("[data-load-annotation]");
  if (loadButton) {
    const item = state.annotationItems.find(annotation => annotation.annotation_id === loadButton.dataset.loadAnnotation);
    if (!item) return;
    annotVideo.pause();
    annotVideo.currentTime = Number(item.video_time);
    const [x1, y1, x2, y2] = item.box;
    state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
    const labelSelect = document.getElementById("annotLabel");
    if (![...labelSelect.options].some(option => option.value === item.label)) labelSelect.add(new Option(item.label, item.label));
    labelSelect.value = item.label;
    const regionSelect = document.getElementById("annotRegion");
    const region = item.region || "未分区";
    if (![...regionSelect.options].some(option => option.value === region)) regionSelect.add(new Option(region, region));
    regionSelect.value = region;
    document.getElementById("annotTrack").value = item.track_id || "";
    state.loadedAnnotation = { ...item };
    markAnnotationDirty(false);
    if (/^#[0-9a-f]{6}$/i.test(item.color || "") && document.getElementById("annotColor")) document.getElementById("annotColor").value = item.color;
    document.getElementById("boxStatus").textContent = `已载入：${item.label}；可修改后保存，或点“设为起始关键帧”`;
    drawBox();
    updateInterpolationControls();
    return;
  }
  const deleteButton = event.target.closest("[data-delete-annotation]");
  if (deleteButton) {
    const sourceNames = { prelabel: "AI预标注", candidate: "小目标候选", manual: "人工标注" };
    const sourceName = sourceNames[deleteButton.dataset.deleteSource] || "标注";
    if (!window.confirm(`确定删除这个${sourceName}框？删除后不再显示或进入导出，操作会留下审计记录。`)) return;
    try {
      const result = await request("/api/annotations/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: deleteButton.dataset.deleteAnnotation, reason: `人工复审删除${sourceName}框` }) });
      state.box = null;
      state.loadedAnnotation = null;
      updateInterpolationControls();
      showToast("annotationToast", result.message);
      await Promise.all([loadAnnotations(), loadAnnotationStats(), loadAnnotationHistory(), loadAnnotationTracks()]);
    } catch (error) { showToast("annotationToast", error.message, true); }
    return;
  }
  const reviewButton = event.target.closest("[data-review-annotation]");
  if (!reviewButton) return;
  try {
    const result = await request("/api/annotations/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: reviewButton.dataset.reviewAnnotation, review_status: reviewButton.dataset.reviewStatus, reviewer: "本地质量员" }) });
    showToast("annotationToast", result.message);
    await Promise.all([loadAnnotations(), loadAnnotationStats()]);
  } catch (error) { showToast("annotationToast", error.message, true); }
});

async function saveCurrentAnnotation() {
  if (state.checkpointPreview) {
    showToast("annotationToast", "历史保存点为只读，请先返回当前标注", true);
    return;
  }
  if (!state.box || state.box.w < 5 || state.box.h < 5) {
    showToast("annotationToast", "请先在视频画面上框选一个零件", true);
    return;
  }
  const normalized = [state.box.x / canvas.width, state.box.y / canvas.height, (state.box.x + state.box.w) / canvas.width, (state.box.y + state.box.h) / canvas.height].map(value => Number(value.toFixed(5)));
  try {
    const editableId = state.loadedAnnotation?.source_kind === "manual" && Number(state.loadedAnnotation.frame) === Math.round(annotVideo.currentTime * Number(currentVideoInfo()?.fps || 30)) ? state.loadedAnnotation.annotation_id : undefined;
    const supersedes = ["prelabel", "candidate"].includes(state.loadedAnnotation?.source_kind) ? state.loadedAnnotation : null;
    const result = await request("/api/annotations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: editableId, supersedes_annotation_id: supersedes?.annotation_id, supersedes_source_kind: supersedes?.source_kind, video_id: state.currentVideoId, video: currentVideoInfo()?.source_video || "原始测试视频_de02.mp4", video_time: Number(annotVideo.currentTime.toFixed(3)), region: document.getElementById("annotRegion").value, track_id: document.getElementById("annotTrack").value, label: document.getElementById("annotLabel").value, color: document.getElementById("annotColor")?.value || "#39e2bc", box: normalized, evidence_data_url: annotationEvidenceDataUrl(), review_status: "pending", reviewer: "本地标注员", source: supersedes ? "人工修正AI预标注" : "平台人工标注" }) });
    showToast("annotationToast", result.message);
    updateLastSaved();
    state.box = null;
    state.loadedAnnotation = null;
    markAnnotationDirty(false);
    await Promise.all([loadAnnotations(), loadAnnotationStats(), loadAnnotationHistory()]);
  } catch (error) { showToast("annotationToast", error.message, true); }
}

document.getElementById("saveAnnotation").addEventListener("click", saveCurrentAnnotation);
document.getElementById("saveCurrentLabelToolbar").addEventListener("click", saveCurrentAnnotation);

document.getElementById("interpolateAnnotation").addEventListener("click", async () => {
  const start = state.interpolationStart;
  if (!start || !state.box) {
    showToast("annotationToast", "请先设置起始关键帧，再跳到后续帧", true);
    return;
  }
  const fps = Number(currentVideoInfo()?.fps || 30);
  const endFrame = Math.round(Number(annotVideo.currentTime) * fps);
  if (endFrame <= start.frame) {
    showToast("annotationToast", `当前是第 ${endFrame} 帧，必须跳到起始帧 ${start.frame} 之后`, true);
    return;
  }
  const visibleBox = normalizedCurrentBox();
  if (!visibleBox) {
    showToast("annotationToast", "当前目标框无效，请重新画框", true);
    return;
  }
  const endBox = state.endKeyframeEdited ? visibleBox : null;
  try {
    state.trackingBusy = true;
    updateInterpolationControls();
    showToast("annotationToast", `正在读取第 ${start.frame}–${endFrame} 帧并跟随目标，请稍候`);
    const result = await request("/api/annotations/interpolate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, start_frame: start.frame, end_frame: endFrame, start_box: start.box, end_box: endBox, start_annotation_id: start.annotationId, label: start.label, region: start.region, frame_step: Number(document.getElementById("interpolationStep").value || 1), track_id: document.getElementById("annotTrack").value || start.trackId, reviewer: "本地标注员" }) });
    showToast("annotationToast", result.message);
    document.getElementById("annotTrack").value = result.track_id;
    const confidence = Math.round(Number(result.quality?.mean_confidence || 0) * 100);
    document.getElementById("boxStatus").textContent = `轨迹 ${result.track_id} 已自动跟踪：第 ${result.start_frame}–${result.end_frame} 帧，共 ${result.generated} 个框，平均跟踪质量 ${confidence}%`;
    clearInterpolationStart();
    (result.items || []).forEach(cacheTrackShape);
    state.box = null;
    markAnnotationDirty(false);
    restoreCachedTrackBox(endFrame);
    updateLastSaved();
    await Promise.all([loadAnnotations(), loadAnnotationStats(), loadAnnotationHistory(), loadAnnotationTracks()]);
  } catch (error) {
    showToast("annotationToast", error.message, true);
  } finally {
    state.trackingBusy = false;
    updateInterpolationControls();
  }
});

function currentAnnotationDraft() {
  const box = normalizedCurrentBox();
  if (!box) return null;
  return {
    box,
    label: document.getElementById("annotLabel").value,
    region: document.getElementById("annotRegion").value,
    track_id: document.getElementById("annotTrack").value.trim(),
    color: document.getElementById("annotColor").value,
    dirty: state.annotationDirty,
  };
}

async function loadAnnotationAutosave() {
  const button = document.getElementById("restoreAnnotationAutosave");
  if (!button || !state.currentVideoId) return;
  try {
    const result = await request(`/api/annotations/autosave?video=${encodeURIComponent(state.currentVideoId)}`);
    state.latestAutosave = result.snapshot || null;
    button.disabled = !state.latestAutosave;
    button.textContent = state.latestAutosave ? `恢复自动缓存 ${state.latestAutosave.recorded_at}` : "暂无自动缓存";
  } catch (error) {
    button.disabled = true;
    button.textContent = `缓存读取失败`;
  }
}

function restoreAnnotationAutosave() {
  const snapshot = state.latestAutosave;
  if (!snapshot) return;
  annotVideo.pause();
  annotVideo.currentTime = Math.max(0, Number(snapshot.current_time || 0));
  const draft = snapshot.draft;
  if (draft?.box?.length === 4) {
    const [x1, y1, x2, y2] = draft.box;
    state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height };
    document.getElementById("annotLabel").value = draft.label || document.getElementById("annotLabel").value;
    document.getElementById("annotRegion").value = draft.region || document.getElementById("annotRegion").value;
    document.getElementById("annotTrack").value = draft.track_id || "";
    if (/^#[0-9a-f]{6}$/i.test(draft.color || "")) document.getElementById("annotColor").value = draft.color;
    markAnnotationDirty(Boolean(draft.dirty));
    document.getElementById("boxStatus").textContent = `已恢复 ${snapshot.recorded_at} 的自动缓存草稿`;
  }
  drawBox();
  showToast("annotationToast", `已恢复自动缓存到第 ${snapshot.current_frame} 帧`);
}

async function saveAnnotationCheckpoint(options = {}) {
  if (state.annotationAutosaveBusy || !state.currentVideoId) return;
  const fps = Number(currentVideoInfo()?.fps || 30);
  const wasDirty = state.annotationDirty;
  state.annotationAutosaveBusy = true;
  try {
    const result = await request("/api/annotations/checkpoint", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, current_time: Number(annotVideo.currentTime.toFixed(3)), current_frame: Math.round(Number(annotVideo.currentTime) * fps), operator: state.user?.username || "本地标注员", draft: currentAnnotationDraft(), automatic: Boolean(options.automatic) }) });
    if (options.automatic) {
      document.getElementById("annotationLastSaved").textContent = `自动缓存：${result.checkpoint.recorded_at} · 数据库 + 视频`;
      markAnnotationDirty(wasDirty);
    } else {
      updateLastSaved(result.checkpoint.recorded_at);
    }
    const cacheMode = result.checkpoint.video_cache?.mode === "hardlink" ? "视频硬链接缓存" : "视频引用缓存";
    document.getElementById("annotationDatabaseState").textContent = `SQLite WAL 正常 · 已备份 · ${result.checkpoint.annotation_count} 条 · ${cacheMode}`;
    if (!options.silent) showToast("annotationToast", result.message);
    state.latestAutosave = result.checkpoint.autosave;
    const restore = document.getElementById("restoreAnnotationAutosave");
    if (restore) { restore.disabled = false; restore.textContent = `恢复自动缓存 ${result.checkpoint.recorded_at}`; }
    await loadAnnotationHistory();
  } catch (error) {
    document.getElementById("annotationDatabaseState").textContent = `自动缓存失败：${error.message}`;
    if (!options.silent) showToast("annotationToast", error.message, true);
  } finally {
    state.annotationAutosaveBusy = false;
  }
}

document.getElementById("saveAnnotationProgress").addEventListener("click", saveAnnotationCheckpoint);
document.getElementById("saveProgressToolbar").addEventListener("click", saveAnnotationCheckpoint);
document.getElementById("restoreAnnotationAutosave").addEventListener("click", restoreAnnotationAutosave);
document.getElementById("refreshAnnotationHistory").addEventListener("click", loadAnnotationHistory);
document.getElementById("refreshTracks").addEventListener("click", loadAnnotationTracks);
document.getElementById("exitCheckpointPreview").addEventListener("click", () => exitCheckpointPreview(true));
document.getElementById("restoreCheckpointDraft").addEventListener("click", restoreCheckpointDraft);
document.getElementById("annotationHistoryRows").addEventListener("click", event => {
  const button = event.target.closest("[data-view-checkpoint]");
  if (button) viewAnnotationCheckpoint(button.dataset.viewCheckpoint);
});
document.getElementById("startAiPrelabel").addEventListener("click", () => controlAiPrelabel("resume"));
document.getElementById("pauseAiPrelabel").addEventListener("click", () => controlAiPrelabel("pause"));
document.getElementById("refreshAiPrelabel").addEventListener("click", loadAiPrelabelStatus);

document.getElementById("trackRows").addEventListener("click", async event => {
  const openButton = event.target.closest("[data-open-track]");
  if (openButton) {
    document.getElementById("annotTrack").value = openButton.dataset.openTrack;
    seekToFrame(Number(openButton.dataset.trackFrame));
    return;
  }
  const segmentButton = event.target.closest("[data-delete-track-segment]");
  if (segmentButton) {
    const trackId = segmentButton.dataset.deleteTrackSegment;
    const startFrame = Number(document.querySelector(`[data-segment-start="${CSS.escape(trackId)}"]`)?.value);
    const endFrame = Number(document.querySelector(`[data-segment-end="${CSS.escape(trackId)}"]`)?.value);
    if (!Number.isInteger(startFrame) || !Number.isInteger(endFrame) || endFrame < startFrame) {
      showToast("annotationToast", "请填写有效的轨迹起止帧", true);
      return;
    }
    if (!window.confirm(`确定删除轨迹 ${trackId} 的第 ${startFrame}–${endFrame} 帧？区间外的轨迹框会保留。`)) return;
    try {
      const result = await request("/api/annotations/tracks/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, track_id: trackId, start_frame: startFrame, end_frame: endFrame }) });
      state.box = null;
      state.loadedAnnotation = null;
      showToast("annotationToast", result.message);
      await Promise.all([loadAnnotations(), loadAnnotationStats(), loadAnnotationHistory(), loadAnnotationTracks()]);
    } catch (error) { showToast("annotationToast", error.message, true); }
    return;
  }
  const deleteButton = event.target.closest("[data-delete-track]");
  if (!deleteButton) return;
  const trackId = deleteButton.dataset.deleteTrack;
  if (!window.confirm(`确定删除轨迹 ${trackId} 及它生成的全部框？`)) return;
  try {
    const result = await request("/api/annotations/tracks/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, track_id: trackId }) });
    if (document.getElementById("annotTrack").value === trackId) document.getElementById("annotTrack").value = "";
    showToast("annotationToast", result.message);
    await Promise.all([loadAnnotations(), loadAnnotationStats(), loadAnnotationHistory(), loadAnnotationTracks()]);
  } catch (error) { showToast("annotationToast", error.message, true); }
});

document.getElementById("saveAnnotationScope").addEventListener("click", async () => {
  const distinguish = document.getElementById("scopeDistinguish").checked;
  const materialA = document.getElementById("scopeMaterialA").value.trim();
  const materialB = document.getElementById("scopeMaterialB").value.trim();
  const requiredLabels = ["PCB板", "操作人员手部", "物料框", distinguish ? `手持${materialA}` : "手持插件", distinguish ? `手持${materialB}` : "插件位置", "插件位置"].filter((item, index, list) => item && list.indexOf(item) === index);
  const stationType = document.getElementById("scopeStationType").value;
  const stationTemplates = {
    test: ["步骤顺序", "手势与操作模式", "电脑显示的测试内容是否一致", "测试完成状态"],
    programming: ["步骤顺序", "插电动作", "烧录内容与状态", "拔线与完成确认"],
    assembly: ["步骤顺序", "装配内容", "标签信息", "手势与操作模式"],
    fastening: ["步骤顺序", "螺丝是否存在", "螺丝点位", "是否执行紧固动作"],
  };
  const verificationPoints = stationTemplates[stationType] || stationTemplates.assembly;
  try {
    const result = await request("/api/annotations/scope", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ station_name: document.getElementById("scopeStationName").value, station_type: stationType, quality_goal: document.getElementById("scopeQualityGoal").value, material_a: materialA, material_b: materialB, distinguish_materials: distinguish, required_labels: requiredLabels, verification_points: verificationPoints, excluded_labels: ["板内器件型号/极性是否正确（当前阶段）", "与本工位流程判断无关的元件"], policy: `当前先核对流程是否符合及手势是否正确：${verificationPoints.join("、")}。板内器件是否正确留到后续器件级模型和人工冻结真值阶段。` }) });
    showToast("annotationToast", result.message);
    await loadAnnotationScope();
  } catch (error) { showToast("annotationToast", error.message, true); }
});

["annotRegion", "annotLabel", "annotTrack", "annotColor"].forEach(id => document.getElementById(id)?.addEventListener("change", () => {
  if (state.box) markAnnotationDirty(true);
}));

window.setInterval(() => {
  if (!document.getElementById("view-annotation")?.classList.contains("active")) return;
  saveAnnotationCheckpoint({ automatic: true, silent: true });
}, 60000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && document.getElementById("view-annotation")?.classList.contains("active")) {
    saveAnnotationCheckpoint({ automatic: true, silent: true });
  }
});

window.addEventListener("beforeunload", event => {
  if (!state.annotationDirty) return;
  event.preventDefault();
  event.returnValue = "";
});

document.getElementById("startTraining").addEventListener("click", () => document.getElementById("realTrainingConsole").scrollIntoView({ behavior: "smooth" }));

async function loadTrainingCatalog() {
  try {
    const catalog = await request("/api/training/catalog");
    state.algorithms = catalog.algorithms || [];
    state.productionLines = catalog.production_lines || [];
    state.datasets = catalog.datasets || [];
    state.trainingDatasets = catalog.training_datasets || [];
    state.trainingOutputs = catalog.outputs || [];
    const algorithm = document.getElementById("trainingAlgorithm");
    const dataset = document.getElementById("trainingDataset");
    algorithm.innerHTML = state.algorithms.map(item => `<option value="${escapeHtml(item.id)}" ${["yolo26n", "yoloe26"].includes(item.id) ? "" : "disabled"}>${escapeHtml(item.name)} · ${escapeHtml(item.role)}${["yolo26n", "yoloe26"].includes(item.id) ? "" : "（训练后端待安装）"}</option>`).join("");
    dataset.innerHTML = state.trainingDatasets.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${Number(item.images).toLocaleString("zh-CN")} 图 / ${Number(item.labels).toLocaleString("zh-CN")} 标签 · ${escapeHtml(item.truth_status)}</option>`).join("");
    const cvatDataset = document.getElementById("cvatDatasetSelect");
    cvatDataset.innerHTML = state.datasets.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.line)}</option>`).join("");
    document.getElementById("trainingCatalogStatus").textContent = `${state.trainingDatasets.length} 个本地 YOLO 数据集 · ${catalog.algorithms.length} 个算法`;
    algorithm.addEventListener("change", renderAlgorithmChoice);
    dataset.addEventListener("change", renderTrainingDatasetState);
    document.getElementById("trainingOutput").addEventListener("change", renderTrainingOutputPath);
    renderProductionLineSelect();
    applyProductionLine(state.activeLineId);
    renderAlgorithmChoice();
    renderTrainingDatasetState();
    renderTrainingOutputPath();
  } catch (error) {
    document.getElementById("trainingCatalogStatus").textContent = "目录读取失败";
    document.getElementById("trainingProgress").textContent = error.message;
  }
}

function renderAlgorithmChoice() {
  const select = document.getElementById("trainingAlgorithm");
  const item = state.algorithms.find(entry => entry.id === select?.value) || state.algorithms[0];
  if (!item) return;
  document.getElementById("selectedAlgorithmLabel").textContent = `${item.name} · ${item.role}`;
  document.getElementById("algorithmChoice").innerHTML = `<b>${escapeHtml(item.name)} · ${escapeHtml(item.task)}</b><span>${escapeHtml(item.note || "")}<br>适用：${escapeHtml((item.recommended_for || []).join("、") || "请按产线真值数据验证")} · 权重：${escapeHtml(item.model_path || "待训练")}</span>`;
}

function renderTrainingDatasetState() {
  const item = state.trainingDatasets.find(entry => entry.id === document.getElementById("trainingDataset")?.value);
  const stateLabel = document.getElementById("trainingDatasetState");
  if (!item) { stateLabel.textContent = "没有发现可训练的 data.yaml"; stateLabel.className = "training-dataset-blocked"; return; }
  stateLabel.textContent = `${Number(item.images).toLocaleString("zh-CN")} 张图片 / ${Number(item.labels).toLocaleString("zh-CN")} 个标签文件 · ${item.truth_status}`;
  stateLabel.className = item.truth_ready ? "training-dataset-ready" : "training-dataset-blocked";
}

function renderTrainingOutputPath() {
  const item = state.trainingOutputs.find(entry => entry.id === document.getElementById("trainingOutput")?.value);
  document.getElementById("trainingOutputPath").textContent = item?.path || "由后端限制在安全目录";
}

function renderProductionLineSelect() {
  const select = document.getElementById("productionLineSelect");
  if (!select) return;
  select.innerHTML = state.productionLines.map(line => `<option value="${escapeHtml(line.id)}">${escapeHtml(line.short_name || line.name)}</option>`).join("");
  select.value = state.productionLines.some(line => line.id === state.activeLineId) ? state.activeLineId : state.productionLines[0]?.id;
}

function activeProductionLine() {
  return state.productionLines.find(line => line.id === state.activeLineId) || null;
}

function applyProductionLine(lineId) {
  const line = state.productionLines.find(item => item.id === lineId) || state.productionLines[0];
  if (!line) return;
  state.activeLineId = line.id;
  localStorage.setItem("sop.activeLine", line.id);
  const selector = document.getElementById("productionLineSelect");
  if (selector) selector.value = line.id;
  document.getElementById("lineModelStatus").textContent = `识别模型：${line.primary_model}${line.quality_model ? ` + ${line.quality_model}` : ""}`;
  document.getElementById("trainingLineName").textContent = line.name;
  const algorithmSelect = document.getElementById("trainingAlgorithm");
  const matchingAlgorithm = state.algorithms.find(item => item.name === line.primary_model && ["yolo26n", "yoloe26"].includes(item.id));
  if (matchingAlgorithm) algorithmSelect.value = matchingAlgorithm.id;
  document.getElementById("selectedAlgorithmLabel").textContent = `${line.primary_model} · ${line.short_name}`;
  const available = new Set(line.dataset_ids || []);
  const lineDatasets = state.datasets.filter(item => available.has(item.id));
  const cvatDataset = document.getElementById("cvatDatasetSelect");
  cvatDataset.innerHTML = lineDatasets.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} · ${escapeHtml(item.status || item.line)}</option>`).join("");
  const match = [...cvatDataset.options][0];
  if (cvatDataset && match) cvatDataset.value = match.value;
  const labelSelect = document.getElementById("annotLabel");
  if (labelSelect) labelSelect.innerHTML = (line.labels || []).map(label => `<option>${escapeHtml(label)}</option>`).join("");
  const note = document.getElementById("trainingProgress");
  if (note) note.textContent = `${line.name}：${line.transfer}`;
  document.getElementById("lineProfileTitle").textContent = line.name;
  document.getElementById("lineProfileStatus").textContent = line.model_status || "迁移学习候选";
  document.getElementById("lineProfileModels").textContent = `${line.primary_model}${line.quality_model ? ` + ${line.quality_model}` : ""}；教师：${line.teacher_model}`;
  document.getElementById("lineProfileInputs").textContent = (line.inputs || []).join("、");
  document.getElementById("lineProfileChecks").textContent = (line.checks || []).join("、");
  document.getElementById("lineProfileTransfer").textContent = line.transfer;
  renderDatasetCatalog(lineDatasets);
  renderAlgorithmChoice();
}

function renderDatasetCatalog(items) {
  const grid = document.getElementById("datasetCatalogGrid");
  if (!grid) return;
  document.getElementById("datasetCatalogCount").textContent = `${items.length} 个数据集`;
  grid.innerHTML = items.map(item => `<article class="dataset-registry-item"><div class="dataset-registry-head"><b>${escapeHtml(item.name)}</b><span class="badge">${escapeHtml(item.status || "来源已登记")}</span></div><small>${escapeHtml(item.task || "")}</small><div class="dataset-local-state"><b>${Number(item.image_count || 0).toLocaleString("zh-CN")}</b><span>图片</span><b>${Number(item.label_count || 0).toLocaleString("zh-CN")}</b><span>标签</span></div><div class="dataset-sources">${(item.sources || []).map(source => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">${escapeHtml(source.name)}</a>`).join("")}</div><p>${escapeHtml(item.local_message || item.download || item.embedding_policy || "下载和许可状态以来源页面为准")}</p><a class="dataset-download" href="/api/datasets/export.csv?dataset=${encodeURIComponent(item.id)}" download>下载该数据集 CSV 清单</a></article>`).join("");
}

async function renderTrainingResults(job) {
  const section = document.getElementById("trainingResults");
  section.hidden = false;
  section.scrollIntoView({ behavior: "smooth", block: "start" });
  try {
    const report = await request(job.report_url || "/api/training/report");
    const models = report.models || [];
    const fastest = [...models].sort((a, b) => Number(b.fps || 0) - Number(a.fps || 0))[0];
    const lowest = [...models].sort((a, b) => Number(a.latency_ms || Infinity) - Number(b.latency_ms || Infinity))[0];
    const bestConfidence = [...models].sort((a, b) => Number(b.mean_confidence || 0) - Number(a.mean_confidence || 0))[0];
    document.getElementById("trainingResultStatus").textContent = `基线验证 · ${job.job_id}`;
    document.getElementById("trainingResultSummary").innerHTML = [
      [job.algorithm, "本次选择模型"],
      [fastest ? `${fastest.fps} FPS` : "--", `最高吞吐 · ${fastest?.name || "无数据"}`],
      [lowest ? `${lowest.latency_ms} ms` : "--", `最低延迟 · ${lowest?.name || "无数据"}`],
      [bestConfidence ? `${Math.round(Number(bestConfidence.mean_confidence || 0) * 100)}%` : "--", `平均置信度 · ${bestConfidence?.name || "无数据"}`],
    ].map(([value, label]) => `<div><b>${escapeHtml(value)}</b><span>${escapeHtml(label)}</span></div>`).join("");
    document.getElementById("trainingStageTrack").innerHTML = (job.workflow || ["训练", "推理", "验证", "测试", "可视化报告"]).map((name, index) => `<div>${index + 1}. ${escapeHtml(name)} ✓</div>`).join("");
    document.getElementById("trainingCharts").innerHTML = (report.charts || []).map(name => `<figure><img loading="lazy" decoding="async" src="analysis/model_benchmark/${encodeURIComponent(name)}" alt="${escapeHtml(name)}"><figcaption>${escapeHtml(name.replace(/\.(png|jpg)$/i, ""))}</figcaption></figure>`).join("");
  } catch (error) {
    document.getElementById("trainingResultStatus").textContent = "报告读取失败";
    document.getElementById("trainingResultSummary").innerHTML = `<div><b>未生成</b><span>${escapeHtml(error.message)}</span></div>`;
  }
}

document.getElementById("applyLineModel").addEventListener("click", async () => {
  const previous = state.activeLineId;
  const selected = document.getElementById("productionLineSelect").value;
  applyProductionLine(selected);
  try {
    const result = await request("/api/production-lines/select", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ line_id: state.activeLineId }) });
    showToast("trainingToast", result.message);
    await pollCameraStatus();
  } catch (error) {
    applyProductionLine(previous);
    showToast("trainingToast", error.message, true);
  }
});

document.getElementById("oneClickTraining").addEventListener("click", async () => {
  const progress = document.getElementById("trainingProgress");
  progress.textContent = "正在检查数据、权重、参数与输出目录…";
  try {
    const payload = {
      algorithm: document.getElementById("trainingAlgorithm").value,
      dataset: document.getElementById("trainingDataset").value,
      truth_mode: document.getElementById("trainingTruthMode").value,
      device: document.getElementById("trainingDevice").value,
      epochs: Number(document.getElementById("trainingEpochs").value),
      batch: Number(document.getElementById("trainingBatch").value),
      imgsz: Number(document.getElementById("trainingImgsz").value),
      workers: Number(document.getElementById("trainingWorkers").value),
      patience: Number(document.getElementById("trainingPatience").value),
      seed: Number(document.getElementById("trainingSeed").value),
      output: document.getElementById("trainingOutput").value,
      target: document.getElementById("trainingTarget").value,
      optimizer: document.getElementById("trainingOptimizer").value,
      lr0: Number(document.getElementById("trainingLr0").value),
      weight_decay: Number(document.getElementById("trainingWeightDecay").value),
      close_mosaic: Number(document.getElementById("trainingCloseMosaic").value),
      freeze: Number(document.getElementById("trainingFreeze").value),
      cache: document.getElementById("trainingCache").value,
      amp: document.getElementById("trainingAmp").checked,
    };
    const result = await request("/api/train/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const range = result.estimated_range_seconds || [];
    progress.textContent = `${result.message} 任务号：${result.job_id}；预计 ${Math.ceil((range[0] || 0) / 60)}–${Math.ceil((range[1] || 0) / 60)} 分钟，实际时间取决于显存和数据解码。`;
    showToast("trainingToast", `${result.message}｜${result.job_id}`);
    document.getElementById("trainingLive").hidden = false;
    await pollTrainingJob(result.job_id);
  } catch (error) { progress.textContent = error.message; showToast("trainingToast", error.message, true); }
});

async function pollTrainingJob(jobId) {
  window.clearTimeout(state.trainingJobTimer);
  try {
    const job = await request(`/api/train/jobs/${encodeURIComponent(jobId)}`);
    document.getElementById("trainingLiveTitle").textContent = `${job.job_id} · ${job.algorithm}`;
    document.getElementById("trainingLiveStatus").textContent = `${job.stage || job.status} · ${job.progress || 0}%`;
    document.getElementById("trainingLiveProgress").value = Number(job.progress || 0);
    document.getElementById("trainingLog").textContent = (job.log_tail || []).join("\n") || "训练进程已启动，等待首批日志…";
    const download = document.getElementById("trainingDownload");
    if (job.download_url) { download.href = job.download_url; download.hidden = false; download.setAttribute("download", ""); }
    if (job.status === "completed") {
      document.getElementById("trainingProgress").textContent = `${job.message}；耗时 ${Math.ceil(Number(job.elapsed_seconds || 0) / 60)} 分钟；输出：${job.output_dir}`;
      renderCompletedTraining(job);
      return;
    }
    if (job.status === "failed") { document.getElementById("trainingProgress").textContent = `训练失败：${job.message}`; return; }
    state.trainingJobTimer = window.setTimeout(() => pollTrainingJob(jobId), 3000);
  } catch (error) { document.getElementById("trainingProgress").textContent = error.message; }
}

function renderCompletedTraining(job) {
  const metrics = job.metrics || {};
  const values = [
    [metrics["metrics/precision(B)"], "Precision"], [metrics["metrics/recall(B)"], "Recall"],
    [metrics["metrics/mAP50(B)"], "mAP50"], [metrics["metrics/mAP50-95(B)"], "mAP50-95"],
  ];
  document.getElementById("trainingResults").hidden = false;
  document.getElementById("trainingResultStatus").textContent = `${job.truth_mode === "human-confirmed" ? "人工真值验证" : "候选一致性验证"} · ${job.job_id}`;
  document.getElementById("trainingResultSummary").innerHTML = values.map(([value, label]) => `<div><b>${value == null ? "--" : (Number(value) * 100).toFixed(1) + "%"}</b><span>${label}</span></div>`).join("");
  document.getElementById("trainingStageTrack").innerHTML = ["数据校验", "真实训练", "验证集评估", "ONNX 导出", "部署包"].map((name, index) => `<div>${index + 1}. ${name} ✓</div>`).join("");
  document.getElementById("trainingCharts").innerHTML = `<p class="quality-copy">输出目录：${escapeHtml(job.output_dir)}<br>${escapeHtml(job.truth_notice || "")}</p>`;
}

async function loadCvatIntegration() {
  try {
    const integration = await request("/api/integrations");
    const cvat = integration.cvat;
    document.getElementById("openCvat").href = cvat.tasks_url;
    document.querySelectorAll("[data-cvat-route]").forEach(link => { link.href = `${String(cvat.url).replace(/\/$/, "")}${link.dataset.cvatRoute}`; });
    document.getElementById("labelStudioLink").href = cvat.tasks_url;
    document.getElementById("labelStudioVideoLink").href = cvat.tasks_url;
    document.getElementById("cvatStatus").textContent = cvat.available ? "CVAT 在线" : `CVAT 未连接 · ${cvat.url}`;
    document.getElementById("cvatStatus").classList.toggle("green", cvat.available);
    document.getElementById("labelStudioStatus").textContent = cvat.available ? "CVAT 服务在线，可创建任务" : `未连接：${cvat.url}`;
    document.getElementById("cvatMessage").textContent = cvat.token_configured ? "本机官方 CVAT 已接通，可由平台创建任务；任务与保存轮次均在本页留痕。" : "CVAT 已运行但尚未配置接口令牌；可打开 CVAT，配置令牌后由平台自动创建任务。";
    await loadCvatTasks();
  } catch (error) { document.getElementById("cvatStatus").textContent = error.message; }
}

async function loadCvatTasks() {
  const rows = document.getElementById("cvatTaskRows");
  if (!rows) return;
  try {
    const result = await request("/api/cvat/tasks");
    const tasks = result.items || [];
    const writableTask = tasks.find(item => item.cvat_task_id != null && item.mode === "api") || tasks.find(item => item.cvat_task_id != null);
    if (!state.latestCvatTaskId && writableTask) state.latestCvatTaskId = Number(writableTask.cvat_task_id);
    document.getElementById("pushAnnotationsToCvat").disabled = !state.latestCvatTaskId;
    const bulk = result.bulk || {};
    const counts = bulk.counts || {};
    const project = bulk.project || {};
    const summary = project.project_id ? `<div class="history-row region-summary"><b>共享项目 #${escapeHtml(project.project_id)} · ${escapeHtml(project.name || "全量视频标注")}</b><span>已扫描 ${Number(bulk.total_seen || 0).toLocaleString("zh-CN")} 段 · 已提交 ${Number(counts.submitted || 0).toLocaleString("zh-CN")} · 解码中 ${Number(counts.created || 0).toLocaleString("zh-CN")} · 无效 ${Number(counts.invalid || 0).toLocaleString("zh-CN")} · 失败 ${Number(counts.failed || 0).toLocaleString("zh-CN")}</span><small>项目位于 lan-team 组织，原有标注账号均可见；后台按文件大小持续断点上传。</small><a href="${escapeHtml(project.url || "#")}" target="_blank" rel="noreferrer">打开项目</a></div>` : "";
    rows.innerHTML = summary + (tasks.length ? tasks.map((item, index) => `<div class="history-row"><b>${index + 1}. ${escapeHtml(item.name)}</b><span>${escapeHtml(item.status)} · ${escapeHtml(item.dataset_id || "未指定数据集")} · ${escapeHtml(item.created_at)}</span><small>${item.cvat_task_id == null ? "入口待配置" : `CVAT #${item.cvat_task_id}`} · ${(item.labels || []).length} 个标签</small><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">打开任务</a></div>`).join("") : "<span>尚无 CVAT 任务。填写名称和数据集后点击“创建 CVAT 任务”。</span>");
  } catch (error) {
    rows.innerHTML = `<span>CVAT 任务读取失败：${escapeHtml(error.message)}</span>`;
  }
}

document.getElementById("createCvatTask").addEventListener("click", async () => {
  const datasetId = document.getElementById("cvatDatasetSelect").value;
  const labels = activeProductionLine()?.labels || [];
  try {
    const result = await request("/api/cvat/task", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: document.getElementById("cvatTaskName").value, dataset_id: datasetId, line_id: state.activeLineId, labels, video_id: state.currentVideoId, upload_video: true }) });
    document.getElementById("cvatMessage").textContent = `${result.message}：${result.url}`;
    if (result.task?.cvat_task_id != null) state.latestCvatTaskId = Number(result.task.cvat_task_id);
    await loadCvatTasks();
    window.open(result.url, "_blank", "noopener,noreferrer");
  } catch (error) { document.getElementById("cvatMessage").textContent = error.message; }
});

document.getElementById("refreshCvatTasks").addEventListener("click", loadCvatTasks);
document.getElementById("pushAnnotationsToCvat").addEventListener("click", async () => {
  const taskId = state.latestCvatTaskId;
  if (!taskId) return;
  if (!window.confirm(`将用平台当前视频的已保存框替换 CVAT 任务 #${taskId} 的标注内容，是否继续？`)) return;
  const button = document.getElementById("pushAnnotationsToCvat");
  button.disabled = true;
  button.textContent = "正在保存到 CVAT…";
  try {
    const result = await request("/api/cvat/annotations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task_id: taskId, video_id: state.currentVideoId }) });
    document.getElementById("cvatMessage").textContent = `${result.message}；${result.skipped} 个框因标签未配置或状态无效而跳过。`;
    showToast("annotationToast", result.message);
  } catch (error) {
    document.getElementById("cvatMessage").textContent = `CVAT 标注同步失败：${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "保存到最近 CVAT 任务";
  }
});

function renderCloudStatus(status) {
  const google = status.google || {};
  const baidu = status.baidu || {};
  const local = status.local || {};
  const latestJob = (status.jobs || [])[0];
  const parts = [
    `Google 拉取：${google.source_configured ? "已授权" : "待 OAuth/rclone"}`,
    `Google 回传：${google.output_configured ? "已授权" : "待 OAuth/rclone"}`,
    `百度拉取：${baidu.configured ? "已授权" : "待同步器授权"}`,
    `百度回传：${baidu.upload_configured ? "已授权" : "待上传器授权"}`,
    `本地缓存：${Number(local.cached_files || 0)} 个文件`,
  ];
  document.getElementById("cloudSyncStatus").textContent = latestJob ? `${latestJob.status} · ${latestJob.message}` : parts.join(" · ");
  document.getElementById("cloudSyncDetail").textContent = `${status.security || ""}${local.latest_export ? ` 最近成片：${local.latest_export.split("/").at(-1)}` : ""}`;
  const googleLink = document.getElementById("openGoogleSource");
  const baiduLink = document.getElementById("openBaiduSource");
  googleLink.href = google.source_url || "#";
  googleLink.setAttribute("aria-disabled", google.source_url ? "false" : "true");
  baiduLink.href = baidu.source_url || "#";
  baiduLink.setAttribute("aria-disabled", baidu.source_url ? "false" : "true");
  document.getElementById("pullGoogleVideos").disabled = !google.source_configured;
  document.getElementById("pushGoogleAnnotations").disabled = !google.output_configured;
  document.getElementById("pullBaiduVideos").disabled = !baidu.configured;
  document.getElementById("pushBaiduAnnotations").disabled = !baidu.upload_configured;
}

for (const linkId of ["openGoogleSource", "openBaiduSource"]) {
  document.getElementById(linkId).addEventListener("click", event => {
    if (event.currentTarget.getAttribute("aria-disabled") === "true") event.preventDefault();
  });
}

async function loadCloudStatus() {
  try {
    renderCloudStatus(await request("/api/cloud/status"));
  } catch (error) {
    document.getElementById("cloudSyncStatus").textContent = `网盘状态读取失败：${error.message}`;
  }
}

async function refreshVideoCatalogAfterCloudSync() {
  const catalog = await request("/api/videos");
  state.catalog = catalog;
  const selected = state.currentVideoId;
  document.getElementById("annotVideoSelect").innerHTML = catalog.videos.map((item, index) => `<option value="${escapeHtml(item.id)}">视频${index + 1} · ${escapeHtml(item.source_video.split("/").at(-1))}</option>`).join("");
  document.getElementById("annotVideoSelect").value = catalog.videos.some(item => item.id === selected) ? selected : catalog.videos[0]?.id;
  renderVideoSwitcher();
}

async function startCloudSync(action) {
  const labels = { pull_google: "Google Drive 拉取", pull_baidu: "百度网盘拉取", push_google: "Google Drive 回传", push_baidu: "百度网盘回传" };
  try {
    const result = await request("/api/cloud/sync", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }) });
    document.getElementById("cloudSyncStatus").textContent = `${labels[action]}：${result.message}`;
    window.clearInterval(state.cloudJobTimer);
    state.cloudJobTimer = window.setInterval(async () => {
      const status = await request("/api/cloud/status").catch(() => null);
      if (!status) return;
      renderCloudStatus(status);
      const job = (status.jobs || []).find(item => item.job_id === result.job.job_id);
      if (job && ["completed", "failed"].includes(job.status)) {
        window.clearInterval(state.cloudJobTimer);
        state.cloudJobTimer = null;
        if (job.status === "completed" && action.startsWith("pull_")) await refreshVideoCatalogAfterCloudSync();
      }
    }, 2000);
  } catch (error) {
    document.getElementById("cloudSyncStatus").textContent = `${labels[action]}失败：${error.message}`;
  }
}

async function renderCurrentAnnotationVideo() {
  try {
    const result = await request("/api/annotations/render", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId }) });
    document.getElementById("cloudSyncStatus").textContent = result.message;
    window.clearInterval(state.renderJobTimer);
    state.renderJobTimer = window.setInterval(async () => {
      const status = await request(`/api/annotations/render/status?job=${encodeURIComponent(result.job.job_id)}`).catch(() => null);
      if (!status) return;
      const job = status.job;
      document.getElementById("cloudSyncStatus").textContent = `${job.message} · ${Number(job.progress || 0).toFixed(1)}%`;
      if (["completed", "failed"].includes(job.status)) {
        window.clearInterval(state.renderJobTimer);
        state.renderJobTimer = null;
        await loadCloudStatus();
      }
    }, 1800);
  } catch (error) {
    document.getElementById("cloudSyncStatus").textContent = `标注成片生成失败：${error.message}`;
  }
}

document.getElementById("refreshCloudStatus").addEventListener("click", loadCloudStatus);
document.getElementById("pullGoogleVideos").addEventListener("click", () => startCloudSync("pull_google"));
document.getElementById("pullBaiduVideos").addEventListener("click", () => startCloudSync("pull_baidu"));
document.getElementById("pushGoogleAnnotations").addEventListener("click", () => startCloudSync("push_google"));
document.getElementById("pushBaiduAnnotations").addEventListener("click", () => startCloudSync("push_baidu"));
document.getElementById("renderAnnotationVideo").addEventListener("click", renderCurrentAnnotationVideo);

document.getElementById("deployModel").addEventListener("click", async () => {
  try {
    const result = await request("/api/deploy", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target_station: "NB-IP-SOP-01", strategy: "单工位灰度", rollback: true }) });
    showToast("trainingToast", `${result.message}｜${result.release_id}`);
  } catch (error) { showToast("trainingToast", error.message, true); }
});

document.getElementById("testMes").addEventListener("click", async () => {
  const output = document.getElementById("mesResult");
  output.textContent = "正在发送…";
  try {
    const result = await request("/api/mes/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ product_sn: "NB202608160001", station_id: "NB-IP-SOP-01", visual_sequence: "PASS", final_result: "HOLD" }) });
    output.className = "api-result ok";
    output.textContent = `${result.message}｜事件号 ${result.event_id}`;
  } catch (error) {
    output.className = "api-result warn";
    output.textContent = error.message;
  }
});

function renderCameraStatus(status) {
  const badge = document.getElementById("cameraBadge");
  const message = document.getElementById("cameraMessage");
  if (!badge || !status) return;
  const running = Boolean(status.running);
  const hasError = Boolean(status.error);
  badge.textContent = status.reconnecting ? "自动重连中" : (hasError ? "异常" : (running ? "实时运行" : "未启动"));
  badge.className = `badge ${hasError ? "camera-error" : (running ? "camera-live" : "")}`;
  document.getElementById("cameraSource").textContent = status.source || "--";
  document.getElementById("cameraResolution").textContent = status.width && status.height ? `${status.width}×${status.height}` : "--";
  document.getElementById("cameraFps").textContent = status.capture_fps_actual || status.output_fps ? `${status.capture_fps_actual || 0} / ${status.output_fps || status.fps || 0}` : "--";
  document.getElementById("cameraLatency").textContent = status.pipeline_ms || status.inference_ms ? `${status.pipeline_ms || 0} / ${status.inference_ms || 0} ms` : "--";
  document.getElementById("cameraDropped").textContent = Number.isFinite(status.dropped_frames) ? `${status.dropped_frames} 帧` : "--";
  document.getElementById("cameraBuffer").textContent = status.buffering_strategy === "latest-frame-mailbox" ? `最新帧 · ${status.queue_depth || 0}/${status.queue_capacity || 1}` : "--";
  document.getElementById("cameraDetections").textContent = Number.isFinite(status.detections) ? `${status.detections} 个` : "--";
  document.getElementById("cameraRecovery").textContent = `${status.reconnects || 0} / ${status.read_failures || 0}`;
  document.getElementById("cameraInferenceAge").textContent = Number.isFinite(status.inference_age_ms) ? `${status.inference_age_ms} ms` : "--";
  document.getElementById("cameraModelName").textContent = status.model || activeProductionLine()?.primary_model || "产线模型";
  const output = document.getElementById("cameraOutput");
  if (output) output.textContent = status.output_dir || "桌面/sop xjai";
  state.recording = Boolean(status.recording);
  const recordButton = document.getElementById("recordLiveCamera");
  if (recordButton) recordButton.textContent = state.recording ? "停止录制" : "开始录制";
  if (status.reconnecting) message.textContent = `${status.error || "摄像头正在自动恢复"}；网页会保持连接，无需重复点击启动。`;
  else if (hasError) message.textContent = status.error;
  else if (running) message.textContent = `模型 ${status.model || "YOLOv11n"} 正在实时检测；过期帧直接丢弃，当前端到端 ${status.pipeline_ms || 0} ms。`;
  else message.textContent = "点击“启动实时检测”打开摄像头";
}

async function pollCameraStatus() {
  try {
    const status = await request(`/api/camera/status?camera=${state.selectedCamera}`);
    if (Array.isArray(status.cameras) && status.cameras.length) {
      const connected = state.connectedCameras.length ? state.connectedCameras : status.cameras.filter(item => item.source);
      renderCameraOptions(connected);
      status.cameras.forEach(item => {
        const tile = document.querySelector(`[data-wall-camera="${CSS.escape(String(item.camera_id))}"] .camera-tile-state`);
        if (!tile) return;
        if (item.reconnecting) tile.textContent = `自动重连 · 已重连${item.reconnects || 0}次`;
        else if (item.running) tile.textContent = `${item.output_fps || 0} FPS · ${item.pipeline_ms || 0} ms · ${item.detections || 0}目标`;
        else tile.textContent = item.error || "未启动";
      });
    }
    renderCameraStatus(status);
    if (status.error && !status.running && state.cameraActive) {
      state.cameraActive = false;
      window.clearInterval(state.cameraTimer);
      state.cameraTimer = null;
    }
  } catch (error) {
    renderCameraStatus({ running: false, error: error.message });
  }
}

document.getElementById("startLiveCamera").addEventListener("click", async () => {
  const feed = document.getElementById("liveCameraFeed");
  const placeholder = document.getElementById("liveCameraPlaceholder");
  state.cameraActive = true;
  state.cameraSingleActive = true;
  placeholder.hidden = true;
  try { await request(`/api/camera/start?camera=${state.selectedCamera}`, { method: "POST" }); } catch (error) {
    document.getElementById("cameraMessage").textContent = error.message;
  }
  feed.src = `/api/camera/mjpeg?camera=${state.selectedCamera}&ts=${Date.now()}`;
  await pollCameraStatus();
  window.clearInterval(state.cameraTimer);
  state.cameraTimer = window.setInterval(pollCameraStatus, 1500);
});

document.getElementById("stopLiveCamera").addEventListener("click", async () => {
  state.cameraSingleActive = false;
  state.cameraActive = state.cameraWallActive;
  window.clearInterval(state.cameraTimer);
  state.cameraTimer = null;
  document.getElementById("liveCameraFeed").removeAttribute("src");
  document.getElementById("liveCameraPlaceholder").hidden = false;
  if (!state.cameraWallActive) {
    try { await request(`/api/camera/stop?camera=${state.selectedCamera}`, { method: "POST" }); } catch (_) { /* 页面关闭时服务可能已经停止 */ }
  }
  await pollCameraStatus();
});

document.getElementById("snapshotLiveCamera").addEventListener("click", async () => {
  try {
    const result = await request(`/api/camera/snapshot?camera=${state.selectedCamera}`, { method: "POST" });
    document.getElementById("cameraMessage").textContent = result.message;
  } catch (error) { document.getElementById("cameraMessage").textContent = error.message; }
});

document.getElementById("recordLiveCamera").addEventListener("click", async () => {
  try {
    const endpoint = state.recording ? "stop" : "start";
    const result = await request(`/api/camera/record/${endpoint}?camera=${state.selectedCamera}`, { method: "POST" });
    document.getElementById("cameraMessage").textContent = result.message;
    await pollCameraStatus();
  } catch (error) { document.getElementById("cameraMessage").textContent = error.message; }
});

document.getElementById("cameraSelect").addEventListener("change", async event => {
  const previous = state.selectedCamera;
  state.selectedCamera = event.target.value;
  if (state.cameraSingleActive && previous !== state.selectedCamera) {
    if (!state.cameraWallActive) await request(`/api/camera/stop?camera=${previous}`, { method: "POST" }).catch(() => {});
    await request(`/api/camera/start?camera=${state.selectedCamera}`, { method: "POST" }).catch(() => {});
    document.getElementById("liveCameraFeed").src = `/api/camera/mjpeg?camera=${state.selectedCamera}&ts=${Date.now()}`;
  }
  pollCameraStatus();
});

document.getElementById("liveCameraFeed").addEventListener("error", () => {
  if (state.cameraActive) {
    document.getElementById("liveCameraPlaceholder").hidden = false;
  }
});

async function loadQualityReports() {
  try {
    const integrations = await request("/api/integrations");
    document.getElementById("labelStudioLink").href = integrations.cvat.tasks_url;
    document.getElementById("labelStudioVideoLink").href = integrations.cvat.tasks_url;
    document.getElementById("labelStudioStatus").textContent = integrations.cvat.available ? "CVAT在线，可直接创建任务" : `未连接：${integrations.cvat.url}`;
  } catch (error) { document.getElementById("labelStudioStatus").textContent = error.message; }
  try {
    const report = await request("/api/model-benchmark");
    const models = report.models || [];
    const fastest = [...models].sort((a, b) => b.fps - a.fps)[0];
    const lowest = [...models].sort((a, b) => a.latency_ms - b.latency_ms)[0];
    document.getElementById("benchmarkBestFps").textContent = fastest ? `${fastest.fps} FPS` : "--";
    document.getElementById("benchmarkBestLatency").textContent = lowest ? `${lowest.latency_ms} ms` : "--";
    document.getElementById("benchmarkImages").textContent = `${report.models?.[0]?.images || 0} 张`;
    document.getElementById("benchmarkStatus").textContent = `${models.length} 个模型`;
    document.getElementById("benchmarkTruth").textContent = report.truth_boundary;
    document.getElementById("benchmarkCharts").innerHTML = (report.charts || []).map(name => `<figure class="benchmark-chart"><img loading="lazy" decoding="async" src="analysis/model_benchmark/${name}" alt="${name}"><figcaption>${name.replace(/\.(png|jpg)$/i, "")}</figcaption></figure>`).join("");
  } catch (error) {
    document.getElementById("benchmarkStatus").textContent = "尚未生成";
    document.getElementById("benchmarkTruth").textContent = `请运行 scripts/benchmark_detection_models.py：${error.message}`;
  }
  try {
    const comparison = await request("/api/algorithm-comparison");
    document.getElementById("algorithmStatus").textContent = `${comparison.models.length} 个候选`;
    document.getElementById("algorithmRecommendation").textContent = comparison.recommendation;
    document.getElementById("algorithmTruth").textContent = comparison.truth_boundary;
    document.getElementById("algorithmRows").innerHTML = comparison.models.map(model => `<tr>
      <td><b>${escapeHtml(model.name)}</b><a href="${escapeHtml(model.repository)}" target="_blank" rel="noreferrer">官方仓库</a></td>
      <td>${escapeHtml(model.family)}<small>${escapeHtml(model.role)} · ${escapeHtml(model.status)}</small></td>
      <td>${escapeHtml(model.local_evidence)}</td>
      <td>${escapeHtml((model.risks || []).join("；"))}</td>
      <td>${escapeHtml(model.decision)}</td>
    </tr>`).join("");
  } catch (error) {
    document.getElementById("algorithmStatus").textContent = "读取失败";
    document.getElementById("algorithmRows").innerHTML = `<tr><td colspan="5">${escapeHtml(error.message)}</td></tr>`;
  }
  try {
    const inventory = await request("/api/software/status");
    const installed = inventory.items.filter(item => item.installed).length;
    document.getElementById("ipcSummary").textContent = `${installed}/${inventory.items.length} 可用`;
    document.getElementById("softwareStatusRows").innerHTML = inventory.items.map(item => `<tr><td>${item.name}</td><td>${item.purpose}</td><td><span class="software-state ${item.installed ? "ready" : "missing"}">${item.installed ? "已安装/在线" : "未安装/未连接"}</span></td><td>${item.endpoint || item.note || "--"}</td></tr>`).join("");
  } catch (error) {
    document.getElementById("ipcSummary").textContent = "检查失败";
    document.getElementById("softwareStatusRows").innerHTML = `<tr><td colspan="4">${error.message}</td></tr>`;
  }
}

function renderDeviceList(elementId, items, emptyText) {
  const element = document.getElementById(elementId);
  if (!element) return;
  element.innerHTML = items?.length ? items.map(item => `<div class="device-row"><b>${escapeHtml(item.device || item.name || "设备")}</b><span>${escapeHtml(item.name || item.model || "")}</span><small>${escapeHtml(item.stable_path || item.usb_path || item.addresses?.join(", ") || item.note || "无独立网络地址")}</small>${item.capabilities?.modes?.length ? `<small>${item.capabilities.modes.length} 种分辨率/FPS模式 · ${item.capabilities.controls.length} 个UVC控制项</small>` : ""}</div>`).join("") : `<span class="device-empty">${emptyText}</span>`;
}

function renderCameraCapabilities(cameras) {
  const element = document.getElementById("cameraCapabilityRows");
  const primary = (cameras || []).filter(item => item.video_capture && item.capabilities?.modes?.length);
  element.innerHTML = primary.map(item => {
    const formats = [...new Set(item.capabilities.modes.map(mode => mode.pixel_format))].join(" / ");
    const maxMode = [...item.capabilities.modes].sort((a, b) => (parseInt(b.size) || 0) - (parseInt(a.size) || 0))[0];
    const controls = item.capabilities.controls.map(control => control.name).join("、");
    const tested = item.probe_report?.tested_modes?.filter(mode => mode.ok).map(mode => `${mode.pixel_format} ${mode.width}×${mode.height}@${mode.fps}`).join("；") || "尚未执行模式测试";
    const recommended = item.probe_report?.recommended_sop_mode;
    return `<article><div><b>${escapeHtml(item.name)}</b><span class="badge green">${escapeHtml(item.stable_path || item.device)}</span></div><dl><dt>格式</dt><dd>${escapeHtml(formats)}</dd><dt>最高模式</dt><dd>${escapeHtml(maxMode ? `${maxMode.size}@${maxMode.fps}FPS` : "--")}</dd><dt>采集测试</dt><dd>${escapeHtml(tested)}</dd><dt>SOP推荐</dt><dd>${escapeHtml(recommended ? `${recommended.pixel_format} ${recommended.width}×${recommended.height}@${recommended.fps}` : "--")}</dd><dt>当前参数</dt><dd>${escapeHtml(item.capabilities.current || "--")}</dd><dt>控制项</dt><dd>${escapeHtml(controls || "--")}</dd></dl></article>`;
  }).join("");
}

function renderCameraWall(cameras) {
  const wall = document.getElementById("cameraWall");
  if (!wall) return;
  state.connectedCameras = cameras || [];
  wall.innerHTML = state.connectedCameras.length ? state.connectedCameras.map(item => `<article class="camera-tile" data-wall-camera="${escapeHtml(item.camera_id)}"><img alt="${escapeHtml(item.camera_name || `摄像头${item.camera_id}`)}实时视频流"><footer><span>${escapeHtml(item.camera_name || `摄像头${item.camera_id}`)}</span><span class="camera-tile-state">待启动 · ${escapeHtml(item.source || "未绑定")}</span></footer></article>`).join("") : `<span class="device-empty">未发现可用的视频采集设备</span>`;
}

function renderCameraRecommendations(items) {
  const element = document.getElementById("cameraRecommendationRows");
  if (!element) return;
  element.innerHTML = items?.length ? items.map(item => `<article><div><b>${escapeHtml(item.priority)}</b><span>${escapeHtml(item.solution)}</span></div><h3>${escapeHtml(item.models)}</h3><dl><dt>传输</dt><dd>${escapeHtml(item.transport)}</dd><dt>预算</dt><dd>${escapeHtml(item.budget_cny)}</dd><dt>延迟</dt><dd>${escapeHtml(item.latency)}</dd><dt>适用</dt><dd>${escapeHtml(item.fit)}</dd></dl><p>${escapeHtml(item.decision)}</p></article>`).join("") : '<span class="device-empty">暂无采购建议</span>';
}

async function startCameraWall() {
  if (!state.connectedCameras.length) await loadDeviceInventory();
  await request("/api/camera/start?camera=all", { method: "POST" }).catch(() => null);
  const stamp = Date.now();
  document.querySelectorAll("[data-wall-camera]").forEach(tile => {
    const id = tile.dataset.wallCamera;
    tile.querySelector("img").src = `/api/camera/mjpeg?camera=${encodeURIComponent(id)}&ts=${stamp}`;
  });
  state.cameraActive = true;
  state.cameraWallActive = true;
  window.clearInterval(state.cameraTimer);
  state.cameraTimer = window.setInterval(pollCameraStatus, 1500);
}

async function stopCameraWall() {
  document.querySelectorAll("[data-wall-camera] img").forEach(image => image.removeAttribute("src"));
  state.cameraWallActive = false;
  await Promise.all(state.connectedCameras.filter(item => !state.cameraSingleActive || String(item.camera_id) !== String(state.selectedCamera)).map(item => request(`/api/camera/stop?camera=${encodeURIComponent(item.camera_id)}`, { method: "POST" }).catch(() => null)));
  state.cameraActive = state.cameraSingleActive;
  window.clearInterval(state.cameraTimer);
  state.cameraTimer = null;
  await pollCameraStatus();
}

async function loadDeviceInventory() {
  try {
    const [inventory, recommendations] = await Promise.all([
      request("/api/device/inventory"),
      request("/api/camera/recommendations"),
    ]);
    document.getElementById("deviceNetworkNote").textContent = `${inventory.camera_network_note} 影石状态：${inventory.insta360?.message || "未读取"}`;
    renderCameraOptions(inventory.camera_sources || []);
    renderCameraWall(inventory.camera_sources || []);
    renderDeviceList("videoDeviceRows", inventory.videos?.filter(item => item.video_capture), "未发现视频采集设备");
    renderDeviceList("serialDeviceRows", inventory.serials, "未发现串口设备");
    renderDeviceList("networkDeviceRows", inventory.network, "未发现网络接口");
    renderCameraCapabilities(inventory.videos);
    renderCameraRecommendations(recommendations.items || []);
  } catch (error) { document.getElementById("deviceNetworkNote").textContent = `设备信息读取失败：${error.message}`; }
}

document.getElementById("refreshDevices").addEventListener("click", async () => {
  const result = await request("/api/camera/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(error => ({ message: error.message }));
  document.getElementById("deviceNetworkNote").textContent = `${result.message || "重新扫描完成"}；${result.insta360?.message || ""}`;
  await loadDeviceInventory();
});
document.getElementById("startAllCameras").addEventListener("click", startCameraWall);
document.getElementById("stopAllCameras").addEventListener("click", stopCameraWall);

async function loadSparkStatus() {
  try {
    const spark = await request("/api/spark/status");
    document.getElementById("sparkStatusBadge").textContent = spark.is_local_spark ? "本机 DGX Spark" : "远程 Spark";
    document.getElementById("sparkStatusBadge").classList.toggle("green", spark.gpu.available);
    document.getElementById("sparkGpu").textContent = spark.gpu.name || "未检测";
    document.getElementById("sparkModelCount").textContent = `${spark.models_available}/${spark.models_registered || spark.models.length}`;
    document.getElementById("sparkBatch").textContent = spark.batch_profile?.recommended_training?.batch || "待调参";
    document.getElementById("sparkInference").textContent = spark.inference_available ? "服务在线" : (spark.is_local_spark ? "本机直连" : "未连接");
    document.getElementById("sparkPolicy").textContent = spark.policy || "训练和主要推理优先在Spark执行。";
    document.getElementById("sparkStore").textContent = `模型仓库：${spark.model_store}`;
  } catch (error) { document.getElementById("sparkStatusBadge").textContent = error.message; }
}

function metricText(value) {
  return value === null || value === undefined ? "--" : `${(Number(value) * 100).toFixed(1)}%`;
}

async function loadPcbModels() {
  const rows = document.getElementById("pcbModelRows");
  if (!rows) return;
  try {
    const registry = await request("/api/models/pcb");
    state.pcbModels = registry.models || [];
    document.getElementById("pcbModelRegistryStatus").textContent = `${state.pcbModels.filter(item => item.selectable).length}/${state.pcbModels.length} 可选`;
    document.getElementById("pcbModelTruth").textContent = registry.truth_boundary || "人工冻结测试集验收前保持 HOLD。";
    rows.innerHTML = state.pcbModels.map(item => `<tr><td><b>${escapeHtml(item.name)}</b></td><td>${escapeHtml(item.dataset)}</td><td>${Number(item.epochs || 0)}</td><td>${metricText(item.metrics?.precision)}</td><td>${metricText(item.metrics?.recall)}</td><td>${metricText(item.metrics?.map50)}</td><td>${metricText(item.metrics?.map50_95)}</td><td><span class="review-state ${item.selectable ? "pending" : "rejected"}">${item.selected ? "当前选择 · HOLD" : (item.selectable ? "待人工验收" : "训练中/待报告")}</span></td><td><button class="${item.selected ? "ghost" : "primary"} choose-pcb-model" data-model-id="${escapeHtml(item.id)}" ${item.selectable && !item.selected ? "" : "disabled"}>${item.selected ? "已选择" : "选择"}</button></td></tr>`).join("");
    const charts = registry.comparison?.charts || [];
    document.getElementById("pcbModelCurves").innerHTML = charts.map(item => `<figure><img loading="lazy" decoding="async" src="${escapeHtml(item.path)}" alt="${escapeHtml(item.title)}"><figcaption>${escapeHtml(item.title)}</figcaption></figure>`).join("");
    document.querySelectorAll(".choose-pcb-model").forEach(button => button.addEventListener("click", () => selectPcbModel(button.dataset.modelId)));
  } catch (error) {
    document.getElementById("pcbModelRegistryStatus").textContent = "读取失败";
    rows.innerHTML = `<tr><td colspan="9">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function selectPcbModel(modelId) {
  try {
    const result = await request("/api/models/pcb/select", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model_id: modelId }) });
    showToast("trainingToast", result.message);
    await loadPcbModels();
  } catch (error) { showToast("trainingToast", error.message, true); }
}

document.getElementById("refreshSparkStatus").addEventListener("click", loadSparkStatus);
document.getElementById("syncSparkModels").addEventListener("click", async () => {
  const button = document.getElementById("syncSparkModels");
  button.disabled = true;
  button.textContent = "正在校验并同步…";
  try {
    const result = await request("/api/spark/sync-models", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    document.getElementById("sparkPolicy").textContent = result.message;
    await loadSparkStatus();
  } catch (error) { document.getElementById("sparkPolicy").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "同步模型到 Spark"; }
});

document.getElementById("viewDatasetCatalog").addEventListener("click", () => document.getElementById("datasetCatalogSection").scrollIntoView({ behavior: "smooth" }));

async function init() {
  try { state.dashboard = await request("/api/dashboard"); } catch (_) { state.dashboard = null; }
  try { state.recipe = await request("/api/recipe"); } catch (_) { state.recipe = { steps: fallbackSteps }; }
  try { state.catalog = await request("/api/videos"); } catch (_) { state.catalog = null; }
  if (state.catalog) {
    document.getElementById("kpiVideos").textContent = `${state.catalog.totals.videos} 段`;
    document.getElementById("kpiDuration").textContent = `累计${state.catalog.totals.duration_s}秒`;
    document.getElementById("kpiFrames").textContent = `${Number(state.catalog.totals.frames).toLocaleString("zh-CN")} 帧`;
    document.getElementById("kpiSteps").textContent = `${state.catalog.totals.steps} 步`;
    const sampled = Number(state.catalog.ningbo_station_extension?.keyframes || (state.catalog.frontier_extension ? 1013 : (state.catalog.small_object_enhancement ? 469 : 0)));
    const prelabelImages = document.getElementById("prelabelImages");
    if (prelabelImages) prelabelImages.textContent = sampled;
    document.getElementById("annotVideoSelect").innerHTML = state.catalog.videos.map((item, index) => `<option value="${escapeHtml(item.id)}">视频${index + 1} · ${escapeHtml(item.source_video.split("/").at(-1))}</option>`).join("");
    document.getElementById("annotVideoSelect").value = state.currentVideoId;
    const selected = currentVideoInfo();
    if (selected) document.getElementById("cvatSelectedVideo").textContent = `${selected.display_name || selected.id} · ${Number(selected.duration_s || 0).toFixed(1)} 秒`;
  }
  renderVideoSwitcher();
  renderLiveSteps();
  renderEvidence();
  renderEditor();
  const video = document.getElementById("sopVideo");
  const info = currentVideoInfo();
  if (info) {
    video.src = authenticatedMediaUrl(info.presentation_video || info.enhanced_video || info.video);
    annotVideo.src = authenticatedMediaUrl(info.source_video);
    annotVideo.load();
    document.getElementById("videoAlgorithm").textContent = info.algorithm?.split(" + ").slice(0, 2).join(" + ") || "目标检测 + SOP状态机";
    document.getElementById("videoResolution").textContent = info.presentation_resolution || info.resolution || "1620×720";
  }
  video.addEventListener("timeupdate", () => updateVideoStatus(video));
  video.addEventListener("loadedmetadata", () => updateVideoStatus(video));
  updateVideoStatus(video);
  pollCameraStatus();
  if (["admin", "manager"].includes(state.user?.role)) {
    await loadTrainingCatalog();
    await loadPcbModels();
    loadSparkStatus();
  }
  fitCanvas();
  if (["admin", "manager"].includes(state.user?.role)) refreshDecision(0, true);
  loadOverviewStatus();
  const initialView = window.location.hash.replace("#", "");
  if (initialView && document.getElementById(`view-${initialView}`)) switchView(initialView);
  state.appInitialized = true;
}

async function loadOverviewStatus() {
  try {
    const inventory = await request("/api/device/inventory");
    const cameras = inventory.camera_sources || [];
    document.getElementById("overviewCameraRows").innerHTML = cameras.length
      ? cameras.map(item => `<span><i class="online"></i><b>${escapeHtml(item.camera_name || `摄像头${item.camera_id}`)}</b><em>已发现</em></span>`).join("")
      : '<span><i class="warning"></i><b>摄像头设备</b><em>未发现</em></span>';
  } catch (_) { /* 总览保持降级状态 */ }
  try {
    const [cvat, tasks] = await Promise.all([request("/api/cvat/status"), request("/api/cvat/tasks")]);
    document.getElementById("overviewCvatDot").className = cvat.available ? "online" : "warning";
    document.getElementById("overviewTaskCount").textContent = `${(tasks.items || []).length} 个已登记任务`;
    document.getElementById("overviewTaskDot").className = (tasks.items || []).length ? "online" : "warning";
  } catch (_) { /* CVAT 状态不阻塞主页 */ }
}

async function bootstrapAuth() {
  try {
    const status = await request("/api/auth/status");
    if (!status.authenticated || !status.user) { showLogin(); return; }
    applyUser(status.user);
    await init();
  } catch (_) { showLogin("无法读取登录状态，请检查平台服务后重试。"); }
}

bootstrapAuth();
