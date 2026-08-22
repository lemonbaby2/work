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
  networkRtspTested: false,
  track: null,
  trackRequest: 0,
  keyframes: [],
  aiStatusTimer: null
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
  // 现场监控保持之前的五视频布局；全部项目 MP4 统一进入数据标注下拉框。
  switcher.innerHTML = state.catalog.videos.slice(0, 5).map((video, index) => `<button data-video="${video.id}" class="${video.id === state.currentVideoId ? "active" : ""}" title="${escapeHtml(video.display_name || video.id)}">视频${numerals[index] || index + 1} · ${Number(video.duration_s || 0).toFixed(1)}秒</button>`).join("");
  switcher.querySelectorAll("button").forEach(button => button.addEventListener("click", () => selectVideo(button.dataset.video)));
}

function selectVideo(videoId) {
  state.currentVideoId = videoId;
  state.decisionSecond = -1;
  state.track = null;
  state.box = null;
  state.keyframes = [];
  renderKeyframes();
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
  loadTracks();
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
  } catch (error) {
    document.getElementById("decisionAction").textContent = `决策服务暂不可用：${error.message}`;
  }
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

function trackFrame() {
  return Math.round(Number(annotVideo.currentTime || 0) * Number(currentVideoInfo()?.fps || 30));
}

function trackBoxAt(frame) {
  const points = state.track?.points || [];
  if (!points.length) return null;
  if (frame <= Number(points[0].frame)) return points[0].box;
  if (frame >= Number(points[points.length - 1].frame)) return points[points.length - 1].box;
  for (let index = 1; index < points.length; index += 1) {
    const before = points[index - 1];
    const after = points[index];
    if (frame <= Number(after.frame)) {
      const ratio = (frame - Number(before.frame)) / Math.max(1, Number(after.frame) - Number(before.frame));
      return before.box.map((value, offset) => Number(value) + (Number(after.box[offset]) - Number(value)) * ratio);
    }
  }
  return points.at(-1).box;
}

function updateTrackPoint(frame, box) {
  if (!state.track) return;
  const normalized = box.map(value => Number(value));
  const existing = state.track.points.find(point => Number(point.frame) === frame);
  if (existing) existing.box = normalized;
  else state.track.points.push({ frame, time_s: frame / Number(currentVideoInfo()?.fps || 30), box: normalized, confidence: 1, source: "manual_edit" });
  state.track.points.sort((left, right) => Number(left.frame) - Number(right.frame));
  state.track.edited = true;
}

function drawTrackOverlay() {
  if (!state.track?.points?.length) return null;
  const current = trackBoxAt(trackFrame());
  if (!current) return null;
  ctx.save();
  ctx.strokeStyle = "rgba(111,220,255,.72)";
  ctx.lineWidth = 2;
  ctx.setLineDash([5, 4]);
  ctx.beginPath();
  state.track.points.forEach((point, index) => {
    const centerX = ((point.box[0] + point.box[2]) / 2) * canvas.width;
    const centerY = ((point.box[1] + point.box[3]) / 2) * canvas.height;
    if (index === 0) ctx.moveTo(centerX, centerY); else ctx.lineTo(centerX, centerY);
  });
  ctx.stroke();
  ctx.setLineDash([]);
  const [x1, y1, x2, y2] = current;
  ctx.strokeStyle = state.track.edited ? "#ffbd66" : "#6fdcff";
  ctx.lineWidth = 3;
  ctx.strokeRect(x1 * canvas.width, y1 * canvas.height, (x2 - x1) * canvas.width, (y2 - y1) * canvas.height);
  ctx.fillStyle = ctx.strokeStyle;
  ctx.fillRect(x1 * canvas.width, Math.max(0, y1 * canvas.height - 20), Math.min(canvas.width - x1 * canvas.width, 180), 20);
  ctx.fillStyle = "#102231";
  ctx.font = "12px Microsoft YaHei, sans-serif";
  ctx.fillText(`${state.track.label} · ${state.track.edited ? "已编辑" : "轨迹"}`, x1 * canvas.width + 6, Math.max(14, y1 * canvas.height - 6));
  ctx.restore();
  return current;
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
  const trackedBox = drawTrackOverlay();
  if (trackedBox && !state.dragging) {
    const [x1, y1, x2, y2] = trackedBox;
    state.box = { x: x1 * canvas.width, y: y1 * canvas.height, w: (x2 - x1) * canvas.width, h: (y2 - y1) * canvas.height, tracked: true };
  }
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
    rows.innerHTML = '<tr><td colspan="7">当前筛选条件下没有标注，请切换时间、来源或状态。</td></tr>';
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
    <td><b>${escapeHtml(item.annotator_name || item.reviewer || "--")}</b><small>${escapeHtml(item.remarks || "无备注")}</small></td>
    <td><div class="row-actions"><button class="ghost" data-load-annotation="${escapeHtml(item.annotation_id)}">载入框</button><button class="ghost" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="human_confirmed">确认</button><button class="ghost danger-action" data-delete-annotation="${escapeHtml(item.annotation_id)}">删除</button><button class="ghost" data-review-annotation="${escapeHtml(item.annotation_id)}" data-review-status="rejected">驳回</button></div></td>
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
    document.getElementById("annotationRows").innerHTML = `<tr><td colspan="7">${escapeHtml(error.message)}</td></tr>`;
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

async function loadAiAnnotationStatus() {
  const badge = document.getElementById("aiAnnotateStatus");
  if (!badge) return;
  try {
    const result = await request("/api/video-library/ai-status");
    const statuses = Object.values(result.items || {});
    const done = statuses.filter(item => item.status === "已完成").length;
    const running = statuses.filter(item => item.status === "运行中" || item.status === "排队中").length;
    badge.textContent = `目录AI：${done}/${statuses.length}完成${running ? ` · ${running}处理中` : ""}`;
    badge.className = `badge ${running ? "" : "green"}`;
  } catch (error) {
    badge.textContent = `AI状态不可用：${error.message}`;
  }
}

async function submitAiAnnotation(videoIds, force = false) {
  const badge = document.getElementById("aiAnnotateStatus");
  try {
    const result = await request("/api/video-library/ai-annotate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_ids: videoIds, force }) });
    if (badge) badge.textContent = result.message;
    loadAiAnnotationStatus();
    window.clearInterval(state.aiStatusTimer);
    state.aiStatusTimer = window.setInterval(async () => {
      await loadAiAnnotationStatus();
      const status = await request("/api/video-library/ai-status");
      if (!Object.values(status.items || {}).some(item => item.status === "运行中" || item.status === "排队中")) window.clearInterval(state.aiStatusTimer);
      if (state.currentVideoId.startsWith("library_")) loadAnnotations();
    }, 2500);
  } catch (error) {
    if (badge) badge.textContent = `AI提交失败：${error.message}`;
    showToast("annotationToast", error.message, true);
  }
}

document.getElementById("aiAnnotateAll")?.addEventListener("click", () => submitAiAnnotation("all"));
document.getElementById("aiAnnotateCurrent")?.addEventListener("click", () => {
  if (!state.currentVideoId.startsWith("library_")) {
    showToast("annotationToast", "原五段演示视频已有AI预标注，可直接人工复核或再次保存", false);
    return;
  }
  submitAiAnnotation([state.currentVideoId], true);
});

canvas.addEventListener("pointerdown", event => {
  state.dragging = true;
  state.start = canvasPoint(event);
  const currentTracked = trackBoxAt(trackFrame());
  state.trackEditing = Boolean(state.track && currentTracked && state.start.x >= currentTracked[0] * canvas.width && state.start.x <= currentTracked[2] * canvas.width && state.start.y >= currentTracked[1] * canvas.height && state.start.y <= currentTracked[3] * canvas.height);
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
canvas.addEventListener("pointerup", () => {
  state.dragging = false;
  if (state.trackEditing && state.track && state.box) {
    const normalized = [state.box.x / canvas.width, state.box.y / canvas.height, (state.box.x + state.box.w) / canvas.width, (state.box.y + state.box.h) / canvas.height];
    updateTrackPoint(trackFrame(), normalized);
    document.getElementById("boxStatus").textContent = "当前轨迹点已修改，可继续跳帧编辑";
  }
  state.trackEditing = false;
});
document.getElementById("clearBox").addEventListener("click", () => {
  state.box = null;
  drawBox();
  document.getElementById("boxStatus").textContent = "请在视频上拖动鼠标框选";
});
window.addEventListener("resize", fitCanvas);
annotVideo.addEventListener("loadedmetadata", () => { fitCanvas(); loadAnnotations(); });
annotVideo.addEventListener("timeupdate", () => {
  const frame = trackFrame();
  document.getElementById("annotFrameTime").textContent = `${Number(annotVideo.currentTime).toFixed(3)} 秒 · 第${frame}帧`;
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

function setAnnotationTime(seconds) {
  const total = Number(annotVideo.duration || currentVideoInfo()?.duration_s || 0);
  annotVideo.currentTime = Math.min(total || seconds, Math.max(0, Number(seconds) || 0));
  annotVideo.pause();
}

document.getElementById("playPause")?.addEventListener("click", async () => {
  if (annotVideo.paused) { try { await annotVideo.play(); } catch (_) { showToast("annotationToast", "浏览器阻止自动播放，请再次点击播放", true); } }
  else annotVideo.pause();
});
document.getElementById("jumpFrameButton")?.addEventListener("click", () => setAnnotationTime(Number(document.getElementById("jumpFrame").value || 0) / Number(currentVideoInfo()?.fps || 30)));
document.getElementById("jumpSecondButton")?.addEventListener("click", () => setAnnotationTime(Number(document.getElementById("jumpSecond").value || 0)));
annotVideo.addEventListener("play", () => { const button = document.getElementById("playPause"); if (button) button.textContent = "暂停"; });
annotVideo.addEventListener("pause", () => { const button = document.getElementById("playPause"); if (button) button.textContent = "播放"; });

function renderKeyframes() {
  const target = document.getElementById("keyframeList");
  if (!target) return;
  target.innerHTML = state.keyframes.length ? state.keyframes.map((point, index) => `<button class="ghost" data-keyframe-index="${index}">第${point.frame}帧 · ${point.label || "框"}</button>`).join("") : "尚未设置关键帧";
  target.querySelectorAll("[data-keyframe-index]").forEach(button => button.addEventListener("click", () => { const point = state.keyframes[Number(button.dataset.keyframeIndex)]; setAnnotationTime(point.frame / Number(currentVideoInfo()?.fps || 30)); state.box = { x: point.box[0] * canvas.width, y: point.box[1] * canvas.height, w: (point.box[2] - point.box[0]) * canvas.width, h: (point.box[3] - point.box[1]) * canvas.height }; drawBox(); }));
}

document.getElementById("setKeyframe")?.addEventListener("click", () => {
  const box = trackAnchorFromBox();
  if (!box) { showToast("annotationToast", "请先框选目标后再设置关键帧", true); return; }
  const frame = trackFrame();
  state.keyframes = state.keyframes.filter(point => point.frame !== frame);
  state.keyframes.push({ frame, box, label: document.getElementById("annotLabel").value });
  state.keyframes.sort((a, b) => a.frame - b.frame); renderKeyframes();
  document.getElementById("boxStatus").textContent = `已记录关键帧 ${frame}，再到另一个帧框选后点击“插值到当前帧”`;
});

document.getElementById("interpolateToCurrent")?.addEventListener("click", async () => {
  const current = trackAnchorFromBox();
  if (!current || state.keyframes.length < 1) { showToast("annotationToast", "需要至少一个已保存关键帧和当前框", true); return; }
  const before = [...state.keyframes].filter(point => point.frame < trackFrame()).at(-1) || state.keyframes[0];
  if (before.frame === trackFrame()) { showToast("annotationToast", "请跳到关键帧之后再插值", true); return; }
  try {
    const result = await request("/api/annotations/interpolate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, start_frame: before.frame, end_frame: trackFrame(), start_box: before.box, end_box: current, label: document.getElementById("annotLabel").value, annotator_name: document.getElementById("annotatorName")?.value.trim() || "标注员12345", remarks: document.getElementById("annotationRemarks")?.value.trim() || "" }) });
    state.track = { ...result, track_id: result.interpolation_id, points: result.points, start_frame: result.start_frame, end_frame: result.end_frame, label: document.getElementById("annotLabel").value, edited: false };
    document.getElementById("boxStatus").textContent = result.message; drawBox(); await loadAnnotations(); await loadTracks();
  } catch (error) { showToast("annotationToast", error.message, true); }
});

async function loadTracks() {
  const target = document.getElementById("trackList"); if (!target) return;
  try {
    const result = await request(`/api/annotations/tracks?video=${encodeURIComponent(state.currentVideoId)}`);
    const tracks = result.tracks || [];
    target.innerHTML = tracks.length ? tracks.slice(0, 8).map(track => `<div class="track-row"><span>${escapeHtml(track.label || "轨迹")} · ${track.start_frame}-${track.end_frame}帧</span><button class="ghost" data-delete-track="${escapeHtml(track.track_id)}">删除</button></div>`).join("") : "尚未保存轨迹";
    target.querySelectorAll("[data-delete-track]").forEach(button => button.addEventListener("click", async () => { await request("/api/annotations/track/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, track_id: button.dataset.deleteTrack, annotator_name: document.getElementById("annotatorName")?.value.trim() || "标注员12345" }) }); await loadTracks(); }));
  } catch (error) { target.textContent = `轨迹读取失败：${error.message}`; }
}
document.getElementById("refreshTracks")?.addEventListener("click", loadTracks);
document.getElementById("showDeletedAnnotations")?.addEventListener("click", async () => {
  const target = document.getElementById("deletionList");
  try {
    const result = await request(`/api/annotations/deletions?video=${encodeURIComponent(state.currentVideoId)}`);
    const items = (result.items || []).filter(item => item.action === "delete");
    target.hidden = false;
    target.innerHTML = items.length ? `<strong>删除审计（可恢复）</strong>${items.slice(0, 30).map(item => `<div class="deletion-row"><span>${escapeHtml(item.annotation_id)} · ${escapeHtml(item.reason || "")}</span><button class="ghost" data-restore-annotation="${escapeHtml(item.annotation_id)}">恢复</button></div>`).join("")}` : "当前视频没有删除记录";
    target.querySelectorAll("[data-restore-annotation]").forEach(button => button.addEventListener("click", async () => { await request("/api/annotations/restore", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: button.dataset.restoreAnnotation, restored_by: document.getElementById("annotatorName")?.value.trim() || "标注员12345" }) }); showToast("annotationToast", "标注已恢复"); await loadAnnotations(); button.closest(".deletion-row")?.remove(); }));
  } catch (error) { showToast("annotationToast", error.message, true); }
});

function trackAnchorFromBox() {
  if (!state.box || state.box.w < 5 || state.box.h < 5) return null;
  return [state.box.x / canvas.width, state.box.y / canvas.height, (state.box.x + state.box.w) / canvas.width, (state.box.y + state.box.h) / canvas.height].map(value => Number(value.toFixed(6)));
}

function setTrackStart() {
  const anchorBox = trackAnchorFromBox();
  if (!anchorBox) {
    showToast("annotationToast", "请先在起始帧框选目标，再设置起始框", true);
    return;
  }
  const frame = trackFrame();
  state.track = { track_id: `track:${state.currentVideoId}:${frame}:${Date.now()}`, video_id: state.currentVideoId, label: document.getElementById("annotLabel").value, start_frame: frame, end_frame: frame, points: [{ frame, time_s: annotVideo.currentTime, box: anchorBox, confidence: 1, source: "manual_anchor" }], method: "人工起始框 + 逐帧检测IoU连续匹配", edited: true };
  document.getElementById("boxStatus").textContent = `已设置起始框：第 ${frame} 帧，可跳到后续帧生成轨迹`;
  drawBox();
}

async function generateTrackTo(frame) {
  if (!state.track?.points?.length) {
    showToast("annotationToast", "请先设置轨迹起始框", true);
    return;
  }
  const anchor = state.track.points.find(point => Number(point.frame) === Number(state.track.start_frame)) || state.track.points[0];
  const requestId = ++state.trackRequest;
  state.track.end_frame = frame;
  state.track.points = [anchor, { frame, time_s: frame / Number(currentVideoInfo()?.fps || 30), box: anchor.box.slice(), confidence: 0, source: "fast_prediction" }].sort((a, b) => a.frame - b.frame);
  state.track.generating = true;
  document.getElementById("boxStatus").textContent = "正在生成轨迹，当前帧先显示快速预测…";
  drawBox();
  const query = new URLSearchParams({ video: state.currentVideoId, start_frame: String(state.track.start_frame), end_frame: String(frame), label: state.track.label, box: JSON.stringify(anchor.box) });
  try {
    const result = await request(`/api/annotations/track?${query.toString()}`);
    if (requestId !== state.trackRequest) return;
    state.track = { ...state.track, ...result, generating: false, edited: false };
    document.getElementById("boxStatus").textContent = `轨迹已生成 ${result.points.length} 点；跳帧后可直接拖框修改当前点`;
    drawBox();
    await request("/api/annotations/track", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...state.track, annotator_name: document.getElementById("annotatorName")?.value.trim() || "标注员12345", remarks: document.getElementById("annotationRemarks")?.value.trim() || "" }) });
    await loadTracks();
  } catch (error) {
    state.track.generating = false;
    document.getElementById("boxStatus").textContent = `轨迹生成失败：${error.message}`;
  }
}

document.getElementById("setTrackStart")?.addEventListener("click", setTrackStart);
document.getElementById("generateTrack")?.addEventListener("click", () => generateTrackTo(trackFrame()));
document.getElementById("autoTrack")?.addEventListener("click", () => generateTrackTo(Math.max(0, Math.round(Number(annotVideo.duration || currentVideoInfo()?.duration_s || 0) * Number(currentVideoInfo()?.fps || 30)) - 1)));
document.getElementById("clearTrack")?.addEventListener("click", () => {
  state.track = null;
  state.box = null;
  document.getElementById("boxStatus").textContent = "轨迹已清除，请重新框选起始目标";
  drawBox();
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
  const deleteButton = event.target.closest("[data-delete-annotation]");
  if (deleteButton) {
    try {
      const result = await request("/api/annotations/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: deleteButton.dataset.deleteAnnotation, video_id: state.currentVideoId, annotator_name: document.getElementById("annotatorName")?.value.trim() || "标注员12345", reason: document.getElementById("annotationRemarks")?.value.trim() || "人工复审删除错误框" }) });
      showToast("annotationToast", result.message); await loadAnnotations(); await loadAnnotationStats();
    } catch (error) { showToast("annotationToast", error.message, true); }
    return;
  }
  const reviewButton = event.target.closest("[data-review-annotation]");
  if (!reviewButton) return;
  try {
    const result = await request("/api/annotations/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ annotation_id: reviewButton.dataset.reviewAnnotation, review_status: reviewButton.dataset.reviewStatus, reviewer: document.getElementById("annotatorName")?.value.trim() || "本地质量员", remarks: document.getElementById("annotationRemarks")?.value.trim() || "" }) });
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
    const annotatorName = document.getElementById("annotatorName")?.value.trim() || "标注员12345";
    const remarks = document.getElementById("annotationRemarks")?.value.trim() || "";
    const result = await request("/api/annotations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: state.currentVideoId, video: currentVideoInfo()?.source_video || "原始测试视频_de02.mp4", video_time: Number(annotVideo.currentTime.toFixed(3)), label: document.getElementById("annotLabel").value, color: document.getElementById("annotColor")?.value || "#39e2bc", box: normalized, evidence_data_url: annotationEvidenceDataUrl(), review_status: "pending", reviewer: annotatorName, annotator_name: annotatorName, remarks, source: "平台人工标注", station_id: document.getElementById("annotStation")?.value, action_id: document.getElementById("annotAction")?.value, inspection_profile: document.getElementById("inspectionProfile")?.value, roi: document.getElementById("annotRoi")?.value, standard_result: document.getElementById("annotStandardResult")?.value, screen_or_version_expectation: document.getElementById("annotScreenExpectation")?.value.trim() || "", temperature_c: document.getElementById("annotTemperature")?.value || null, heating_duration_s: document.getElementById("annotHeatingDuration")?.value || null }) });
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

function formatBytes(bytes) {
  const value = Number(bytes || 0);
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(2)} GB`;
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`;
  return `${(value / 1024).toFixed(0)} KB`;
}

function renderVideoLibrary(payload) {
  const items = payload.items || [];
  document.getElementById("librarySummary").textContent = `${items.length} 个素材 · ${items.filter(item => item.status === "已导入").length} 个已缓存`;
  const rows = document.getElementById("libraryRows");
  if (!items.length) {
    rows.innerHTML = '<tr><td colspan="6">未发现视频文件，请检查 8 月 20/21 日素材目录。</td></tr>';
    return;
  }
  rows.innerHTML = items.map(item => {
    const dimensions = item.width && item.height ? `${item.width}×${item.height}` : "待探测";
    const duration = item.duration_s == null ? "待探测" : `${Number(item.duration_s).toFixed(1)} 秒`;
    const imported = item.status === "已导入";
    return `<tr>
      <td><b title="${escapeHtml(item.source_path)}">${escapeHtml(item.name)}</b><small>${escapeHtml(item.relative_path)}</small></td>
      <td><span class="library-date">${escapeHtml(item.date_bucket)}</span><small>${formatBytes(item.size_bytes)} · ${escapeHtml(item.modified_at)}</small></td>
      <td>${duration}<small>${dimensions}${item.fps ? ` · ${item.fps} FPS` : ""}</small></td>
      <td><span class="quality-pill">抽帧待办</span><span class="quality-pill">OpenCV待检</span><span class="quality-pill">去重待检</span></td>
      <td><span class="review-state ${imported ? "human_confirmed" : "pending"}">${imported ? "已导入缓存区" : "可导入"}</span></td>
      <td><div class="row-actions">${imported ? `<a class="ghost library-play" href="${escapeHtml(item.import_url)}" target="_blank">播放</a>` : ""}<button class="ghost" data-import-video="${escapeHtml(item.id)}">${imported ? "再次登记" : "导入缓存"}</button></div></td>
    </tr>`;
  }).join("");
}

async function loadVideoLibrary(refresh = false) {
  const message = document.getElementById("libraryMessage");
  message.textContent = "正在扫描素材文件并读取视频元数据…";
  try {
    const result = await request(`/api/video-library${refresh ? "?refresh=1" : ""}`);
    renderVideoLibrary(result);
    message.textContent = `${result.cache_policy}｜扫描时间 ${result.generated_at}`;
  } catch (error) {
    document.getElementById("librarySummary").textContent = "读取失败";
    message.textContent = `素材库读取失败：${error.message}`;
  }
}

document.getElementById("refreshLibrary")?.addEventListener("click", () => loadVideoLibrary(true));
document.getElementById("libraryRows")?.addEventListener("click", async event => {
  const button = event.target.closest("[data-import-video]");
  if (!button) return;
  button.disabled = true;
  try {
    const result = await request("/api/video-library/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ video_id: button.dataset.importVideo, operator: document.getElementById("annotatorName")?.value.trim() || "平台管理员", remarks: document.getElementById("annotationRemarks")?.value.trim() || "" }) });
    document.getElementById("libraryMessage").textContent = result.message;
    window.open(result.media_url, "_blank", "noopener");
    await loadVideoLibrary(true);
  } catch (error) {
    document.getElementById("libraryMessage").textContent = `导入失败：${error.message}`;
    button.disabled = false;
  }
});

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
    document.getElementById("annotVideoSelect").innerHTML = state.catalog.videos.map((item, index) => `<option value="${escapeHtml(item.id)}">${item.library ? "素材" : `视频${index + 1}`} · ${escapeHtml(item.display_name || item.source_video?.split("/").at(-1) || item.id)}</option>`).join("");
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
  loadAiAnnotationStatus();
  loadAnnotations();
  loadQualityReports();
  loadVideoLibrary();
  loadDeviceInventory();
  fitCanvas();
  refreshDecision(0, true);
  const initialView = window.location.hash.replace("#", "");
  if (initialView && document.getElementById(`view-${initialView}`)) switchView(initialView);
}

init();
