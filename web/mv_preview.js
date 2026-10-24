import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const NODE_NAME = "Xiaoyue_MV_PreviewDesk";
const WORKBENCH_NODE_NAME = "MiniMaxH3_MV_Planner_xiaoyue";
const TRACK_GUTTER = 54;
const openDesks = new WeakMap();
const EXPORT_NAMES = {
    movie: "无字成片 + SRT",
    fcp_xml: "FCP7 XML + PR 工程包",
    premiere: "旧版 Premiere 工程包",
    jianying: "旧版剪映草稿包",
};

function exportEntryTitle(entry) {
    const name = EXPORT_NAMES[entry.kind] || "导出包";
    if (entry.state === "running") return `导出中 · ${name}`;
    if (entry.state === "failed") return `导出失败 · ${name}`;
    if (entry.state === "cancelled") return `已取消 · ${name}`;
    return name;
}

function exportEntryDetail(entry) {
    if (entry.state === "running") {
        return `${Math.round(Number(entry.progress || 0) * 100)}% · ${entry.message || "正在导出"}`;
    }
    if (entry.state === "failed" || entry.state === "cancelled") return entry.message || entry.state;
    return new Date(entry.created_at * 1000).toLocaleString("zh-CN");
}

function installStyles() {
    if (document.getElementById("xiaoyue-mv-preview-styles")) return;
    const link = document.createElement("link");
    link.id = "xiaoyue-mv-preview-styles";
    link.rel = "stylesheet";
    link.href = new URL("./mv_preview.css", import.meta.url).href;
    document.head.append(link);
}

function mediaUrl(source) {
    const query = new URLSearchParams({
        filename: source.filename,
        subfolder: source.subfolder,
        type: source.type,
    });
    return api.apiURL(`/view?${query}`);
}

function linkedProjectId(node) {
    if ((node.comfyClass || node.type) === WORKBENCH_NODE_NAME) {
        return projectIdFromWorkbench(node);
    }
    const input = node.inputs?.find((item) => item.name === "mv_plan");
    const graph = node.graph || app.graph;
    const links = graph?.links;
    const link = links instanceof Map ? links.get(input?.link) : links?.[input?.link];
    const upstream = link && graph?.getNodeById?.(link.origin_id);
    if (!upstream || (upstream.comfyClass || upstream.type) !== WORKBENCH_NODE_NAME) {
        return "";
    }
    return projectIdFromWorkbench(upstream);
}

function projectIdFromWorkbench(node) {
    const currentId = String(node.widgets?.find((item) => item.name === "project_id")?.value || "");
    if (currentId) return currentId;
    const raw = node.widgets?.find((item) => item.name === "plan_json")?.value;
    if (raw) {
        try {
            const plan = JSON.parse(raw);
            if (plan?.project?.id) return String(plan.project.id);
        } catch { /* Fall back to the project's stable widget. */ }
    }
    return "";
}

function nodeElement(tag, className = "", text = "") {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
}

function timecode(frame, fps = 24) {
    const totalSeconds = Math.floor(Math.max(0, frame) / fps);
    const frames = Math.floor(Math.max(0, frame) % fps);
    const seconds = totalSeconds % 60;
    const minutes = Math.floor(totalSeconds / 60) % 60;
    const hours = Math.floor(totalSeconds / 3600);
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}:${String(frames).padStart(2, "0")}`;
}

async function responseJson(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `请求失败（HTTP ${response.status}）`);
    return body;
}

class PreviewDesk {
    constructor(node, projectId) {
        this.node = node;
        this.projectId = projectId;
        this.manifest = null;
        this.pool = new Map();
        this.raf = 0;
        this.fit = true;
        this.pixelsPerSecond = 48;
        this.activeShotUid = "";
        this.followedShotUid = "";
        this.followedSubtitleIndex = -1;
        this.suppressFollowScroll = false;
        this.shotRows = new Map();
        this.subtitleRows = [];
        this.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        this.timelinePointerId = null;
        this.timelineWasPlaying = false;
        this.dialog = nodeElement("dialog", "xiaoyue-preview-dialog");
        this.dialog.setAttribute("aria-labelledby", "xp-dialog-title");
        this.dialog.innerHTML = `
            <div class="xp-shell">
                <header class="xp-header">
                    <div><h2 id="xp-dialog-title">MV 最终验收剪辑台</h2><p class="xp-project"></p></div>
                    <div class="xp-header-actions">
                        <span class="xp-status" role="status" aria-live="polite"></span>
                        <details class="xp-export-menu"><summary>导出</summary><div class="xp-export-options" aria-label="导出格式"><button type="button" data-export-kind="movie">无字成片 + SRT</button><button type="button" data-export-kind="fcp_xml">FCP7 XML + PR 工程包</button><p class="xp-export-error" role="alert" hidden></p></div></details>
                        <button type="button" class="xp-refresh">刷新工程</button>
                        <button type="button" class="xp-close" aria-label="关闭剪辑台">关闭</button>
                    </div>
                </header>
                <div class="xp-tabs" role="tablist" aria-label="验收与导出页面">
                    <button type="button" id="xp-tab-acceptance" role="tab" data-tab="acceptance" aria-selected="true" aria-controls="xp-acceptance-panel">验收时间轴</button>
                    <button type="button" id="xp-tab-exports" role="tab" data-tab="exports" aria-selected="false" aria-controls="xp-exports-panel" tabindex="-1">已导出</button>
                </div>
                <div id="xp-acceptance-panel" class="xp-acceptance-page" role="tabpanel" aria-labelledby="xp-tab-acceptance">
                <div class="xp-workspace">
                    <section class="xp-preview-panel" aria-label="整片预览">
                        <div class="xp-stage"><div class="xp-empty">选择时间点后预览镜头</div><div class="xp-caption" aria-live="off"></div></div>
                        <audio class="xp-master" preload="metadata"></audio>
                        <div class="xp-controls">
                            <button type="button" class="xp-play" aria-label="播放原曲与画面" aria-keyshortcuts="Space">播放</button>
                            <input class="xp-scrub" type="range" min="0" max="1" value="0" aria-label="整片时间位置">
                            <output class="xp-time">00:00:00:00</output>
                            <label class="xp-volume-label">原曲音量 <input class="xp-volume" type="range" min="0" max="1" step="0.05" value="1"></label>
                        </div>
                        <p class="xp-audio-note">镜头视频全部静音；只播放工程原曲。预览按交付帧跳过前置引导与尾部出血，不修改源文件。</p>
                    </section>
                    <aside class="xp-inspector" aria-label="镜头选版与字幕">
                        <section class="xp-shot-section"><div class="xp-section-head"><h3>镜头版本</h3><span class="xp-progress"></span></div><div class="xp-shots"></div></section>
                        <section class="xp-subtitle-section"><div class="xp-section-head"><h3>最终字幕表</h3><div><a class="xp-srt" download>导出 SRT</a><a class="xp-csv" download>导出 CSV</a></div></div><p>只使用正式字幕；前扩、后扩不进入最终预览和导出。</p><div class="xp-subtitles"></div></section>
                    </aside>
                </div>
                <section class="xp-timeline-panel" aria-label="总时间轴">
                    <div class="xp-timeline-tools"><h3>总时间轴</h3><span>拖动时间轴定位 · 续写逐轨上叠</span><button type="button" class="xp-fit">适配全曲</button><label>缩放 <input class="xp-zoom" type="range" min="12" max="160" value="48"></label></div>
                    <div class="xp-timeline-scroll"><div class="xp-timeline-inner"><div class="xp-ruler" role="slider" tabindex="0" aria-label="总时间轴游标" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"></div><div class="xp-track-list"></div><div class="xp-playhead"></div></div></div>
                </section>
                </div>
                <section id="xp-exports-panel" class="xp-exports-page" role="tabpanel" aria-labelledby="xp-tab-exports" hidden>
                    <aside class="xp-export-list" aria-label="导出记录">
                        <div class="xp-export-list-head"><h3>导出记录</h3><button type="button" class="xp-export-refresh">刷新</button></div>
                        <ul class="xp-export-history-list" role="list"></ul>
                    </aside>
                    <section class="xp-export-detail" aria-labelledby="xp-export-detail-title">
                        <div class="xp-export-detail-head"><div><h3 id="xp-export-detail-title" class="xp-export-detail-title">选择导出记录</h3><p class="xp-export-detail-meta"></p></div><button type="button" class="xp-return-to-acceptance">返回验收时间轴</button></div>
                        <div class="xp-export-detail-body"><video controls playsinline preload="metadata" width="1280" height="736" aria-label="已导出成片预览（叠加正式字幕，文件仍无字）" hidden></video><div class="xp-export-job" hidden><progress class="xp-export-progress" max="1" value="0" aria-label="导出进度"></progress><p class="xp-export-job-message" role="status" aria-live="polite"></p><button type="button" class="xp-export-cancel">取消导出</button></div><p class="xp-export-detail-empty">从左侧选择一次导出，在这里查看成片或工程包。</p></div>
                        <div class="xp-export-detail-actions"><a class="xp-export-detail-download" hidden download>下载 ZIP</a></div>
                    </section>
                </section>
            </div>`;
        document.body.appendChild(this.dialog);
        this.stage = this.dialog.querySelector(".xp-stage");
        this.audio = this.dialog.querySelector(".xp-master");
        this.acceptancePage = this.dialog.querySelector(".xp-acceptance-page");
        this.exportsPage = this.dialog.querySelector(".xp-exports-page");
        this.exportVideo = this.dialog.querySelector(".xp-export-detail video");
        this.exportMenu = this.dialog.querySelector(".xp-export-menu");
        this.exportSubmitting = false;
        this.exportJob = null;
        this.exportJobId = "";
        this.exportTimer = 0;
        this.exportEntries = [];
        this.selectedExportId = "";
        this.activeTab = "acceptance";
        this.dialog.querySelectorAll(".xp-tabs [role='tab']").forEach((button) => {
            button.addEventListener("click", () => this.setActiveTab(button.dataset.tab));
        });
        this.dialog.querySelector(".xp-tabs").addEventListener("keydown", (event) => this.handleTabKeydown(event));
        this.exportMenu.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && this.exportMenu.open) {
                event.preventDefault();
                event.stopPropagation();
                this.exportMenu.open = false;
                this.exportMenu.querySelector("summary").focus();
            }
        });
        this.dialog.addEventListener("click", (event) => {
            if (!event.target.closest(".xp-export-menu")) this.exportMenu.open = false;
        });
        this.dialog.querySelector(".xp-export-refresh").addEventListener("click", () => {
            this.loadExportHistory().catch((error) => this.status(error.message || String(error), true));
        });
        this.dialog.querySelector(".xp-return-to-acceptance").addEventListener("click", () => this.setActiveTab("acceptance"));
        this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => {
            button.addEventListener("click", () => this.startExport(button.dataset.exportKind));
        });
        this.exportsPage.querySelector(".xp-export-cancel").addEventListener("click", () => this.cancelExport());
        this.exportVideo.addEventListener("play", () => this.pause());
        this.scrub = this.dialog.querySelector(".xp-scrub");
        this.audio.addEventListener("timeupdate", () => this.renderFrame());
        this.audio.addEventListener("ended", () => this.pause());
        this.dialog.querySelector(".xp-close").addEventListener("click", () => this.dialog.close());
        this.dialog.querySelector(".xp-refresh").addEventListener("click", () => this.load());
        this.dialog.querySelector(".xp-play").addEventListener("click", () => this.togglePlayback());
        this.dialog.querySelector(".xp-volume").addEventListener("input", (event) => {
            this.audio.volume = Number(event.target.value);
        });
        this.scrub.addEventListener("input", () => this.seekFrame(Number(this.scrub.value)));
        this.timelineInner = this.dialog.querySelector(".xp-timeline-inner");
        this.timelineRuler = this.dialog.querySelector(".xp-ruler");
        this.timelineInner.style.setProperty("--xp-track-gutter", `${TRACK_GUTTER}px`);
        this.timelineInner.addEventListener("pointerdown", (event) => this.startTimelineScrub(event));
        this.timelineInner.addEventListener("pointermove", (event) => this.moveTimelineScrub(event));
        this.timelineInner.addEventListener("pointerup", (event) => this.endTimelineScrub(event));
        this.timelineInner.addEventListener("pointercancel", (event) => this.endTimelineScrub(event));
        this.timelineInner.addEventListener("lostpointercapture", (event) => this.endTimelineScrub(event));
        this.timelineRuler.addEventListener("keydown", (event) => this.timelineKeydown(event));
        this.dialog.addEventListener("keydown", (event) => this.handleShortcut(event));
        this.dialog.addEventListener("click", (event) => {
            if (event.detail > 0 && event.target.closest(".xp-clip, .xp-shot-header, .xp-version, .xp-subtitle-row")) {
                this.dialog.focus({ preventScroll: true });
            }
        });
        this.dialog.querySelector(".xp-fit").addEventListener("click", () => {
            this.fit = true;
            this.renderTimeline();
        });
        this.dialog.querySelector(".xp-zoom").addEventListener("input", (event) => {
            this.fit = false;
            this.pixelsPerSecond = Number(event.target.value);
            this.renderTimeline();
        });
        this.dialog.addEventListener("close", () => this.destroy());
        this.dialog.tabIndex = -1;
        this.dialog.showModal();
        this.dialog.focus({ preventScroll: true });
        this.load();
        this.resumeExport();
    }

    status(message, error = false) {
        const target = this.dialog.querySelector(".xp-status");
        target.textContent = message;
        target.classList.toggle("is-error", error);
    }

    setActiveTab(name) {
        const showExports = name === "exports";
        if (showExports === (this.activeTab === "exports")) return;
        this.activeTab = showExports ? "exports" : "acceptance";
        if (showExports) this.pause();
        else this.clearExportVideo();
        this.acceptancePage.hidden = showExports;
        this.exportsPage.hidden = !showExports;
        this.dialog.querySelectorAll(".xp-tabs [role='tab']").forEach((button) => {
            const selected = button.dataset.tab === this.activeTab;
            button.setAttribute("aria-selected", String(selected));
            button.tabIndex = selected ? 0 : -1;
        });
        if (showExports) {
            this.renderSelectedExport();
            this.loadExportHistory().catch((error) => this.status(error.message || String(error), true));
        } else {
            requestAnimationFrame(() => { if (this.dialog.open && this.manifest) this.renderTimeline(); });
        }
    }

    handleTabKeydown(event) {
        if (event.target.getAttribute("role") !== "tab") return;
        const tabs = [...this.dialog.querySelectorAll(".xp-tabs [role='tab']")];
        const current = tabs.indexOf(event.target);
        let next = current;
        if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
        else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = tabs.length - 1;
        else return;
        event.preventDefault();
        tabs[next].focus();
        this.setActiveTab(tabs[next].dataset.tab);
    }

    async resumeExport() {
        try {
            await this.loadExportHistory();
        } catch (error) {
            this.status(error.message || String(error), true);
        }
    }

    async loadExportHistory() {
        const response = await api.fetchApi(
            `/xiaoyue-mv/preview/${encodeURIComponent(this.projectId)}/exports`);
        const result = await responseJson(response);
        const activeJob = result.active_job || null;
        if (activeJob) {
            this.exportJob = activeJob;
            this.exportJobId = activeJob.job_id;
            this.scheduleExportPoll();
        }
        this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => {
            button.disabled = this.exportSubmitting || Boolean(this.exportJobId);
        });
        const transientJob = activeJob || (["failed", "cancelled"].includes(this.exportJob?.state)
            ? this.exportJob : null);
        const entries = transientJob ? [transientJob, ...(result.exports || [])] : (result.exports || []);
        this.exportEntries = entries;
        if (!entries.some((entry) => entry.job_id === this.selectedExportId)) {
            this.selectedExportId = entries[0]?.job_id || "";
        }
        const archiveCount = (result.exports || []).length;
        this.dialog.querySelector("#xp-tab-exports").textContent =
            `已导出${archiveCount ? `（${archiveCount}）` : ""}${activeJob ? " · 导出中" : ""}`;
        const list = this.exportsPage.querySelector(".xp-export-history-list");
        list.replaceChildren();
        if (!entries.length) list.append(nodeElement("li", "xp-export-list-empty", "还没有导出记录。先在验收时间轴点击“导出”。"));
        for (const entry of entries) {
            const listItem = nodeElement("li");
            const row = nodeElement("button", "xp-export-history-row");
            row.type = "button";
            row.dataset.exportId = entry.job_id;
            row.setAttribute("aria-current", String(entry.job_id === this.selectedExportId));
            row.append(
                nodeElement("strong", "xp-export-history-title", exportEntryTitle(entry)),
                nodeElement("span", "xp-export-history-time", exportEntryDetail(entry)),
            );
            row.addEventListener("click", () => this.selectExport(entry));
            listItem.append(row);
            list.append(listItem);
        }
        this.renderSelectedExport();
    }

    selectExport(entry) {
        this.selectedExportId = entry.job_id;
        this.exportsPage.querySelectorAll(".xp-export-history-row").forEach((row) => {
            row.setAttribute("aria-current", String(row.dataset.exportId === entry.job_id));
        });
        this.renderSelectedExport();
    }

    clearExportVideo() {
        this.exportVideo.pause();
        this.exportVideo.querySelectorAll("track").forEach((track) => track.remove());
        if (this.exportVideo.hasAttribute("src")) {
            this.exportVideo.removeAttribute("src");
            this.exportVideo.load();
        }
        this.exportVideo.dataset.exportId = "";
    }

    renderSelectedExport() {
        const entry = this.exportEntries.find((item) => item.job_id === this.selectedExportId);
        const title = this.exportsPage.querySelector(".xp-export-detail-title");
        const meta = this.exportsPage.querySelector(".xp-export-detail-meta");
        const empty = this.exportsPage.querySelector(".xp-export-detail-empty");
        const jobPanel = this.exportsPage.querySelector(".xp-export-job");
        const download = this.exportsPage.querySelector(".xp-export-detail-download");
        title.textContent = entry ? exportEntryTitle(entry) : "选择导出记录";
        meta.textContent = entry ? new Date(entry.created_at * 1000).toLocaleString("zh-CN") : "";
        download.hidden = !entry?.download_url;
        if (entry?.download_url) {
            download.href = api.apiURL(entry.download_url);
            download.textContent = "下载 ZIP（含素材与字幕）";
        }
        const showJob = this.activeTab === "exports" && Boolean(entry?.state);
        jobPanel.hidden = !showJob;
        if (showJob) {
            jobPanel.querySelector(".xp-export-progress").value = Number(entry.progress || 0);
            jobPanel.querySelector(".xp-export-job-message").textContent = entry.message || entry.state;
            jobPanel.querySelector(".xp-export-cancel").hidden = entry.state !== "running";
        }
        const showMovie = this.activeTab === "exports" && Boolean(entry?.preview_url) && !showJob;
        this.exportVideo.hidden = !showMovie;
        empty.hidden = showMovie || showJob;
        empty.textContent = entry
            ? "这次导出是工程包，没有可播放的成片；请下载 ZIP 后导入剪辑软件。"
            : "从左侧选择一次导出，在这里查看成片或工程包。";
        if (showMovie) {
            if (this.exportVideo.dataset.exportId !== entry.job_id) {
                this.clearExportVideo();
                this.exportVideo.src = api.apiURL(entry.preview_url);
                if (entry.subtitle_url) {
                    const track = nodeElement("track");
                    track.kind = "subtitles";
                    track.srclang = "zh";
                    track.label = "正式字幕";
                    track.default = true;
                    track.src = api.apiURL(entry.subtitle_url);
                    track.addEventListener("load", () => {
                        if (track.isConnected) track.track.mode = "showing";
                    });
                    this.exportVideo.append(track);
                    track.track.mode = "showing";
                }
                this.exportVideo.dataset.exportId = entry.job_id;
                this.exportVideo.load();
                this.exportVideo.play().catch(() => { /* Browser may require a second gesture. */ });
            }
        } else {
            this.clearExportVideo();
        }
    }

    async startExport(kind) {
        if (this.exportSubmitting || this.exportJobId) return;
        this.exportSubmitting = true;
        this.exportMenu.open = false;
        this.exportMenu.querySelector(".xp-export-error").hidden = true;
        this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => { button.disabled = true; });
        this.status("正在提交后台导出…");
        try {
            const response = await api.fetchApi("/xiaoyue-mv/delivery/export", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ project_id: this.projectId, kind }),
            });
            const job = await responseJson(response);
            this.exportJob = job;
            this.exportJobId = job.job_id;
            this.selectedExportId = job.job_id;
            this.status("后台导出中；在“已导出”查看进度");
            this.scheduleExportPoll();
            await this.loadExportHistory().catch((error) => {
                this.status(`导出已启动，记录暂未加载：${error.message || error}`, true);
            });
        } catch (error) {
            const message = error.message || String(error);
            this.status(message, true);
            const errorText = this.exportMenu.querySelector(".xp-export-error");
            errorText.textContent = message;
            errorText.hidden = false;
            this.exportMenu.open = true;
        } finally {
            this.exportSubmitting = false;
            this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => {
                button.disabled = Boolean(this.exportJobId);
            });
        }
    }

    showExportJob(job) {
        this.exportJob = job;
        const entry = this.exportEntries.find((item) => item.job_id === job.job_id);
        if (entry) {
            Object.assign(entry, job);
            const row = [...this.exportsPage.querySelectorAll(".xp-export-history-row")]
                .find((item) => item.dataset.exportId === job.job_id);
            if (row) {
                row.querySelector(".xp-export-history-title").textContent = exportEntryTitle(entry);
                row.querySelector(".xp-export-history-time").textContent = exportEntryDetail(entry);
            }
        }
        if (this.selectedExportId === job.job_id) this.renderSelectedExport();
    }

    scheduleExportPoll() {
        clearTimeout(this.exportTimer);
        if (this.exportJobId) this.exportTimer = setTimeout(() => this.pollExport(), 1200);
    }

    async pollExport() {
        if (!this.exportJobId) return;
        let jobNotFound = false;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/preview/export/${encodeURIComponent(this.exportJobId)}`);
            jobNotFound = response.status === 404;
            const job = await responseJson(response);
            this.showExportJob(job);
            if (job.state === "running") {
                this.scheduleExportPoll();
            } else {
                this.exportJobId = "";
                this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => { button.disabled = false; });
                if (job.state === "completed") {
                    await this.loadExportHistory();
                    this.status("导出完成；已放入“已导出”");
                } else {
                    this.status(job.message || "导出已结束", job.state === "failed");
                    await this.loadExportHistory();
                }
            }
        } catch (error) {
            this.status(error.message || String(error), true);
            if (jobNotFound) {
                this.exportJobId = "";
                this.exportJob = null;
                this.exportMenu.querySelectorAll("[data-export-kind]").forEach((button) => { button.disabled = false; });
                await this.loadExportHistory().catch(() => {});
            } else {
                this.scheduleExportPoll();
            }
        }
    }

    async cancelExport() {
        if (!this.exportJobId) return;
        const button = this.exportsPage.querySelector(".xp-export-cancel");
        button.disabled = true;
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/preview/export/${encodeURIComponent(this.exportJobId)}/cancel`,
                { method: "POST" });
            this.showExportJob(await responseJson(response));
        } catch (error) {
            this.status(error.message || String(error), true);
        } finally {
            button.disabled = false;
        }
    }

    async load() {
        this.status("读取工程…");
        try {
            const response = await api.fetchApi(
                `/xiaoyue-mv/preview/${encodeURIComponent(this.projectId)}`);
            this.applyManifest(await responseJson(response));
            this.status("已同步");
        } catch (error) {
            this.status(error.message || String(error), true);
        }
    }

    applyManifest(manifest) {
        const audioKey = JSON.stringify(manifest.master_audio);
        if (this.audio.dataset.sourceKey !== audioKey) {
            this.audio.dataset.sourceKey = audioKey;
            this.audio.src = mediaUrl(manifest.master_audio);
            this.audio.load();
        }
        this.manifest = manifest;
        this.followedShotUid = "";
        this.followedSubtitleIndex = -1;
        this.dialog.querySelector(".xp-project").textContent =
            `${manifest.project.code} · ${manifest.project.name} · ${manifest.fps} fps`;
        this.scrub.max = String(Math.max(0, manifest.total_frames - 1));
        const subtitleBase = `/xiaoyue-mv/preview/${encodeURIComponent(this.projectId)}/subtitles`;
        this.dialog.querySelector(".xp-srt").href = api.apiURL(`${subtitleBase}?format=srt`);
        this.dialog.querySelector(".xp-csv").href = api.apiURL(`${subtitleBase}?format=csv`);
        this.renderShots();
        this.renderSubtitles();
        this.renderTimeline();
        this.renderFrame();
    }

    selectedClips() {
        if (!this.manifest) return [];
        return this.manifest.shots.flatMap((shot) => {
            const version = shot.versions.find((item) => item.id === shot.selected_version_id);
            return shot.selected_playable && version?.playable && version.source
                ? [{ ...version, shot }] : [];
        }).sort((a, b) => a.timeline_in_frame - b.timeline_in_frame);
    }

    renderShots() {
        const container = this.dialog.querySelector(".xp-shots");
        container.replaceChildren();
        this.shotRows.clear();
        const accepted = this.manifest.shots.filter((shot) =>
            shot.selected_playable && shot.selection_confirmed).length;
        this.dialog.querySelector(".xp-progress").textContent =
            `${accepted} / ${this.manifest.shots.length} 镜已验收`;
        for (const shot of this.manifest.shots) {
            const row = nodeElement("article", "xp-shot");
            row.dataset.shotUid = shot.shot_uid;
            const header = nodeElement("button", "xp-shot-header");
            header.type = "button";
            const label = nodeElement("strong", "", shot.selected_label || shot.label);
            const detail = nodeElement("span", "", `${this.manifest.tracks[shot.track]} 轨`);
            header.append(label, detail);
            header.addEventListener("click", () => {
                this.activeShotUid = shot.shot_uid;
                const chosen = shot.versions.find((item) => item.id === shot.selected_version_id);
                this.seekFrame(chosen?.timeline_in_frame ?? shot.planned_start_frame);
            });
            row.append(header);
            const choices = nodeElement("div", "xp-versions");
            const completed = shot.versions.filter((item) => item.status === "completed");
            if (!completed.length) choices.append(nodeElement("span", "xp-missing", "尚无成功素材"));
            for (const version of shot.versions) {
                const failed = version.status === "failed";
                const number = `v${version.number || "?"}`;
                const transitionName = {
                    hard_cut: "切镜", long_take: "续写", crossfade: "叠化",
                }[version.incoming_transition] || "切镜";
                const history = `生成时 ${version.editorial_label || shot.label} · ${transitionName}`;
                const button = nodeElement("button", "xp-version", number);
                button.type = "button";
                button.disabled = !version.playable;
                button.setAttribute("aria-pressed", String(version.id === shot.selected_version_id));
                button.setAttribute("aria-label", `${shot.selected_label || shot.label} ${number}${failed ? "，生成失败" : version.status !== "completed" ? "，生成中" : version.playable ? "" : "，素材不可用"}`);
                button.title = version.playable
                    ? `选择 ${history} ${number}${version.parent_version_id
                        ? version.independent_edit_allowed
                            ? "；此版曾断开续写，可与其他前段切接，选回原父版仍可续写"
                            : "；此续写版只接生成时的原父版"
                        : ""}${version.transition_recorded ? "" : "；旧版本接法根据记录推断"}`
                    : failed ? `生成失败：${version.error || "请在主工作台查看任务记录"}`
                        : "尚未完成，或素材文件缺失／交付帧范围无效";
                button.addEventListener("click", () => this.selectVersion(shot, version));
                choices.append(button);
            }
            row.append(choices);
            if (shot.selected_version_id && (!shot.selected_playable || !shot.selection_confirmed)) {
                row.append(nodeElement("p", "xp-warning", shot.selection_confirmed
                    ? shot.selection_issue || "所选版本暂不能预览"
                    : "多个版本，请明确确认最终版本"));
            }
            container.append(row);
            this.shotRows.set(shot.shot_uid, row);
        }
    }

    async selectVersion(shot, version) {
        if (!version.playable) return;
        this.status(`确认 ${shot.selected_label || shot.label} v${version.number}…`);
        try {
            const shotsPane = this.dialog.querySelector(".xp-shots");
            const subtitlesPane = this.dialog.querySelector(".xp-subtitles");
            const timelinePane = this.dialog.querySelector(".xp-timeline-scroll");
            const response = await api.fetchApi("/xiaoyue-mv/preview/select", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    project_id: this.projectId,
                    shot_uid: shot.shot_uid,
                    version_id: version.id,
                }),
            });
            const result = await responseJson(response);
            const positions = [shotsPane, subtitlesPane, timelinePane].map((pane) =>
                ({ top: pane.scrollTop, left: pane.scrollLeft }));
            this.activeShotUid = shot.shot_uid;
            this.suppressFollowScroll = true;
            try {
                this.applyManifest(result.manifest);
                [shotsPane, subtitlesPane, timelinePane].forEach((pane, index) => {
                    pane.scrollTop = positions[index].top;
                    pane.scrollLeft = positions[index].left;
                });
            } finally {
                this.suppressFollowScroll = false;
            }
            const selectedRow = this.shotRows.get(shot.shot_uid);
            selectedRow?.querySelector('.xp-version[aria-pressed="true"]')?.focus({ preventScroll: true });
            const count = Object.keys(result.selected_ancestors || {}).length;
            const currentLabel = result.manifest.shots.find((item) => item.shot_uid === shot.shot_uid)?.selected_label
                || shot.selected_label || shot.label;
            this.status(count > 1 ? `已选 ${currentLabel}，并同步 ${count - 1} 个续写前段` : `已选 ${currentLabel} v${version.number}`);
        } catch (error) {
            this.status(error.message || String(error), true);
        }
    }

    renderSubtitles() {
        const container = this.dialog.querySelector(".xp-subtitles");
        container.replaceChildren();
        this.subtitleRows = [];
        if (!this.manifest.subtitles.length) {
            container.append(nodeElement("p", "xp-missing", "还没有正式字幕。"));
            return;
        }
        for (const cue of this.manifest.subtitles) {
            const button = nodeElement("button", "xp-subtitle-row");
            button.type = "button";
            button.dataset.start = String(cue.start_frame);
            button.dataset.end = String(cue.end_frame_exclusive);
            button.append(
                nodeElement("span", "xp-cue-time", `${cue.shot} · ${timecode(cue.start_frame)}`),
                nodeElement("span", "xp-cue-text", cue.text),
            );
            button.addEventListener("click", () => this.seekFrame(cue.start_frame));
            container.append(button);
            this.subtitleRows.push(button);
        }
    }

    shotAtFrame(frame) {
        let current = null;
        let latestStart = -1;
        for (const shot of this.manifest.shots) {
            const version = shot.versions.find((item) => item.id === shot.selected_version_id);
            const start = version?.timeline_in_frame ?? shot.planned_start_frame;
            if (start <= frame && start >= latestStart) {
                current = shot;
                latestStart = start;
            }
        }
        return current || this.manifest.shots[0];
    }

    followListRow(container, row) {
        if (this.suppressFollowScroll || !row || !container.clientHeight) return;
        const viewport = container.getBoundingClientRect();
        const target = row.getBoundingClientRect();
        const delta = target.top + target.height / 2 - viewport.top - container.clientHeight / 2;
        if (Math.abs(delta) < 2) return;
        container.scrollTo({
            top: container.scrollTop + delta,
            behavior: this.audio.paused || this.timelinePointerId !== null || this.reduceMotion
                ? "auto" : "smooth",
        });
    }

    followPlaybackLists(frame) {
        const shot = this.shotAtFrame(frame);
        const shotUid = shot?.shot_uid || "";
        if (shotUid !== this.followedShotUid) {
            const previous = this.shotRows.get(this.followedShotUid);
            previous?.classList.remove("is-current");
            previous?.querySelector(".xp-shot-header")?.removeAttribute("aria-current");
            const row = this.shotRows.get(shotUid);
            row?.classList.add("is-current");
            row?.querySelector(".xp-shot-header")?.setAttribute("aria-current", "true");
            this.followedShotUid = shotUid;
            this.followListRow(this.dialog.querySelector(".xp-shots"), row);
        }

        const cues = this.manifest.subtitles;
        let activeIndex = -1;
        let followIndex = cues.length ? 0 : -1;
        for (let index = 0; index < cues.length; index += 1) {
            if (cues[index].start_frame > frame) break;
            followIndex = index;
            if (activeIndex < 0 && frame < cues[index].end_frame_exclusive) {
                activeIndex = index;
            }
        }
        for (let index = 0; index < cues.length; index += 1) {
            this.subtitleRows[index]?.classList.toggle("is-active",
                frame >= cues[index].start_frame && frame < cues[index].end_frame_exclusive);
        }
        if (activeIndex >= 0) followIndex = activeIndex;
        if (followIndex !== this.followedSubtitleIndex) {
            this.followedSubtitleIndex = followIndex;
            this.followListRow(this.dialog.querySelector(".xp-subtitles"),
                this.subtitleRows[followIndex]);
        }
        this.dialog.querySelector(".xp-caption").textContent =
            activeIndex >= 0 ? cues[activeIndex].text : "";
    }

    renderTimeline() {
        if (!this.manifest) return;
        const viewport = this.dialog.querySelector(".xp-timeline-scroll");
        const seconds = this.manifest.total_frames / this.manifest.fps;
        const pxPerSecond = this.fit
            ? Math.max(6, (viewport.clientWidth - TRACK_GUTTER - 16) / seconds)
            : this.pixelsPerSecond;
        this.pixelsPerFrame = pxPerSecond / this.manifest.fps;
        const width = Math.max(viewport.clientWidth - 2,
            TRACK_GUTTER + this.manifest.total_frames * this.pixelsPerFrame);
        const inner = this.dialog.querySelector(".xp-timeline-inner");
        inner.style.width = `${width}px`;
        const ruler = this.dialog.querySelector(".xp-ruler");
        ruler.replaceChildren();
        ruler.append(nodeElement("span", "xp-ruler-gutter"));
        const tickSeconds = pxPerSecond < 25 ? 20 : pxPerSecond < 75 ? 10 : 5;
        for (let second = 0; second <= seconds; second += tickSeconds) {
            const tick = nodeElement("span", "xp-tick", timecode(second * this.manifest.fps));
            tick.style.left = `${TRACK_GUTTER + second * pxPerSecond}px`;
            ruler.append(tick);
        }
        const rows = this.dialog.querySelector(".xp-track-list");
        rows.replaceChildren();
        const maxTrack = this.manifest.tracks.length - 1;
        for (let track = maxTrack; track >= 0; track -= 1) {
            const row = nodeElement("div", "xp-track-row");
            row.append(nodeElement("span", "xp-track-name", this.manifest.tracks[track]));
            for (const shot of this.manifest.shots.filter((item) => item.track === track)) {
                const version = shot.versions.find((item) => item.id === shot.selected_version_id);
                const start = version?.timeline_in_frame ?? shot.planned_start_frame;
                const end = version?.timeline_out_frame_exclusive ?? shot.planned_end_frame_exclusive;
                const label = shot.selected_label || shot.label;
                const isCrossfade = shot.incoming_transition === "crossfade";
                const clip = nodeElement("button", "xp-clip", `${label}${version?.number ? ` · v${version.number}` : " · 缺素材"}${isCrossfade ? " · 叠化" : ""}`);
                clip.type = "button";
                clip.classList.toggle("is-missing", !shot.selected_playable);
                clip.classList.toggle("is-crossfade", isCrossfade);
                clip.style.left = `${TRACK_GUTTER + start * this.pixelsPerFrame}px`;
                clip.style.width = `${Math.max(5, (end - start) * this.pixelsPerFrame)}px`;
                clip.title = `${label} · ${timecode(start)}–${timecode(end)} · ${this.manifest.tracks[track]} 轨${isCrossfade ? " · 与前段叠化" : ""}${shot.selection_issue ? ` · ${shot.selection_issue}` : ""}`;
                clip.addEventListener("click", () => {
                    this.activeShotUid = shot.shot_uid;
                    this.seekFrame(start);
                    this.dialog.querySelector(`[data-shot-uid="${shot.shot_uid}"]`)?.scrollIntoView({ block: "nearest" });
                });
                row.append(clip);
            }
            rows.append(row);
        }
        this.renderFrame();
    }

    seekFrame(frame) {
        if (!this.manifest) return;
        const bounded = Math.max(0, Math.min(this.manifest.total_frames - 1, Math.floor(frame)));
        this.audio.currentTime = bounded / this.manifest.fps;
        this.renderFrame();
    }

    seekTimelinePointer(clientX) {
        const x = clientX - this.timelineInner.getBoundingClientRect().left - TRACK_GUTTER;
        this.seekFrame(Math.round(x / this.pixelsPerFrame));
    }

    startTimelineScrub(event) {
        if (!this.manifest || event.button !== 0 || this.timelinePointerId !== null
                || event.target.closest(".xp-clip, .xp-track-name, .xp-ruler-gutter")) return;
        this.timelinePointerId = event.pointerId;
        this.timelineWasPlaying = !this.audio.paused;
        if (this.timelineWasPlaying) this.pause();
        this.timelineInner.setPointerCapture(event.pointerId);
        this.timelineInner.classList.add("is-scrubbing");
        if (event.target.closest(".xp-ruler")) this.timelineRuler.focus({ preventScroll: true });
        this.seekTimelinePointer(event.clientX);
        event.preventDefault();
    }

    moveTimelineScrub(event) {
        if (event.pointerId !== this.timelinePointerId) return;
        this.seekTimelinePointer(event.clientX);
        event.preventDefault();
    }

    endTimelineScrub(event) {
        if (event.pointerId !== this.timelinePointerId) return;
        const resume = this.timelineWasPlaying;
        this.timelinePointerId = null;
        this.timelineWasPlaying = false;
        this.timelineInner.classList.remove("is-scrubbing");
        if (this.timelineInner.hasPointerCapture(event.pointerId)) {
            this.timelineInner.releasePointerCapture(event.pointerId);
        }
        if (resume) this.togglePlayback();
    }

    timelineKeydown(event) {
        if (!this.manifest) return;
        const frame = Math.floor(this.audio.currentTime * this.manifest.fps);
        let target;
        if (event.key === "ArrowLeft") target = frame - (event.shiftKey ? 6 : 1);
        else if (event.key === "ArrowRight") target = frame + (event.shiftKey ? 6 : 1);
        else if (event.key === "Home") target = 0;
        else if (event.key === "End") target = this.manifest.total_frames - 1;
        else return;
        event.preventDefault();
        this.seekFrame(target);
    }

    handleShortcut(event) {
        if ((event.code !== "Space" && event.key !== " ") || event.repeat
                || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.target.closest("button, a, input, textarea, select, summary, video, [contenteditable]:not([contenteditable='false'])")) return;
        event.preventDefault();
        if (this.activeTab === "exports") {
            if (!this.exportVideo.hidden) {
                if (this.exportVideo.paused) this.exportVideo.play().catch(() => {});
                else this.exportVideo.pause();
            }
        } else this.togglePlayback();
    }

    async togglePlayback() {
        if (!this.manifest) return;
        if (!this.audio.paused) { this.pause(); return; }
        this.exportVideo.pause();
        try {
            await this.audio.play();
            this.dialog.querySelector(".xp-play").textContent = "暂停";
            this.tick();
        } catch (error) {
            this.status(`原曲无法播放：${error.message || error}`, true);
        }
    }

    pause() {
        this.audio.pause();
        cancelAnimationFrame(this.raf);
        for (const video of this.pool.values()) video.pause();
        this.dialog.querySelector(".xp-play").textContent = "播放";
        this.renderFrame();
    }

    tick() {
        this.renderFrame();
        if (!this.audio.paused) this.raf = requestAnimationFrame(() => this.tick());
    }

    ensureVideo(clip) {
        let video = this.pool.get(clip.id);
        if (video) return video;
        video = nodeElement("video", "xp-video");
        video.width = 1280;
        video.height = 720;
        video.muted = true;
        video.defaultMuted = true;
        video.playsInline = true;
        video.preload = "metadata";
        video.src = mediaUrl(clip.source);
        video.addEventListener("loadedmetadata", () => {
            try { video.currentTime = clip.source_in_frame / this.manifest.fps; } catch { /* Browser may still be seeking. */ }
        }, { once: true });
        video.addEventListener("error", () => this.status(`镜头 ${clip.shot.label} 无法解码，请检查视频格式。`, true));
        this.stage.prepend(video);
        this.pool.set(clip.id, video);
        return video;
    }

    renderFrame() {
        if (!this.manifest || !this.pixelsPerFrame) return;
        const frame = Math.max(0, Math.min(this.manifest.total_frames - 1,
            Math.floor(this.audio.currentTime * this.manifest.fps)));
        this.scrub.value = String(frame);
        this.timelineRuler.setAttribute("aria-valuemax", String(this.manifest.total_frames - 1));
        this.timelineRuler.setAttribute("aria-valuenow", String(frame));
        this.timelineRuler.setAttribute("aria-valuetext", timecode(frame, this.manifest.fps));
        this.dialog.querySelector(".xp-time").textContent =
            `${timecode(frame, this.manifest.fps)} / ${timecode(this.manifest.total_frames, this.manifest.fps)}`;
        const playheadX = TRACK_GUTTER + frame * this.pixelsPerFrame;
        this.dialog.querySelector(".xp-playhead").style.left = `${playheadX}px`;
        const viewport = this.dialog.querySelector(".xp-timeline-scroll");
        if (!this.suppressFollowScroll && !this.fit && this.timelinePointerId === null && (playheadX < viewport.scrollLeft + 30
                || playheadX > viewport.scrollLeft + viewport.clientWidth - 30)) {
            viewport.scrollLeft = Math.max(0, playheadX - viewport.clientWidth * .25);
        }
        const clips = this.selectedClips();
        const active = clips.filter((clip) => frame >= clip.timeline_in_frame
            && frame < clip.timeline_out_frame_exclusive).slice(-2);
        const upcoming = clips.filter((clip) => clip.timeline_in_frame > frame
            && !active.some((item) => item.id === clip.id)).slice(0, 3);
        const keep = new Set([...active, ...upcoming].map((clip) => clip.id));
        for (const [id, video] of this.pool) {
            if (keep.has(id)) continue;
            video.pause();
            video.removeAttribute("src");
            video.load();
            video.remove();
            this.pool.delete(id);
        }
        for (const clip of upcoming) {
            const video = this.ensureVideo(clip);
            video.pause();
            video.style.visibility = "hidden";
        }
        const blend = active.length === 2 && active[1].shot.incoming_transition === "crossfade";
        const blendEnd = blend ? Math.min(active[0].timeline_out_frame_exclusive,
            active[1].timeline_out_frame_exclusive) : 0;
        active.forEach((clip, index) => {
            const video = this.ensureVideo(clip);
            const desired = (clip.source_in_frame + frame - clip.timeline_in_frame) / this.manifest.fps;
            if (video.readyState >= 1 && Math.abs(video.currentTime - desired) > 0.14) {
                video.currentTime = desired;
            }
            video.style.visibility = !blend && index === 0 && active.length === 2 ? "hidden" : "visible";
            video.style.zIndex = String(index + 1);
            video.style.opacity = blend && index === 1
                ? String(Math.max(0, Math.min(1,
                    (frame - clip.timeline_in_frame) / Math.max(1, blendEnd - clip.timeline_in_frame))))
                : "1";
            if (!this.audio.paused && video.style.visibility !== "hidden") {
                if (video.paused) video.play().catch(() => {});
            } else if (!video.paused) video.pause();
        });
        this.stage.querySelector(".xp-empty").hidden = active.length > 0;
        this.followPlaybackLists(frame);
    }

    destroy() {
        clearTimeout(this.exportTimer);
        cancelAnimationFrame(this.raf);
        this.clearExportVideo();
        this.audio.pause();
        this.audio.removeAttribute("src");
        for (const video of this.pool.values()) {
            video.pause();
            video.removeAttribute("src");
            video.load();
        }
        this.pool.clear();
        this.dialog.remove();
        openDesks.delete(this.node);
    }
}

app.registerExtension({
    name: "xiaoyue.mv.preview_desk",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (![NODE_NAME, WORKBENCH_NODE_NAME].includes(nodeData.name)) return;
        installStyles();
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            onCreated?.apply(this, arguments);
            this.addWidget("button", nodeData.name === WORKBENCH_NODE_NAME
                ? "③ 剪辑台" : "最终验收剪辑台", null, () => {
                const existing = openDesks.get(this);
                if (existing?.dialog?.open) return;
                const projectId = linkedProjectId(this) || this._xiaoyuePreviewProjectId;
                if (!projectId) {
                    app.extensionManager?.toast?.add?.({
                        severity: "warn", summary: "剪辑台尚未接入工程",
                        detail: nodeData.name === WORKBENCH_NODE_NAME
                            ? "请先在工作台创建或打开项目。"
                            : "请连接 MV 项目工作台的 mv_plan，并先运行或保存计划。",
                        life: 7000,
                    });
                    return;
                }
                openDesks.set(this, new PreviewDesk(this, projectId));
            }, { serialize: false });
            this.setSize([Math.max(this.size?.[0] || 0, 330), Math.max(this.size?.[1] || 0, 110)]);
        };
        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            onExecuted?.apply(this, arguments);
            const item = message?.xiaoyue_mv_preview_desk?.[0];
            if (item?.project_id) this._xiaoyuePreviewProjectId = String(item.project_id);
        };
    },
});
