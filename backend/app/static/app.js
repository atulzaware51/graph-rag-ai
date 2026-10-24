const byId = (id) => document.getElementById(id);

const notice = byId("notice");
const datasetSelect = byId("dataset-select");

function showNotice(message, isError = false) {
  notice.textContent = message;
  notice.hidden = !message;
  notice.classList.toggle("error", isError);
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  let body;
  try {
    body = await response.json();
  } catch {
    throw new Error(`The server returned an unreadable response (${response.status}).`);
  }
  if (!response.ok) {
    throw new Error(body.detail || `Request failed (${response.status}).`);
  }
  return body;
}

function clear(element, className = "") {
  element.replaceChildren();
  element.className = className;
}

function makeTable(container, columns, rows) {
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
  return datasetSelect.value;
}

async function loadDatasets(preferredId = "") {
  const response = await requestJson("/datasets/");
  const datasets = response.datasets || [];
  clear(datasetSelect);

  if (!datasets.length) {
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
  await loadDataset(nextId);
}

async function loadDataset(datasetId) {
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
  const button = form.querySelector("button[type=submit]");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Working...";
  try {
    await action();
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

byId("file-input").addEventListener("change", (event) => {
  const file = event.target.files[0];
  byId("file-name").textContent = file ? file.name : "Choose a CSV or XLSX file";
});

byId("upload-form").addEventListener("submit", async (event) => {
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
  try {
    await loadDatasets(selectedDatasetId());
  } catch (error) {
    showNotice(error.message, true);
  }
});

datasetSelect.addEventListener("change", async () => {
  try {
    await loadDataset(selectedDatasetId());
    showNotice("");
  } catch (error) {
    showNotice(error.message, true);
  }
});

byId("rag-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const datasetId = selectedDatasetId();
  if (!datasetId) {
    showNotice("Select a dataset before asking a question.", true);
    return;
  }
  const output = byId("rag-result");
  await withFormLoading(event.currentTarget, async () => {
    clear(output, "result-box");
    try {
      const result = await requestJson(`/datasets/${encodeURIComponent(datasetId)}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: byId("rag-question").value,
          top_k: 5,
        }),
      });
      const answer = document.createElement("p");
      answer.textContent = result.answer;
      output.append(answer);

      if (result.sources && result.sources.length) {
        const title = document.createElement("p");
        title.className = "result-title";
        title.textContent = "Evidence sources";
        const sources = document.createElement("ul");
        sources.className = "source-list";
        result.sources.forEach((source) => {
          const item = document.createElement("li");
          item.textContent = `Chunk ${source.chunk_index} (${source.chunk_id}) | score ${source.score}`;
          sources.append(item);
        });
        output.append(title, sources);
      }
      output.classList.remove("empty-state");
    } catch (error) {
      output.className = "result-box";
      output.textContent = error.message;
    }
  });
});

function renderChart(columns, rows) {
  const chart = byId("sql-chart");
  clear(chart, "chart");
  if (!columns || columns.length < 2 || !rows || rows.length < 1) return;

  const valueColumn = columns.find((column) =>
    rows.some((row) => typeof row[column] === "number" && Number.isFinite(row[column])),
  );
  if (!valueColumn) return;
  const labelColumn = columns.find((column) => column !== valueColumn);
  const values = rows.slice(0, 12).map((row) => ({
    label: String(row[labelColumn] ?? "(empty)"),
    value: Number(row[valueColumn]),
  })).filter((item) => Number.isFinite(item.value));
  if (!values.length) return;
  const maximum = Math.max(...values.map((item) => Math.abs(item.value)), 1);

  values.forEach((item) => {
    const line = document.createElement("div");
    line.className = "chart-row";
    const label = document.createElement("span");
    label.className = "chart-label";
    label.textContent = item.label;
    const track = document.createElement("span");
    track.className = "bar-track";
    const bar = document.createElement("span");
    bar.className = "bar";
    bar.style.width = `${Math.max(2, Math.abs(item.value) / maximum * 100)}%`;
    track.append(bar);
    const value = document.createElement("span");
    value.className = "chart-value";
    value.textContent = String(item.value);
    line.append(label, track, value);
    chart.append(line);
  });
}

byId("sql-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const datasetId = selectedDatasetId();
  if (!datasetId) {
    showNotice("Select a dataset before running an analysis.", true);
    return;
  }
  const output = byId("sql-result");
  await withFormLoading(event.currentTarget, async () => {
    clear(output, "result-box");
    clear(byId("sql-chart"), "chart");
    try {
      const response = await requestJson(
        `/datasets/${encodeURIComponent(datasetId)}/ask-sql`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question: byId("sql-question").value }),
        },
      );
      const sql = document.createElement("code");
      sql.className = "sql-code";
      sql.textContent = response.sql;
      const summary = document.createElement("p");
      summary.className = "result-title";
      summary.textContent = `${response.result.row_count} result rows`;
      const table = document.createElement("div");
      makeTable(table, response.result.columns, response.result.rows);
      output.append(sql, summary, table);
      renderChart(response.result.columns, response.result.rows);
    } catch (error) {
      output.className = "result-box";
      output.textContent = error.message;
    }
  });
});

byId("load-graph").addEventListener("click", async (event) => {
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
