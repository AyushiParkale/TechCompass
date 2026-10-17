const apiBase = window.TECHCOMPASS_API_URL;
const loginView = document.querySelector("#login-view");
const chatView = document.querySelector("#chat-view");
const loginForm = document.querySelector("#login-form");
const chatForm = document.querySelector("#chat-form");
const passwordInput = document.querySelector("#password-input");
const questionInput = document.querySelector("#question-input");
const loginError = document.querySelector("#login-error");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const logoutButton = document.querySelector("#logout-button");

let accessToken = null;

function setAuthenticated(authenticated) {
  loginView.classList.toggle("hidden", authenticated);
  chatView.classList.toggle("hidden", !authenticated);
  logoutButton.classList.toggle("hidden", !authenticated);
  if (authenticated) questionInput.focus();
}

async function apiRequest(path, options = {}) {
  if (!apiBase) {
    throw new Error("The chat API URL has not been configured.");
  }
  const headers = new Headers(options.headers || {});
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (options.body) headers.set("Content-Type", "application/json");

  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && accessToken) {
    accessToken = null;
    setAuthenticated(false);
    throw new Error("Your session expired. Sign in again.");
  }
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function showMessage(role, text, toolTrace) {
  const article = document.createElement("article");
  article.className = `message message-${role}`;

  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "TechCompass";

  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  article.append(label, body);

  if (Array.isArray(toolTrace) && toolTrace.length > 0) {
    const details = document.createElement("details");
    details.className = "tool-details";
    const summary = document.createElement("summary");
    summary.textContent = `Evidence tools used (${toolTrace.length})`;
    const trace = document.createElement("pre");
    trace.textContent = JSON.stringify(toolTrace, null, 2);
    details.append(summary, trace);
    article.append(details);
  }

  messages.append(article);
  article.scrollIntoView({ behavior: "smooth", block: "end" });
  return article;
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const button = loginForm.querySelector("button");
  button.disabled = true;
  try {
    const result = await apiRequest("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ password: passwordInput.value }),
    });
    accessToken = result.access_token;
    passwordInput.value = "";
    setAuthenticated(true);
  } catch (error) {
    loginError.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (question.length < 3 || !accessToken) return;

  showMessage("user", question);
  questionInput.value = "";
  sendButton.disabled = true;
  sendButton.textContent = "Thinking...";
  const pending = showMessage("assistant", "Searching the graph and preparing an answer...");

  try {
    const result = await apiRequest("/api/agent/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    pending.remove();
    showMessage("assistant", result.answer, result.tools_used);
  } catch (error) {
    pending.remove();
    showMessage("assistant error", error.message);
  } finally {
    sendButton.disabled = false;
    sendButton.textContent = "Send";
    questionInput.focus();
  }
});

questionInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    chatForm.requestSubmit();
  }
});

logoutButton.addEventListener("click", () => {
  accessToken = null;
  messages.replaceChildren();
  setAuthenticated(false);
  passwordInput.focus();
});

setAuthenticated(false);
