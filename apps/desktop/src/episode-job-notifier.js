const http = require("node:http");
const https = require("node:https");

const DEFAULT_POLL_INTERVAL_MS = 5000;
const TERMINAL_JOB_STATUSES = new Set(["completed", "failed", "interrupted"]);

function startEpisodeJobNotifier({
  apiUrl,
  notify,
  writeLog = () => {},
  bootTime = new Date(),
  pollIntervalMs = DEFAULT_POLL_INTERVAL_MS,
}) {
  const observedActiveEpisodeJobs = new Set();
  const notifiedEpisodeJobs = new Set();

  const poll = () => {
    pollEpisodeJobStatus({
      apiUrl,
      bootTime,
      notify,
      notifiedEpisodeJobs,
      observedActiveEpisodeJobs,
    }).catch((error) => {
      writeLog(`episode job notifier skipped: ${formatError(error)}`);
    });
  };

  poll();
  const timer = setInterval(poll, pollIntervalMs);
  if (typeof timer.unref === "function") {
    timer.unref();
  }

  return {
    stop() {
      clearInterval(timer);
    },
  };
}

async function pollEpisodeJobStatus({
  apiUrl,
  bootTime,
  notify,
  notifiedEpisodeJobs,
  observedActiveEpisodeJobs,
}) {
  const episode = await getJson(`${apiUrl}/api/episodes/today`);
  notifyTerminalEpisodeJob(episode, {
    activeStatuses: new Set(["processing"]),
    bootTime,
    error: episode.topic_job_error,
    finished_at: episode.topic_job_finished_at,
    message: episode.topic_job_message,
    notifiedEpisodeJobs,
    notify,
    observedActiveEpisodeJobs,
    status: episode.topic_job_status,
    title: "选题生成",
    type: "topic",
  });
  notifyTerminalEpisodeJob(episode, {
    activeStatuses: new Set(["processing", "drafting"]),
    bootTime,
    error: episode.video_job_error,
    finished_at: episode.video_job_finished_at,
    message: episode.video_job_message,
    notifiedEpisodeJobs,
    notify,
    observedActiveEpisodeJobs,
    status: episode.video_job_status,
    title: "视频处理",
    type: "video",
  });
}

function notifyTerminalEpisodeJob(episode, job) {
  if (!episode?.id || !job.status) {
    return;
  }
  const activeKey = `${episode.id}:${job.type}`;
  if (job.activeStatuses.has(job.status)) {
    job.observedActiveEpisodeJobs.add(activeKey);
    return;
  }
  if (!TERMINAL_JOB_STATUSES.has(job.status)) {
    return;
  }
  const finishedAt = job.finished_at || "";
  if (!job.observedActiveEpisodeJobs.has(activeKey) && !isRecentJobFinish(finishedAt, job.bootTime)) {
    return;
  }
  const notificationKey = `${activeKey}:${job.status}:${finishedAt}`;
  if (job.notifiedEpisodeJobs.has(notificationKey)) {
    return;
  }
  job.notifiedEpisodeJobs.add(notificationKey);
  job.notify(
    `AI视频日记 · ${job.title}${episodeJobStatusLabel(job.status)}`,
    episodeJobNotificationBody(job),
  );
}

function isRecentJobFinish(finishedAt, bootTime) {
  if (!finishedAt) {
    return false;
  }
  const finishedDate = new Date(finishedAt);
  return !Number.isNaN(finishedDate.getTime()) && finishedDate >= bootTime;
}

function episodeJobStatusLabel(status) {
  return {
    completed: "完成",
    failed: "失败",
    interrupted: "中断",
  }[status] || status;
}

function episodeJobNotificationBody(job) {
  const message = job.message || episodeJobStatusLabel(job.status);
  const error = job.error ? `：${truncateText(job.error, 120)}` : "";
  return `${message}${error}`;
}

function truncateText(value, maxLength) {
  const text = String(value);
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function getJson(url) {
  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const client = parsedUrl.protocol === "https:" ? https : http;
    const request = client.get(parsedUrl, (response) => {
      let payload = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => {
        payload += chunk;
      });
      response.on("end", () => {
        if ((response.statusCode ?? 500) < 200 || (response.statusCode ?? 500) >= 300) {
          reject(new Error(`GET ${url} returned ${response.statusCode}`));
          return;
        }
        try {
          resolve(JSON.parse(payload));
        } catch (error) {
          reject(error);
        }
      });
    });
    request.setTimeout(4000, () => request.destroy(new Error(`GET ${url} timed out`)));
    request.on("error", reject);
  });
}

function formatError(error) {
  if (error instanceof Error) {
    return error.stack || error.message;
  }
  return String(error);
}

module.exports = {
  startEpisodeJobNotifier,
};
