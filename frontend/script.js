const API_BASE_URL = localStorage.getItem("assistantApiBaseUrl") || "http://127.0.0.1:8001";

const chatForm = document.querySelector("#chatForm");
const uploadForm = document.querySelector("#uploadForm");
const messageInput = document.querySelector("#messageInput");
const messages = document.querySelector("#messages");
const uploadStatus = document.querySelector("#uploadStatus");
const apiBaseUrlInput = document.querySelector("#apiBaseUrl");
const userIdInput = document.querySelector("#userId");
const sendButton = document.querySelector("#sendButton");
const refreshDataButton = document.querySelector("#refreshDataButton");
const memoryList = document.querySelector("#memoryList");
const appointmentsList = document.querySelector("#appointmentsList");
const productsList = document.querySelector("#productsList");

let apiBaseUrl = normalizeApiBaseUrl(API_BASE_URL);

function normalizeApiBaseUrl(value) {
  return value.trim().replace(/\/+$/, "");
}

function friendlyErrorMessage(error) {
  if (error instanceof TypeError && error.message.toLowerCase().includes("fetch")) {
    return `Cannot reach backend at ${getApiBaseUrl()}. Start it with: backend\\venv\\Scripts\\python.exe -m uvicorn backend.main:app --reload --port 8001`;
  }

  return error.message || "Request failed.";
}

function getUserId() {
  return userIdInput.value.trim() || "demo-user";
}

function getApiBaseUrl() {
  const configuredValue = normalizeApiBaseUrl(apiBaseUrlInput.value);
  return configuredValue || apiBaseUrl;
}

function setBusy(isBusy) {
  sendButton.disabled = isBusy;
  sendButton.textContent = isBusy ? "Thinking..." : "Send";
}

async function readResponse(response) {
  const payload = await response.json().catch(() => ({
    detail: response.statusText || "Unexpected server response.",
  }));

  if (!response.ok) {
    throw new Error(payload.detail || "Request failed.");
  }

  return payload;
}

async function checkApiHealth() {
  try {
    const response = await fetch(`${getApiBaseUrl()}/health`);
    await readResponse(response);
    uploadStatus.textContent = `Backend connected: ${getApiBaseUrl()}`;
  } catch (error) {
    uploadStatus.textContent = friendlyErrorMessage(error);
  }
}

function appendMessage(role, text, sources = [], actions = []) {
  const article = document.createElement("article");
  article.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = role === "user" ? "You" : "AI";

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  const paragraph = document.createElement("p");
  paragraph.textContent = text;
  bubble.appendChild(paragraph);

  if (sources.length) {
    bubble.appendChild(renderSources(sources));
  }

  if (actions.length) {
    bubble.appendChild(renderActions(actions));
  }

  article.append(avatar, bubble);
  messages.appendChild(article);
  messages.scrollTop = messages.scrollHeight;
}

function renderListState(container, items, renderItem, emptyText) {
  container.replaceChildren();

  if (!items.length) {
    container.textContent = emptyText;
    container.classList.add("empty-state");
    return;
  }

  container.classList.remove("empty-state");
  items.forEach((item) => {
    container.appendChild(renderItem(item));
  });
}

function createDataItem(title, meta, body) {
  const article = document.createElement("article");
  article.className = "data-item";

  const heading = document.createElement("strong");
  heading.textContent = title;

  const metaLine = document.createElement("span");
  metaLine.className = "data-meta";
  metaLine.textContent = meta;

  const paragraph = document.createElement("p");
  paragraph.textContent = body;

  article.append(heading, metaLine, paragraph);
  return article;
}

async function loadDashboardData() {
  const userId = getUserId();

  try {
    const [memoryResponse, appointmentsResponse, productsResponse] = await Promise.all([
      fetch(`${getApiBaseUrl()}/memory/${encodeURIComponent(userId)}?limit=6`),
      fetch(`${getApiBaseUrl()}/appointments/${encodeURIComponent(userId)}`),
      fetch(`${getApiBaseUrl()}/products?limit=6`),
    ]);

    const [memoryPayload, appointmentsPayload, productsPayload] = await Promise.all([
      readResponse(memoryResponse),
      readResponse(appointmentsResponse),
      readResponse(productsResponse),
    ]);

    renderListState(
      memoryList,
      memoryPayload.messages || [],
      (item) =>
        createDataItem(
          item.role.toUpperCase(),
          new Date(item.created_at).toLocaleString(),
          item.content,
        ),
      "No conversation history yet.",
    );

    renderListState(
      appointmentsList,
      appointmentsPayload.appointments || [],
      (item) =>
        createDataItem(
          item.customer_name,
          `${item.date} at ${item.time}`,
          item.topic,
        ),
      "No appointments booked yet.",
    );

    renderListState(
      productsList,
      productsPayload.products || [],
      (item) =>
        createDataItem(
          item.name,
          item.price,
          item.description,
        ),
      "No products found.",
    );
  } catch (error) {
    renderListState(memoryList, [], () => null, "Unable to load memory.");
    renderListState(appointmentsList, [], () => null, "Unable to load appointments.");
    renderListState(productsList, [], () => null, "Unable to load products.");
    uploadStatus.textContent = friendlyErrorMessage(error);
  }
}

function renderSources(sources) {
  const details = document.createElement("details");
  details.className = "sources";

  const summary = document.createElement("summary");
  summary.textContent = `Sources (${sources.length})`;
  details.appendChild(summary);

  sources.forEach((source) => {
    const item = document.createElement("div");
    item.className = "source-item";
    const numericScore = Number(source.score);
    const scoreText = Number.isFinite(numericScore) ? numericScore.toFixed(2) : "n/a";
    item.textContent = `${source.source} | chunk ${source.chunk_index} | score ${scoreText}: ${source.snippet}`;
    details.appendChild(item);
  });

  return details;
}

function renderActions(actions) {
  const wrapper = document.createElement("div");
  wrapper.className = "actions";

  actions.forEach((action) => {
    const card = document.createElement("div");
    card.className = "action-card";

    const title = document.createElement("strong");
    title.textContent = `Action: ${action.name}`;

    const body = document.createElement("pre");
    body.textContent = JSON.stringify(action.result, null, 2);

    card.append(title, body);
    wrapper.appendChild(card);
  });

  return wrapper;
}

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  appendMessage("user", text);
  messageInput.value = "";
  setBusy(true);

  try {
    const response = await fetch(`${getApiBaseUrl()}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: getUserId(), message: text }),
    });
    const data = await readResponse(response);
    appendMessage("assistant", data.answer || data.reply || "No response.", data.sources, data.actions);
    await loadDashboardData();
  } catch (error) {
    appendMessage("assistant", `Error: ${friendlyErrorMessage(error)}`);
  } finally {
    setBusy(false);
    messageInput.focus();
  }
});

uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.querySelector("#pdfFile");
  const file = fileInput.files[0];
  if (!file) return;

  const formData = new FormData();
  formData.append("file", file);
  formData.append("user_id", getUserId());

  uploadStatus.textContent = "Indexing document...";
  uploadStatus.classList.add("loading");

  try {
    const response = await fetch(`${getApiBaseUrl()}/upload`, {
      method: "POST",
      body: formData,
    });
    const data = await readResponse(response);
    uploadStatus.textContent = data.message;
    uploadForm.reset();
    await loadDashboardData();
  } catch (error) {
    uploadStatus.textContent = `Upload failed: ${friendlyErrorMessage(error)}`;
  } finally {
    uploadStatus.classList.remove("loading");
  }
});

apiBaseUrlInput.value = apiBaseUrl;
apiBaseUrlInput.addEventListener("change", async () => {
  apiBaseUrl = getApiBaseUrl();
  apiBaseUrlInput.value = apiBaseUrl;
  localStorage.setItem("assistantApiBaseUrl", apiBaseUrl);
  await checkApiHealth();
  await loadDashboardData();
});

userIdInput.addEventListener("change", async () => {
  await loadDashboardData();
});

refreshDataButton.addEventListener("click", async () => {
  await checkApiHealth();
  await loadDashboardData();
});

checkApiHealth();
loadDashboardData();
