const state = {
  dashboard: null,
  recipe: null,
  catalog: null,
  currentVideoId: "video_de02",
  box: null,
  dragging: false,
  start: null,
  decisionSecond: -1,
  cameraTimer: null,
  cameraActive: false,
  recording: false,
  selectedCamera: "0",
  annotationItems: [],
  networkRtspTested: false
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
  if (!response.ok) throw new Error(data.message || "请求失败");
  return data;
}

function showToast(id, message, bad = false) {
  const element = document.getElementById(id);
  element.textContent = message;
  element.style.background = bad ? "#fff0ee" : "#e6f5f0";
  element.style.color = bad ? "#a33a34" : "#08705d";
  element.classList.add("show");
  window.setTimeout(() => element.classList.remove("show"), 4200);
}

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === name));
  document.querySelectorAll(".view").forEach(view => view.classList.toggle("active", view.id === `view-${name}`));
  if (name === "decision") refreshDecision(document.getElementById("sopVideo").currentTime, true);
  if (name === "annotation") {
    fitCanvas();
    loadAnnotations();
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-jump]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.jump)));

function currentVideoInfo() {
  return state.catalog?.videos.find(video => video.id === state.currentVideoId) || null;
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
  const numerals = ["一", "二", "三", "四", "五", "六", "七", "八"];
  switcher.innerHTML = state.catalog.videos.map((video, index) => `<button data-video="${video.id}" class="${video.id === state.currentVideoId ? "active" : ""}">视频${numerals[index] || index + 1} · ${video.duration_s}秒</button>`).join("");
  switcher.querySelectorAll("button").forEach(button => button.addEventListener("click", () => selectVideo(button.dataset.video)));
}

function selectVideo(videoId) {
  state.currentVideoId = videoId;
  state.decisionSecond = -1;
  const info = currentVideoInfo();
  if (!info) return;
  const video = document.getElementById("sopVideo");
  video.pause();
  video.src = info.presentation_video || info.enhanced_video || info.video;
  video.load();
  const annotVideo = document.getElementById("annotVideo");
  annotVideo.src = info.source_video;
  annotVideo.load();
  document.getElementById("annotVideoSelect").value = videoId;
  document.getElementById("videoAlgorithm").textContent = info.algorithm?.split(" + ").slice(0, 2).join(" + ") || "目标检测 + SOP状态机";
  document.getElementById("videoResolution").textContent = info.presentation_resolution || info.resolution || "1620×720";
  renderVideoSwitcher();
  renderLiveSteps();
  renderEvidence();
  updateVideoStatus(video);
  refreshDecision(0, true);
  loadAnnotations();
}

function renderLiveSteps() {
  const steps = playbackSteps();
  document.getElementById("liveSteps").innerHTML = steps.map(step => `<li data-step="${step.id}"><b>${step.id}</b> ${step.label}</li>`).join("");
}

function formatTime(value) {
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function updateVideoStatus(video) {
  const steps = playbackSteps();
  const step = activeStep(video.currentTime || 0);
  const currentIndex = steps.findIndex(item => item.id === step.id);
  document.getElementById("liveStepTitle").textContent = `${step.id} ${step.label}`;
  const total = Number.isFinite(video.duration) ? video.duration : Number(currentVideoInfo()?.duration_s || 77.53);
  document.getElementById("liveStepTime").textContent = `${formatTime(video.currentTime || 0)} / ${formatTime(total)}`;
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
    document.getElementById("evidenceScore").textContent = `${decision.evidence_score}%`;
    document.getElementById("evidenceMeter").style.width = `${decision.evidence_score}%`;
    document.getElementById("decisionStep").textContent = `${decision.step.id} ${decision.step.label}`;
    document.getElementById("decisionTime").textContent = `${decision.time_s.toFixed(1)}秒`;
    document.getElementById("decisionReasons").innerHTML = decision.reasons.map(reason => `<li>${reason}</li>`).join("");
    document.getElementById("decisionChain").innerHTML = decision.decision_chain.map((item, index) => `<span>${item}</span>${index < decision.decision_chain.length - 1 ? "<i>→</i>" : ""}`).join("");
    document.getElementById("countRegions").textContent = decision.objects.business_regions;
    document.getElementById("countDynamic").textContent = decision.objects.dynamic;
    document.getElementById("countFastener").textContent = decision.objects.fastener_candidates;
    document.getElementById("truthNotice").textContent = decision.truth_notice;
    renderDecisionVisuals(decision);
  } catch (error) {
    document.getElementById("decisionAction").textContent = `决策服务暂不可用：${error.message}`;
  }
}

function renderDecisionVisuals(decision) {
  const gates = decision.gate_status || {};
  const ringColors = { pass: "#32c9a3", watch: "#e7a33b", blocked: "#df6b62" };
  const rings = document.getElementById("gateRings");
  if (rings) rings.innerHTML = Object.entries(gates).map(([name, gate]) => {
    const score = Math.round(Number(gate.score || 0));
    const color = ringColors[gate.state] || "#8aa0a9";
    return `<div class="gate-ring" style="--score:${score}%;--ring:${color}"><div><b>${score}</b><span>${escapeHtml(name)}</span></div><small>${escapeHtml(gate.detail || "")}</small></div>`;
  }).join("");
  const readiness = document.getElementById("decisionReadiness");
  if (readiness) readiness.textContent = `${Number(decision.release_readiness || 0).toFixed(0)}% 可放行`;
  const breakdown = decision.evidence_breakdown || {};
  const chart = document.getElementById("decisionTrendChart");
  if (chart) {
    const values = [breakdown.visual || 0, breakdown.temporal || 0, breakdown.tool || 0, breakdown.mes || 0];
    const labels = ["视觉", "时序", "工具", "MES"];
    chart.innerHTML = `<div class="trend-bars">${values.map((value, index) => `<div class="trend-column"><span>${Math.round(value)}%</span><i style="height:${Math.max(3, value)}%"></i><b>${labels[index]}</b></div>`).join("")}</div><div class="trend-caption">当前帧 ${Number(decision.frame || 0).toLocaleString("zh-CN")} · ${Number(decision.time_s || 0).toFixed(1)} 秒 · 仅作证据强度展示</div>`;
  }
  const radar = document.getElementById("decisionRadar");
  if (radar) radar.innerHTML = Object.entries(gates).map(([name, gate]) => `<div class="radar-row"><span>${escapeHtml(name)}</span><i><b style="width:${Math.max(0, Math.min(100, Number(gate.score || 0)))}%"></b></i><strong>${Math.round(Number(gate.score || 0))}</strong></div>`).join("");
  const timeline = document.getElementById("decisionTimeline");
  if (timeline) timeline.innerHTML = (decision.timeline || []).map(item => `<div class="timeline-step ${item.state}"><span>${escapeHtml(item.id)}</span><b>${escapeHtml(item.label)}</b><i><em style="width:${item.state === "done" ? 100 : item.progress || 0}%"></em></i><small>${item.state === "done" ? "已完成" : item.state === "current" ? `${Number(item.progress || 0).toFixed(0)}% 进行中` : "待执行"}</small></div>`).join("");
  const timelineState = document.getElementById("decisionTimelineState");
  if (timelineState) timelineState.textContent = `${decision.completed_steps || 0}/${(decision.timeline || []).length} 步已完成`;
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
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
}

function drawBox() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const currentTime = Number(annotVideo.currentTime || 0);
  const fps = Number(currentVideoInfo()?.fps || 30);
  state.annotationItems.filter(item => Math.abs(Number(item.video_time) - currentTime) <= Math.max(0.05, 1.1 / fps)).forEach(item => {
    const [x1, y1, x2, y2] = item.box;
    const x = x1 * canvas.width;
    const y = y1 * canvas.height;
    const width = (x2 - x1) * canvas.width;
    const height = (y2 - y1) * canvas.height;
    const palette = ["#39e2bc", "#ffbd66", "#6db6ff", "#ef6a62", "#c28cff", "#f28f6b"];
    const paletteIndex = [...new Set(state.annotationItems.map(entry => entry.label))].indexOf(item.label);
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

function renderAnnotationRows(items) {
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
    <td>${escapeHtml(item.label)}</td>
    <td><span class="source-tag ${item.source_kind}">${sourceNames[item.source_kind] || escapeHtml(item.source_kind)}</span><small title="${escapeHtml(item.source)}">${escapeHtml(item.source)}</small></td>
    <td>${item.confidence == null ? "--" : `${(Number(item.confidence) * 100).toFixed(1)}%`}</td>
    <td><span class="review-state ${item.review_status}">${statusNames[item.review_status] || escapeHtml(item.review_status)}</span></td>
    <td><div class="row-actions"><button class="ghost" data-load-annotation="${escapeHtml(item.annotation_id)}">载入框</button><button class="ghost" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="human_confirmed">确认</button><button class="ghost danger-action" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="rejected">驳回</button></div></td>
  </tr>`).join("");
}

async function loadAnnotations() {
  if (!state.catalog) return;
  const source = document.getElementById("annotSourceFilter").value;
  const status = document.getElementById("annotStatusFilter").value;
  const time = Number(annotVideo.currentTime || 0);
  document.getElementById("annotFrameTime").textContent = `${time.toFixed(3)} 秒`;
  try {
    const result = await request(`/api/annotations?video=${encodeURIComponent(state.currentVideoId)}&time=${time.toFixed(3)}&source=${encodeURIComponent(source)}&status=${encodeURIComponent(status)}&limit=300`);
    state.annotationItems = result.items || [];
    renderAnnotationRows(state.annotationItems);
    drawBox();
  } catch (error) {
    state.annotationItems = [];
    document.getElementById("annotationRows").innerHTML = `<tr><td colspan="6">${escapeHtml(error.message)}</td></tr>`;
    document.getElementById("annotationResultCount").textContent = "读取失败";
  }
}

async function loadAnnotationStats() {
  try {
    const stats = await request("/api/annotations/stats");
    document.getElementById("prelabelCount").textContent = Number(stats.prelabels).toLocaleString("zh-CN");
    document.getElementById("candidateCount").textContent = Number(stats.candidates).toLocaleString("zh-CN");
    document.getElementById("manualCount").textContent = Number(stats.manual).toLocaleString("zh-CN");
    document.getElementById("annotationTruth").textContent = stats.truth_boundary;
  } catch (error) {
    document.getElementById("annotationTruth").textContent = `标注统计暂不可用：${error.message}`;
  }
}

canvas.addEventListener("pointerdown", event => {
  state.dragging = true;
  state.start = canvasPoint(event);
  state.box = { x: state.start.x, y: state.start.y, w: 0, h: 0 };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", event => {
  if (!state.dragging) return;
  const point = canvasPoint(event);
  state.box = { x: Math.min(state.start.x, point.x), y: Math.min(state.start.y, point.y), w: Math.abs(point.x - state.start.x), h: Math.abs(point.y - state.start.y) };
  drawBox();
  document.getElementById("boxStatus").textContent = `框选 ${Math.round(state.box.w)} × ${Math.round(state.box.h)} 像素`;
});
canvas.addEventListener("pointerup", () => state.dragging = false);
document.getElementById("clearBox").addEventListener("click", () => {
  state.box = null;
  drawBox();
  document.getElementById("boxStatus").textContent = "请在视频上拖动鼠标框选";
});
window.addEventListener("resize", fitCanvas);
annotVideo.addEventListener("loadedmetadata", () => { fitCanvas(); loadAnnotations(); });
annotVideo.addEventListener("timeupdate", () => {
  document.getElementById("annotFrameTime").textContent = `${Number(annotVideo.currentTime).toFixed(3)} 秒`;
  drawBox();
});
annotVideo.addEventListener("seeked", loadAnnotations);
annotVideo.addEventListener("pause", loadAnnotations);

document.getElementById("annotVideoSelect").addEventListener("change", event => selectVideo(event.target.value));
document.getElementById("annotSourceFilter").addEventListener("change", loadAnnotations);
document.getElementById("annotStatusFilter").addEventListener("change", loadAnnotations);
document.getElementById("refreshAnnotations").addEventListener("click", loadAnnotations);
document.getElementById("prevFrame").addEventListener("click", () => {
  annotVideo.pause();
  annotVideo.currentTime = Math.max(0, annotVideo.currentTime - 1 / Number(currentVideoInfo()?.fps || 30));
});
document.getElementById("nextFrame").addEventListener("click", () => {
  annotVideo.pause();
  annotVideo.currentTime = Math.min(Number(currentVideoInfo()?.duration_s || annotVideo.duration || 0), annotVideo.currentTime + 1 / Number(currentVideoInfo()?.fps || 30));
});

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
    if (/^#[0-9a-f]{6}$/i.test(item.color || "") && document.getElementById("annotColor")) document.getElementById("annotColor").value = item.color;
    document.getElementById("boxStatus").textContent = `已载入：${item.label}，可拖框重画后保存`;
    drawBox();
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

document.getElementById("saveAnnotation").addEventListener("click", async () => {
  if (!state.box || state.box.w < 5 || state.box.h < 5) {
    showToast("annotationToast", "请先在视频画面上框选一个零件", true);
    return;
  }
  const normalized = [state.box.x / canvas.width, state.box.y / canvas.height, (state.box.x + state.box.w) / canvas.width, (state.box.y + state.box.h) / canvas.height].map(value => Number(value.toFixed(5)));
  try {
    const result = await request("/api/annotations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, video: currentVideoInfo()?.source_video || "原始测试视频_de02.mp4", video_time: Number(annotVideo.currentTime.toFixed(3)), label: document.getElementById("annotLabel").value, color: document.getElementById("annotColor")?.value || "#39e2bc", box: normalized, evidence_data_url: annotationEvidenceDataUrl(), review_status: "pending", reviewer: "本地标注员", source: "平台人工标注" }) });
    showToast("annotationToast", result.message);
    state.box = null;
    await Promise.all([loadAnnotations(), loadAnnotationStats()]);
  } catch (error) { showToast("annotationToast", error.message, true); }
});

document.getElementById("startTraining").addEventListener("click", async () => {
  try {
    const result = await request("/api/train/start", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ dataset: "新增两视频_YOLOE26_SAHI细粒度预标注_待人工复核", model: "YOLO26N", device: "RTX 4060", gate: "人工复核完成后重新训练并用冻结测试集验收" }) });
    showToast("trainingToast", `${result.message}｜${result.job_id}`);
  } catch (error) { showToast("trainingToast", error.message, true); }
});

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
    const result = await request("/api/mes/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ product_sn: document.getElementById("mesProductSn")?.value || "NB202608160001", station_id: document.getElementById("mesStationId")?.value || "NB-IP-SOP-01", visual_sequence: "PASS", torque_result: "WAITING", final_result: document.getElementById("mesFinalResult")?.value || "HOLD", recipe_version: "IP-ASSEMBLY-V1", model_version: "YOLO26N-candidate", evidence: { video: "现场证据待接入", frame: 0 } }) });
    output.className = "api-result ok";
    output.textContent = `${result.message}｜事件号 ${result.event_id}｜状态 ${result.state}｜幂等键 ${result.idempotency_key}`;
    document.getElementById("mesState").textContent = result.state;
    document.getElementById("mesTransport").textContent = result.transport;
  } catch (error) {
    output.className = "api-result warn";
    output.textContent = error.message;
  }
});

function updateMesPayloadPreview() {
  const preview = document.getElementById("mesPayloadPreview");
  if (!preview) return;
  preview.textContent = JSON.stringify({ product_sn: document.getElementById("mesProductSn")?.value || "", station_id: document.getElementById("mesStationId")?.value || "", recipe: "IP-ASSEMBLY-V1", visual_sequence: "PASS", torque_result: "WAITING", final_result: document.getElementById("mesFinalResult")?.value || "HOLD", evidence_video: "...mp4", idempotency_key: "event_id:product_sn:station_id" }, null, 2);
}
["mesProductSn", "mesStationId", "mesFinalResult"].forEach(id => document.getElementById(id)?.addEventListener("input", updateMesPayloadPreview));
request("/api/mes/schema").then(schema => { const status = document.getElementById("mesSchemaStatus"); if (status) status.textContent = `${schema.required_fields.length} 个必填字段 / ${schema.guarantees.length} 项可靠性保障`; }).catch(error => { const status = document.getElementById("mesSchemaStatus"); if (status) status.textContent = `读取失败：${error.message}`; });

function renderCameraStatus(status) {
  const badge = document.getElementById("cameraBadge");
  const message = document.getElementById("cameraMessage");
  if (!badge || !status) return;
  const running = Boolean(status.running);
  const hasError = Boolean(status.error);
  badge.textContent = hasError ? "异常" : (running ? "实时运行" : "未启动");
  badge.className = `badge ${hasError ? "camera-error" : (running ? "camera-live" : "")}`;
  document.getElementById("cameraSource").textContent = status.source || "--";
  document.getElementById("cameraResolution").textContent = status.width && status.height ? `${status.width}×${status.height}` : "--";
  document.getElementById("cameraFps").textContent = status.fps ? `${status.fps} FPS` : "--";
  document.getElementById("cameraLatency").textContent = status.inference_ms ? `${status.inference_ms} ms` : "--";
  document.getElementById("cameraDetections").textContent = Number.isFinite(status.detections) ? `${status.detections} 个` : "--";
  const output = document.getElementById("cameraOutput");
  if (output) output.textContent = status.output_dir || "桌面/sop xjai";
  state.recording = Boolean(status.recording);
  const recordButton = document.getElementById("recordLiveCamera");
  if (recordButton) recordButton.textContent = state.recording ? "停止录制" : "开始录制";
  if (hasError) message.textContent = status.error;
  else if (running) message.textContent = `模型 ${status.model || "YOLOv11n"} 正在实时检测，结果仅作现场验证留证。`;
  else message.textContent = "点击“启动实时检测”打开摄像头";
}

async function pollCameraStatus() {
  try {
    const status = await request(`/api/camera/status?camera=${state.selectedCamera}`);
    renderCameraStatus(status);
    if (status.error && state.cameraActive) {
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
  state.cameraActive = false;
  window.clearInterval(state.cameraTimer);
  state.cameraTimer = null;
  document.getElementById("liveCameraFeed").removeAttribute("src");
  document.getElementById("liveCameraPlaceholder").hidden = false;
  try { await request(`/api/camera/stop?camera=${state.selectedCamera}`, { method: "POST" }); } catch (_) { /* 页面关闭时服务可能已经停止 */ }
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

document.getElementById("cameraSelect").addEventListener("change", event => {
  state.selectedCamera = event.target.value;
  if (state.cameraActive) {
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
    document.getElementById("labelStudioLink").href = integrations.label_studio.url;
    document.getElementById("labelStudioVideoLink").href = integrations.label_studio.url;
    document.getElementById("labelStudioStatus").textContent = integrations.label_studio.available ? "服务在线，可直接创建视频任务" : `未连接：${integrations.label_studio.url}`;
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
    document.getElementById("benchmarkCharts").innerHTML = (report.charts || []).map(name => `<figure class="benchmark-chart"><img src="analysis/model_benchmark/${name}" alt="${name}"><figcaption>${name.replace(/\.(png|jpg)$/i, "")}</figcaption></figure>`).join("");
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
  element.innerHTML = items?.length ? items.map(item => `<div class="device-row"><b>${escapeHtml(item.device || item.name || "设备")}</b><span>${escapeHtml(item.name || item.model || "")}</span><small>${escapeHtml(item.stable_path || item.usb_path || item.addresses?.join(", ") || item.note || "无独立网络地址")}</small></div>`).join("") : `<span class="device-empty">${emptyText}</span>`;
}

function networkField(id) {
  return document.getElementById(id)?.value.trim() || "";
}

function renderNetworkCamera(camera) {
  if (!camera) return;
  document.getElementById("networkCameraName").value = camera.name || "网络摄像头3 · 镭威视电池智链摄像机";
  document.getElementById("networkCameraIp").value = camera.ip || "192.168.1.135";
  document.getElementById("networkCameraMac").value = camera.mac || "c4:3c:b0:be:40:e8";
  document.getElementById("networkCameraGateway").value = camera.gateway || "192.168.1.1";
  document.getElementById("networkCameraNetmask").value = camera.netmask || "255.255.255.0";
  document.getElementById("networkCameraDns").value = camera.dns || "192.168.1.1";
  document.getElementById("networkCameraRtsp").value = camera.rtsp_url || "rtsp://192.168.1.135:554/";
  document.getElementById("networkCameraUsername").value = "";
  document.getElementById("networkCameraPassword").value = "";
  state.networkRtspTested = Boolean(camera.rtsp_path_confirmed);
  const status = document.getElementById("networkCameraBindStatus");
  status.textContent = camera.reachable === false ? "设备未验证" : (state.networkRtspTested ? "已绑定" : "身份已绑定");
  status.className = `badge ${state.networkRtspTested ? "green" : ""}`;
  document.getElementById("networkCameraMessage").textContent = camera.note || "MAC/IP 已登记，RTSP 路径待确认。";
}

async function loadDeviceInventory() {
  try {
    const inventory = await request("/api/device/inventory");
    document.getElementById("deviceNetworkNote").textContent = inventory.camera_network_note;
    renderDeviceList("videoDeviceRows", inventory.videos?.filter(item => item.video_capture), "未发现视频采集设备");
    renderDeviceList("serialDeviceRows", inventory.serials, "未发现串口设备");
    renderDeviceList("networkDeviceRows", inventory.network, "未发现网络接口");
    renderNetworkCamera(inventory.network_cameras?.[0]);
  } catch (error) { document.getElementById("deviceNetworkNote").textContent = `设备信息读取失败：${error.message}`; }
}

document.getElementById("refreshDevices").addEventListener("click", loadDeviceInventory);

document.getElementById("networkCameraRtsp").addEventListener("input", () => {
  state.networkRtspTested = false;
  document.getElementById("networkCameraBindStatus").textContent = "待测试";
  document.getElementById("networkCameraMessage").textContent = "RTSP 地址发生变化，请先测试再保存。";
});

document.getElementById("testNetworkCamera").addEventListener("click", async () => {
  const message = document.getElementById("networkCameraMessage");
  message.textContent = "正在通过 TCP 测试 RTSP 视频流…";
  try {
    const result = await request("/api/network-cameras/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rtsp_url: networkField("networkCameraRtsp") }) });
    state.networkRtspTested = true;
    document.getElementById("networkCameraBindStatus").textContent = "RTSP 可用";
    document.getElementById("networkCameraBindStatus").className = "badge green";
    message.textContent = `${result.message} ${JSON.stringify(result.streams?.streams || [])}`;
  } catch (error) {
    state.networkRtspTested = false;
    document.getElementById("networkCameraBindStatus").textContent = "RTSP 未通过";
    document.getElementById("networkCameraBindStatus").className = "badge camera-error";
    message.textContent = `测试失败：${error.message}`;
  }
});

document.getElementById("discoverNetworkCamera").addEventListener("click", async () => {
  const message = document.getElementById("networkCameraMessage");
  message.textContent = "正在探测 TCP、常见 RTSP 路径和视频 SDP…";
  try {
    const result = await request("/api/network-cameras/discover", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ip: networkField("networkCameraIp"), username: networkField("networkCameraUsername"), password: document.getElementById("networkCameraPassword")?.value || "" }) });
    const success = (result.results || []).find(item => item.ok);
    if (success) {
      document.getElementById("networkCameraRtsp").value = success.url;
      state.networkRtspTested = true;
      document.getElementById("networkCameraBindStatus").textContent = "发现可用流";
      document.getElementById("networkCameraBindStatus").className = "badge green";
      message.textContent = `${result.message}：${success.url}`;
    } else {
      state.networkRtspTested = false;
      message.textContent = `${result.message}。设备在线但路径/认证仍需从 BTACAM App 获取。`;
    }
  } catch (error) { message.textContent = `探测失败：${error.message}`; }
});

document.getElementById("saveNetworkCamera").addEventListener("click", async () => {
  const message = document.getElementById("networkCameraMessage");
  try {
    const camera = {
      id: 2,
      name: networkField("networkCameraName"),
      ip: networkField("networkCameraIp"),
      mac: networkField("networkCameraMac").toLowerCase(),
      gateway: networkField("networkCameraGateway"),
      netmask: networkField("networkCameraNetmask"),
      dns: networkField("networkCameraDns"),
      protocol: "RTSP",
      rtsp_url: networkField("networkCameraRtsp"),
      ap_ip: "192.168.188.2",
      ap_gateway: "192.168.188.1",
      wifi_ssid: "IPC-631180002304",
      rtsp_path_confirmed: state.networkRtspTested,
      note: state.networkRtspTested ? "RTSP 地址已通过 ffprobe 测试。" : "身份已绑定，RTSP 路径待确认。"
    };
    const result = await request("/api/network-cameras/bind", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ camera }) });
    message.textContent = `${result.message} 当前状态：${state.networkRtspTested ? "可尝试启动" : "需确认 RTSP 路径"}`;
    document.getElementById("networkCameraBindStatus").textContent = state.networkRtspTested ? "已绑定" : "身份已绑定";
    document.getElementById("networkCameraBindStatus").className = `badge ${state.networkRtspTested ? "green" : ""}`;
  } catch (error) { message.textContent = `保存失败：${error.message}`; }
});

async function init() {
  try { state.dashboard = await request("/api/dashboard"); } catch (_) { state.dashboard = null; }
  try { state.recipe = await request("/api/recipe"); } catch (_) { state.recipe = { steps: fallbackSteps }; }
  try { state.catalog = await request("/api/videos"); } catch (_) { state.catalog = null; }
  if (state.catalog) {
    document.getElementById("kpiVideos").textContent = `${state.catalog.totals.videos} 段`;
    document.getElementById("kpiDuration").textContent = `累计${state.catalog.totals.duration_s}秒`;
    document.getElementById("kpiFrames").textContent = `${Number(state.catalog.totals.frames).toLocaleString("zh-CN")} 帧`;
    document.getElementById("kpiSteps").textContent = `${state.catalog.totals.steps} 步`;
    const sampled = state.catalog.frontier_extension ? 1013 : (state.catalog.small_object_enhancement ? 469 : 0);
    const prelabelImages = document.getElementById("prelabelImages");
    if (prelabelImages) prelabelImages.textContent = sampled;
    document.getElementById("annotVideoSelect").innerHTML = state.catalog.videos.map((item, index) => `<option value="${escapeHtml(item.id)}">视频${index + 1} · ${escapeHtml(item.source_video.split("/").at(-1))}</option>`).join("");
    document.getElementById("annotVideoSelect").value = state.currentVideoId;
  }
  renderVideoSwitcher();
  renderLiveSteps();
  renderEvidence();
  renderEditor();
  const video = document.getElementById("sopVideo");
  const info = currentVideoInfo();
  if (info) {
    video.src = info.presentation_video || info.enhanced_video || info.video;
    document.getElementById("videoAlgorithm").textContent = info.algorithm?.split(" + ").slice(0, 2).join(" + ") || "目标检测 + SOP状态机";
    document.getElementById("videoResolution").textContent = info.presentation_resolution || info.resolution || "1620×720";
  }
  video.addEventListener("timeupdate", () => updateVideoStatus(video));
  video.addEventListener("loadedmetadata", () => updateVideoStatus(video));
  updateVideoStatus(video);
  pollCameraStatus();
  loadAnnotationStats();
  loadAnnotations();
  loadQualityReports();
  loadDeviceInventory();
  fitCanvas();
  refreshDecision(0, true);
  const initialView = window.location.hash.replace("#", "");
  if (initialView && document.getElementById(`view-${initialView}`)) switchView(initialView);
}

init();
