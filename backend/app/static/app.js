// Drive the standalone browser UI served by FastAPI without a frontend framework.
const byId = (id) => document.getElementById(id);

const notice = byId("notice");
const datasetSelect = byId("dataset-select");
const ANALYST_DATASETS_KEY = "graphsheet-analyst-datasets";
const ANALYST_CONVERSATION_KEY = "graphsheet-analyst-conversation";
let datasets = [];
let analystDatasetIds = [];
let analystConversationId = localStorage.getItem(ANALYST_CONVERSATION_KEY);

function showNotice(message, isError = false) {
  // Use one live region for upload, request, and validation feedback.
  notice.textContent = message;
  notice.hidden = !message;
  notice.classList.toggle("error", isError);
}

async function requestJson(path, options = {}) {
  // Decode API responses consistently and turn non-2xx responses into UI errors.
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`The server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message || `Request failed (${response.status}).`;
    const error = new Error(message);
    error.status = response.status;
    throw error;
  }
  return body;
}

function clear(element, className = "") {
  // Remove old rendered content before rebuilding a dynamic panel.
  element.replaceChildren();
  element.className = className;
}

function makeTable(container, columns, rows) {
  // Build preview and result tables with text nodes rather than injected HTML.
  clear(container, "table-wrap");
  if (!columns.length || !rows.length) {
    container.className = "table-wrap empty-state";
    container.textContent = "No rows to display.";
    return;
  }

  const table = document.createElement("table");
  const head = document.createElement("thead");
  const headerRow = document.createElement("tr");
  columns.forEach((column) => {
    const cell = document.createElement("th");
    cell.scope = "col";
    cell.textContent = column;
    headerRow.append(cell);
  });
  head.append(headerRow);

  const body = document.createElement("tbody");
  rows.forEach((row) => {
    const tableRow = document.createElement("tr");
    columns.forEach((column) => {
      const cell = document.createElement("td");
      const value = row[column];
      cell.textContent = value === null || value === undefined ? "" : String(value);
      tableRow.append(cell);
    });
    body.append(tableRow);
  });

  table.append(head, body);
  container.append(table);
}

function selectedDatasetId() {
  // Read the dataset currently chosen for the preview and schema panels.
  return datasetSelect.value;
}

function selectedAnalystDatasetIds() {
  // Read checked sheets so each Analyst request uses the user's explicit selection.
  return [...document.querySelectorAll("#analyst-datasets input:checked")]
    .map((input) => input.value);
}

function renderAnalystDatasetChoices() {
  // Rebuild selectable sheets and persist the five-sheet selection limit.
  const container = byId("analyst-datasets");
  clear(container, "analyst-dataset-list");
  if (!datasets.length) {
    container.className = "empty-state";
    container.textContent = "Upload or refresh datasets to select sheets.";
    return;
  }

  datasets.forEach((dataset) => {
    const label = document.createElement("label");
    label.className = "analyst-dataset-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = dataset.dataset_id;
    input.checked = analystDatasetIds.includes(dataset.dataset_id);
    input.addEventListener("change", () => {
      const selected = selectedAnalystDatasetIds();
      if (selected.length > 5) {
        input.checked = false;
        showNotice("A chat can use at most five sheets.", true);
        return;
      }
      analystDatasetIds = selected;
      localStorage.setItem(ANALYST_DATASETS_KEY, JSON.stringify(selected));
      showNotice("");
    });
    const name = document.createElement("span");
    name.textContent = dataset.original_filename;
    const meta = document.createElement("small");
    meta.textContent = `${dataset.row_count} rows`;
    label.append(input, name, meta);
    container.append(label);
  });
}

async function loadDatasets(preferredId = "") {
  // Populate both dataset selectors, restore chat selection, and load the chosen sheet.
  const response = await requestJson("/datasets/");
  datasets = response.datasets || [];
  clear(datasetSelect);

  if (!datasets.length) {
    analystDatasetIds = [];
    renderAnalystDatasetChoices();
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No datasets yet";
    datasetSelect.append(option);
    datasetSelect.disabled = true;
    showNotice("Upload a CSV or XLSX file to begin.");
    return;
  }

  datasetSelect.disabled = false;
  datasets.forEach((dataset) => {
    const option = document.createElement("option");
    option.value = dataset.dataset_id;
    option.textContent = `${dataset.original_filename} (${dataset.row_count} rows)`;
    datasetSelect.append(option);
  });

  const nextId = datasets.some((dataset) => dataset.dataset_id === preferredId)
    ? preferredId
    : datasets[0].dataset_id;
  datasetSelect.value = nextId;
  const savedSelection = JSON.parse(
    localStorage.getItem(ANALYST_DATASETS_KEY) || "[]",
  );
  analystDatasetIds = Array.isArray(savedSelection)
    ? savedSelection.filter((id) => datasets.some((dataset) => dataset.dataset_id === id)).slice(0, 5)
    : [];
  if (!analystDatasetIds.length) analystDatasetIds = [nextId];
  renderAnalystDatasetChoices();
  await loadDataset(nextId);
  await restoreAnalystConversation();
}

async function loadDataset(datasetId) {
  // Refresh the preview and schema profile together to keep them in sync.
  if (!datasetId) return;
  const safeId = encodeURIComponent(datasetId);
  const [preview, profile] = await Promise.all([
    requestJson(`/datasets/${safeId}/preview?limit=10`),
    requestJson(`/datasets/${safeId}/schema`),
  ]);

  const schema = profile.schema;
  byId("dataset-summary").textContent =
    `${schema.row_count} rows | ${schema.column_count} columns`;
  const rows = preview.rows || [];
  makeTable(
    byId("preview-table"),
    rows.length ? Object.keys(rows[0]) : schema.columns.map((column) => column.name),
    rows,
  );

  const schemaContent = byId("schema-content");
  clear(schemaContent, "schema-list");
  if (!schema.columns.length) {
    schemaContent.className = "empty-state";
    schemaContent.textContent = "This dataset has no columns to profile.";
    return;
  }

  schema.columns.forEach((column) => {
    const item = document.createElement("div");
    item.className = "schema-item";
    const title = document.createElement("div");
    title.className = "schema-name";
    const name = document.createElement("span");
    name.textContent = column.name;
    const type = document.createElement("span");
    type.className = "schema-meta";
    type.textContent = column.sqlite_type || "UNKNOWN";
    title.append(name, type);
    const details = document.createElement("div");
    details.className = "schema-meta";
    details.textContent =
      `${column.null_count} missing | ${column.unique_count} unique` +
      (column.is_potential_key ? " | possible key" : "");
    const samples = document.createElement("div");
    samples.className = "schema-meta";
    samples.textContent = `Sample: ${(column.sample_values || []).map(String).join(", ") || "none"}`;
    item.append(title, details, samples);
    schemaContent.append(item);
  });
}

async function withFormLoading(form, action) {
  // Prevent duplicate form submissions and restore controls even after failure.
  const buttons = [...form.querySelectorAll("button[type=submit]")];
  const originalTexts = buttons.map((button) => button.textContent);
  buttons.forEach((button, index) => {
    button.disabled = true;
    if (index === 0) button.textContent = "Working...";
  });
  try {
    await action();
  } finally {
    buttons.forEach((button, index) => {
      button.disabled = false;
      button.textContent = originalTexts[index];
    });
  }
}

byId("file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  byId("file-name").textContent = file ? file.name : "Choose a CSV or XLSX file";
});

byId("upload-form").addEventListener("submit", async (event) => {
  // Upload the selected spreadsheet and refresh the dataset view on success.
  event.preventDefault();
  const form = event.currentTarget;
  await withFormLoading(form, async () => {
    showNotice("");
    try {
      const formData = new FormData();
      formData.append("file", byId("file-input").files[0]);
      const result = await requestJson("/documents/upload", {
        method: "POST",
        body: formData,
      });
      const datasetId = result.storage.dataset_id;
      await loadDatasets(datasetId);
      showNotice(`Uploaded ${result.storage.filename}: ${result.storage.row_count} rows and ${result.storage.column_count} columns.`);
      form.reset();
      byId("file-name").textContent = "Choose a CSV or XLSX file";
    } catch (error) {
      showNotice(error.message, true);
    }
  });
});

byId("refresh-datasets").addEventListener("click", async () => {
  // Refresh without changing the currently selected dataset when possible.
  try {
    await loadDatasets(selectedDatasetId());
  } catch (error) {
    showNotice(error.message, true);
  }
});

datasetSelect.addEventListener("change", async () => {
  // Update the preview and profile whenever the selected sheet changes.
  try {
    await loadDataset(selectedDatasetId());
    showNotice("");
  } catch (error) {
    showNotice(error.message, true);
  }
});

function appendHistoryMessage(role, text, className = "") {
  // Add a message safely and keep the latest Analyst activity visible.
  const history = byId("analyst-history");
  if (history.classList.contains("empty-state")) clear(history, "analyst-history");
  const message = document.createElement("article");
  message.className = `analyst-message ${role}${className ? ` ${className}` : ""}`;
  const heading = document.createElement("strong");
  heading.textContent = role === "user" ? "You" : "AI Analyst";
  const body = document.createElement("p");
  body.textContent = text;
  message.append(heading, body);
  history.append(message);
  history.scrollTop = history.scrollHeight;
  return message;
}

function renderSQLAnalysis(message, analysis, question = "") {
  // Render query plans, execution results, and explicit join-approval actions.
  if (!analysis) return;
  if (analysis.clarification) {
    const clarification = document.createElement("p");
    clarification.className = "analyst-clarification";
    clarification.textContent = analysis.clarification;
    message.append(clarification);
  }

  (analysis.queries || []).forEach((query, index) => {
    const card = document.createElement("section");
    card.className = "analyst-query";
    const heading = document.createElement("h3");
    heading.textContent = query.title || `Result ${index + 1}`;
    card.append(heading);
    if (query.description) {
      const description = document.createElement("p");
      description.textContent = query.description;
      card.append(description);
    }

    const status = document.createElement("p");
    status.className = `analyst-query-status ${query.execution_status || "failed"}`;
    status.textContent = (query.execution_status || "failed").replaceAll("_", " ");
    card.append(status);
    if (query.error) {
      const error = document.createElement("p");
      error.className = "analyst-error";
      error.textContent = query.error;
      card.append(error);
    }
    if (query.sql) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = "View generated SQL";
      const code = document.createElement("pre");
      code.className = "sql-code";
      code.textContent = query.sql;
      details.append(summary, code);
      card.append(details);
    }
    if (query.execution_status === "success" && query.result) {
      const count = document.createElement("p");
      count.className = "result-title";
      count.textContent = `${query.result.row_count || 0} result rows`;
      const table = document.createElement("div");
      makeTable(table, query.result.columns || [], query.result.rows || []);
      card.append(count, table);
    }
    message.append(card);
  });

  const candidates = (analysis.relationships || []).filter(
    (relationship) => relationship.status === "heuristic_candidate",
  );
  if (candidates.length) {
    const group = document.createElement("div");
    group.className = "analyst-relationship-list";
    const title = document.createElement("h3");
    title.textContent = "Possible sheet relationships";
    group.append(title);
    candidates.forEach((relationship) => {
      const item = document.createElement("div");
      item.className = "analyst-relationship";
      const label = document.createElement("span");
      label.textContent =
        `${relationship.left.filename}.${relationship.left.column} <-> ` +
        `${relationship.right.filename}.${relationship.right.column}`;
      const approve = document.createElement("button");
      approve.type = "button";
      approve.className = "button button-secondary";
      approve.textContent = "Approve and retry";
      approve.addEventListener("click", async () => {
        approve.disabled = true;
        try {
          await requestJson(
            `/conversations/${encodeURIComponent(analystConversationId)}` +
              `/sql-relationships/${encodeURIComponent(relationship.relationship_id)}/approval`,
            { method: "POST" },
          );
          await submitAnalystQuestion(question, "sql_analysis");
        } catch (error) {
          showNotice(error.message, true);
          approve.disabled = false;
        }
      });
      item.append(label, approve);
      group.append(item);
    });
    message.append(group);
  }
}

function renderEvidence(message, result) {
  // Show the generated answer together with references to its supporting chunks.
  if (result.answer) {
    const answer = document.createElement("p");
    answer.textContent = result.answer;
    message.append(answer);
  }
  if (result.sources?.length) {
    const title = document.createElement("p");
    title.className = "result-title";
    title.textContent = "Evidence chunks";
    const list = document.createElement("ul");
    list.className = "source-list";
    result.sources.forEach((source) => {
      const item = document.createElement("li");
      item.textContent =
        `Chunk ${source.chunk_index} (${source.chunk_id}) | score ${source.score}`;
      list.append(item);
    });
    message.append(title, list);
  }
}

function renderConversationHistory(events = []) {
  // Rebuild chat history from persisted message events after a page reload.
  const history = byId("analyst-history");
  clear(history, "analyst-history");
  const messages = events.filter((event) => event.event_type === "message");
  if (!messages.length) {
    history.className = "analyst-history empty-state";
    history.textContent = "Your questions, answers, generated SQL, and result tables will appear here.";
    return;
  }

  messages.forEach((event) => {
    const role = event.actor === "user" ? "user" : "assistant";
    const item = appendHistoryMessage(role, event.message || "");
    const analysis = event.metadata?.sql_analysis;
    if (analysis) renderSQLAnalysis(item, analysis, analysis.original_prompt || "");
    else if (role === "assistant" && event.metadata?.response) {
      renderEvidence(item, event.metadata.response);
    }
  });
}

async function restoreAnalystConversation() {
  // Restore saved sheet selections and render the persistent conversation events.
  if (!analystConversationId) return;
  try {
    const response = await requestJson(
      `/conversations/${encodeURIComponent(analystConversationId)}`,
    );
    const conversation = response.conversation;
    analystDatasetIds = (conversation.selected_dataset_ids || [])
      .filter((id) => datasets.some((dataset) => dataset.dataset_id === id))
      .slice(0, 5);
    localStorage.setItem(ANALYST_DATASETS_KEY, JSON.stringify(analystDatasetIds));
    renderAnalystDatasetChoices();
    renderConversationHistory(conversation.events || []);
  } catch (error) {
    if (error.status !== 404) throw error;
    localStorage.removeItem(ANALYST_CONVERSATION_KEY);
    analystConversationId = null;
  }
}

async function ensureAnalystConversation(datasetIds) {
  // Create or update the conversation before sending a chat-scoped request.
  if (analystConversationId) {
    try {
      await requestJson(
        `/conversations/${encodeURIComponent(analystConversationId)}/datasets`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dataset_ids: datasetIds }),
        },
      );
      return analystConversationId;
    } catch (error) {
      if (error.status !== 404) throw error;
      localStorage.removeItem(ANALYST_CONVERSATION_KEY);
      analystConversationId = null;
    }
  }

  const response = await requestJson("/conversations/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: "AI Analyst",
      selected_dataset_ids: datasetIds,
    }),
  });
  analystConversationId = response.conversation.conversation_id;
  localStorage.setItem(ANALYST_CONVERSATION_KEY, analystConversationId);
  return analystConversationId;
}

async function submitAnalystQuestion(question, mode) {
  // Validate selected sheets and route the question to evidence answering or SQL analysis.
  const datasetIds = selectedAnalystDatasetIds();
  if (!datasetIds.length) {
    showNotice("Select at least one sheet for this chat.", true);
    return;
  }
  if (datasetIds.length > 5) {
    showNotice("A chat can use at most five sheets.", true);
    return;
  }
  const questionText = question.trim();
  if (!questionText) {
    showNotice("Enter a question before submitting.", true);
    return;
  }

  const form = byId("analyst-form");
  await withFormLoading(form, async () => {
    showNotice("");
    const userMessage = appendHistoryMessage("user", questionText);
    byId("analyst-question").value = "";
    try {
      const conversationId = await ensureAnalystConversation(datasetIds);
      const requestId = crypto.randomUUID();
      const isSQL = mode === "sql_analysis";
      const response = await requestJson(
        `/conversations/${encodeURIComponent(conversationId)}/${isSQL ? "sql-analysis" : "ask"}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(isSQL
            ? { question: questionText, request_id: requestId }
            : { query: questionText, top_k: 5, request_id: requestId }),
        },
      );
      userMessage.dataset.requestId = requestId;
      const answerText = isSQL
        ? (response.analysis.clarification ||
          `${response.analysis.analysis_title}: ${response.analysis.queries.filter((query) => query.execution_status === "success").length} of ${response.analysis.queries.length} result tables completed.`)
        : response.answer;
      const assistantMessage = appendHistoryMessage("assistant", answerText);
      if (isSQL) renderSQLAnalysis(assistantMessage, response.analysis, questionText);
      else renderEvidence(assistantMessage, response);
    } catch (error) {
      appendHistoryMessage("assistant", `Request failed: ${error.message}`, "error");
      showNotice(error.message, true);
    }
  });
}

byId("analyst-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await submitAnalystQuestion(
    byId("analyst-question").value,
    event.submitter?.dataset.mode || "sql_analysis",
  );
});

byId("load-graph").addEventListener("click", async (event) => {
  // Fetch and summarize graph nodes and candidate links for the standalone UI.
  const button = event.currentTarget;
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "Loading...";
  const content = byId("graph-content");
  try {
    const graph = await requestJson("/graph/?limit=500");
    clear(content, "graph-list");
    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const nodeById = new Map(nodes.map((node) => [node.id, node]));
    const summary = document.createElement("p");
    summary.className = "result-title";
    summary.textContent =
      `${graph.dataset_count} datasets | ${nodes.length} nodes | ${edges.length} edges shown`;
    content.append(summary);

    const entries = [];
    nodes.slice(0, 30).forEach((node) => {
      entries.push({
        title: node.label || node.id,
        detail: node.type === "Column" ? `${node.type} | ${node.data_type || "UNKNOWN"}` : node.type,
      });
    });
    edges.filter((edge) => edge.type === "CANDIDATE_MATCH" || edge.type === "RELATED_TO")
      .slice(0, 30)
      .forEach((edge) => {
        const source = nodeById.get(edge.source);
        const target = nodeById.get(edge.target);
        entries.push({
          title: `${source?.label || edge.source} <-> ${target?.label || edge.target}`,
          detail: `${edge.status || "relationship"} | confidence ${edge.confidence}`,
        });
      });

    entries.forEach((entry) => {
      const item = document.createElement("div");
      item.className = "graph-item";
      const title = document.createElement("strong");
      title.textContent = entry.title;
      const detail = document.createElement("span");
      detail.textContent = entry.detail;
      item.append(title, detail);
      content.append(item);
    });
    if (!entries.length) {
      content.className = "empty-state";
      content.textContent = "No graph nodes or relationship candidates were found.";
    }
  } catch (error) {
    content.className = "empty-state";
    content.textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});

loadDatasets().catch((error) => showNotice(error.message, true));
