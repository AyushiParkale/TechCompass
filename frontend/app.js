const apiBase = window.TECHCOMPASS_API_URL;
const chatView = document.querySelector("#chat-view");
const chatForm = document.querySelector("#chat-form");
const questionInput = document.querySelector("#question-input");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const graphForm = document.querySelector("#graph-form");
const graphButton = document.querySelector("#graph-button");
const graphStatus = document.querySelector("#graph-status");
const graphCanvas = document.querySelector("#graph-canvas");
const externalEvidence = document.querySelector("#external-evidence");
const syncForm = document.querySelector("#sync-form");
const syncButton = document.querySelector("#sync-button");
const previewForm = document.querySelector("#preview-form");
const previewButton = document.querySelector("#preview-button");
const connectorResults = document.querySelector("#connector-results");
const googleNewsQuery = document.querySelector("#google-news-query");
const googleNewsLanguage = document.querySelector("#google-news-language");
const googleNewsCountry = document.querySelector("#google-news-country");
const previewSize = document.querySelector("#preview-size");
const articleForm = document.querySelector("#article-form");
const articleButton = document.querySelector("#article-button");
const sourceStatus = document.querySelector("#source-status");
const searchForm = document.querySelector("#search-form");
const searchResults = document.querySelector("#search-results");

const svgNamespace = "http://www.w3.org/2000/svg";

async function apiRequest(path, options = {}) {
  if (!apiBase) {
    throw new Error("The chat API URL has not been configured.");
  }
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");

  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `Request failed (${response.status}).`);
  }
  return payload;
}

function getGoogleNewsOptions() {
  return {
    query: googleNewsQuery.value.trim(),
    language: googleNewsLanguage.value,
    country: googleNewsCountry.value,
  };
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

function setStatus(element, message, isError = false) {
  element.textContent = message;
  element.classList.toggle("error", isError);
}

function makeSvgElement(name, attributes = {}) {
  const element = document.createElementNS(svgNamespace, name);
  for (const [key, value] of Object.entries(attributes)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

function drawDependencyGraph(trace, evidence = []) {
  graphCanvas.replaceChildren();
  externalEvidence.replaceChildren();
  if (!trace || trace.status !== "paths_found" || !trace.paths?.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = trace?.message || "No graph paths were returned.";
    graphCanvas.append(empty);
    return;
  }

  const paths = trace.paths.slice(0, 8);
  const linkedEvidence = (evidence || [])
    .filter((item) => item.package)
    .slice(0, 8);
  const maxNodes = Math.max(
    ...paths.map((path) => path.dependency_path?.length || 1),
  );
  const width = Math.max(740, 155 * maxNodes + 40);
  const rowHeight = 82;
  const height = Math.max(
    130,
    rowHeight * (paths.length + linkedEvidence.length) + 24,
  );
  const svg = makeSvgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${paths.length} dependency paths from ${trace.component}`,
  });
  const defs = makeSvgElement("defs");
  const marker = makeSvgElement("marker", {
    id: "graph-arrow",
    markerWidth: 8,
    markerHeight: 8,
    refX: 7,
    refY: 3,
    orient: "auto",
  });
  marker.append(makeSvgElement("path", {
    d: "M0,0 L0,6 L8,3 z",
    fill: "#8da2c5",
  }));
  defs.append(marker);
  svg.append(defs);

  paths.forEach((path, rowIndex) => {
    const names = path.dependency_path || [];
    const versions = path.versions || [];
    const y = 38 + rowIndex * rowHeight;
    const label = makeSvgElement("text", {
      x: 12,
      y: y + 4,
      class: "graph-path-label",
    });
    label.textContent = `${path.hops} hop${path.hops === 1 ? "" : "s"}`;
    svg.append(label);
    names.forEach((name, index) => {
      const x = 96 + index * 150;
      if (index > 0) {
        svg.append(makeSvgElement("line", {
          x1: x - 58,
          y1: y + 13,
          x2: x - 42,
          y2: y + 13,
          class: "graph-edge",
        }));
      }
      const node = makeSvgElement("g", {
        class: `graph-node ${index === 0 ? "component" : "package"}`,
      });
      node.append(makeSvgElement("rect", {
        x: x - 63,
        y: y - 5,
        width: 126,
        height: 37,
        rx: 9,
      }));
      const nodeName = makeSvgElement("text", { x, y: y + 8 });
      nodeName.textContent = name;
      node.append(nodeName);
      const version = versions[index - 1];
      if (index > 0 && version) {
        const versionLabel = makeSvgElement("text", {
          x,
          y: y + 24,
          class: "graph-path-label",
        });
        versionLabel.textContent = `v${version}`;
        node.append(versionLabel);
      }
      svg.append(node);
    });
  });
  linkedEvidence.forEach((item, itemIndex) => {
    const y = 38 + (paths.length + itemIndex) * rowHeight;
    const label = makeSvgElement("text", {
      x: 12,
      y: y + 4,
      class: "graph-path-label",
    });
    label.textContent = "linked evidence";
    svg.append(label);

    const packageNode = makeSvgElement("g", { class: "graph-node package" });
    packageNode.append(makeSvgElement("rect", {
      x: 112,
      y: y - 5,
      width: 180,
      height: 37,
      rx: 9,
    }));
    const packageLabel = makeSvgElement("text", { x: 202, y: y + 13 });
    packageLabel.textContent = item.package;
    packageNode.append(packageLabel);
    svg.append(packageNode);
    svg.append(makeSvgElement("line", {
      x1: 292,
      y1: y + 13,
      x2: 330,
      y2: y + 13,
      class: "graph-edge",
    }));

    const evidenceNode = makeSvgElement("g", {
      class: "graph-node external",
    });
    evidenceNode.append(makeSvgElement("rect", {
      x: 340,
      y: y - 5,
      width: 360,
      height: 37,
      rx: 9,
    }));
    const evidenceLabel = makeSvgElement("text", {
      x: 520,
      y: y + 13,
    });
    const title = item.title || item.source || "External evidence";
    evidenceLabel.textContent = title.length > 46
      ? `${title.slice(0, 43)}...`
      : title;
    evidenceNode.append(evidenceLabel);
    svg.append(evidenceNode);
  });
  graphCanvas.append(svg);
}

function renderEvidence(container, items) {
  container.replaceChildren();
  for (const item of (items || []).slice(0, 40)) {
    const card = document.createElement("article");
    card.className = "evidence-card";
    let link;
    try {
      const parsedUrl = new URL(item.url);
      if (parsedUrl.protocol === "https:") {
        link = document.createElement("a");
        link.href = parsedUrl.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
    } catch {
      link = null;
    }
    if (!link) {
      link = document.createElement("span");
    }
    link.textContent = item.title || item.source || "External source";
    const meta = document.createElement("p");
    meta.className = "evidence-meta";
    meta.textContent = [
      item.source,
      item.item_type,
      item.package ? `Package: ${item.package}` : "",
      item.affected_versions?.length
        ? `Affected versions: ${item.affected_versions.join(", ")}`
        : "",
      item.affected_range ? `Reported range: ${item.affected_range}` : "",
      item.match_type ? `Link: ${item.match_type}` : "",
      item.is_version_affected
        ? "Exact installed version match"
        : item.installed_versions?.length
          ? "No exact installed-version match"
          : "",
      item.published_at,
    ].filter(Boolean).join(" · ");
    const summary = document.createElement("p");
    summary.className = "evidence-summary";
    summary.textContent = item.summary || "";
    card.append(link, meta, summary);
    container.append(card);
  }
}

async function loadDependencyGraph() {
  const component = document.querySelector("#component-input").value.trim();
  const depth = document.querySelector("#depth-input").value;
  if (!component) return;

  graphButton.disabled = true;
  setStatus(graphStatus, "Loading dependency paths...");
  try {
    const result = await apiRequest(
      `/api/graph/visualization?component=${encodeURIComponent(component)}&depth=${depth}`,
    );
    drawDependencyGraph(result.trace, result.external_evidence);
    renderEvidence(externalEvidence, result.external_evidence);
    const count = result.trace.paths?.length || 0;
    setStatus(
      graphStatus,
      result.trace.status === "paths_found"
        ? `Showing ${Math.min(count, 8)} of ${count} paths (up to ${depth} hops).`
        : result.trace.message,
    );
  } catch (error) {
    setStatus(graphStatus, error.message, true);
  } finally {
    graphButton.disabled = false;
  }
}

graphForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await loadDependencyGraph();
});

previewForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  previewButton.disabled = true;
  connectorResults.replaceChildren();
  setStatus(sourceStatus, "Fetching a small live sample from each configured connector. Nothing will be saved to FalkorDB...");
  try {
    const result = await apiRequest("/api/knowledge/preview", {
      method: "POST",
      body: JSON.stringify({ size: previewSize.value, google_news: getGoogleNewsOptions() }),
    });
    const contextDetails = document.createElement("details");
    contextDetails.className = "connector-context";
    const contextSummary = document.createElement("summary");
    contextSummary.textContent = "Project inputs used for these API queries";
    const context = document.createElement("pre");
    context.textContent = JSON.stringify(result.context, null, 2);
    contextDetails.append(contextSummary, context);
    connectorResults.append(contextDetails);

    const sources = Object.entries(result.sources || {});
    const cards = document.createElement("div");
    cards.className = "connector-grid";
    for (const [name, source] of sources) {
      const card = document.createElement("section");
      card.className = `connector-card connector-${source.status || "unknown"}`;
      const heading = document.createElement("h3");
      heading.textContent = name.replaceAll("_", " ");
      if (source.feeds?.length) {
        heading.textContent += ` (${source.feeds.length} configured)`;
      }
      const status = document.createElement("p");
      status.className = "evidence-meta";
      status.textContent = source.status === "ok"
        ? `${source.records} fetched record(s) · preview shows up to 3`
        : source.reason || source.error || source.status || "No result";
      card.append(heading, status);
      for (const feed of source.feeds || []) {
        const feedStatus = document.createElement("p");
        feedStatus.className = "evidence-meta";
        feedStatus.textContent = `${feed.url}: ${feed.status}`
          + (feed.status === "ok" ? ` · ${feed.records} record(s)` : ` · ${feed.error || ""}`);
        card.append(feedStatus);
      }
      const previews = source.preview || [];
      for (const item of previews) {
        const entry = document.createElement("article");
        entry.className = "connector-entry";
        let title;
        try {
          const url = new URL(item.url);
          title = document.createElement(url.protocol === "https:" ? "a" : "span");
          if (url.protocol === "https:") {
            title.href = url.href;
            title.target = "_blank";
            title.rel = "noopener noreferrer";
          }
        } catch {
          title = document.createElement("span");
        }
        title.textContent = item.title || item.source || "Untitled record";
        const meta = document.createElement("p");
        meta.className = "evidence-meta";
        meta.textContent = [item.source, item.item_type, item.published_at,
          item.package ? `Package: ${item.package}` : "",
          item.version ? `Installed version: ${item.version}` : "",
          item.affected_range ? `Reported range: ${item.affected_range}` : "",
          item.references ? `${item.references} references` : ""]
          .filter(Boolean).join(" · ");
        const summary = document.createElement("p");
        summary.className = "evidence-summary";
        summary.textContent = item.summary || "No abstract or description returned.";
        entry.append(title, meta, summary);
        card.append(entry);
      }
      if (source.status === "ok" && !source.records) {
        const empty = document.createElement("p");
        empty.className = "evidence-meta";
        empty.textContent = "The source responded, but returned no matching records.";
        card.append(empty);
      }
      cards.append(card);
    }
    connectorResults.append(cards);
    setStatus(sourceStatus, `${previewSize.value === "medium" ? "Medium" : "Small"} read-only connector preview finished. Nothing was written to FalkorDB.`);
  } catch (error) {
    setStatus(sourceStatus, error.message, true);
  } finally {
    previewButton.disabled = false;
  }
});

syncForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  syncButton.disabled = true;
  setStatus(
    sourceStatus,
    "Syncing security advisories, releases, research papers, engineering stories, and configured feeds...",
  );
  try {
    const result = await apiRequest("/api/knowledge/sync", {
      method: "POST",
      body: JSON.stringify({ google_news: getGoogleNewsOptions() }),
    });
    const totals = result.totals || {};
    setStatus(
      sourceStatus,
      `Sync finished: ${totals.items || 0} unique records, `
        + `${totals.package_links || 0} package links, `
        + `${totals.affected_version_links || 0} exact affected-version links. `
        + `Source results: ${JSON.stringify(result.sources || {})}`,
    );
    await loadDependencyGraph();
  } catch (error) {
    setStatus(sourceStatus, error.message, true);
  } finally {
    syncButton.disabled = false;
  }
});

articleForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = document.querySelector("#article-url").value.trim();
  articleButton.disabled = true;
  setStatus(sourceStatus, "Fetching and indexing the public article...");
  try {
    const result = await apiRequest("/api/knowledge/articles", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
    setStatus(
      sourceStatus,
      `Added “${result.title}”. Linked packages: `
        + `${result.matched_packages?.join(", ") || "none identified"}.`,
    );
    document.querySelector("#article-url").value = "";
    renderEvidence(externalEvidence, [
      {
        title: result.title,
        source: result.source,
        item_type: result.item_type,
        url: result.url,
        summary: result.summary,
        package: result.matched_packages?.join(", "),
      },
    ]);
  } catch (error) {
    setStatus(sourceStatus, error.message, true);
  } finally {
    articleButton.disabled = false;
  }
});

searchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = document.querySelector("#knowledge-query").value.trim();
  setStatus(sourceStatus, "Searching ingested external evidence...");
  try {
    const result = await apiRequest(
      `/api/knowledge/search?q=${encodeURIComponent(query)}`,
    );
    renderEvidence(searchResults, result.results);
    setStatus(
      sourceStatus,
      result.results.length
        ? `Found ${result.results.length} matching external record(s).`
        : "No matching records in the graph. Sync sources or add an article URL first.",
    );
  } catch (error) {
    setStatus(sourceStatus, error.message, true);
  }
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = questionInput.value.trim();
  if (question.length < 3) return;

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

loadDependencyGraph();
