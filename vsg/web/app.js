"use strict";

const state = {
  token: "",
  platform: null,
  config: null,
  snapshot: null,
  source: "all",
  riskOnly: false,
  query: "",
  timer: null,
  stopTarget: null,
  stopObservationJob: null,
  dismissedObservationJobId: null,
  workloadMatrixTarget: null,
  workloadMatrixPlan: null,
  workloadMatrixJob: null,
  workloadMatrixTimer: null,
  activeView: "services",
  plannerStatus: null,
  estimate: null,
  healthBenchmarkTarget: null,
  diagnosticTarget: null,
  restoreTarget: null,
  snapshots: [],
  advisor: null,
  logEvents: null,
  operations: { incidents: null, timeline: [], inventory: [], rules: [], topology: null },
  attributionTarget: null,
  confirmationAction: null,
  detailTarget: null,
  impactReport: null,
  notifiedObservationJobs: new Set(),
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const uiLocale = () => window.VSG_I18N?.locale === "en" ? "en-US" : "zh-CN";

function localPreference(key, fallback = null) {
  try { return localStorage.getItem(key) ?? fallback; }
  catch { return fallback; }
}

function saveLocalPreference(key, value) {
  try { localStorage.setItem(key, value); }
  catch { /* Private browsing or policy may disable local preferences. */ }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.method && options.method !== "GET") {
    headers["Content-Type"] = "application/json";
    headers["X-VSG-Token"] = state.token;
  }
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  let payload;
  try { payload = await response.json(); } catch { payload = { ok: false, error: `HTTP ${response.status}` }; }
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.hidden = true; }, 3200);
}

function formatTime(timestamp) {
  if (!timestamp) return "—";
  return new Intl.DateTimeFormat(uiLocale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp * 1000));
}

function formatDate(timestamp) {
  if (!timestamp) return "未知";
  return new Intl.DateTimeFormat(uiLocale(), { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(timestamp * 1000));
}

function formatDuration(start) {
  if (!start) return "未知";
  const seconds = Math.max(0, Date.now() / 1000 - start);
  if (seconds < 60) return `${Math.floor(seconds)}秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}分钟`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}小时${Math.floor((seconds % 3600) / 60)}分`;
  return `${Math.floor(seconds / 86400)}天${Math.floor((seconds % 86400) / 3600)}小时`;
}

function sourceLabel(source) {
  return {
    host: "宿主机",
    agent: "Agent 进程",
    model_runtime: "模型推理",
    windows_service: "Windows 服务",
    docker: "Docker",
    wsl: "WSL",
    platform: "平台",
  }[source] || source;
}

function riskPresentation(risk) {
  if (!risk?.scored) return { label: "未评分", cls: "unscored", orb: "normal" };
  if (risk.level === "likely_stale") return { label: `疑似遗留 ${risk.score}`, cls: "stale", orb: "stale" };
  if (risk.level === "review") return { label: `建议复核 ${risk.score}`, cls: "review", orb: "review" };
  if (risk.level === "expected") return { label: "预期服务", cls: "", orb: "normal" };
  return { label: `正常 ${risk.score}`, cls: "", orb: "normal" };
}

function searchable(service) {
  return [
    service.display_name, service.runtime, service.source, service.process?.pid,
    service.process?.name, service.process?.command, service.process?.cwd,
    service.project?.name, service.project?.path, service.agent?.provider,
    service.agent?.session_id, ...(service.endpoints || []).flatMap((item) => [item.port, item.address, item.protocol]),
  ].filter(Boolean).join(" ").toLocaleLowerCase();
}

function filteredServices() {
  const services = state.snapshot?.services || [];
  return services.filter((service) => {
    if (state.source === "model_runtime" && !service.metadata?.model_runtime) return false;
    if (state.source !== "all" && state.source !== "model_runtime" && service.source !== state.source) return false;
    if (state.riskOnly && !["review", "likely_stale"].includes(service.risk?.level)) return false;
    if (state.query && !searchable(service).includes(state.query)) return false;
    return true;
  });
}

function renderSummary() {
  const summary = state.snapshot?.summary || {};
  $("#metric-services").textContent = summary.services ?? "—";
  $("#metric-ports").textContent = (summary.tcp_listeners ?? 0) + (summary.udp_bindings ?? 0);
  $("#metric-protocols").textContent = `${summary.tcp_listeners ?? 0} TCP / ${summary.udp_bindings ?? 0} UDP`;
  $("#metric-projects").textContent = summary.projects ?? "—";
  $("#metric-agents").textContent = summary.agents ?? "—";
  $("#metric-review").textContent = summary.review_count ?? "—";
  $("#metric-load").textContent = `${summary.cpu_percent ?? 0}%`;
  $("#metric-load").nextElementSibling.textContent = `CPU / 内存 ${summary.memory_percent ?? 0}%`;
  $("#metric-updated").textContent = formatTime(state.snapshot?.generated_at);
  $("#metric-duration").textContent = state.snapshot?.duration_ms != null ? `采集耗时 ${state.snapshot.duration_ms} ms` : "等待采集";
}

function renderCollectors() {
  const strip = $("#collector-strip");
  strip.replaceChildren();
  const collectors = state.snapshot?.collectors || {};
  for (const [name, info] of Object.entries(collectors)) {
    const pill = document.createElement("span");
    pill.className = `collector-pill ${info.status || ""}`;
    pill.textContent = `${sourceLabel(name)}：${info.message || info.status}`;
    strip.appendChild(pill);
  }
  for (const error of state.snapshot?.errors || []) {
    const pill = document.createElement("span");
    pill.className = "collector-pill error";
    pill.textContent = error;
    strip.appendChild(pill);
  }
}

function renderRelationships() {
  const relationships = state.snapshot?.service_relationships || {};
  const summary = relationships.summary || {};
  $("#relationship-dependencies").textContent = summary.local_dependencies ?? "—";
  $("#relationship-allowed").textContent = summary.stop_allowed ?? "—";
  $("#relationship-review").textContent = summary.stop_review ?? "—";
  $("#relationship-blocked").textContent = summary.stop_blocked ?? "—";
  const services = new Map((state.snapshot?.services || []).map((item) => [item.id, item]));
  const container = $("#relationship-list");
  container.replaceChildren();
  const dependencies = relationships.dependencies || [];
  if (!dependencies.length) {
    const empty = document.createElement("span");
    empty.className = "confidence-note";
    empty.textContent = relationships.collection?.local_connections === "unavailable"
      ? "当前权限无法读取本机连接依赖，关停评估将明确标记证据不足。"
      : "当前没有检测到服务之间的本机 TCP 依赖。";
    container.appendChild(empty);
    return;
  }
  for (const item of dependencies.slice(0, 12)) {
    const target = services.get(item.target_service_id);
    const node = document.createElement("span");
    node.className = "relationship-edge";
    node.innerHTML = `<b>${escapeHtml(item.source_name || `PID ${item.source_pid}`)}</b><span>→</span><b>${escapeHtml(target?.display_name || item.target_service_id)}</b><span>:${escapeHtml(item.port)}</span>`;
    container.appendChild(node);
  }
}

function updateCounts() {
  const services = state.snapshot?.services || [];
  $("#count-all").textContent = services.length;
  $("#count-host").textContent = services.filter((item) => item.source === "host").length;
  $("#count-agent").textContent = services.filter((item) => item.source === "agent").length;
  $("#count-model-runtime").textContent = services.filter((item) => item.metadata?.model_runtime).length;
  $("#count-windows").textContent = services.filter((item) => item.source === "windows_service").length;
  $("#count-docker").textContent = services.filter((item) => item.source === "docker").length;
  $("#count-wsl").textContent = services.filter((item) => item.source === "wsl").length;
}

function renderRows() {
  const body = $("#service-body");
  const services = filteredServices();
  body.replaceChildren();
  $("#loading-state").hidden = true;
  $("#empty-state").hidden = services.length !== 0;

  for (const service of services) {
    const risk = riskPresentation(service.risk);
    let endpoints = (service.endpoints || []).slice(0, 5).map((endpoint) =>
      `<span class="port-tag" title="${escapeHtml(endpoint.protocol)} ${escapeHtml(endpoint.address)}">:${escapeHtml(endpoint.port)}</span>`
    ).join("");
    if (!endpoints) endpoints = `<span class="subline">无监听端口</span>`;
    const extraPorts = (service.endpoints || []).length > 5 ? `<span class="subline">+${service.endpoints.length - 5}</span>` : "";
    const project = service.project?.name || "未归类项目";
    const agent = service.agent?.provider || "来源未知";
    const confidence = service.agent?.provider ? `<span class="confidence-badge">${escapeHtml(service.agent.confidence)}% 证据</span>` : "";
    const cpu = Number(service.process?.cpu_percent || 0);
    const memory = Number(service.process?.memory_percent || 0);
    const firstTcp = (service.endpoints || []).find((item) => item.protocol === "TCP");
    const canStop = service.source === "host" && !service.protected && service.metadata?.stoppable_candidate && service.process?.pid > 0;
    const canOpen = Boolean(firstTcp) && (service.source === "docker" || service.source === "wsl" || service.metadata?.openable_candidate);
    const historyLabel = service.metadata?.historical_lifecycle_label === "safe_cleanup"
      ? '<span class="source-badge history-label">用户历史标记 · 可安全清理</span>'
      : service.metadata?.historical_lifecycle_label === "expected"
        ? '<span class="source-badge history-label">用户历史标记 · 预期</span>'
        : "";
    const row = document.createElement("tr");
    row.dataset.id = service.id;
    row.innerHTML = `
      <td><div class="service-cell"><i class="status-orb ${risk.orb}"></i><div><span class="service-name" title="${escapeHtml(service.display_name)}">${escapeHtml(service.display_name)}</span><span class="subline">${escapeHtml(service.runtime)} · ${escapeHtml(service.process?.name || "unknown")}</span><span class="source-badge">${escapeHtml(sourceLabel(service.source))}</span>${historyLabel}</div></div></td>
      <td><span class="mono">PID ${escapeHtml(service.process?.pid || "—")}</span><div class="port-list">${endpoints}${extraPorts}</div></td>
      <td><strong>${escapeHtml(project)}</strong><span class="subline" title="${escapeHtml(service.project?.path || "")}">${escapeHtml(agent)} ${confidence}</span></td>
      <td class="load-cell"><div class="load-values"><span>CPU <b>${cpu.toFixed(1)}%</b></span><span>MEM <b>${memory.toFixed(1)}%</b></span></div><progress class="load-progress" max="100" value="${Math.min(100, Math.max(cpu, memory))}"></progress></td>
      <td><span class="mono">${escapeHtml(formatDuration(service.process?.create_time))}</span><br><span class="risk-badge ${risk.cls}">${escapeHtml(risk.label)}</span></td>
      <td class="actions-column"><div class="actions">
        <button class="row-button" type="button" data-action="details" title="查看归属证据">i</button>
        <button class="row-button" type="button" data-action="url" title="打开本地 URL" ${canOpen ? "" : "disabled"}>↗</button>
        <button class="row-button" type="button" data-action="folder" title="打开项目目录" ${service.project?.path ? "" : "disabled"}>⌂</button>
        <button class="row-button" type="button" data-action="attribute" title="纠正项目与 Agent 归属">✎</button>
        <button class="row-button" type="button" data-action="mark" title="${service.expected ? "取消预期标记" : "标记为预期服务"}">${service.expected ? "★" : "☆"}</button>
        <button class="row-button" type="button" data-action="impact" title="查看关停影响与恢复证据">◇</button>
        <button class="row-button danger" type="button" data-action="stop" title="停止进程树" ${canStop ? "" : "disabled"}>■</button>
      </div></td>`;
    body.appendChild(row);
  }
}

function render() {
  renderSummary();
  renderCollectors();
  renderRelationships();
  updateCounts();
  renderRows();
  renderHealth();
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "未知";
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${bytes} B`;
}

function valueOrUnknown(value, suffix = "") {
  return value == null ? "不可用" : `${Number(value).toFixed(1)}${suffix}`;
}

function healthStateLabel(value) {
  return { healthy: "健康", warning: "需处理", critical: "高风险", unknown: "证据不足" }[value] || value || "未知";
}

function renderHealthOverview() {
  const posture = state.snapshot?.posture || {};
  const overall = posture.overall || { state: "unknown", score: 0, summary: "证据采集中" };
  const card = $("#health-overall-card");
  card.className = `overall-card ${overall.state || "unknown"}`;
  $("#health-overall").textContent = healthStateLabel(overall.state);
  $("#health-overall-summary").textContent = overall.summary || "当前证据不足";
  const scoreSuffix = Number(overall.unknown_domain_count || 0) > 0 ? "*" : "";
  $("#health-overall-score").textContent = overall.state === "unknown" ? "—" : `${overall.score}${scoreSuffix}`;
  const grid = $("#health-domain-grid");
  grid.replaceChildren();
  for (const key of ["machine", "performance", "security", "stability", "capacity"]) {
    const domain = posture.domains?.[key] || { label: key, state: "unknown", score: 0, evidence: [], unknowns: [] };
    const article = document.createElement("article");
    article.className = "domain-card";
    const evidence = domain.evidence?.[0] || domain.unknowns?.[0] || "暂无证据";
    article.innerHTML = `<span class="domain-state ${escapeHtml(domain.state)}">${escapeHtml(healthStateLabel(domain.state))}</span><strong>${escapeHtml(domain.label)}</strong><b>${domain.state === "unknown" ? "—" : escapeHtml(domain.score)}</b><small>${escapeHtml(evidence)}</small>`;
    grid.appendChild(article);
  }
}

function telemetryRow(title, detail, value, cls = "") {
  return `<div class="telemetry-row ${escapeHtml(cls)}"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(detail)}</span><span>${escapeHtml(value)}</span></div>`;
}

function renderLiveResources() {
  const telemetry = state.snapshot?.telemetry || {};
  const cpu = telemetry.cpu || {};
  const memory = telemetry.memory || {};
  $("#live-cpu").textContent = valueOrUnknown(cpu.percent, "%");
  $("#live-cpu-progress").value = Number(cpu.percent || 0);
  $("#live-cpu-detail").textContent = `${cpu.physical_cores ?? "?"} 核 / ${cpu.logical_cores ?? "?"} 线程 · ${cpu.current_frequency_mhz == null ? "频率不可用" : `${cpu.current_frequency_mhz} MHz`}`;
  $("#live-memory").textContent = valueOrUnknown(memory.used_percent, "%");
  $("#live-memory-progress").value = Number(memory.used_percent || 0);
  $("#live-memory-detail").textContent = `${memory.used_gib ?? "?"} / ${memory.total_gib ?? "?"} GiB · 可用 ${memory.available_gib ?? "?"} GiB`;
  const power = telemetry.power || {};
  $("#live-power").textContent = power.gpu_power_w == null ? "传感器不可用" : `${Number(power.gpu_power_w).toFixed(1)} W GPU`;
  $("#live-cost").textContent = power.estimated_cost_since_start == null ? power.note || "未获得实测功耗" : `本次启动累计 ${power.energy_wh_since_start} Wh · 约 ${power.estimated_cost_since_start}（按 ${power.electricity_price_per_kwh}/kWh）`;
  const network = telemetry.network || {};
  $("#live-network").textContent = network.rate_status === "warming_up" ? "正在建立速率基线" : `↑ ${network.send_mib_per_second ?? 0} / ↓ ${network.receive_mib_per_second ?? 0} MiB/s`;
  $("#live-network-detail").textContent = `模型服务公网对端 ${network.public_remote_connections ?? 0} · 不抓包、不读 URL/内容`;

  const gpuContainer = $("#live-gpus");
  const gpus = telemetry.gpus || [];
  gpuContainer.innerHTML = gpus.length ? gpus.map((gpu) => {
    const memoryTotal = gpu.memory_total_gib == null ? "总量未知" : `${gpu.memory_total_gib} GiB`;
    const memoryText = gpu.memory_used_gib == null ? `VRAM 实时占用不可用 / ${memoryTotal}` : `VRAM ${gpu.memory_used_gib} GiB / ${memoryTotal}`;
    const live = String(gpu.telemetry_status || "").startsWith("measured");
    const detail = live ? `GPU ${valueOrUnknown(gpu.gpu_util_percent, "%")} · 温度 ${valueOrUnknown(gpu.temperature_c, "°C")} · 风扇 ${valueOrUnknown(gpu.fan_percent, "%")}${gpu.shared_memory_used_gib == null ? "" : ` · 共享 ${gpu.shared_memory_used_gib} GiB`}` : gpu.limitation || "厂商遥测不可用";
    return telemetryRow(gpu.name || `GPU ${gpu.index}`, detail, live ? `${memoryText} · ${valueOrUnknown(gpu.power_w, " W")}` : memoryText, live ? (gpu.telemetry_status === "measured_partial" ? "warning" : "") : "unavailable");
  }).join("") : telemetryRow("未检测到 GPU", "将使用 CPU/系统内存路径", "不可用", "unavailable");

  const diskContainer = $("#live-disks");
  const disks = telemetry.disks || [];
  diskContainer.innerHTML = disks.length ? disks.map((disk) => telemetryRow(disk.root, `${disk.scope === "system" ? "系统卷" : "项目卷"} · 已用 ${disk.used_percent ?? "?"}%`, disk.free_gib == null ? "不可用" : `剩余 ${disk.free_gib} GiB`, disk.low_space ? "warning" : disk.status === "unavailable" ? "unavailable" : "")).join("") : telemetryRow("磁盘", "未获得卷信息", "不可用", "unavailable");

  const sensorContainer = $("#live-sensors");
  const sensors = telemetry.sensors || {};
  const sensorRows = [];
  for (const item of sensors.temperatures || []) sensorRows.push(telemetryRow(item.label || item.group, item.group, `${item.current_c}°C`, Number(item.current_c) >= 90 ? "critical" : Number(item.current_c) >= 80 ? "warning" : ""));
  for (const item of sensors.fans || []) sensorRows.push(telemetryRow(item.label || item.group, item.group, `${item.rpm} RPM`));
  if (!sensorRows.length) sensorRows.push(telemetryRow("温度 / 风扇", (sensors.limitations || ["当前系统未暴露传感器"])[0], "不可用", "unavailable"));
  sensorContainer.innerHTML = sensorRows.join("");
  renderServiceResources();
}

function renderServiceResources() {
  const payload = state.snapshot?.telemetry?.service_resources || {};
  const english = window.VSG_I18N?.locale === "en";
  const body = $("#service-resource-body");
  body.replaceChildren();
  for (const item of payload.items || []) {
    const row = document.createElement("tr");
    const gpuText = item.gpu_memory_used_gib == null ? (english ? "Unknown" : "未知") : `${Number(item.gpu_memory_used_gib).toFixed(3)} GiB`;
    row.innerHTML = `<td><strong>${escapeHtml(item.display_name)}</strong>${item.model_runtime ? `<span class="source-badge">${english ? "Model service" : "模型服务"}</span>` : ""}</td><td><span class="mono">PID ${escapeHtml(item.pid)}</span><span class="subline">${escapeHtml(item.runtime)}</span></td><td>${item.cpu_percent == null ? (english ? "Unknown" : "未知") : `${Number(item.cpu_percent).toFixed(1)}%`}</td><td>${item.rss_gib == null ? (english ? "Unknown" : "未知") : `${Number(item.rss_gib).toFixed(3)} GiB`}<span class="subline">${item.memory_percent == null ? "—" : `${Number(item.memory_percent).toFixed(2)}% RAM`}</span></td><td>${escapeHtml(gpuText)}</td><td><span class="candidate-status ${item.gpu_status === "measured" ? "compatible" : "medium"}">${escapeHtml(item.gpu_status || "unknown")}</span><span class="subline">${escapeHtml(item.gpu_source || item.process_status || "unknown")}</span></td>`;
    body.appendChild(row);
  }
  const limitations = payload.limitations || [];
  $("#service-resource-limitation").textContent = english
    ? (limitations.length ? "Per-process GPU memory is shown only when a stable unprivileged vendor interface is available." : "No per-process resource evidence.")
    : (limitations[0] || "没有进程级资源证据。");
}

function exposureLabel(service) {
  const exposures = new Set((service?.endpoints || []).map((item) => item.exposure));
  if (exposures.has("all_interfaces")) return { label: "所有网卡", cls: "unhealthy" };
  if (exposures.has("lan")) return { label: "指定网卡/LAN", cls: "warning" };
  return { label: "仅回环", cls: "ready" };
}

function renderRuntimeHealth() {
  const body = $("#runtime-health-body");
  const probes = state.snapshot?.runtime_probes || [];
  const services = state.snapshot?.services || [];
  const firewall = state.snapshot?.posture?.firewall || {};
  const network = state.snapshot?.telemetry?.network || {};
  body.replaceChildren();
  $("#runtime-health-empty").hidden = probes.length !== 0;
  for (const probe of probes) {
    const service = services.find((item) => item.id === probe.service_id) || {};
    const model = probe.models?.[0] || {};
    const config = probe.configuration || {};
    const perf = probe.performance || {};
    const capacity = probe.capacity || {};
    const exposure = exposureLabel(service);
    const auth = probe.security?.auth_posture || "unknown";
    const rules = firewall.inbound_allow_matches?.[String(probe.port)] || [];
    const remotes = (network.model_remote_connections || []).filter((item) => Number(item.pid) === Number(probe.pid));
    const uptime = service.process?.create_time ? formatDuration(service.process.create_time) : "未知";
    const benchmarkAllowed = probe.health === "ready" && auth !== "required" && service.runtime !== "ComfyUI";
    const row = document.createElement("tr");
    row.dataset.serviceId = service.id || "";
    row.innerHTML = `
      <td><strong>${escapeHtml(service.display_name || probe.runtime)}</strong><span class="subline">${escapeHtml(probe.runtime)} · PID ${escapeHtml(probe.pid)} · :${escapeHtml(probe.port)}</span><span class="subline">${escapeHtml(model.name || "未报告已加载模型")}</span></td>
      <td><span class="status-chip ${escapeHtml(probe.health)}">${escapeHtml(probe.health)}</span><span class="status-chip ${escapeHtml(probe.model_load)}">${escapeHtml(probe.model_load)}</span><span class="subline">${escapeHtml(model.quantization || config.quantization || "量化未知")} · ${escapeHtml(config.backend || "后端未知")} · ${escapeHtml(config.accelerator || "加速路径未知")}</span><span class="subline">工具 ${escapeHtml(config.capabilities?.tools || "unknown")} · 视觉 ${escapeHtml(config.capabilities?.vision || "unknown")} · 语音 ${escapeHtml(config.capabilities?.audio || "unknown")}</span></td>
      <td><strong>${perf.generation_tps == null ? "TPS 未暴露" : `${perf.generation_tps} tok/s`}</strong><span class="subline">Prompt ${perf.prompt_tps == null ? "—" : `${perf.prompt_tps} tok/s`} · TTFT ${perf.ttft_seconds_average == null ? "—" : `${perf.ttft_seconds_average}s`}</span><span class="subline">来源：${escapeHtml(perf.source || "unavailable")}</span></td>
      <td><strong>运行 ${perf.requests_running ?? "?"} / 等待 ${perf.requests_waiting ?? "?"}</strong><span class="subline">已观察峰值 ${perf.observed_max_concurrency ?? 0} · 配置 ${config.configured_concurrency ?? "未知"}</span><span class="subline">上下文 ${capacity.context_tokens ?? config.context_tokens ?? "未知"} · KV ${perf.kv_cache_usage_percent == null ? "未知" : `${perf.kv_cache_usage_percent}%`}</span></td>
      <td><span class="status-chip ${escapeHtml(exposure.cls)}">${escapeHtml(exposure.label)}</span><span class="status-chip ${escapeHtml(auth)}">认证 ${escapeHtml(auth)}</span><span class="subline">防火墙允许匹配 ${rules.length} · 当前远端 ${remotes.length}</span><span class="subline">${escapeHtml(remotes.slice(0, 2).map((item) => `${item.remote_address}:${item.remote_port} ${item.scope}`).join("；") || "无可见远端连接")}</span></td>
      <td><strong>运行 ${escapeHtml(uptime)}</strong><span class="subline">历史重启 ${service.metadata?.restart_count ?? 0} · 进程 ${escapeHtml(service.process?.status || "unknown")}</span><span class="subline">自动重启 ${service.metadata?.auto_restart == null ? "未知" : service.metadata.auto_restart ? "已配置" : "未配置"} ${service.metadata?.restart_policy ? `· ${escapeHtml(service.metadata.restart_policy)}` : ""}</span><span class="subline">日志错误需显式选文件</span></td>
      <td><div class="runtime-actions"><button class="runtime-action" type="button" data-health-action="benchmark" ${benchmarkAllowed ? "" : "disabled"}>短基准</button><button class="runtime-action" type="button" data-health-action="matrix" ${benchmarkAllowed ? "" : "disabled"}>负载矩阵</button><button class="runtime-action" type="button" data-health-action="inspect">日志/配置</button><button class="runtime-action" type="button" data-health-action="details">证据详情</button></div></td>`;
    body.appendChild(row);
  }
}

function renderProjectRuntimeViews() {
  const views = state.snapshot?.service_relationships?.project_runtime_views || [];
  const body = $("#project-runtime-body");
  body.replaceChildren();
  $("#project-runtime-empty").hidden = views.length !== 0;
  for (const item of views) {
    const project = item.project || {};
    const service = item.service || {};
    const model = item.models?.[0] || {};
    const resources = item.resources || {};
    const capacity = item.capacity || {};
    const risk = riskPresentation(item.risk || {});
    const currentLoad = `CPU ${resources.cpu_percent == null ? "?" : `${Number(resources.cpu_percent).toFixed(1)}%`} · RAM ${resources.rss_gib == null ? "?" : `${Number(resources.rss_gib).toFixed(2)} GiB`} · VRAM ${resources.gpu_memory_used_gib == null ? "?" : `${Number(resources.gpu_memory_used_gib).toFixed(2)} GiB`}`;
    const row = document.createElement("tr");
    row.innerHTML = `<td><strong>${escapeHtml(project.name || "未归类项目")}</strong><span class="subline">${escapeHtml(item.agent?.provider || "Agent 未归属")} · 归属置信度 ${escapeHtml(project.confidence || 0)}%</span></td><td><strong>${escapeHtml(service.display_name)}</strong><span class="subline">${escapeHtml(service.runtime)} · PID ${escapeHtml(service.pid || "—")} · ${escapeHtml(item.health)}</span></td><td><strong>${escapeHtml(model.name || "未报告")}</strong><span class="subline">${escapeHtml(model.quantization || "量化未知")}</span></td><td>${escapeHtml(currentLoad)}<span class="subline">运行请求 ${escapeHtml(item.performance?.requests_running ?? "未知")} · 排队 ${escapeHtml(item.performance?.requests_waiting ?? "未知")}</span></td><td><strong>${capacity.available_concurrency == null ? "证据不足" : `约 ${escapeHtml(capacity.available_concurrency)} 并发`}</strong><span class="subline">实测安全上限 ${escapeHtml(capacity.measured_safe_concurrency ?? "—")} · ${escapeHtml(capacity.evidence_label)}</span></td><td><span class="risk-badge ${escapeHtml(risk.cls)}">${escapeHtml(risk.label)}</span><span class="subline">监听 ${(service.endpoints || []).map((endpoint) => `:${endpoint.port}`).join(" ") || "无"}</span></td>`;
    body.appendChild(row);
  }
}

function renderSecurityAndNetwork() {
  const posture = state.snapshot?.posture || {};
  const firewall = posture.firewall || {};
  const profiles = firewall.profiles || [];
  const reverse = posture.reverse_proxies || [];
  $("#security-posture").innerHTML = [
    telemetryRow("防火墙", profiles.length ? profiles.map((item) => `${item.name}:${item.enabled ? "启用" : "未启用"}`).join(" · ") : "无法读取", firewall.status || "unknown", firewall.all_profiles_enabled === false ? "warning" : firewall.status !== "measured" ? "unavailable" : ""),
    telemetryRow("入站允许", `宽泛 Any 规则 ${firewall.broad_allow_rule_count ?? "未知"}`, "仅匹配非回环模型端口"),
    telemetryRow("反向代理", reverse.length ? reverse.map((item) => item.name).join("、") : "未从进程表识别", reverse.length ? `${reverse.length} 个` : "无", reverse.length ? "warning" : ""),
    telemetryRow("公网判断", "0.0.0.0/:: 仅代表潜在远程可达，不证明已穿透 NAT", "不执行外网回连"),
  ].join("");
  const network = state.snapshot?.telemetry?.network || {};
  const nodes = state.snapshot?.trusted_nodes || {};
  const nodeText = (nodes.nodes || []).map((item) => `${item.url}=${item.status}`).join("；") || "未配置；不会扫描局域网";
  $("#network-posture").innerHTML = [
    telemetryRow("模型服务联网", `公网对端 ${network.public_remote_connections ?? 0}`, network.model_connections_status || "unknown", Number(network.public_remote_connections || 0) ? "warning" : ""),
    telemetryRow("总流量", `累计发送 ${formatBytes(network.bytes_sent_total)} · 接收 ${formatBytes(network.bytes_recv_total)}`, network.rate_status || "unknown"),
    telemetryRow("多卡", `检测 ${state.snapshot?.telemetry?.hardware?.gpu_count ?? 0} 个显示/计算适配器；可独立容量计算 ${state.snapshot?.telemetry?.hardware?.capacity_gpu_count ?? 0} 个`, state.snapshot?.telemetry?.hardware?.multi_gpu ? "多 GPU 容量" : "非多 GPU 容量"),
    telemetryRow("受信节点", nodeText, `${nodes.nodes?.length || 0} 个`),
  ].join("");
}

function renderFindingsAndLimitations() {
  const posture = state.snapshot?.posture || {};
  const container = $("#health-findings");
  const findings = posture.findings || [];
  container.innerHTML = findings.length ? findings.map((item) => `<article class="finding-item ${escapeHtml(item.severity)}"><span class="finding-severity">${escapeHtml(item.severity)}</span><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.evidence)}</span><span>${escapeHtml(item.action)}</span></article>`).join("") : `<article class="finding-item info"><span class="finding-severity">INFO</span><strong>暂无高优先级发现</strong><span>这不等于所有未知项都安全。</span><span>展开下方证据边界继续核实。</span></article>`;
  const unknowns = [];
  for (const domain of Object.values(posture.domains || {})) unknowns.push(...(domain.unknowns || []).map((item) => `${domain.label}：${item}`));
  unknowns.push(...(posture.limitations || []));
  unknowns.push(...(state.snapshot?.telemetry?.sensors?.limitations || []));
  const list = $("#health-limitations");
  list.replaceChildren();
  for (const text of [...new Set(unknowns)]) {
    const item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }
}

function renderHealth() {
  renderHealthOverview();
  renderLiveResources();
  renderRuntimeHealth();
  renderProjectRuntimeViews();
  renderSecurityAndNetwork();
  renderFindingsAndLimitations();
}

function architectureLabel(value) {
  return value === "moe" ? "MoE" : value === "dense" ? "Dense" : "Hybrid";
}

function formatGib(value) {
  return value == null ? "未知" : `${Number(value).toFixed(1)} GiB`;
}

function formatParams(item) {
  if (item.architecture === "moe") return `${item.total_params_b}B 总 / ${item.active_params_b}B 激活`;
  if (Number(item.active_params_b) < Number(item.total_params_b)) return `${item.total_params_b}B 总 / ${item.active_params_b}B 有效`;
  return `${item.total_params_b}B`;
}

function setPlannerBusy(busy, label = "计算可运行上限") {
  const button = $("#planner-submit");
  button.disabled = busy;
  button.textContent = busy ? "正在计算…" : label;
}

function plannerBody() {
  return {
    total_users: Number($("#planner-users").value),
    concurrency: Number($("#planner-concurrency").value),
    prompt_tokens: Number($("#planner-prompt").value),
    context_tokens: Number($("#planner-context").value),
    output_tokens: Number($("#planner-output").value),
    target_tps_per_user: Number($("#planner-tps").value),
    target_ttft_seconds: Number($("#planner-ttft").value),
    preference: $("#planner-preference").value,
    runtime: $("#planner-runtime").value,
    kv_cache_bits: Number($("#planner-kv").value),
  };
}

function renderHardware() {
  const payload = state.plannerStatus;
  if (!payload) return;
  const hardware = payload.hardware || {};
  const cpu = hardware.cpu || {};
  const memory = hardware.memory || {};
  $("#hardware-cpu").textContent = cpu.name || "未知 CPU";
  $("#hardware-cpu-detail").textContent = `${cpu.physical_cores ?? "?"} 核 / ${cpu.logical_cores ?? "?"} 线程 · 带宽估算 ${cpu.memory_bandwidth_gbps_estimate ?? "?"} GB/s`;
  $("#hardware-memory").textContent = `${formatGib(memory.total_gib)} 总内存`;
  $("#hardware-memory-detail").textContent = `${formatGib(memory.available_gib)} 当前可用${memory.unified ? " · Apple 统一内存" : ""}`;

  const gpuList = $("#hardware-gpus");
  gpuList.replaceChildren();
  const gpus = hardware.gpus || [];
  if (!gpus.length) gpuList.textContent = "未获得可用于容量计算的 GPU，将使用 CPU / 系统内存。";
  for (const gpu of gpus) {
    const item = document.createElement("div");
    const memoryText = gpu.memory_total_gib == null ? "显存未知" : `${formatGib(gpu.memory_total_gib)} ${gpu.unified_memory ? "统一内存" : "显存"}`;
    item.innerHTML = `<strong>${escapeHtml(gpu.name)}</strong><span>${escapeHtml(memoryText)} · ${escapeHtml(gpu.backend)} · ${escapeHtml(gpu.support_tier)} · ${escapeHtml(gpu.confidence)} 置信度</span>`;
    gpuList.appendChild(item);
  }

  const runtimeList = $("#runtime-list");
  runtimeList.replaceChildren();
  for (const runtime of payload.runtimes || []) {
    const pill = document.createElement("span");
    pill.className = `runtime-pill ${runtime.installed ? "installed" : ""} ${runtime.support_tier === "experimental" ? "experimental" : ""}`;
    pill.textContent = `${runtime.label} · ${runtime.installed ? (runtime.version || "已检测") : "未检测"}`;
    runtimeList.appendChild(pill);
  }

  const benchmark = payload.benchmark || {};
  $("#benchmark-availability").textContent = benchmark.available ? "llama-bench 可用" : "未检测到 llama-bench";
  $("#benchmark-button").disabled = !benchmark.available;
  renderBenchmarkHistory();
  renderMeasuredProfiles();
  fillBenchmarkModels();
}

function renderMeasuredProfiles() {
  const payload = state.plannerStatus || {};
  const profiles = payload.measured_profiles || { items: [], summary: {} };
  const margin = payload.current_resource_margin || {};
  const ram = margin.ram || {};
  const gpuMargins = (margin.gpus || []).map((item) => `${item.name || "GPU"} ${item.memory_free_gib == null ? "余量未知" : `${item.memory_free_gib} GiB 可用`}`).join(" · ");
  $("#measured-profile-margin").textContent = `当前 RAM 可用 ${ram.available_gib ?? "未知"} GiB / ${ram.available_percent ?? "未知"}%${gpuMargins ? ` · ${gpuMargins}` : " · 未获得 VRAM 余量"} · 85% 护栏`;
  const summary = profiles.summary || {};
  $("#measured-profile-summary").innerHTML = `<span class="source-badge">有效 ${escapeHtml(summary.valid || 0)}</span><span class="source-badge">可能失效 ${escapeHtml(summary.possibly_invalid || 0)}</span><span class="source-badge">已过期 ${escapeHtml(summary.expired || 0)}</span>`;
  const container = $("#measured-profile-list");
  const targetSelect = $("#planner-calibration-service");
  const previousTarget = targetSelect.value;
  targetSelect.replaceChildren();
  const probes = state.snapshot?.runtime_probes || [];
  for (const probe of probes.filter((item) => item.health === "ready" && item.models?.length)) {
    const service = state.snapshot?.services?.find((item) => item.id === probe.service_id);
    if (!service || service.runtime === "ComfyUI" || probe.security?.auth_posture === "required") continue;
    const option = document.createElement("option");
    option.value = service.id;
    option.textContent = `${service.project?.name || "未归类项目"} · ${service.display_name} · ${probe.models[0].name}`;
    targetSelect.appendChild(option);
  }
  if ([...targetSelect.options].some((item) => item.value === previousTarget)) targetSelect.value = previousTarget;
  $("#planner-calibrate-one").disabled = !targetSelect.options.length;
  $("#planner-calibrate-two").disabled = !targetSelect.options.length;
  container.replaceChildren();
  if (!(profiles.items || []).length) {
    container.innerHTML = '<span class="confidence-note">尚无本机 60 秒实测档案。请到 AI 运行体检，对已加载模型运行单并发或双并发校准。</span>';
    return;
  }
  for (const profile of profiles.items.slice(0, 20)) {
    const measurement = profile.measurement || {};
    const theoretical = profile.theoretical_capacity?.max_concurrency?.effective;
    const measured = profile.measured_safe ? profile.concurrency : 0;
    const recommended = profile.recommended_safe_concurrency ?? measured;
    const node = document.createElement("article");
    node.className = `measured-profile ${escapeHtml(profile.validity || profile.status || "active")}`;
    node.dataset.profileId = profile.profile_id;
    node.innerHTML = `<div><strong>${escapeHtml(profile.model_name)} · ${escapeHtml(profile.quantization || "量化未知")}</strong><span>${escapeHtml(profile.runtime)} · ${escapeHtml(formatDate(profile.created_at))}</span><span>理论容量上限 ${theoretical == null ? "未映射" : `${escapeHtml(theoretical)} 并发`} · 实测可用上限 ${escapeHtml(measured)} 并发 · 推荐安全并发 ${escapeHtml(recommended)}</span><small>${escapeHtml(profile.evidence_label || "本机实测")} · 生成 ${measurement.generation_tps ?? "—"} tok/s · TTFT ${measurement.ttft_seconds ?? "—"}s · ${profile.validity === "valid" ? "当前硬件有效" : profile.validity === "expired" ? "用户标记过期" : "硬件变化，可能失效"}</small></div><div class="profile-actions"><button class="button button-small" type="button" data-profile-action="apply">应用到规划</button><button class="button button-small" type="button" data-profile-action="expire">${profile.status === "expired" ? "恢复有效" : "标记过期"}</button><button class="button button-small" type="button" data-profile-action="delete">删除</button></div>`;
    container.appendChild(node);
  }
}

async function handleMeasuredProfileAction(event) {
  const button = event.target.closest("button[data-profile-action]");
  if (!button) return;
  const profileId = button.closest("[data-profile-id]")?.dataset.profileId;
  const profile = state.plannerStatus?.measured_profiles?.items?.find((item) => item.profile_id === profileId);
  if (!profile) return;
  const action = button.dataset.profileAction;
  if (action === "apply") {
    $("#planner-concurrency").value = String(profile.recommended_safe_concurrency || profile.concurrency || 1);
    $("#planner-context").value = String(Math.max(512, Number(profile.context_tokens || 1024)));
    $("#planner-prompt").value = String(Math.max(16, Number(profile.context_tokens || 512)));
    $("#planner-output").value = String(Math.max(1, Number(profile.output_tokens || 32)));
    showToast("已应用该档案的实测工作负载；候选模型仍按容量条件重新计算");
    await runPlannerEstimate();
    return;
  }
  if (action === "expire") {
    const status = profile.status === "expired" ? "active" : "expired";
    await api("/api/calibration-profiles/status", { method: "POST", body: JSON.stringify({ profile_id: profileId, status }) });
    await loadPlannerStatus();
    showToast(status === "expired" ? "档案已标记过期，实测数据仍保留" : "档案已恢复；硬件不匹配时仍会自动标记可能失效");
    return;
  }
  if (action === "delete") {
    const phrase = `DELETE PROFILE ${profileId}`;
    state.confirmationAction = { kind: "delete-profile", profileId, phrase };
    openConfirmationDialog("删除本机实测档案", "只删除该条本机 SQLite 档案，不影响模型、服务或基准原始运行。", phrase);
  }
}

function fillBenchmarkModels() {
  const select = $("#benchmark-model");
  const selected = select.value;
  select.replaceChildren();
  for (const model of state.plannerStatus?.models || []) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = `${model.name} · ${architectureLabel(model.architecture)} · ${formatParams(model)}`;
    select.appendChild(option);
  }
  if ([...select.options].some((item) => item.value === selected)) select.value = selected;
}

function renderBenchmarkHistory() {
  const container = $("#benchmark-history");
  container.replaceChildren();
  const items = state.plannerStatus?.benchmarks || [];
  if (!items.length) {
    const empty = document.createElement("span");
    empty.className = "confidence-note";
    empty.textContent = "当前硬件指纹下暂无校准结果。";
    container.appendChild(empty);
    return;
  }
  for (const item of items.slice(0, 8)) {
    const node = document.createElement("span");
    node.className = "benchmark-item";
    const source = item.calibration_source === "service_matrix" ? "服务矩阵" : "llama-bench";
    const workload = item.calibration_source === "service_matrix"
      ? ` · 并发 ${item.concurrency || 1} / 上下文 ${item.requested_context_tokens || "?"} / 样本 ${item.sample_count || item.successful_requests || "?"}`
      : "";
    node.textContent = `${item.model_id} · ${item.quantization} · ${source} · 生成 ${Number(item.generation_tps).toFixed(1)} tok/s${item.prompt_tps ? ` · 提示 ${Number(item.prompt_tps).toFixed(1)}` : ""}${workload}`;
    container.appendChild(node);
  }
}

async function loadPlannerStatus(refresh = false) {
  const payload = refresh
    ? await api("/api/model-planner/refresh", { method: "POST", body: "{}" })
    : await api("/api/model-planner/status");
  state.plannerStatus = payload;
  renderHardware();
  return payload;
}

function ceilingStatus(ceiling) {
  if (!ceiling?.available) return `<span class="ceiling-value unavailable">${escapeHtml(ceiling?.reason || "没有匹配方案")}</span>`;
  const mode = { accelerator: "全 GPU", unified: "统一内存", hybrid: "混合卸载", cpu: "CPU" }[ceiling.execution_mode] || ceiling.execution_mode;
  const params = ceiling.architecture === "moe" ? `${ceiling.total_params_b}B 总 / ${ceiling.active_params_b}B 激活` : Number(ceiling.active_params_b) < Number(ceiling.total_params_b) ? `${ceiling.total_params_b}B 总 / ${ceiling.active_params_b}B 有效` : `${ceiling.total_params_b}B Dense`;
  const current = ceiling.current_fit ? "当前可装入" : "释放资源后理论可装入";
  return `<strong>${escapeHtml(ceiling.name)}</strong><span>${escapeHtml(params)} · ${escapeHtml(ceiling.quantization)}</span><small>${escapeHtml(mode)} · ${escapeHtml(ceiling.required_gib)} GiB · ${escapeHtml(current)}<br>预计 ${escapeHtml(ceiling.per_user_tps?.expected)} tok/s/用户 · TTFT ${escapeHtml(ceiling.ttft_seconds?.expected)}s · ${escapeHtml(ceiling.confidence)}</small>`;
}

function renderCeilings(ceilings) {
  for (const level of ["physical", "usable", "sla"]) {
    const element = $(`#ceiling-${level}`);
    element.classList.toggle("unavailable", !ceilings?.[level]?.available);
    element.innerHTML = ceilingStatus(ceilings?.[level]);
  }
}

function candidateStatusLabel(status) {
  return {
    meets_sla: "满足目标",
    current_pressure: "当前内存紧张",
    performance_risk: "性能不达标",
    does_not_fit: "无法装入",
    context_unsupported: "超出上下文上限",
  }[status] || status;
}

function renderCandidates(items) {
  const body = $("#candidate-body");
  body.replaceChildren();
  for (const item of items || []) {
    const perf = item.performance || {};
    const tps = perf.per_user_generation_tps || {};
    const ttft = perf.ttft_seconds || {};
    const execution = item.execution || {};
    const memory = item.memory || {};
    const maxConcurrency = item.max_concurrency || {};
    const calibration = perf.calibration || null;
    const calibrationLine = calibration
      ? `<span class="subline calibration-line">${calibration.workload_match ? "同负载实测校准" : "单请求基线校准"} · 样本 ${escapeHtml(calibration.sample_count || 1)} · 预测误差 ${calibration.absolute_error_percent == null ? "不可计算" : `${escapeHtml(calibration.absolute_error_percent)}%`}</span>`
      : `<span class="subline calibration-line uncalibrated">尚无匹配实测校准</span>`;
    const row = document.createElement("tr");
    row.innerHTML = `
      <td><strong class="model-title">${escapeHtml(item.name)}</strong><span class="architecture-badge ${escapeHtml(item.architecture)}">${escapeHtml(architectureLabel(item.architecture))}</span><span class="subline">${escapeHtml(item.publisher)} · ${escapeHtml(item.license)}</span></td>
      <td><strong>${escapeHtml(formatParams(item))}</strong><span class="subline">${escapeHtml(item.quantization)} · ${escapeHtml(item.bits_per_weight)} bpw</span></td>
      <td><div class="memory-lines">权重 ${escapeHtml(memory.weights_gib)} GiB<br>KV ${escapeHtml(memory.kv_cache_gib)} GiB<br>工作区 ${escapeHtml(memory.workspace_gib)} GiB<br><strong>合计 ${escapeHtml(memory.required_gib)} GiB</strong></div></td>
      <td><strong>${escapeHtml(tps.expected)} tok/s/用户</strong><span class="subline">范围 ${escapeHtml(tps.low)}–${escapeHtml(tps.high)} · TTFT ${escapeHtml(ttft.expected)}s</span><span class="subline">目标下最大并发 ${escapeHtml(maxConcurrency.effective)} · ${escapeHtml(perf.confidence)}</span>${calibrationLine}</td>
      <td><strong>${escapeHtml(execution.label)}</strong><span class="subline">${execution.current_fit ? "当前可装入" : execution.clean_fit ? "需释放资源" : "预算不足"} · 余量 ${escapeHtml(execution.current_headroom_gib)} GiB</span></td>
      <td><span class="candidate-status ${escapeHtml(item.status)}">${escapeHtml(candidateStatusLabel(item.status))}</span><span class="candidate-risk" title="${escapeHtml((item.risks || []).join("；"))}">${escapeHtml((item.risks || ["无额外风险提示"])[0])}</span></td>`;
    body.appendChild(row);
  }
}

function renderEstimate(estimate) {
  state.estimate = estimate;
  $("#planner-results").hidden = false;
  renderCeilings(estimate.ceilings || {});
  const list = $("#planner-bottlenecks");
  list.replaceChildren();
  for (const text of estimate.bottlenecks || ["未识别额外瓶颈"]) {
    const item = document.createElement("li");
    item.textContent = text;
    list.appendChild(item);
  }
  const plan = estimate.runtime_plan || {};
  $("#runtime-plan-title").textContent = plan.available ? `推荐 ${plan.runtime}` : "当前没有可执行方案";
  $("#runtime-plan-meta").textContent = plan.available ? `${plan.installed ? "已检测到运行时" : "尚未检测到运行时"} · ${plan.binding || "仅本机"} · 命令只生成不执行` : plan.reason;
  $("#runtime-command").textContent = plan.display || plan.reason || "—";
  $("#copy-command-button").disabled = !plan.display;
  const calibration = estimate.calibration_summary || {};
  const selectedCalibration = calibration.selected || null;
  const calibrationNode = $("#planner-calibration-summary");
  if (selectedCalibration) {
    const source = selectedCalibration.source === "service_matrix" ? "服务工作负载矩阵" : "llama-bench";
    calibrationNode.className = "calibration-summary calibrated";
    calibrationNode.innerHTML = `<strong>${selectedCalibration.workload_match ? "已按同负载实测校准" : "已按单请求基线校准"}</strong><span>${escapeHtml(source)} · 样本 ${escapeHtml(selectedCalibration.sample_count || 1)}</span><span>理论预测 ${escapeHtml(selectedCalibration.predicted_generation_tps)} tok/s → 实测/校准 ${escapeHtml(selectedCalibration.measured_generation_tps)} tok/s</span><span class="prediction-error">误差 ${selectedCalibration.signed_error_percent > 0 ? "+" : ""}${escapeHtml(selectedCalibration.signed_error_percent)}% · 绝对误差 ${escapeHtml(selectedCalibration.absolute_error_percent)}%</span>`;
  } else {
    calibrationNode.className = "calibration-summary";
    calibrationNode.innerHTML = `<strong>尚无当前候选的匹配实测</strong><span>可在模型服务页运行固定负载矩阵；系统会按同硬件、模型、量化、并发、上下文和输出长度反向校准。</span><span>当前可用样本 ${escapeHtml(calibration.available_samples || 0)} · 已校准候选 ${escapeHtml(calibration.calibrated_candidates || 0)}</span>`;
  }
  renderCandidates(estimate.candidates || []);
  const assumptions = $("#planner-assumptions");
  assumptions.replaceChildren();
  for (const text of estimate.assumptions || []) {
    const item = document.createElement("li");
    item.textContent = text;
    assumptions.appendChild(item);
  }
}

async function runPlannerEstimate() {
  setPlannerBusy(true);
  try {
    const payload = await api("/api/model-planner/estimate", { method: "POST", body: JSON.stringify(plannerBody()) });
    renderEstimate(payload.estimate);
  } finally {
    setPlannerBusy(false);
  }
}

async function submitPlanner(event) {
  event?.preventDefault();
  try {
    await runPlannerEstimate();
    showToast("容量方案已按当前硬件和目标重新计算");
  } catch (error) { showToast(error.message, true); }
}

function advisorBody() {
  return {
    model_format: $("#advisor-format").value,
    priority: $("#advisor-priority").value,
    concurrency: Number($("#advisor-concurrency").value),
    context_tokens: Number($("#advisor-context").value),
    features: $$('input[name="advisor-feature"]:checked').map((item) => item.value),
    allow_wsl: $("#advisor-wsl").checked,
    allow_docker: $("#advisor-docker").checked,
  };
}

function localized(item, key) {
  const english = window.VSG_I18N?.locale === "en";
  return english ? (item?.[`${key}_en`] || item?.[key] || "") : (item?.[key] || "");
}

function renderEngineAdvice() {
  const engine = state.advisor?.engine || {};
  const container = $("#engine-top3");
  container.replaceChildren();
  const top3 = engine.top3 || [];
  if (!top3.length) {
    container.innerHTML = `<div class="empty-state"><strong>${window.VSG_I18N?.locale === "en" ? "No compatible engine" : "没有兼容引擎"}</strong><span>${window.VSG_I18N?.locale === "en" ? "Review format, platform, accelerator, and WSL constraints." : "请复核格式、平台、加速器与 WSL 条件。"}</span></div>`;
  }
  top3.forEach((item, index) => {
    const node = document.createElement("article");
    node.className = "engine-card";
    const reasons = localized(item, "reasons") || [];
    const cautions = localized(item, "cautions") || [];
    const source = String(item.source_url || "");
    const sourceLink = source.startsWith("https://") ? `<a href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${window.VSG_I18N?.locale === "en" ? "Official / upstream basis" : "官方 / 上游依据"}</a>` : "";
    node.innerHTML = `
      <div class="engine-rank">#${index + 1}</div>
      <div><strong>${escapeHtml(item.name)}</strong><span class="candidate-status ${escapeHtml(item.state)}">${escapeHtml(item.state)} · ${escapeHtml(item.score)}/100</span></div>
      <p>${escapeHtml(reasons.slice(0, 3).join("；") || (window.VSG_I18N?.locale === "en" ? "Compatibility path matched" : "兼容路径匹配"))}</p>
      ${cautions.length ? `<small>${escapeHtml(cautions[0])}</small>` : ""}
      <div class="engine-meta"><span>${item.installed ? (window.VSG_I18N?.locale === "en" ? "Detected" : "本机已检测") : (window.VSG_I18N?.locale === "en" ? "Not detected" : "本机未检测")}</span>${sourceLink}</div>`;
    container.appendChild(node);
  });
  $("#engine-conclusion").textContent = localized(engine, "conclusion");
  renderEngineMatrix(engine.candidates || []);
}

function renderEngineMatrix(candidates) {
  const body = $("#engine-candidate-body");
  body.replaceChildren();
  const english = window.VSG_I18N?.locale === "en";
  for (const item of candidates) {
    const checks = (item.compatibility_checks || []).map((check) => `${check.id}: ${check.state}`).join(" · ");
    const detected = item.detected || {};
    const blockers = localized(item, "blockers") || [];
    const steps = localized(item, "unblock_steps") || [];
    const row = document.createElement("tr");
    row.innerHTML = `<td><strong>${escapeHtml(item.name)}</strong><span class="subline">${escapeHtml(item.support_tier)}</span></td><td><span class="candidate-status ${escapeHtml(item.state)}">${escapeHtml(item.state)} · ${escapeHtml(item.score)}/100</span></td><td>${escapeHtml(checks || (english ? "No checks" : "无检查项"))}</td><td><strong>${item.installed ? (english ? "Detected" : "已检测") : (english ? "Not detected" : "未检测")}</strong><span class="subline">${escapeHtml(detected.runtime_version || detected.runtime_detection || "unknown")}</span><span class="subline">Driver ${escapeHtml((detected.driver_versions || []).join(", ") || "unknown")} · CC ${escapeHtml((detected.compute_capabilities || []).join(", ") || "unknown")}</span></td><td>${blockers.length ? `<strong class="danger-text">${escapeHtml(blockers.join("；"))}</strong>` : `<strong>${english ? "No hard blocker" : "无硬阻断"}</strong>`}<span class="subline">${escapeHtml(steps[0] || (english ? "Run an identical-load local benchmark before final selection." : "最终选择前运行同负载本机基准。"))}</span></td>`;
    body.appendChild(row);
  }
}

function renderOptimizationAdvice() {
  const advice = state.advisor?.advice || {};
  const summary = advice.summary || {};
  $("#advisor-summary").textContent = window.VSG_I18N?.locale === "en"
    ? `${summary.actionable || 0} actionable · ${summary.critical || 0} critical`
    : `${summary.actionable || 0} 条可执行 · ${summary.critical || 0} 条严重`;
  const container = $("#advisor-recommendations");
  container.replaceChildren();
  for (const item of advice.recommendations || []) {
    const node = document.createElement("article");
    node.className = `recommendation-card severity-${escapeHtml(item.severity)}`;
    node.innerHTML = `
      <header><span class="source-badge">${escapeHtml(item.domain)} · ${escapeHtml(item.severity)}</span><strong>${escapeHtml(localized(item, "title"))}</strong></header>
      <dl><div><dt>${window.VSG_I18N?.locale === "en" ? "Evidence" : "证据"}</dt><dd>${escapeHtml(localized(item, "evidence"))}</dd></div><div><dt>${window.VSG_I18N?.locale === "en" ? "Action" : "建议动作"}</dt><dd>${escapeHtml(localized(item, "action"))}</dd></div><div><dt>${window.VSG_I18N?.locale === "en" ? "Trade-off" : "代价"}</dt><dd>${escapeHtml(localized(item, "tradeoff"))}</dd></div><div><dt>${window.VSG_I18N?.locale === "en" ? "Validation" : "回验"}</dt><dd>${escapeHtml(localized(item, "validation"))}</dd></div></dl>
      <small>${window.VSG_I18N?.locale === "en" ? "Confidence" : "置信度"}: ${escapeHtml(item.confidence)} · ${window.VSG_I18N?.locale === "en" ? "No automatic change" : "不自动变更"}</small>`;
    container.appendChild(node);
  }
  const unknowns = $("#advisor-unknowns");
  unknowns.replaceChildren();
  const values = advice.unknowns || [];
  for (const text of values.length ? values : [window.VSG_I18N?.locale === "en" ? "No additional unknowns were recorded." : "未记录额外未知项。"]) {
    const item = document.createElement("li");
    item.textContent = text;
    unknowns.appendChild(item);
  }
  const method = document.createElement("li");
  method.textContent = localized(advice, "method");
  unknowns.appendChild(method);
}

function renderBenchmarkComparison() {
  const payload = state.advisor?.benchmarks || {};
  const container = $("#benchmark-comparison");
  container.replaceChildren();
  const cohorts = (payload.cohorts || []).filter((item) => item.comparable);
  if (!cohorts.length) {
    container.innerHTML = `<span class="confidence-note">${window.VSG_I18N?.locale === "en" ? "No comparable cohort yet. Run the same workload against at least two runtimes." : "暂无可比样本；请对至少两个运行时执行完全相同的负载。"}</span>`;
    return;
  }
  for (const cohort of cohorts) {
    const card = document.createElement("article");
    card.className = "comparison-card";
    const rows = (cohort.rows || []).map((row, index) => `<tr><td>#${index + 1} ${escapeHtml(row.runtime)}</td><td>${row.generation_tps == null ? "—" : `${Number(row.generation_tps).toFixed(2)} tok/s`}</td><td>${row.ttft_seconds == null ? "—" : `${Number(row.ttft_seconds).toFixed(3)} s`}</td><td>${escapeHtml(row.successful_requests)} / ${escapeHtml(Number(row.successful_requests || 0) + Number(row.failed_requests || 0))}</td></tr>`).join("");
    card.innerHTML = `<header><strong>${escapeHtml(cohort.model_name)}</strong><span>${escapeHtml(cohort.concurrency)} concurrency · ${escapeHtml(cohort.context_tokens)} ctx · ${escapeHtml(cohort.output_tokens)} out</span></header><table><thead><tr><th>Runtime</th><th>Generation</th><th>TTFT</th><th>Success</th></tr></thead><tbody>${rows}</tbody></table>`;
    container.appendChild(card);
  }
}

function fillLogWatchServices() {
  const select = $("#log-watch-service");
  const selected = select.value;
  select.replaceChildren();
  const services = (state.snapshot?.services || []).filter((item) => item.metadata?.model_runtime && Number(item.process?.pid) > 0 && item.source === "host");
  for (const service of services) {
    const option = document.createElement("option");
    option.value = service.id;
    option.textContent = `${service.display_name} · ${service.runtime} · PID ${service.process.pid}`;
    select.appendChild(option);
  }
  if ([...select.options].some((item) => item.value === selected)) select.value = selected;
  select.disabled = !services.length;
  $("#log-watch-submit").disabled = !services.length;
  updateLogWatchPrompt();
}

function updateLogWatchPrompt() {
  const service = state.snapshot?.services?.find((item) => item.id === $("#log-watch-service").value);
  const phrase = service ? `WATCH ${service.process.pid}` : "WATCH <PID>";
  $("#log-watch-prompt").textContent = `${window.VSG_I18N?.locale === "en" ? "Enter confirmation phrase" : "输入确认短语"} ${phrase}`;
}

function renderLogMonitor() {
  const payload = state.advisor?.log_monitor || state.snapshot?.log_monitor || {};
  $("#log-monitor-count").textContent = window.VSG_I18N?.locale === "en"
    ? `${payload.active_count || 0} active watches`
    : `${payload.active_count || 0} 个活动监控`;
  fillLogWatchServices();
  const watches = $("#log-watches");
  watches.replaceChildren();
  if (!(payload.watches || []).length) watches.innerHTML = `<span class="confidence-note">${window.VSG_I18N?.locale === "en" ? "No log watch configured." : "尚未配置日志监控。"}</span>`;
  for (const watch of payload.watches || []) {
    const node = document.createElement("div");
    node.className = "log-watch-item";
    node.innerHTML = `<div><strong>${escapeHtml(watch.file_name)}</strong><span>${escapeHtml(watch.runtime)} · PID ${escapeHtml(watch.pid)} · ${escapeHtml(watch.status)}</span>${watch.last_error ? `<small>${escapeHtml(watch.last_error)}</small>` : ""}</div>${watch.enabled ? `<button class="button button-small" type="button" data-stop-watch="${escapeHtml(watch.id)}" data-watch-pid="${escapeHtml(watch.pid)}">${window.VSG_I18N?.locale === "en" ? "Stop watch" : "停止监控"}</button>` : ""}`;
    watches.appendChild(node);
  }
  const events = state.logEvents || payload.events || [];
  const body = $("#log-event-body");
  body.replaceChildren();
  for (const event of events) {
    const row = document.createElement("tr");
    row.innerHTML = `<td><strong>${escapeHtml(formatDate(event.last_seen))}</strong><span class="candidate-status ${escapeHtml(event.severity)}">${escapeHtml(event.severity)}</span></td><td><strong>${escapeHtml(event.runtime)}</strong><span class="subline">${escapeHtml(event.category)} · ${escapeHtml(event.code)}</span></td><td>${escapeHtml(event.message)}</td><td>${escapeHtml(event.occurrences)}</td>`;
    body.appendChild(row);
  }
  $("#log-event-empty").hidden = Boolean(events.length);
}

async function loadFilteredLogEvents() {
  const severity = $("#log-filter-severity").value;
  const code = $("#log-filter-code").value.trim();
  const query = new URLSearchParams({ hours: "168", limit: "500" });
  if (severity) query.set("severity", severity);
  if (code) query.set("code", code);
  const payload = await api(`/api/log-events?${query}`);
  state.logEvents = payload.items || [];
  renderLogMonitor();
}

function renderAdvisor() {
  if (!state.advisor) return;
  renderEngineAdvice();
  renderOptimizationAdvice();
  renderBenchmarkComparison();
  renderLogMonitor();
}

async function loadAdvisor(evaluate = false, quiet = false) {
  const button = $("#advisor-refresh-button");
  if (!quiet) {
    button.disabled = true;
    button.textContent = window.VSG_I18N?.locale === "en" ? "Calculating…" : "正在计算…";
  }
  try {
    const payload = evaluate
      ? await api("/api/advisor/evaluate", { method: "POST", body: JSON.stringify(advisorBody()) })
      : await api("/api/advisor/status");
    state.advisor = payload;
    renderAdvisor();
  } finally {
    if (!quiet) {
      button.disabled = false;
      button.textContent = window.VSG_I18N?.locale === "en" ? "Regenerate" : "重新生成建议";
    }
  }
}

async function submitAdvisor(event) {
  event.preventDefault();
  try {
    await loadAdvisor(true);
    showToast(window.VSG_I18N?.locale === "en" ? "Recommendations recalculated from current evidence" : "已按当前证据重新计算建议");
  } catch (error) { showToast(error.message, true); }
}

async function submitLogWatch(event) {
  event.preventDefault();
  try {
    await api("/api/log-monitor/watch", {
      method: "POST",
      body: JSON.stringify({ service_id: $("#log-watch-service").value, path: $("#log-watch-path").value, confirmation: $("#log-watch-confirmation").value }),
    });
    state.logEvents = null;
    $("#log-watch-confirmation").value = "";
    await loadAdvisor(false, true);
    showToast(window.VSG_I18N?.locale === "en" ? "Redacted log monitoring started" : "脱敏日志监控已启动");
  } catch (error) { showToast(error.message, true); }
}

function stopLogWatch(button) {
  const phrase = `WATCH ${button.dataset.watchPid}`;
  state.confirmationAction = { kind: "unwatch", watchId: button.dataset.stopWatch, phrase };
  openConfirmationDialog(
    window.VSG_I18N?.locale === "en" ? "Stop log watch" : "停止日志监控",
    window.VSG_I18N?.locale === "en" ? "Stops only the local monitoring cursor; it does not change the model service or log file." : "只停止本机监控游标；不会修改模型服务或日志文件。",
    phrase,
  );
}

function operationEventTitle(item) {
  return window.VSG_I18N?.locale === "en" ? (item.title_en || item.title_zh || item.code) : (item.title_zh || item.title_en || item.code);
}

function renderOperations() {
  const data = state.operations;
  const english = window.VSG_I18N?.locale === "en";
  const incidents = data.incidents || {};
  $("#operations-incident-health").textContent = english
    ? ({ healthy: "Healthy", warning: "Needs attention", critical: "Critical", unknown: "Insufficient evidence" }[incidents.health] || incidents.health || "Unknown")
    : healthStateLabel(incidents.health);
  $("#operations-incident-count").textContent = `${incidents.counts?.critical || 0} critical · ${incidents.counts?.warning || 0} warning`;
  const topology = data.topology || {};
  const topologySummary = topology.summary || {};
  $("#operations-network-count").textContent = `${topologySummary.listeners || 0} / ${topologySummary.remote_connections || 0}`;
  $("#operations-exposure-count").textContent = english
    ? `${topologySummary.exposures?.all_interfaces || 0} all-interface listeners · ${topologySummary.public_connections || 0} public connections`
    : `${topologySummary.exposures?.all_interfaces || 0} 个所有网卡监听 · ${topologySummary.public_connections || 0} 个公网连接`;
  const latest = (data.inventory || [])[0] || null;
  $("#operations-model-count").textContent = latest?.summary?.models ?? 0;
  $("#operations-model-size").textContent = latest
    ? `${latest.summary?.total_weight_gib ?? 0} GiB · ${latest.summary?.weight_files ?? 0} ${english ? "weight files" : "个权重文件"}`
    : (english ? "No explicit scan yet" : "尚未显式扫描");
  $("#operations-rule-count").textContent = data.rules?.length || 0;
  renderInventory(latest);
  renderTopology(topology);
  renderAttributionRules(data.rules || []);
  renderTimeline();
}

function renderInventory(scan) {
  const english = window.VSG_I18N?.locale === "en";
  const summary = $("#model-inventory-summary");
  const body = $("#model-inventory-body");
  body.replaceChildren();
  if (!scan) {
    summary.innerHTML = `<span class="confidence-note">${window.VSG_I18N?.locale === "en" ? "No model directory has been scanned." : "尚未扫描模型目录。"}</span>`;
    $("#model-inventory-empty").hidden = false;
    return;
  }
  summary.innerHTML = [
    telemetryRow(english ? "Directory" : "目录", `${scan.root_name} · ${scan.root_hash}`, scan.truncated ? (english ? "Truncated" : "已截断") : (english ? "Complete" : "完整完成"), scan.truncated ? "warning" : ""),
    telemetryRow(english ? "Models / Weights" : "模型 / 权重", `${scan.summary?.models || 0} ${english ? "model candidates" : "个模型候选"} · ${scan.summary?.weight_files || 0} ${english ? "weight files" : "个权重文件"}`, `${scan.summary?.total_weight_gib || 0} GiB`),
    telemetryRow(english ? "Formats" : "格式", Object.entries(scan.summary?.formats || {}).map(([key, value]) => `${key}:${value}`).join(" · ") || (english ? "Unknown" : "未知"), `${scan.summary?.duplicate_groups || 0} ${english ? "possible duplicate groups" : "组疑似重复"}`),
  ].join("");
  for (const model of scan.models || []) {
    const hint = model.capacity_hint || {};
    const fit = hint.single_accelerator_weight_fit === true ? (english ? "Single-GPU weight fit" : "单卡权重可装") : hint.system_memory_weight_fit === true ? (english ? "System-memory weight fit" : "系统内存权重可装") : hint.single_accelerator_weight_fit === false && hint.system_memory_weight_fit === false ? (english ? "Insufficient current memory" : "当前内存不足") : (english ? "Insufficient evidence" : "证据不足");
    const row = document.createElement("tr");
    row.dataset.modelFormat = model.advisor_seed?.model_format || model.format || "auto";
    const fitClass = hint.single_accelerator_weight_fit === true || hint.system_memory_weight_fit === true ? "compatible" : hint.single_accelerator_weight_fit === false && hint.system_memory_weight_fit === false ? "does_not_fit" : "medium";
    row.innerHTML = `<td><strong>${escapeHtml(model.name)}</strong><span class="subline" title="${escapeHtml(model.relative_location)}">${escapeHtml(model.relative_location)}</span></td><td><strong>${escapeHtml(model.format || "unknown")}</strong><span class="subline">${escapeHtml(model.quantization || (english ? "Quantization unknown" : "量化未知"))} · ${escapeHtml(model.files)} files</span></td><td>${escapeHtml(model.architecture || (english ? "Unknown" : "未知"))}<span class="architecture-badge ${model.model_type === "moe" ? "" : "dense"}">${escapeHtml(model.model_type || "unknown")}</span><span class="subline">Experts ${escapeHtml(model.expert_count ?? "?")} / active ${escapeHtml(model.active_experts ?? "?")}</span></td><td><strong>${escapeHtml(model.weight_gib)} GiB</strong><span class="subline">${model.estimated_parameters_billion == null ? (english ? "Parameter count not declared" : "参数量未声明") : `${model.estimated_parameters_billion}B ${english ? "parameters from tensor shapes" : "参数（按张量形状）"}`}</span></td><td><span class="candidate-status ${fitClass}">${escapeHtml(fit)}</span><span class="subline">${english ? "Minimum weights + workspace" : "最低权重+工作区"} ${escapeHtml(hint.minimum_weight_workspace_gib ?? "?")} GiB · ${english ? "KV excluded" : "不含 KV"}</span></td><td><button class="button button-small" type="button" data-inventory-advisor>${english ? "Engine advice" : "进入引擎建议"}</button></td>`;
    body.appendChild(row);
  }
  $("#model-inventory-empty").hidden = Boolean((scan.models || []).length);
}

function renderTopology(topology) {
  const english = window.VSG_I18N?.locale === "en";
  const container = $("#network-topology-list");
  const summary = topology?.summary || {};
  const nodes = topology?.nodes || [];
  const rows = [
    telemetryRow(english ? "Listener scope" : "监听范围", `loopback ${summary.exposures?.loopback || 0} · LAN ${summary.exposures?.lan || 0}`, `${english ? "All interfaces" : "所有网卡"} ${summary.exposures?.all_interfaces || 0}`, Number(summary.exposures?.all_interfaces || 0) ? "warning" : ""),
    telemetryRow(english ? "Current remotes" : "当前远端", `${english ? "Connections" : "连接"} ${summary.remote_connections || 0}`, `${english ? "Public" : "公网"} ${summary.public_connections || 0}`, Number(summary.public_connections || 0) ? "critical" : ""),
  ];
  for (const node of nodes.filter((item) => item.kind === "remote").slice(0, 10)) rows.push(telemetryRow(node.label, english ? "Live memory snapshot only; never stored in history" : "仅当前内存快照，不写入历史", node.scope, node.scope === "public" ? "critical" : ""));
  container.innerHTML = rows.join("");
}

function renderAttributionRules(rules) {
  const container = $("#attribution-rule-list");
  container.replaceChildren();
  if (!rules.length) container.innerHTML = `<span class="confidence-note">${window.VSG_I18N?.locale === "en" ? "No user correction rule." : "暂无用户纠正规则；可在服务列表点击 ✎ 创建。"}</span>`;
  for (const rule of rules) {
    const node = document.createElement("div");
    node.className = "rule-item";
    node.innerHTML = `<div><strong>#${escapeHtml(rule.id)} · ${escapeHtml(rule.name)}</strong><span>${escapeHtml(JSON.stringify(rule.match))}</span><small>${escapeHtml(JSON.stringify(rule.override))}</small></div><button class="button button-small" type="button" data-delete-rule="${escapeHtml(rule.id)}">${window.VSG_I18N?.locale === "en" ? "Delete" : "删除"}</button>`;
    container.appendChild(node);
  }
}

function renderTimeline() {
  const container = $("#timeline-list");
  const category = $("#timeline-category").value;
  const items = (state.operations.incidents?.items || []).filter((item) => !category || item.category === category);
  container.replaceChildren();
  if (!items.length) container.innerHTML = `<span class="confidence-note">${window.VSG_I18N?.locale === "en" ? "No matching event in this period." : "该时间范围内没有匹配事件。"}</span>`;
  for (const item of items.slice(0, 500)) {
    const node = document.createElement("article");
    node.className = `timeline-item severity-${escapeHtml(item.severity || "info")}`;
    node.innerHTML = `<time>${escapeHtml(formatDate(item.last_seen))}</time><span class="candidate-status ${escapeHtml(item.severity || "info")}">${escapeHtml(item.severity || "info")}</span><div><strong>${escapeHtml(operationEventTitle(item))}</strong><span>${escapeHtml(item.category)} · ${escapeHtml(item.code)} · ${escapeHtml(item.project_name || item.agent_provider || item.source || "local")}</span></div><b>×${escapeHtml(item.occurrences || 1)}</b>`;
    container.appendChild(node);
  }
}

async function loadOperations() {
  const hours = $("#timeline-hours").value || "24";
  const [incidents, inventory, rules, topology] = await Promise.all([
    api(`/api/incidents?hours=${encodeURIComponent(hours)}`),
    api("/api/model-inventory?limit=10"),
    api("/api/attribution/rules"),
    api("/api/network-topology"),
  ]);
  state.operations = {
    incidents,
    timeline: incidents.items || [],
    inventory: inventory.items || [],
    rules: rules.items || [],
    topology: topology.topology || {},
  };
  renderOperations();
}

async function submitModelInventory(event) {
  event.preventDefault();
  const button = $("#model-inventory-submit");
  button.disabled = true;
  button.textContent = window.VSG_I18N?.locale === "en" ? "Scanning…" : "正在盘点…";
  try {
    const payload = await api("/api/model-inventory/scan", { method: "POST", body: JSON.stringify({ root: $("#model-inventory-root").value.trim(), confirmation: $("#model-inventory-confirmation").value }) });
    $("#model-inventory-confirmation").value = "";
    showToast(`${window.VSG_I18N?.locale === "en" ? "Inventory completed" : "模型盘点完成"}：${payload.scan.summary?.models || 0}`);
    await loadOperations();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = window.VSG_I18N?.locale === "en" ? "Start local inventory" : "开始本地盘点"; }
}

function deleteAttributionRule(button) {
  const ruleId = Number(button.dataset.deleteRule);
  const phrase = `DELETE RULE ${ruleId}`;
  state.confirmationAction = { kind: "delete-rule", ruleId, phrase };
  openConfirmationDialog(
    window.VSG_I18N?.locale === "en" ? "Delete attribution rule" : "删除归属规则",
    window.VSG_I18N?.locale === "en" ? `Deletes local attribution rule #${ruleId}; no process or project file is changed.` : `删除本机归属规则 #${ruleId}；不会修改进程或项目文件。`,
    phrase,
  );
}

function openConfirmationDialog(title, summary, phrase) {
  $("#confirmation-title").textContent = title;
  $("#confirmation-summary").textContent = summary;
  $("#confirmation-prompt").textContent = `${window.VSG_I18N?.locale === "en" ? "Enter confirmation phrase" : "请输入确认短语"} ${phrase}`;
  $("#confirmation-input").value = "";
  $("#confirmation-input").placeholder = phrase;
  $("#confirmation-dialog").showModal();
  $("#confirmation-input").focus();
}

async function submitConfirmation(event) {
  event.preventDefault();
  const action = state.confirmationAction;
  if (!action) return;
  const confirmation = $("#confirmation-input").value;
  try {
    if (action.kind === "delete-rule") {
      await api("/api/attribution/rules/delete", { method: "POST", body: JSON.stringify({ rule_id: action.ruleId, confirmation }) });
      await loadOperations();
      showToast(window.VSG_I18N?.locale === "en" ? "Attribution rule deleted" : "归属规则已删除");
    } else if (action.kind === "delete-profile") {
      await api("/api/calibration-profiles/delete", { method: "POST", body: JSON.stringify({ profile_id: action.profileId, confirmation }) });
      await loadPlannerStatus();
      showToast("本机实测档案已删除；模型和服务未受影响");
    } else if (action.kind === "unwatch") {
      await api("/api/log-monitor/unwatch", { method: "POST", body: JSON.stringify({ watch_id: action.watchId, confirmation }) });
      state.logEvents = null;
      await loadAdvisor(false, true);
      showToast(window.VSG_I18N?.locale === "en" ? "Log watch stopped" : "日志监控已停止");
    }
    $("#confirmation-dialog").close();
    state.confirmationAction = null;
  } catch (error) { showToast(error.message, true); }
}

async function submitHistoryClear(event) {
  event.preventDefault();
  const categories = $$('input[name="history-category"]:checked').map((item) => item.value);
  try {
    const payload = await api("/api/history/clear", { method: "POST", body: JSON.stringify({ categories, confirmation: $("#history-clear-confirmation").value }) });
    $("#history-clear-confirmation").value = "";
    showToast(`${window.VSG_I18N?.locale === "en" ? "Local history cleared" : "已清除所选本机历史"}：${Object.values(payload.removed || {}).reduce((a, b) => a + Number(b || 0), 0)}`);
    await loadOperations();
  } catch (error) { showToast(error.message, true); }
}

function openAttributionDialog(service) {
  state.attributionTarget = service;
  $("#attribution-summary").textContent = `${service.display_name} · PID ${service.process?.pid} · ${service.fingerprint}`;
  $("#attribution-service-name").value = service.display_name || "";
  $("#attribution-agent").value = service.agent?.provider || "";
  $("#attribution-project-name").value = service.project?.name || "";
  $("#attribution-project-path").value = service.project?.path || "";
  const historicalLabel = service.metadata?.historical_lifecycle_label || "";
  $("#attribution-expected").checked = Boolean(
    service.expected && historicalLabel !== "expected"
  );
  $("#attribution-protected").checked = Boolean(service.protected);
  $("#attribution-lifecycle-label").value = historicalLabel;
  $("#attribution-inherit").checked = Boolean(
    historicalLabel && service.metadata?.historical_label_inherited
  );
  $("#attribution-inherit").disabled = !(
    historicalLabel && service.metadata?.ownership_signature
  );
  $("#attribution-clear-label").hidden = !historicalLabel;
  $("#attribution-dialog").showModal();
}

async function submitAttribution(event) {
  event.preventDefault();
  const service = state.attributionTarget;
  if (!service) return;
  const override = {
    service_name: $("#attribution-service-name").value.trim(),
    agent_provider: $("#attribution-agent").value.trim(),
    project_name: $("#attribution-project-name").value.trim(),
    project_path: $("#attribution-project-path").value.trim(),
    expected: $("#attribution-expected").checked,
    protected: $("#attribution-protected").checked,
  };
  const lifecycleLabel = $("#attribution-lifecycle-label").value;
  if (lifecycleLabel) override.lifecycle_label = lifecycleLabel;
  for (const key of Object.keys(override)) if (override[key] === "") delete override[key];
  try {
    await postService("/api/service/attribute", service, {
      override,
      inherit_similar: $("#attribution-inherit").checked,
      name: lifecycleLabel ? `Historical lifecycle label · ${service.display_name}` : undefined,
    });
    $("#attribution-dialog").close();
    showToast(window.VSG_I18N?.locale === "en" ? "Local attribution correction saved" : "本机归属纠正规则已保存");
    setTimeout(loadStatus, 350);
  } catch (error) { showToast(error.message, true); }
}

async function clearLifecycleLabel() {
  const service = state.attributionTarget;
  if (!service) return;
  try {
    await postService("/api/service/lifecycle-label/clear", service);
    $("#attribution-dialog").close();
    showToast("当前历史生命周期标签已撤销");
    setTimeout(loadStatus, 350);
  } catch (error) { showToast(error.message, true); }
}

async function activateView(view) {
  state.activeView = view;
  $("#services-view").hidden = view !== "services";
  $("#model-planner-view").hidden = view !== "model-planner";
  $("#health-view").hidden = view !== "health";
  $("#advisor-view").hidden = view !== "advisor";
  $("#operations-view").hidden = view !== "operations";
  $("#service-search-box").hidden = view !== "services";
  $("#service-action-row").hidden = false;
  $$(".workspace-tab").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (view === "model-planner") {
    try {
      if (!state.plannerStatus) await loadPlannerStatus();
      if (!state.estimate) await runPlannerEstimate();
    } catch (error) { showToast(`模型容量模块初始化失败：${error.message}`, true); }
  }
  if (view === "health") {
    renderHealth();
    await loadSnapshots();
  }
  if (view === "advisor") {
    try { await loadAdvisor(false); await loadFilteredLogEvents(); } catch (error) { showToast(`${window.VSG_I18N?.locale === "en" ? "Advisor initialization failed" : "优化建议初始化失败"}：${error.message}`, true); }
  }
  if (view === "operations") {
    try { await loadOperations(); } catch (error) { showToast(`${window.VSG_I18N?.locale === "en" ? "Events and assets initialization failed" : "事件与资产初始化失败"}：${error.message}`, true); }
  }
}

async function refreshHardware() {
  const button = $("#hardware-refresh-button");
  button.disabled = true;
  button.textContent = "正在读取…";
  try {
    await loadPlannerStatus(true);
    await runPlannerEstimate();
    showToast("硬件、运行时和容量方案已刷新");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "重新读取硬件"; }
}

function openBenchmarkDialog() {
  const selected = state.estimate?.selected_model_id;
  if (selected && [...$("#benchmark-model").options].some((item) => item.value === selected)) $("#benchmark-model").value = selected;
  const selectedCandidate = state.estimate?.candidates?.find((item) => item.model_id === selected);
  if (selectedCandidate && [...$("#benchmark-quant").options].some((item) => item.value === selectedCandidate.quantization)) $("#benchmark-quant").value = selectedCandidate.quantization;
  $("#benchmark-path").value = "";
  $("#benchmark-confirmation").value = "";
  $("#benchmark-dialog").showModal();
}

async function submitBenchmark(event) {
  event.preventDefault();
  const button = $("#benchmark-submit");
  button.disabled = true;
  button.textContent = "基准运行中…";
  try {
    const body = {
      model_id: $("#benchmark-model").value,
      quantization: $("#benchmark-quant").value,
      model_path: $("#benchmark-path").value.trim(),
      confirmation: $("#benchmark-confirmation").value.trim(),
    };
    const payload = await api("/api/model-planner/benchmark", { method: "POST", body: JSON.stringify(body) });
    $("#benchmark-dialog").close();
    await loadPlannerStatus();
    await runPlannerEstimate();
    showToast(`校准完成：生成 ${Number(payload.benchmark.generation_tps).toFixed(1)} tokens/s`);
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "确认运行短基准"; }
}

function healthTarget(serviceId) {
  const service = state.snapshot?.services?.find((item) => item.id === serviceId);
  const probe = state.snapshot?.runtime_probes?.find((item) => item.service_id === serviceId);
  return service && probe ? { service, probe } : null;
}

function openServiceBenchmark(serviceId) {
  const target = healthTarget(serviceId);
  if (!target) return showToast("服务状态已变化，请刷新后重试", true);
  state.healthBenchmarkTarget = target;
  const { service, probe } = target;
  $("#service-benchmark-title").textContent = `${service.display_name} · :${probe.port} 短基准`;
  $("#service-benchmark-model").value = probe.models?.[0]?.name || "";
  $("#service-benchmark-concurrency").value = "1";
  $("#service-benchmark-context").value = String(Math.min(512, Number(probe.capacity?.context_tokens || 512)));
  $("#service-benchmark-output").value = "32";
  $("#service-benchmark-prompt").textContent = `输入确认短语 BENCHMARK ${probe.port}`;
  $("#service-benchmark-confirmation").value = "";
  $("#service-benchmark-confirmation").placeholder = `BENCHMARK ${probe.port}`;
  $("#service-benchmark-result").hidden = true;
  $("#service-benchmark-result").replaceChildren();
  $("#service-benchmark-dialog").showModal();
}

async function submitServiceBenchmark(event) {
  event.preventDefault();
  const target = state.healthBenchmarkTarget;
  if (!target) return;
  const button = $("#service-benchmark-submit");
  button.disabled = true;
  button.textContent = "基准运行中…";
  try {
    const payload = await api("/api/service/benchmark", {
      method: "POST",
      body: JSON.stringify({
        service_id: target.service.id,
        model: $("#service-benchmark-model").value.trim(),
        concurrency: Number($("#service-benchmark-concurrency").value),
        context_tokens: Number($("#service-benchmark-context").value),
        output_tokens: Number($("#service-benchmark-output").value),
        confirmation: $("#service-benchmark-confirmation").value.trim(),
      }),
    });
    const item = payload.benchmark;
    const result = $("#service-benchmark-result");
    result.hidden = false;
    result.innerHTML = `<h3>实测完成</h3><p>成功 ${escapeHtml(item.successful_requests)} / ${escapeHtml(item.concurrency)} · 平均 TTFT ${item.ttft_seconds == null ? "未获得" : `${escapeHtml(item.ttft_seconds)}s`} · 生成 ${item.generation_tps == null ? "未获得" : `${escapeHtml(item.generation_tps)} tok/s`} · 聚合 ${item.aggregate_generation_tps == null ? "未获得" : `${escapeHtml(item.aggregate_generation_tps)} tok/s`}<br>服务报告实际提示 tokens：${escapeHtml(item.verified_prompt_tokens_min ?? "未报告")}–${escapeHtml(item.verified_prompt_tokens_max ?? "未报告")} · OOM 证据：${item.oom_observed ? "有" : "无"}</p>`;
    showToast("模型服务短基准已完成并写入本地数值历史");
    await loadStatus();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "确认运行"; }
}

function matrixModelLabel(model) {
  const params = model.architecture === "moe"
    ? `${model.total_params_b}B / ${model.active_params_b}B 激活`
    : `${model.total_params_b}B`;
  return `${model.name} · ${params} · ${architectureLabel(model.architecture)}`;
}

function fillWorkloadMatrixModels(target) {
  const select = $("#workload-matrix-model");
  const current = select.value;
  select.innerHTML = '<option value="">不映射，仅记录运行时性能</option>';
  for (const model of state.plannerStatus?.models || []) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = matrixModelLabel(model);
    select.appendChild(option);
  }
  if (current && [...select.options].some((item) => item.value === current)) {
    select.value = current;
    return;
  }
  const reported = String(target?.probe?.models?.[0]?.name || "").toLocaleLowerCase();
  if (!reported) return;
  const compact = (value) => String(value || "").toLocaleLowerCase().replace(/[^a-z0-9]+/g, "");
  const reportedCompact = compact(reported);
  const match = (state.plannerStatus?.models || []).find((model) => {
    const id = compact(model.id);
    const name = compact(model.name);
    return (name.length >= 5 && reportedCompact.includes(name))
      || (reportedCompact.length >= 5 && name.includes(reportedCompact))
      || (id.length >= 5 && reportedCompact.includes(id));
  });
  if (match) select.value = match.id;
}

function matrixPredictionText(prediction) {
  if (!prediction) return "未映射容量目录，不计算预测误差";
  const tps = prediction.per_user_generation_tps || {};
  const ttft = prediction.ttft_seconds || {};
  return `预测 ${tps.expected ?? "?"} tok/s/用户 · 聚合 ${prediction.aggregate_generation_tps ?? "?"} tok/s · TTFT ${ttft.expected ?? "?"}s`;
}

function renderWorkloadMatrixPlan(plan) {
  state.workloadMatrixPlan = plan;
  const guard = plan.guard || {};
  const guardText = guard.allowed
    ? `护栏通过：${(guard.evidence || []).join(" · ") || "当前没有可用资源读数"}`
    : `护栏阻断：${(guard.blockers || []).join("；")}`;
  const steps = (plan.steps || []).map((step, index) => `
    <article class="matrix-step">
      <span class="matrix-step-index">${index + 1}</span>
      <div><strong>${escapeHtml(step.label)}</strong><span>并发 ${escapeHtml(step.concurrency)} · 上下文 ${escapeHtml(step.context_tokens)} · 输出 ${escapeHtml(step.output_tokens)}${step.duration_seconds ? ` · 固定 ${escapeHtml(step.duration_seconds)} 秒` : ` · 请求 ${escapeHtml(step.request_count)} / ${escapeHtml(step.waves)} 波`}</span><small>${escapeHtml(matrixPredictionText(step.prediction))}</small></div>
    </article>`).join("");
  $("#workload-matrix-plan").innerHTML = `
    <div class="matrix-plan-head"><div><strong>${escapeHtml(plan.model_name)}</strong><span>${escapeHtml(plan.runtime)} · :${escapeHtml(plan.port)} · PID ${escapeHtml(plan.pid)}</span></div><span class="candidate-status ${guard.allowed ? "compatible" : "does_not_fit"}">${guard.allowed ? "可确认" : "已阻断"}</span></div>
    <div class="matrix-guard ${guard.allowed ? "allowed" : "blocked"}">${escapeHtml(guardText)}</div>
    <div class="matrix-steps">${steps}</div>
    <p class="help-text">${plan.mode === "calibration" ? "60 秒校准窗口，最多 120 个请求；本机很快时可能提前用完请求预算，窗口到期后不再发起新请求" : `固定 ${escapeHtml(plan.steps?.length || 0)} 步、共 ${escapeHtml(plan.total_requests)} 个合成请求`}；不会自动扩档，不会故意试探 OOM。预览 5 分钟内有效。</p>`;
  $("#workload-matrix-confirmation-field").hidden = !guard.allowed;
  $("#workload-matrix-prompt").textContent = `输入确认短语 ${plan.confirmation}`;
  $("#workload-matrix-confirmation").placeholder = plan.confirmation;
  $("#workload-matrix-confirmation").value = "";
  $("#workload-matrix-start").disabled = !guard.allowed;
  $("#workload-matrix-start").hidden = false;
  $("#workload-matrix-cancel").hidden = true;
  $("#workload-matrix-status").hidden = true;
}

async function previewWorkloadMatrix() {
  const target = state.workloadMatrixTarget;
  if (!target) return;
  const button = $("#workload-matrix-preview");
  button.disabled = true;
  button.textContent = "正在生成可预览计划…";
  try {
    const modelId = $("#workload-matrix-model").value;
    const mode = $("#workload-matrix-mode").value;
    const payload = await api("/api/benchmark-matrix/preview", {
      method: "POST",
      body: JSON.stringify({
        service_id: target.service.id,
        catalog_model_id: modelId || null,
        quantization: modelId ? $("#workload-matrix-quant").value : null,
        mode,
        model_name: $("#workload-matrix-loaded-model").value,
        concurrency: mode === "calibration" ? Number($("#workload-calibration-concurrency").value) : null,
        duration_seconds: mode === "calibration" ? 60 : null,
      }),
    });
    renderWorkloadMatrixPlan(payload.plan);
  } catch (error) {
    state.workloadMatrixPlan = null;
    $("#workload-matrix-plan").innerHTML = `<div class="matrix-guard blocked"><strong>无法生成计划</strong><br>${escapeHtml(error.message)}</div>`;
    $("#workload-matrix-confirmation-field").hidden = true;
    $("#workload-matrix-start").disabled = true;
    showToast(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "生成或刷新预览";
  }
}

function predictionErrorText(error) {
  if (!error) return "无匹配预测";
  const sign = Number(error.signed_percent) > 0 ? "+" : "";
  return `${sign}${error.signed_percent}%（绝对 ${error.absolute_percent}%）`;
}

function renderWorkloadMatrixStatus(job) {
  if (!job) return;
  state.workloadMatrixJob = job;
  const active = ["queued", "running", "cancelling"].includes(job.status);
  const labels = {
    queued: "排队中", running: "运行中", cancelling: "正在中止", completed: "全部完成",
    cancelled: "已中止剩余负载", guard_stopped: "资源护栏停止", identity_changed: "服务身份已变化", failed: "运行失败",
  };
  const current = job.current_step || {};
  const elapsed = Math.max(0, Date.now() / 1000 - Number(job.started_at || Date.now() / 1000));
  const durationProgress = current.duration_seconds ? Math.min(Number(current.duration_seconds), elapsed) : null;
  const latestResource = (current.resource_samples || []).at(-1) || {};
  const progress = current.id
    ? `<div class="matrix-current"><strong>当前：${escapeHtml(current.label)} · ${current.duration_seconds ? `${escapeHtml(Math.floor(durationProgress))} / ${escapeHtml(current.duration_seconds)} 秒` : `${escapeHtml(current.completed_requests || 0)} / ${escapeHtml(current.request_count || 0)} 请求`}</strong><progress max="${escapeHtml(current.duration_seconds || current.request_count || 1)}" value="${escapeHtml(current.duration_seconds ? durationProgress : current.completed_requests || 0)}"></progress><small>实时 RAM ${latestResource.memory_used_percent ?? "?"}% · VRAM ${latestResource.gpu_memory_used_percent ?? "?"}% · GPU ${latestResource.gpu_temperature_c ?? "?"}°C</small></div>`
    : "";
  const results = (job.results || []).map((item) => {
    const error = item.prediction_error || {};
    const peaks = item.resource_peaks || {};
    return `<article class="matrix-result"><strong>${escapeHtml(item.matrix_step_id || "step")} · 成功 ${escapeHtml(item.successful_requests)} / ${escapeHtml(item.request_count || item.successful_requests + item.failed_requests)}</strong><span>生成 ${item.generation_tps == null ? "未获得" : `${escapeHtml(item.generation_tps)} tok/s/用户`} · 聚合 ${item.aggregate_generation_tps == null ? "未获得" : `${escapeHtml(item.aggregate_generation_tps)} tok/s`} · TTFT P50 ${item.ttft_seconds == null ? "—" : `${escapeHtml(item.ttft_seconds)}s`} / P95 ${item.ttft_p95_seconds == null ? "样本不足" : `${escapeHtml(item.ttft_p95_seconds)}s`}</span><span>生成预测误差 ${escapeHtml(predictionErrorText(error.per_user_generation_tps))} · TTFT 预测误差 ${escapeHtml(predictionErrorText(error.ttft_seconds))}</span><small>峰值 RAM ${peaks.peak_ram_used_percent ?? "?"}% · VRAM ${peaks.peak_vram_used_percent ?? "?"}% · GPU ${peaks.peak_gpu_temperature_c ?? "?"}°C · 最低磁盘 ${peaks.minimum_disk_free_gib ?? "?"} GiB${item.calibration_profile ? ` · 已生成本机实测档案 ${escapeHtml(item.calibration_profile.profile_id)}` : ""}</small></article>`;
  }).join("");
  const container = $("#workload-matrix-status");
  container.hidden = false;
  container.innerHTML = `<div class="matrix-status-head"><strong>${escapeHtml(labels[job.status] || job.status)}</strong><span>完成步骤 ${escapeHtml(job.completed_steps || 0)} / ${escapeHtml(job.step_count || 0)}</span></div>${progress}${job.error ? `<div class="matrix-guard blocked">${escapeHtml(job.error)}</div>` : ""}<div class="matrix-results">${results || '<span class="confidence-note">尚无完成步骤。</span>'}</div><p class="help-text">协作式中止不会强杀已经发出的请求；资源采样可能遗漏短于刷新间隔的瞬时峰值。</p>`;
  $("#workload-matrix-plan").hidden = active;
  $("#workload-matrix-confirmation-field").hidden = true;
  $("#workload-matrix-start").hidden = true;
  $("#workload-matrix-cancel").hidden = !active;
  $("#workload-matrix-cancel").disabled = job.status === "cancelling";
  $("#workload-matrix-preview").disabled = active;
  $("#workload-matrix-model").disabled = active;
  $("#workload-matrix-quant").disabled = active;
  $("#workload-matrix-mode").disabled = active;
  $("#workload-matrix-loaded-model").disabled = active;
  $("#workload-calibration-concurrency").disabled = active;
}

function scheduleWorkloadMatrixPoll(jobId) {
  clearTimeout(state.workloadMatrixTimer);
  state.workloadMatrixTimer = setTimeout(() => pollWorkloadMatrix(jobId), 900);
}

async function pollWorkloadMatrix(jobId) {
  try {
    const payload = await api(`/api/benchmark-matrix/status?job_id=${encodeURIComponent(jobId)}`);
    const job = payload.job;
    if (!job) return;
    renderWorkloadMatrixStatus(job);
    if (["queued", "running", "cancelling"].includes(job.status)) {
      scheduleWorkloadMatrixPoll(jobId);
    } else {
      clearTimeout(state.workloadMatrixTimer);
      $("#workload-matrix-preview").disabled = false;
      $("#workload-matrix-model").disabled = false;
      $("#workload-matrix-quant").disabled = false;
      $("#workload-matrix-mode").disabled = false;
      $("#workload-matrix-loaded-model").disabled = false;
      $("#workload-calibration-concurrency").disabled = false;
      await loadPlannerStatus();
      if (state.estimate) await runPlannerEstimate();
      showToast(job.status === "completed" ? "工作负载矩阵完成，容量预测已获得校准样本" : `工作负载矩阵结束：${job.status}`, job.status !== "completed" && job.status !== "cancelled");
    }
  } catch (error) {
    clearTimeout(state.workloadMatrixTimer);
    showToast(`负载矩阵状态读取失败：${error.message}`, true);
  }
}

async function submitWorkloadMatrix(event) {
  event.preventDefault();
  const plan = state.workloadMatrixPlan;
  if (!plan) return showToast("请先生成固定计划预览", true);
  const button = $("#workload-matrix-start");
  button.disabled = true;
  button.textContent = "正在启动…";
  try {
    const payload = await api("/api/benchmark-matrix/start", {
      method: "POST",
      body: JSON.stringify({ plan_id: plan.plan_id, confirmation: $("#workload-matrix-confirmation").value.trim() }),
    });
    renderWorkloadMatrixStatus(payload.job);
    scheduleWorkloadMatrixPoll(payload.job.job_id);
    showToast(plan.mode === "calibration" ? "60 秒本机校准已启动；可随时中止" : "固定工作负载矩阵已启动；可随时中止剩余负载");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  } finally {
    button.textContent = "确认运行矩阵";
  }
}

async function cancelWorkloadMatrix() {
  const job = state.workloadMatrixJob;
  if (!job) return;
  const button = $("#workload-matrix-cancel");
  button.disabled = true;
  try {
    const payload = await api("/api/benchmark-matrix/cancel", {
      method: "POST",
      body: JSON.stringify({ job_id: job.job_id }),
    });
    renderWorkloadMatrixStatus(payload.job);
    scheduleWorkloadMatrixPoll(job.job_id);
    showToast("已请求中止；正在等待当前请求波次安全返回");
  } catch (error) {
    button.disabled = false;
    showToast(error.message, true);
  }
}

async function openWorkloadMatrix(serviceId, calibrationConcurrency = 1) {
  const target = healthTarget(serviceId);
  if (!target) return showToast("服务状态已变化，请刷新后重试", true);
  state.workloadMatrixTarget = target;
  state.workloadMatrixPlan = null;
  state.workloadMatrixJob = null;
  $("#workload-matrix-title").textContent = `${target.service.display_name} · :${target.probe.port} 分级负载矩阵`;
  $("#workload-matrix-mode").value = "calibration";
  $("#workload-calibration-concurrency").value = String(calibrationConcurrency === 2 ? 2 : 1);
  $("#workload-calibration-concurrency-field").hidden = false;
  const loadedSelect = $("#workload-matrix-loaded-model");
  loadedSelect.replaceChildren();
  for (const model of target.probe.models || []) {
    const option = document.createElement("option");
    option.value = model.name;
    option.textContent = model.name;
    loadedSelect.appendChild(option);
  }
  $("#workload-matrix-plan").hidden = false;
  $("#workload-matrix-plan").innerHTML = '<span class="confidence-note">正在读取目录与当前任务…</span>';
  $("#workload-matrix-status").hidden = true;
  $("#workload-matrix-confirmation-field").hidden = true;
  $("#workload-matrix-start").hidden = false;
  $("#workload-matrix-start").disabled = true;
  $("#workload-matrix-cancel").hidden = true;
  $("#workload-matrix-dialog").showModal();
  try {
    if (!state.plannerStatus) await loadPlannerStatus();
    fillWorkloadMatrixModels(target);
    const status = await api("/api/benchmark-matrix/status");
    if (status.job && ["queued", "running", "cancelling"].includes(status.job.status)) {
      renderWorkloadMatrixStatus(status.job);
      scheduleWorkloadMatrixPoll(status.job.job_id);
      return;
    }
    await previewWorkloadMatrix();
  } catch (error) {
    $("#workload-matrix-plan").innerHTML = `<div class="matrix-guard blocked">${escapeHtml(error.message)}</div>`;
    showToast(error.message, true);
  }
}

async function openPlannerCalibration(concurrency) {
  const serviceId = $("#planner-calibration-service").value;
  if (!serviceId) return showToast("当前没有可校准的已加载本地模型", true);
  await openWorkloadMatrix(serviceId, concurrency);
}

function openDiagnostic(serviceId) {
  const target = healthTarget(serviceId);
  if (!target) return showToast("服务状态已变化，请刷新后重试", true);
  state.diagnosticTarget = target;
  const pid = target.service.process?.pid;
  $("#diagnostic-title").textContent = `${target.service.display_name} · 日志/配置检查`;
  $("#diagnostic-mode").value = "log";
  $("#diagnostic-path").value = "";
  $("#diagnostic-confirmation").value = "";
  $("#diagnostic-confirmation").placeholder = `INSPECT ${pid}`;
  $("#diagnostic-prompt").textContent = `输入确认短语 INSPECT ${pid}`;
  $("#diagnostic-result").hidden = true;
  $("#diagnostic-result").textContent = "";
  $("#diagnostic-dialog").showModal();
}

async function submitDiagnostic(event) {
  event.preventDefault();
  const target = state.diagnosticTarget;
  if (!target) return;
  const mode = $("#diagnostic-mode").value;
  const button = $("#diagnostic-submit");
  button.disabled = true;
  button.textContent = "正在脱敏检查…";
  try {
    const payload = await api(`/api/diagnostics/${mode}`, {
      method: "POST",
      body: JSON.stringify({
        service_id: target.service.id,
        path: $("#diagnostic-path").value.trim(),
        confirmation: $("#diagnostic-confirmation").value.trim(),
      }),
    });
    const output = $("#diagnostic-result");
    output.hidden = false;
    output.textContent = JSON.stringify(payload.result, null, 2);
    showToast("脱敏检查完成；原始内容未持久化");
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "脱敏检查"; }
}

function renderSnapshots() {
  const container = $("#snapshot-history");
  const items = state.snapshots || [];
  if (!items.length) {
    container.innerHTML = `<div class="telemetry-row unavailable"><strong>暂无快照</strong><span>选择模型或配置文件后创建第一份清单</span><span>—</span></div>`;
    return;
  }
  container.innerHTML = items.map((snapshot) => `<section class="snapshot-item"><header><strong>${escapeHtml(snapshot.snapshot_id)}</strong><span>${escapeHtml(formatDate(snapshot.created_at))}</span></header><div class="snapshot-files">${(snapshot.items || []).map((item) => `<div class="snapshot-file"><span title="${escapeHtml(item.file_name)}">${escapeHtml(item.file_name)} · ${escapeHtml(formatBytes(item.size_bytes))}</span><span>${item.sha256 ? `SHA ${escapeHtml(item.sha256.slice(0, 10))}…` : escapeHtml(item.sha256_status || "无哈希")}</span>${item.rollback_available ? `<button type="button" data-restore snapshot-id="${escapeHtml(snapshot.snapshot_id)}" item-index="${escapeHtml(item.index)}" file-name="${escapeHtml(item.file_name)}">回滚</button>` : "<span>仅清单</span>"}</div>`).join("")}</div></section>`).join("");
}

async function loadSnapshots() {
  try {
    const payload = await api("/api/snapshots");
    state.snapshots = payload.items || [];
    renderSnapshots();
  } catch (error) { $("#snapshot-history").textContent = `快照读取失败：${error.message}`; }
}

async function submitSnapshot(event) {
  event.preventDefault();
  const button = $("#snapshot-submit");
  button.disabled = true;
  button.textContent = "正在创建…";
  try {
    const payload = await api("/api/snapshots/create", {
      method: "POST",
      body: JSON.stringify({
        paths: $("#snapshot-paths").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
        confirmation: $("#snapshot-confirmation").value.trim(),
      }),
    });
    $("#snapshot-confirmation").value = "";
    showToast(`已创建 ${payload.snapshot.snapshot_id}；未自动复制大模型权重`);
    await loadSnapshots();
  } catch (error) { showToast(error.message, true); }
  finally { button.disabled = false; button.textContent = "创建本地清单"; }
}

function openRestore(button) {
  state.restoreTarget = {
    snapshotId: button.getAttribute("snapshot-id"),
    itemIndex: Number(button.getAttribute("item-index")),
    fileName: button.getAttribute("file-name"),
  };
  $("#restore-summary").textContent = `将用快照 ${state.restoreTarget.snapshotId} 覆盖原位置的 ${state.restoreTarget.fileName}。覆盖前会保存当前文件副本，但相关模型服务不会自动重启。`;
  $("#restore-prompt").textContent = `输入确认短语 RESTORE ${state.restoreTarget.fileName}`;
  $("#restore-confirmation").value = "";
  $("#restore-confirmation").placeholder = `RESTORE ${state.restoreTarget.fileName}`;
  $("#restore-dialog").showModal();
}

async function submitRestore(event) {
  event.preventDefault();
  const target = state.restoreTarget;
  if (!target) return;
  try {
    await api("/api/snapshots/restore", {
      method: "POST",
      body: JSON.stringify({ snapshot_id: target.snapshotId, item_index: target.itemIndex, confirmation: $("#restore-confirmation").value.trim() }),
    });
    $("#restore-dialog").close();
    showToast(`${target.fileName} 已回滚；请手工重启并验证相关服务`);
    await loadSnapshots();
  } catch (error) { showToast(error.message, true); }
}

async function copyRuntimeCommand() {
  const command = state.estimate?.runtime_plan?.display;
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    showToast("命令模板已复制；执行前请替换模型占位符并复核参数");
  } catch { showToast("浏览器未允许剪贴板访问，请手动复制命令框内容", true); }
}

function detailCard(title, content, full = false, pre = false) {
  return `<section class="detail-card${full ? " full" : ""}"><h3>${escapeHtml(title)}</h3>${pre ? `<pre>${escapeHtml(content)}</pre>` : `<p>${escapeHtml(content)}</p>`}</section>`;
}

function openDetails(service) {
  const endpoints = (service.endpoints || []).map((item) => `${item.protocol} ${item.address}:${item.port} · ${item.state} · ${item.exposure}`).join("\n") || "无";
  const chain = (service.ancestor_chain || []).map((item, index) => `${index + 1}. PID ${item.pid} · ${item.name} · ${item.exe || "路径不可见"}`).join("\n") || "未获得父进程链";
  const projectEvidence = service.project?.evidence || [];
  const agentEvidence = service.agent?.evidence || [];
  const riskEvidence = service.risk?.reasons || [];
  const probe = service.runtime_probe;
  const runtimeEvidence = probe ? `健康：${probe.health}\n模型加载：${probe.model_load}\n认证：${probe.security?.auth_posture || "unknown"}\n配置：${JSON.stringify(probe.configuration || {}, null, 2)}` : "该服务没有模型运行时探测结果";
  const feedback = service.impact_feedback || {};
  const feedbackLabels = { confirmed_stale: "确属遗留", not_stale: "不是遗留", uncertain: "暂不确定" };
  const feedbackStatus = feedback.outcome
    ? `当前结论：${feedbackLabels[feedback.outcome] || feedback.outcome} · 更新于 ${formatDate(feedback.updated_at)}`
    : "尚未记录人工结论。每个服务指纹只保留一条可更新结果。";
  state.detailTarget = service.id;
  $("#detail-title").textContent = service.display_name;
  $("#detail-content").innerHTML = `<div class="detail-grid">
    ${detailCard("进程", `PID ${service.process?.pid}\n${service.process?.exe || "可执行路径不可见"}\n启动：${formatDate(service.process?.create_time)}`, false, true)}
    ${detailCard("监听端点", endpoints, false, true)}
    ${detailCard("项目归属", `${service.project?.name || "未归类"}\n${service.project?.path || "—"}\n置信度 ${service.project?.confidence || 0}%`, false, true)}
    ${detailCard("Agent / 会话", `${service.agent?.provider || "来源未知"}\n${service.agent?.session_id || "未获得稳定会话 ID"}\n置信度 ${service.agent?.confidence || 0}%`, false, true)}
    ${detailCard("已脱敏命令", service.process?.command || "命令不可见", true, true)}
    ${detailCard("父进程链", chain, true, true)}
    ${detailCard("模型运行时只读证据", runtimeEvidence, true, true)}
    <section class="detail-card full"><h3>判断证据</h3><ul class="evidence-list">${[...projectEvidence, ...agentEvidence, ...riskEvidence].map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul></section>
    <section class="detail-card full impact-feedback-card"><h3>遗留判断结果确认</h3><p>请按当前实际情况确认；“暂不确定”不会被计入明确结论。该记录只留在本机，并用于评估判断规则是否命中。</p><div class="impact-feedback-actions">
      ${Object.entries(feedbackLabels).map(([outcome, label]) => `<button class="button${feedback.outcome === outcome ? " is-selected" : ""}" type="button" data-impact-outcome="${outcome}" aria-pressed="${feedback.outcome === outcome}">${escapeHtml(label)}</button>`).join("")}
    </div><small>${escapeHtml(feedbackStatus)}</small></section>
  </div>`;
  if (!$("#detail-dialog").open) $("#detail-dialog").showModal();
}

async function handleImpactFeedback(event) {
  const button = event.target.closest("button[data-impact-outcome]");
  if (!button || button.disabled || !state.detailTarget) return;
  const service = state.snapshot?.services?.find((item) => item.id === state.detailTarget);
  if (!service) return showToast("服务快照已变化，请刷新后重试", true);
  button.disabled = true;
  try {
    const payload = await postService("/api/impact/feedback", service, { outcome: button.dataset.impactOutcome });
    service.impact_feedback = payload.feedback;
    openDetails(service);
    showToast("本机判断结果已更新；重复确认不会增加样本数");
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function postService(path, service, extra = {}) {
  return api(path, { method: "POST", body: JSON.stringify({ service_id: service.id, ...extra }) });
}

async function handleRowAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button || button.disabled) return;
  const row = button.closest("tr");
  const service = state.snapshot?.services?.find((item) => item.id === row?.dataset.id);
  if (!service) return;
  const action = button.dataset.action;
  try {
    if (action === "details") return openDetails(service);
    if (action === "url") {
      const endpoint = service.endpoints.find((item) => item.protocol === "TCP");
      await postService("/api/open/url", service, { port: endpoint.port });
      return showToast(`已打开本地端口 ${endpoint.port}`);
    }
    if (action === "folder") {
      await postService("/api/open/path", service);
      return showToast("已打开项目目录");
    }
    if (action === "attribute") return openAttributionDialog(service);
    if (action === "mark") {
      await postService("/api/process/mark", service, { expected: !service.expected, protected: service.protected });
      showToast(service.expected ? "已取消预期标记" : "已标记为预期服务");
      return setTimeout(loadStatus, 350);
    }
    if (action === "impact" || action === "stop") return openStopDialog(service);
  } catch (error) { showToast(error.message, true); }
}

function assessmentList(items, empty) {
  const values = items?.length ? items : [empty];
  return `<ul>${values.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderStopAssessment(service, assessment) {
  const decisionLabels = { allowed: "可确认停止", review: "停止前需复核", blocked: "当前只读阻断" };
  const impact = assessment.impact || {};
  const relaunch = assessment.relaunch || {};
  const recovery = assessment.recovery || {};
  $("#stop-summary").textContent = `${service.display_name} · PID ${service.process.pid} · ${decisionLabels[assessment.decision] || "证据不足"}。 关停会同时影响 ${impact.endpoint_count || 0} 个端点和 ${impact.client_count || 0} 个当前本机客户端。`;
  const operations = (assessment.recommended_operations || []).map((item, index) => `
    <div class="recommended-operation"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.instruction)}</span>${item.copy_text ? `<button class="button button-small" type="button" data-copy-operation="${index}">复制建议命令</button><code>${escapeHtml(item.copy_text)}</code>` : ""}</div>`).join("");
  $("#stop-assessment").innerHTML = `
    <section class="assessment-card"><span class="assessment-decision ${escapeHtml(assessment.decision || "blocked")}">${escapeHtml(decisionLabels[assessment.decision] || "证据不足")}</span><strong>阻断与警告</strong>${assessmentList([...(assessment.blockers || []), ...(assessment.warnings || [])], "未识别额外阻断；仍需人工确认当前请求是否可中断。")}</section>
    <section class="assessment-card"><strong>当前影响</strong><p>客户端 ${escapeHtml(impact.client_count || 0)} · 端点 ${escapeHtml(impact.endpoint_count || 0)} · 项目 ${escapeHtml(impact.project || "未知")} · Agent ${escapeHtml(impact.agent || "未知")}</p>${assessmentList((impact.clients || []).map((item) => `PID ${item.source_pid} ${item.source_name} → :${item.port}`), "未检测到本机 TCP 客户端依赖。")}</section>
    <section class="assessment-card"><strong>重启与恢复</strong><p>重新拉起风险 ${escapeHtml(relaunch.risk || "unknown")} · 策略 ${escapeHtml(relaunch.restart_policy || "未检测到")} · 管理器 ${escapeHtml(relaunch.lifecycle_manager || "未识别")}</p>${assessmentList(recovery.steps || [], "回到项目目录复核原运行方式后手工恢复。")}</section>
    <section class="assessment-card"><strong>推荐操作（只展示，不执行）</strong>${operations || "<p>暂无可验证的生命周期管理器命令，请根据父进程证据回到原启动入口。</p>"}</section>`;
  $("#stop-assessment").dataset.operations = JSON.stringify(assessment.recommended_operations || []);
  const canStop = Boolean(assessment.can_request_stop);
  $("#stop-confirmation-field").hidden = !canStop;
  $("#stop-submit").hidden = !canStop;
  $("#stop-submit").disabled = !canStop;
  return canStop;
}

function renderStopVerification(verification) {
  const outcomeLabels = {
    stopped: "已停止并验证端口关闭",
    relaunched: "检测到替代 PID 或端口重新监听",
    stop_incomplete: "仍有原进程树成员存活",
    verification_partial: "停止已请求，但端口证据不完整",
  };
  const endpoints = (verification.endpoint_verification || []).map((item) => `${item.protocol} :${item.port} ${item.closed === true ? "已关闭" : item.closed === false ? `仍监听 PID ${(item.listener_pids || []).join(",") || "未知"}` : "无法核实"}`).join("；") || "无原监听端点";
  const container = $("#stop-verification");
  container.hidden = false;
  container.className = `stop-verification ${escapeHtml(verification.outcome || "verification_partial")}`;
  const limitations = (verification.limitations || []).join("；");
  container.innerHTML = `<strong>${escapeHtml(outcomeLabels[verification.outcome] || verification.outcome || "验证未知")}</strong><br>${escapeHtml(endpoints)}<br>观察 ${escapeHtml(verification.observation_window_seconds)} 秒 · 检查 ${escapeHtml(verification.checks)} 次 · 替代 PID ${escapeHtml((verification.replacement_pids || []).join(", ") || "无")}${limitations ? `<br><small>${escapeHtml(limitations)}</small>` : ""}<br><small>不会自动结束重新拉起的进程；更晚的重启由生命周期时间线继续记录。</small>`;
}

async function openStopDialog(service) {
  state.stopTarget = service;
  const pid = service.process.pid;
  $("#stop-summary").textContent = `${service.display_name} · PID ${pid} · 正在读取本机客户端、监听端点与生命周期证据…`;
  $("#stop-assessment").replaceChildren();
  $("#stop-verification").hidden = true;
  $("#stop-confirmation-field").hidden = true;
  $("#stop-submit").hidden = false;
  $("#stop-submit").disabled = true;
  $("#stop-prompt").textContent = `请输入 STOP ${pid}`;
  $("#stop-confirmation").value = "";
  $("#stop-confirmation").placeholder = `STOP ${pid}`;
  const remembered = localPreference("vsg.stopObservationMinutes", "15");
  $("#stop-observation-minutes").value = ["5", "15", "30"].includes(remembered) ? remembered : "15";
  $("#stop-dialog").showModal();
  try {
    const payload = await postService("/api/service/stop-assessment", service);
    if (state.stopTarget?.id !== service.id) return;
    const canStop = renderStopAssessment(service, payload.assessment || service.stop_assessment || {});
    if (canStop) $("#stop-confirmation").focus();
  } catch (error) {
    $("#stop-summary").textContent = `关停评估失败：${error.message}`;
    $("#stop-assessment").innerHTML = `<section class="assessment-card"><span class="assessment-decision blocked">证据不足</span><p>未执行任何停止操作。</p></section>`;
  }
}

async function submitStop(event) {
  event.preventDefault();
  const service = state.stopTarget;
  if (!service) return;
  const button = $("#stop-submit");
  button.disabled = true;
  button.textContent = "停止并验证中…";
  try {
    const observationMinutes = Number($("#stop-observation-minutes").value || 15);
    saveLocalPreference("vsg.stopObservationMinutes", String(observationMinutes));
    const payload = await postService("/api/process/stop", service, {
      confirmation: $("#stop-confirmation").value,
      observation_minutes: observationMinutes,
    });
    const verification = payload.result?.verification || {};
    state.stopObservationJob = payload.result?.observation || null;
    renderStopVerification(verification);
    $("#stop-confirmation-field").hidden = true;
    button.hidden = true;
    renderStopObservationBar();
    const observationFailed = state.stopObservationJob?.status === "failed_to_start";
    showToast(
      observationFailed
        ? state.stopObservationJob.limitations?.[0] || "停止已完成，但持续观察未能启动；请立即人工复核端口"
        : verification.outcome === "stopped"
          ? `PID ${service.process.pid} 已停止；已开始 ${observationMinutes} 分钟持续观察`
          : `停止后验证结果：${verification.outcome || "unknown"}`,
      observationFailed || verification.outcome !== "stopped",
    );
    setTimeout(loadStatus, 400);
  } catch (error) {
    showToast(error.message, true);
    button.disabled = false;
  } finally {
    button.textContent = "停止并观察";
  }
}

function formatCountdown(value) {
  const seconds = Math.max(0, Math.ceil(Number(value || 0)));
  const minutes = Math.floor(seconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}

function maybeNotifyRelaunch(job) {
  if (job?.status !== "relaunched" || state.notifiedObservationJobs.has(job.job_id)) return;
  state.notifiedObservationJobs.add(job.job_id);
  if ("Notification" in window && Notification.permission === "granted") {
    new Notification("VSG：服务已复活，需人工介入", {
      body: `${job.display_name || "service"} · 原 PID ${job.original_pid || "—"}`,
    });
  }
}

function renderStopObservationBar() {
  const job = state.stopObservationJob;
  const bar = $("#stop-observation-bar");
  if (!job || state.dismissedObservationJobId === job.job_id) {
    bar.hidden = true;
    return;
  }
  const active = ["observing", "cancel_requested"].includes(job.status);
  const labels = {
    observing: "正在持续观察停止结果",
    cancel_requested: "正在中止持续观察",
    completed: "停止验证报告：成功消失",
    relaunched: job.conclusion === "higher_level_relaunch" ? "停止验证报告：疑似被更高层进程拉起" : "停止验证报告：服务已复活",
    evidence_insufficient: "停止验证报告：证据不足",
    cancelled: "持续观察已由用户中止",
    interrupted: "持续观察因 VSG 退出而中断",
    failed: "持续观察失败：证据不足",
    failed_to_start: "持续观察未能启动：请人工复核",
  };
  bar.classList.toggle("attention", job.status === "relaunched");
  $("#stop-observation-title").textContent = labels[job.status] || "停止后持续观察";
  const parentChanged = job.report?.parent_process_changed ? " · 父进程已变化" : "";
  const portState = job.port_state === "reopened"
    ? "端口已重新监听"
    : job.port_state === "closed"
      ? "端口仍关闭"
      : "端口状态待确认";
  $("#stop-observation-summary").textContent = job.status === "failed_to_start"
    ? job.limitations?.[0] || "停止动作已经发生；当前没有持续观察证据"
    : active
    ? `PID ${job.original_pid}（${job.project_name || "未归类项目"}）· 剩余 ${formatCountdown(job.remaining_seconds)} · ${portState}`
    : `PID ${job.original_pid} · ${portState}${parentChanged} · 检查 ${job.report?.checks || job.checks || 0} 次`;
  const button = $("#stop-observation-cancel");
  button.textContent = active ? "中止观察" : "关闭报告";
  button.disabled = job.status === "cancel_requested";
  bar.hidden = false;
  maybeNotifyRelaunch(job);
}

async function loadStopObservations() {
  try {
    const payload = await api("/api/stop-observations");
    const active = payload.active?.[0];
    const latest = payload.items?.[0];
    const pendingFailure = state.stopObservationJob?.status === "failed_to_start"
      && state.dismissedObservationJobId !== state.stopObservationJob.job_id
      ? state.stopObservationJob
      : null;
    state.stopObservationJob = active || pendingFailure || latest || state.stopObservationJob;
    renderStopObservationBar();
  } catch {
    // The service dashboard remains usable if this optional status read fails.
  }
}

async function handleStopObservationButton() {
  const job = state.stopObservationJob;
  if (!job) return;
  if (!["observing", "cancel_requested"].includes(job.status)) {
    state.dismissedObservationJobId = job.job_id;
    renderStopObservationBar();
    return;
  }
  try {
    const payload = await api("/api/stop-observations/cancel", {
      method: "POST",
      body: JSON.stringify({ job_id: job.job_id }),
    });
    state.stopObservationJob = payload.job;
    renderStopObservationBar();
    showToast("已请求中止持续观察；不会执行任何进程操作");
  } catch (error) { showToast(error.message, true); }
}

async function copyRecommendedOperation(event) {
  const button = event.target.closest("button[data-copy-operation]");
  if (!button) return;
  const operations = JSON.parse($("#stop-assessment").dataset.operations || "[]");
  const item = operations[Number(button.dataset.copyOperation)];
  if (!item?.copy_text) return;
  try {
    await navigator.clipboard.writeText(item.copy_text);
    showToast("建议命令已复制；VSG 未执行该命令");
  } catch (error) { showToast(`复制失败：${error.message}`, true); }
}

function fillSettings() {
  const config = state.config;
  $("#setting-roots").value = (config.project_roots || []).join("\n");
  $("#setting-refresh").value = config.refresh_seconds;
  $("#setting-hours").value = config.stale_after_hours;
  $("#setting-review").value = config.review_score;
  $("#setting-stale").value = config.likely_stale_score;
  $("#setting-udp").checked = config.include_udp;
  $("#setting-windows").checked = config.include_windows_services;
  $("#setting-docker").checked = config.include_docker;
  $("#setting-wsl").checked = config.include_wsl;
  $("#setting-runtime-probes").checked = config.enable_runtime_probes;
  $("#setting-system-notifications").checked = config.enable_system_notifications;
  $("#setting-low-disk").value = config.low_disk_free_gib;
  $("#setting-log-retention").value = config.log_retention_days;
  $("#setting-electricity").value = config.electricity_price_per_kwh;
  $("#setting-trusted-nodes").value = (config.trusted_nodes || []).join("\n");
}

function applyPlatformUi() {
  const platform = state.platform || state.snapshot?.platform || {};
  const capabilities = platform.capabilities || {};
  $("#platform-badge").textContent = `${platform.label || "未知平台"} · ${platform.architecture || "unknown"}`;
  $("#search-shortcut").textContent = platform.key === "macos" ? "⌘ K" : "Ctrl K";
  const windowsOnly = [
    document.querySelector('[data-source="windows_service"]'),
    $("#setting-windows-field"),
  ];
  const wslOnly = [document.querySelector('[data-source="wsl"]'), $("#setting-wsl-field")];
  for (const element of windowsOnly) if (element) element.hidden = !capabilities.windows_services;
  for (const element of wslOnly) if (element) element.hidden = !capabilities.wsl;
  if ((state.source === "windows_service" && !capabilities.windows_services) || (state.source === "wsl" && !capabilities.wsl)) {
    state.source = "all";
    $$("#source-tabs .chip").forEach((item) => item.classList.toggle("active", item.dataset.source === "all"));
  }
}

async function saveSettings(event) {
  event.preventDefault();
  const body = {
    project_roots: $("#setting-roots").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
    refresh_seconds: Number($("#setting-refresh").value),
    stale_after_hours: Number($("#setting-hours").value),
    review_score: Number($("#setting-review").value),
    likely_stale_score: Number($("#setting-stale").value),
    include_udp: $("#setting-udp").checked,
    include_windows_services: $("#setting-windows").checked,
    include_docker: $("#setting-docker").checked,
    include_wsl: $("#setting-wsl").checked,
    enable_runtime_probes: $("#setting-runtime-probes").checked,
    enable_system_notifications: $("#setting-system-notifications").checked,
    low_disk_free_gib: Number($("#setting-low-disk").value),
    log_retention_days: Number($("#setting-log-retention").value),
    electricity_price_per_kwh: Number($("#setting-electricity").value),
    trusted_nodes: $("#setting-trusted-nodes").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
  };
  try {
    const payload = await api("/api/settings", { method: "POST", body: JSON.stringify(body) });
    state.config = payload.config;
    $("#settings-dialog").close();
    schedulePolling();
    showToast("设置已保存，正在重新扫描");
  } catch (error) { showToast(error.message, true); }
}

async function openAudit() {
  try {
    const payload = await api("/api/audit?limit=150");
    const list = $("#audit-list");
    list.replaceChildren();
    if (!payload.items.length) list.textContent = "暂无操作记录。";
    for (const item of payload.items) {
      const node = document.createElement("div");
      node.className = "audit-item";
      node.innerHTML = `<span>${escapeHtml(formatDate(item.created_at))}</span><strong>${escapeHtml(item.action)}</strong><span>${escapeHtml(item.target)} · ${escapeHtml(item.result)}</span>`;
      list.appendChild(node);
    }
    $("#audit-dialog").showModal();
  } catch (error) { showToast(error.message, true); }
}

function impactMetric(title, value, detail) {
  return `<section class="detail-card impact-metric"><h3>${escapeHtml(title)}</h3><strong>${escapeHtml(value ?? "—")}</strong><p>${escapeHtml(detail)}</p></section>`;
}

function renderImpactReport(report) {
  const current = report?.current_snapshot || {};
  const retained = report?.retained_local_evidence || {};
  const feedback = retained.feedback || {};
  const outcomes = feedback.outcomes || {};
  const stops = retained.stop_verifications || {};
  const benchmarks = retained.benchmarks || {};
  const prediction = retained.prediction_error || {};
  const tpsError = prediction.metrics?.per_user_generation_tps || {};
  const errorValue = tpsError.mean_absolute_error_percent == null ? "暂无实测" : `${tpsError.mean_absolute_error_percent}%`;
  $("#impact-report-summary").innerHTML = `
    ${impactMetric("当前服务", current.services || 0, `项目归属 ${current.project_attributed_services || 0} · Agent 归属 ${current.agent_attributed_services || 0}`)}
    ${impactMetric("遗留候选", (current.review_candidates || 0) + (current.likely_stale_candidates || 0), `建议复核 ${current.review_candidates || 0} · 疑似遗留 ${current.likely_stale_candidates || 0}`)}
    ${impactMetric("人工结果", feedback.total || 0, `确属遗留 ${outcomes.confirmed_stale || 0} · 不是遗留 ${outcomes.not_stale || 0} · 不确定 ${outcomes.uncertain || 0}`)}
    ${impactMetric("停止验证", stops.total || 0, `停止成功 ${stops.outcomes?.stopped || 0} · 检测重启 ${stops.restart_detected || 0}`)}
    ${impactMetric("服务基准", benchmarks.service_runs || 0, `成功记录 ${benchmarks.successful_service_runs || 0} · 服务数 ${benchmarks.unique_services || 0}`)}
    ${impactMetric("TPS 预测误差", errorValue, `实测样本 ${tpsError.samples || 0} · 仅统计绝对百分比误差`)}
    <section class="detail-card full"><h3>证据边界</h3><p>单机、自报、受历史保留期限制；不代表独立用户、公开采用、下载量或社区影响力。生成于 ${escapeHtml(formatDate(report?.generated_at))}。</p></section>`;
}

async function loadImpactReport() {
  const payload = await api("/api/impact");
  state.impactReport = payload.report;
  renderImpactReport(payload.report);
}

async function openImpactReport() {
  $("#impact-export-confirmation").value = "";
  $("#impact-report-summary").innerHTML = '<span class="confidence-note">正在汇总保留期内的本机证据…</span>';
  if (!$("#impact-report-dialog").open) $("#impact-report-dialog").showModal();
  try {
    await loadImpactReport();
  } catch (error) {
    $("#impact-report-summary").innerHTML = `<span class="confidence-note danger-text">${escapeHtml(error.message)}</span>`;
    showToast(error.message, true);
  }
}

async function exportImpactReport(event) {
  event.preventDefault();
  try {
    const payload = await api("/api/impact/export", {
      method: "POST",
      body: JSON.stringify({ confirmation: $("#impact-export-confirmation").value.trim() }),
    });
    const blob = new Blob([`${JSON.stringify(payload.export, null, 2)}\n`], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = payload.filename || "vsg-impact-report.json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    state.impactReport = payload.export.report;
    renderImpactReport(state.impactReport);
    $("#impact-export-confirmation").value = "";
    showToast("脱敏成效报告已下载；对外分享前仍需人工复核");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadStatus() {
  try {
    const payload = await api("/api/status");
    state.snapshot = payload.snapshot;
    state.config = payload.config;
    state.platform = payload.platform || payload.snapshot?.platform || state.platform;
    applyPlatformUi();
    render();
    await loadStopObservations();
  } catch (error) {
    showToast(`状态读取失败：${error.message}`, true);
  }
}

function schedulePolling() {
  clearInterval(state.timer);
  const seconds = Math.max(2, state.config?.refresh_seconds || 3);
  state.timer = setInterval(loadStatus, seconds * 1000);
}

async function refreshNow() {
  try {
    await api("/api/refresh", { method: "POST", body: "{}" });
    showToast("已请求重新扫描");
    setTimeout(loadStatus, 450);
  } catch (error) { showToast(error.message, true); }
}

function handleHealthAction(event) {
  const button = event.target.closest("button[data-health-action]");
  if (!button || button.disabled) return;
  const serviceId = button.closest("tr")?.dataset.serviceId;
  if (!serviceId) return;
  const action = button.dataset.healthAction;
  if (action === "benchmark") return openServiceBenchmark(serviceId);
  if (action === "matrix") return openWorkloadMatrix(serviceId);
  if (action === "inspect") return openDiagnostic(serviceId);
  if (action === "details") {
    const service = state.snapshot?.services?.find((item) => item.id === serviceId);
    if (service) openDetails(service);
  }
}

function bindEvents() {
  document.addEventListener("vsg:localechange", () => {
    applyPlatformUi();
    if (state.snapshot) render();
    if (state.plannerStatus) {
      renderHardware();
      renderBenchmarkHistory();
    }
    if (state.estimate) renderEstimate(state.estimate);
    if (state.advisor) renderAdvisor();
    if (state.operations?.incidents) renderOperations();
    if ($("#audit-dialog")?.open) openAudit();
    if ($("#impact-report-dialog")?.open && state.impactReport) renderImpactReport(state.impactReport);
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-close-dialog]");
    if (!button) return;
    const dialog = button.closest("dialog");
    if (dialog?.open) dialog.close("cancel");
  });
  $("#service-body").addEventListener("click", handleRowAction);
  $("#detail-content").addEventListener("click", handleImpactFeedback);
  $("#refresh-button").addEventListener("click", refreshNow);
  $("#settings-button").addEventListener("click", () => { fillSettings(); $("#settings-dialog").showModal(); });
  $("#audit-button").addEventListener("click", openAudit);
  $("#impact-report-button").addEventListener("click", openImpactReport);
  $("#impact-report-form").addEventListener("submit", exportImpactReport);
  $("#impact-report-cancel").addEventListener("click", () => $("#impact-report-dialog").close());
  $("#search-input").addEventListener("input", (event) => { state.query = event.target.value.trim().toLocaleLowerCase(); renderRows(); });
  $("#risk-only").addEventListener("change", (event) => { state.riskOnly = event.target.checked; renderRows(); });
  $("#source-tabs").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-source]");
    if (!button) return;
    state.source = button.dataset.source;
    $$("#source-tabs .chip").forEach((item) => item.classList.toggle("active", item === button));
    renderRows();
  });
  $("#stop-form").addEventListener("submit", submitStop);
  $("#stop-cancel").addEventListener("click", () => $("#stop-dialog").close());
  $("#stop-assessment").addEventListener("click", copyRecommendedOperation);
  $("#stop-observation-cancel").addEventListener("click", handleStopObservationButton);
  $("#settings-form").addEventListener("submit", saveSettings);
  $("#settings-cancel").addEventListener("click", () => $("#settings-dialog").close());
  $("#attribution-form").addEventListener("submit", submitAttribution);
  $("#attribution-cancel").addEventListener("click", () => $("#attribution-dialog").close());
  $("#attribution-clear-label").addEventListener("click", clearLifecycleLabel);
  $("#attribution-lifecycle-label").addEventListener("change", (event) => {
    const canInherit = Boolean(
      event.target.value && state.attributionTarget?.metadata?.ownership_signature
    );
    $("#attribution-inherit").disabled = !canInherit;
    $("#attribution-inherit").checked = canInherit;
  });
  $("#confirmation-form").addEventListener("submit", submitConfirmation);
  $("#confirmation-cancel").addEventListener("click", () => { state.confirmationAction = null; $("#confirmation-dialog").close(); });
  $$(".workspace-tab").forEach((button) => button.addEventListener("click", () => activateView(button.dataset.view)));
  $("#planner-form").addEventListener("submit", submitPlanner);
  $("#hardware-refresh-button").addEventListener("click", refreshHardware);
  $("#copy-command-button").addEventListener("click", copyRuntimeCommand);
  $("#benchmark-button").addEventListener("click", openBenchmarkDialog);
  $("#benchmark-form").addEventListener("submit", submitBenchmark);
  $("#benchmark-cancel").addEventListener("click", () => $("#benchmark-dialog").close());
  $("#health-refresh-button").addEventListener("click", async () => { await refreshNow(); setTimeout(loadStatus, 700); });
  $("#runtime-health-body").addEventListener("click", handleHealthAction);
  $("#service-benchmark-form").addEventListener("submit", submitServiceBenchmark);
  $("#service-benchmark-cancel").addEventListener("click", () => $("#service-benchmark-dialog").close());
  $("#workload-matrix-form").addEventListener("submit", submitWorkloadMatrix);
  $("#workload-matrix-preview").addEventListener("click", previewWorkloadMatrix);
  $("#workload-matrix-mode").addEventListener("change", (event) => {
    const calibration = event.target.value === "calibration";
    $("#workload-calibration-concurrency-field").hidden = !calibration;
    $("#workload-matrix-title").textContent = calibration
      ? `${state.workloadMatrixTarget?.service?.display_name || "模型服务"} · 60 秒本机校准`
      : `${state.workloadMatrixTarget?.service?.display_name || "模型服务"} · 分级负载矩阵`;
  });
  $("#workload-matrix-cancel").addEventListener("click", cancelWorkloadMatrix);
  $("#workload-matrix-close").addEventListener("click", () => $("#workload-matrix-dialog").close());
  $("#diagnostic-form").addEventListener("submit", submitDiagnostic);
  $("#diagnostic-cancel").addEventListener("click", () => $("#diagnostic-dialog").close());
  $("#snapshot-form").addEventListener("submit", submitSnapshot);
  $("#snapshot-history").addEventListener("click", (event) => { const button = event.target.closest("button[data-restore]"); if (button) openRestore(button); });
  $("#restore-form").addEventListener("submit", submitRestore);
  $("#restore-cancel").addEventListener("click", () => $("#restore-dialog").close());
  $("#advisor-form").addEventListener("submit", submitAdvisor);
  $("#advisor-refresh-button").addEventListener("click", async () => { try { await loadAdvisor(true); } catch (error) { showToast(error.message, true); } });
  $("#log-watch-form").addEventListener("submit", submitLogWatch);
  $("#log-watch-service").addEventListener("change", updateLogWatchPrompt);
  $("#log-watches").addEventListener("click", (event) => { const button = event.target.closest("button[data-stop-watch]"); if (button) stopLogWatch(button); });
  $("#log-filter-apply").addEventListener("click", async () => { try { await loadFilteredLogEvents(); } catch (error) { showToast(error.message, true); } });
  $("#operations-refresh-button").addEventListener("click", async () => { try { await loadOperations(); showToast(window.VSG_I18N?.locale === "en" ? "Events and assets refreshed" : "事件与资产已刷新"); } catch (error) { showToast(error.message, true); } });
  $("#model-inventory-form").addEventListener("submit", submitModelInventory);
  $("#timeline-filter-apply").addEventListener("click", async () => { try { await loadOperations(); } catch (error) { showToast(error.message, true); } });
  $("#timeline-category").addEventListener("change", renderTimeline);
  $("#attribution-rule-list").addEventListener("click", (event) => { const button = event.target.closest("button[data-delete-rule]"); if (button) deleteAttributionRule(button); });
  $("#model-inventory-body").addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-inventory-advisor]");
    if (!button) return;
    const format = button.closest("tr")?.dataset.modelFormat || "auto";
    await activateView("advisor");
    if ([...$("#advisor-format").options].some((item) => item.value === format)) $("#advisor-format").value = format;
    try { await loadAdvisor(true); } catch (error) { showToast(error.message, true); }
  });
  $("#history-clear-form").addEventListener("submit", submitHistoryClear);
  $("#measured-profile-list").addEventListener("click", handleMeasuredProfileAction);
  $("#planner-calibrate-one").addEventListener("click", () => openPlannerCalibration(1));
  $("#planner-calibrate-two").addEventListener("click", () => openPlannerCalibration(2));
  document.addEventListener("keydown", (event) => {
    if (state.activeView === "services" && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#search-input").focus();
    }
  });
}

async function init() {
  bindEvents();
  try {
    const bootstrap = await api("/api/bootstrap");
    state.token = bootstrap.token;
    state.platform = bootstrap.platform || null;
    applyPlatformUi();
    await loadStatus();
    schedulePolling();
  } catch (error) {
    $("#loading-state").textContent = `控制台初始化失败：${error.message}`;
    showToast(error.message, true);
  }
}

init();
