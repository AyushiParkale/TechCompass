const apiBase = window.TECHCOMPASS_API_URL;
const runButton = document.querySelector("#run-button");
const daysInput = document.querySelector("#lookback-days");
const progressPanel = document.querySelector("#progress-panel");
const progressDetail = document.querySelector("#progress-detail");
const progressStatus = document.querySelector("#progress-status");
const reportPanel = document.querySelector("#report-panel");
const reportContent = document.querySelector("#report-content");
const reportPeriod = document.querySelector("#report-period");
const auditDetails = document.querySelector("#audit-details");
const auditContent = document.querySelector("#audit-content");

function appendInlineMarkdown(parent, text) {
  const tokenPattern = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*(.+?)\*\*|`([^`]+)`|\*([^*]+)\*/g;
  let cursor = 0;
  for (const match of text.matchAll(tokenPattern)) {
    const start = match.index ?? 0;
    if (start > cursor) parent.append(document.createTextNode(text.slice(cursor, start)));
    if (match[1]) {
      const link = document.createElement("a");
      link.href = match[2];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[1];
      parent.append(link);
    } else if (match[3]) {
      const strong = document.createElement("strong");
      strong.textContent = match[3];
      parent.append(strong);
    } else if (match[4]) {
      const code = document.createElement("code");
      code.textContent = match[4];
      parent.append(code);
    } else if (match[5]) {
      const emphasis = document.createElement("em");
      emphasis.textContent = match[5];
      parent.append(emphasis);
    }
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function renderMarkdown(container, markdown) {
  const lines = String(markdown || "").replace(/\r/g, "").split("\n");
  const isTableDivider = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const cellsFrom = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  let index = 0;

  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) { index += 1; continue; }

    if (line.startsWith("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const wrapper = document.createElement("div");
      wrapper.className = "report-table-wrapper";
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headerRow = document.createElement("tr");
      for (const cell of cellsFrom(line)) {
        const th = document.createElement("th");
        appendInlineMarkdown(th, cell);
        headerRow.append(th);
      }
      head.append(headerRow);
      const body = document.createElement("tbody");
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        const row = document.createElement("tr");
        for (const cell of cellsFrom(lines[index])) {
          const td = document.createElement("td");
          appendInlineMarkdown(td, cell);
          row.append(td);
        }
        body.append(row);
        index += 1;
      }
      table.append(head, body);
      wrapper.append(table);
      container.append(wrapper);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const element = document.createElement(`h${heading[1].length}`);
      appendInlineMarkdown(element, heading[2]);
      container.append(element);
      index += 1;
      continue;
    }

    const listItem = line.match(/^\s*([-*+]\s+|\d+[.)]\s+)(.+)$/);
    if (listItem) {
      const ordered = /^\d/.test(listItem[1]);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const item = lines[index].trim().match(/^([-*+]\s+|\d+[.)]\s+)(.+)$/);
        if (!item || /^\d/.test(item[1]) !== ordered) break;
        const li = document.createElement("li");
        appendInlineMarkdown(li, item[2]);
        list.append(li);
        index += 1;
      }
      container.append(list);
      continue;
    }

    const paragraphLines = [];
    while (index < lines.length && lines[index].trim() &&
      !/^#{1,6}\s/.test(lines[index].trim()) &&
      !/^\s*([-*+]\s+|\d+[.)]\s+)/.test(lines[index]) &&
      !(lines[index].trim().startsWith("|") && index + 1 < lines.length && isTableDivider(lines[index + 1]))) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    if (paragraphLines.length) {
      const paragraph = document.createElement("p");
      appendInlineMarkdown(paragraph, paragraphLines.join(" "));
      container.append(paragraph);
    } else {
      index += 1;
    }
  }
  decorateReportCatalog(container);
}

function decorateReportCatalog(container) {
  for (const paragraph of container.querySelectorAll("p")) {
    if (/gemini analysis status|gemini was unavailable/i.test(paragraph.textContent || "")) {
      paragraph.classList.add("report-warning");
    }
  }
  const nodes = [...container.children];
  const themeFor = (text) => {
    const value = text.toLowerCase();
    if (/security|access|self-host|privacy|identity/.test(value)) return "security";
    if (/retrieval|document|embedding|vector|knowledge/.test(value)) return "retrieval";
    if (/agent|tool use|orchestrat|workflow|memory/.test(value)) return "agents";
    if (/model|inference|provider|hosting|runtime/.test(value)) return "models";
    if (/speech|audio|meeting|voice/.test(value)) return "media";
    return "default";
  };
  let index = 0;
  while (index < nodes.length) {
    const heading = nodes[index];
    if (heading.tagName !== "H3") { index += 1; continue; }
    const section = document.createElement("section");
    section.className = "objective-catalog-card";
    section.dataset.theme = themeFor(heading.textContent || "");
    heading.classList.add("catalog-section-title");
    section.append(heading);
    index += 1;
    while (index < nodes.length && nodes[index].tagName !== "H3") {
      const node = nodes[index];
      if (node.tagName === "H4") {
        const finding = document.createElement("article");
        finding.className = "technology-card";
        finding.dataset.kind = "impact";
        const badge = document.createElement("span");
        badge.className = "catalog-badge";
        badge.textContent = "External technology signal";
        node.prepend(badge);
        finding.append(node);
        index += 1;
        while (index < nodes.length && nodes[index].tagName !== "H4" && nodes[index].tagName !== "H3") {
          finding.append(nodes[index]);
          index += 1;
        }
        for (const child of finding.children) {
          const text = child.textContent || "";
          if (/potential impact|impact hypothesis/i.test(text)) child.classList.add("impact-callout");
          if (/no defensible|no specific|no .*connection|not established/i.test(text)) {
            child.classList.add("impact-callout", "impact-callout--none");
            finding.dataset.kind = "context";
          }
          if (/graph evidence|graph path|falkordb|implementation path|remembered in graph/i.test(text)) {
            child.classList.add("graph-trail");
          }
          if (/not saved|save failed|persistence/i.test(text)) child.classList.add("save-warning");
        }
        section.append(finding);
        continue;
      }
      section.append(node);
      index += 1;
    }
    container.insertBefore(section, nodes[index] || null);
  }
}

function setCheckState(stage, status, detail = "") {
  const item = document.querySelector(`[data-stage="${stage}"]`);
  if (!item) return;
  item.dataset.status = status;
  const state = item.querySelector(".check-state");
  const icon = item.querySelector(".check-icon");
  if (status === "complete") {
    state.textContent = "Complete";
    icon.textContent = "✓";
  } else if (status === "running") {
    state.textContent = "In progress";
    icon.textContent = "…";
  } else if (status === "failed") {
    state.textContent = "Failed";
    icon.textContent = "!";
  } else if (status === "skipped") {
    state.textContent = "Not needed";
    icon.textContent = "–";
  }
  if (detail) progressDetail.textContent = detail;
}

function handleProgress(event) {
  if (event.detail) progressDetail.textContent = event.detail;
  if (event.stage && event.status) {
    setCheckState(event.stage, event.status, event.detail);
  }
  if (event.type === "error") {
    progressStatus.textContent = "Failed";
    progressStatus.dataset.status = "failed";
    reportPanel.hidden = false;
    reportContent.replaceChildren();
    const message = document.createElement("p");
    message.className = "report-error";
    message.textContent = event.message || "The update check failed. Try again.";
    reportContent.append(message);
    reportPeriod.textContent = "Check failed";
  }
}

function renderAudit(toolTrace = []) {
  auditContent.replaceChildren();
  const graphCheck = toolTrace.find((item) => item.tool === "get_external_project_context");
  const searchCheck = toolTrace.find((item) => item.tool === "search_web");
  const diagnostics = graphCheck?.context_diagnostics || {};
  const args = searchCheck?.arguments || {};
  const queries = String(args.query || "").split("\n").filter(Boolean);
  const resultCount = Number(searchCheck?.result_count || 0);
  const impactChecks = toolTrace.filter((item) => item.tool === "objective_impact_analysis");
  const savedAssessments = impactChecks.reduce(
    (total, item) => total + Number(item.graph_assessments_saved || 0),
    0,
  );
  const rows = [
    ["Graph objectives", diagnostics.objective_count ?? "—"],
    ["Graph capabilities", diagnostics.capability_count ?? "—"],
    ["Graph-derived searches", queries.length || "—"],
    ["Dated records returned", resultCount],
    ["Objective impact analyses completed", impactChecks.length
      ? `${impactChecks.filter((item) => item.ok).length} / ${impactChecks.length}`
      : "Not run"],
    ["Impact assessments remembered in FalkorDB", savedAssessments],
    ["Lookback window", args.days ? `${args.days} days` : "—"],
  ];
  const list = document.createElement("dl");
  list.className = "audit-list";
  for (const [label, value] of rows) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = String(value);
    list.append(term, description);
  }
  auditContent.append(list);
}

function updatePeriod(days, generatedAt) {
  const end = generatedAt ? new Date(generatedAt) : new Date();
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  const format = (date) => date.toISOString().slice(0, 10);
  const generatedLabel = generatedAt && !Number.isNaN(end.valueOf())
    ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(end)
    : "time unavailable";
  reportPeriod.textContent = `${format(start)} — ${format(end)} · ${days} days · Generated ${generatedLabel}`;
}

function handleEvent(event) {
  if (event.type === "progress") {
    handleProgress(event);
    return;
  }
  if (event.type === "error") {
    handleProgress(event);
    return;
  }
  if (event.type === "report") {
    const report = event.report || {};
    renderMarkdown(reportContent, report.answer || "The report was empty.");
    renderAudit(report.tools_used || []);
    auditDetails.hidden = false;
    reportPanel.hidden = false;
    updatePeriod(event.days || Number(daysInput.value), report.generated_at || event.generated_at);
    const searchFailure = (report.tools_used || []).find(
      (item) => item.tool === "search_web" && !item.ok,
    );
    const impactFailure = (report.tools_used || []).find(
      (item) => item.tool === "objective_impact_analysis" && !item.ok,
    );
    const graphFallback = report.analysis_status === "graph_fallback";
    progressStatus.textContent = searchFailure
      ? "Search failed"
      : graphFallback || impactFailure
        ? "Partial · Gemini unavailable"
        : "Complete";
    progressStatus.dataset.status = searchFailure ? "failed" : graphFallback || impactFailure ? "partial" : "complete";
    for (const item of document.querySelectorAll(".check-item")) {
      if (!item.dataset.status || item.dataset.status === "waiting") {
        setCheckState(item.dataset.stage, "skipped");
      }
    }
    progressDetail.textContent = searchFailure
      ? "Graph context is shown below, but no external search completed."
      : graphFallback || impactFailure
        ? "Search completed. Gemini did not finish; the report labels low-confidence graph-matched leads separately."
        : "The latest report is ready below.";
    reportPanel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function runUpdateCheck() {
  if (!apiBase) {
    throw new Error("The report API URL has not been configured.");
  }
  const days = Number(daysInput.value);
  if (!Number.isInteger(days) || days < 1 || days > 90) {
    daysInput.setCustomValidity("Choose a whole number from 1 to 90 days.");
    daysInput.reportValidity();
    return;
  }
  daysInput.setCustomValidity("");
  runButton.disabled = true;
  runButton.querySelector("span:last-child").textContent = "Checking…";
  progressPanel.hidden = false;
  progressStatus.textContent = "Running";
  progressStatus.dataset.status = "running";
  progressDetail.textContent = "Starting the update check…";
  reportPanel.hidden = true;
  reportContent.replaceChildren();
  auditDetails.hidden = true;
  for (const item of document.querySelectorAll(".check-item")) {
    item.dataset.status = "waiting";
    item.querySelector(".check-state").textContent = "Waiting";
    item.querySelector(".check-icon").textContent = item.querySelector(".check-icon").dataset.number;
  }

  try {
    const response = await fetch(`${apiBase}/api/updates/run`, {
      method: "POST",
      cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ days }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `Request failed (${response.status}).`);
    }
    if (!response.body) throw new Error("The server did not provide a progress stream.");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let receivedReport = false;
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const dataLine = block.split("\n").find((line) => line.startsWith("data: "));
        if (!dataLine) continue;
        const event = JSON.parse(dataLine.slice(6));
        if (event.type === "report") receivedReport = true;
        handleEvent(event);
      }
      if (done) break;
    }
    if (!receivedReport && progressStatus.dataset.status !== "failed") {
      throw new Error("The report stream ended before a report was returned.");
    }
  } finally {
    runButton.disabled = false;
    runButton.querySelector("span:last-child").textContent = "Check for updates";
  }
}

runButton.addEventListener("click", () => {
  runUpdateCheck().catch((error) => {
    progressPanel.hidden = false;
    progressStatus.textContent = "Failed";
    progressStatus.dataset.status = "failed";
    progressDetail.textContent = error.message;
    reportPanel.hidden = false;
    reportPeriod.textContent = "Check failed";
    reportContent.replaceChildren();
    const message = document.createElement("p");
    message.className = "report-error";
    message.textContent = error.message;
    reportContent.append(message);
  });
});
