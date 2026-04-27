const STATUS_PROGRESS = {
  idle: 0,
  queued: 5,
  processing: 20,
  converting: 55,
  analyzing: 85,
  completed: 100,
  failed: 100,
};

const STORAGE_KEY = "cad-text-mover-active-job";
const DEFAULT_OPTIONS = { all_layouts: true };
const POLL_INTERVAL_MS = 2000;
const MAX_TRANSIENT_POLL_ERRORS = 8;

const form = document.getElementById("upload-form");
const fileInput = document.getElementById("file-input");
const fileLabel = document.getElementById("file-label");
const uploadButton = document.getElementById("upload-button");
const jobPanel = document.getElementById("job-panel");
const jobStatus = document.getElementById("job-status");
const jobFile = document.getElementById("job-file");
const jobMessage = document.getElementById("job-message");
const progressFill = document.getElementById("progress-fill");
const progressBar = document.querySelector(".progress");
const downloadActions = document.getElementById("download-actions");
const downloadLink = document.getElementById("download-link");

let pollTimer = null;
let activeJobId = null;
let activeFileName = "";
let transientPollErrors = 0;

function setStatus(status, message = "") {
  const progress = STATUS_PROGRESS[status] ?? 0;
  const normalizedStatus = status ? status.charAt(0).toUpperCase() + status.slice(1) : "Idle";

  jobStatus.textContent = normalizedStatus;
  jobMessage.textContent = message;
  progressFill.style.width = `${progress}%`;
  progressBar.setAttribute("aria-valuenow", String(progress));

  form.classList.toggle("is-complete", status === "completed");
  form.classList.toggle("is-failed", status === "failed");
}

function resetIdleState() {
  setStatus("idle", "");
  jobStatus.textContent = "Idle";
  jobMessage.textContent = "";
}

function setBusy(isBusy) {
  uploadButton.disabled = isBusy;
  uploadButton.textContent = isBusy ? "Uploading..." : "Upload";
}

function showJobPanel() {
  jobPanel.classList.remove("hidden");
}

function hideDownloads() {
  downloadActions.classList.add("hidden");
  downloadLink.removeAttribute("href");
}

function showDownload(link) {
  downloadLink.href = link;
  downloadActions.classList.remove("hidden");
}

function saveActiveJob(jobId, fileName) {
  activeJobId = jobId;
  activeFileName = fileName || activeFileName;
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId: activeJobId, fileName: activeFileName }));
}

function clearActiveJob() {
  activeJobId = null;
  activeFileName = "";
  localStorage.removeItem(STORAGE_KEY);
}

function loadActiveJob() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function schedulePoll(delay = POLL_INTERVAL_MS) {
  stopPolling();
  pollTimer = setTimeout(() => {
    if (activeJobId) {
      pollJob(activeJobId).catch((error) => {
        handlePollError(error);
      });
    }
  }, delay);
}

async function parseJsonResponse(response) {
  const rawText = await response.text();

  if (!rawText || !rawText.trim()) {
    const err = new Error("Empty response from server");
    err.isTransient = true;
    throw err;
  }

  try {
    return JSON.parse(rawText);
  } catch {
    const err = new Error(response.ok ? "Server returned an invalid response" : "Server returned a non-JSON error response");
    err.isTransient = response.status >= 500 || response.status === 0;
    throw err;
  }
}

async function fetchJson(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      ...options,
      headers: {
        Accept: "application/json",
        ...(options.headers || {}),
      },
    });
  } catch {
    const err = new Error("Network connection interrupted");
    err.isTransient = true;
    throw err;
  }

  const payload = await parseJsonResponse(response);

  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : `Request failed (${response.status})`;
    const err = new Error(detail);
    err.status = response.status;
    err.isTransient = response.status >= 500 || response.status === 429;
    throw err;
  }

  return payload;
}

async function createJob(file) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("cloudconvert_options", JSON.stringify(DEFAULT_OPTIONS));
  return fetchJson("/v1/jobs", {
    method: "POST",
    body: formData,
  });
}

async function fetchJob(jobId) {
  return fetchJson(`/v1/jobs/${jobId}`);
}

function handlePollError(error) {
  transientPollErrors += 1;

  if (transientPollErrors <= MAX_TRANSIENT_POLL_ERRORS && (error.isTransient || !navigator.onLine)) {
    setStatus("processing", "Reconnecting...");
    schedulePoll(Math.min(8000, POLL_INTERVAL_MS * transientPollErrors));
    return;
  }

  setStatus("failed", error.message || "Polling failed");
  setBusy(false);
  hideDownloads();
}

async function pollJob(jobId) {
  const job = await fetchJob(jobId);
  transientPollErrors = 0;
  showJobPanel();
  jobFile.textContent = job.input_filename || activeFileName;

  if (job.status === "failed") {
    setStatus("failed", job.error_message || "Processing failed");
    setBusy(false);
    hideDownloads();
    clearActiveJob();
    return;
  }

  if (job.status === "completed") {
    setStatus("completed", "Ready");
    setBusy(false);
    if (job.links && job.links.output_pdf) {
      showDownload(job.links.output_pdf);
    } else {
      hideDownloads();
    }
    clearActiveJob();
    return;
  }

  hideDownloads();
  const statusMessage = {
    queued: "Waiting",
    processing: "Starting",
    converting: "Converting",
    analyzing: "Processing",
  }[job.status] || "Working";

  setStatus(job.status, statusMessage);
  schedulePoll();
}

fileInput.addEventListener("change", () => {
  const [file] = fileInput.files;
  fileLabel.textContent = file ? file.name : "Select DWG / DXF / DWF";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const [file] = fileInput.files;
  if (!file) {
    return;
  }

  stopPolling();
  transientPollErrors = 0;
  setBusy(true);
  showJobPanel();
  hideDownloads();
  jobFile.textContent = file.name;
  setStatus("processing", "Creating job");

  try {
    const job = await createJob(file);
    saveActiveJob(job.id, file.name);
    setStatus(job.status, "Waiting");
    await pollJob(job.id);
  } catch (error) {
    setStatus("failed", error.message || "Upload failed");
    setBusy(false);
  }
});

(function resumeJobIfNeeded() {
  const saved = loadActiveJob();
  if (!saved || !saved.jobId) {
    resetIdleState();
    return;
  }

  showJobPanel();
  activeFileName = saved.fileName || "";
  jobFile.textContent = activeFileName;
  setStatus("processing", "Resuming");
  saveActiveJob(saved.jobId, activeFileName);
  pollJob(saved.jobId).catch((error) => {
    handlePollError(error);
    setBusy(false);
  });
})();
