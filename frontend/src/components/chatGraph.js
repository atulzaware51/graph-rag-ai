function graphId(value) {
  return encodeURIComponent(String(value ?? ''))
}

export function buildChatGraph(chat) {
  const nodes = []
  const edges = []
  const uploads = Array.isArray(chat?.uploads) ? chat.uploads : []
  const queries = Array.isArray(chat?.graphQueries)
    ? chat.graphQueries
    : []
  const title = chat?.title || 'New chat'
  const rootId = `chat:${graphId(chat?.id || title)}`
  const sheets = new Map()
  const entityIds = new Set()

  nodes.push({
    id: rootId,
    type: 'Chat',
    label: title,
    details: `Chat graph: ${title}`,
  })

  for (const upload of uploads) {
    const identity = upload.content_fingerprint || upload.dataset_id
    if (!identity) continue

    const sheetId = `sheet:${graphId(identity)}`
    let sheet = sheets.get(sheetId)
    if (!sheet) {
      sheet = {
        id: sheetId,
        type: 'Dataset',
        label: upload.filename || 'Untitled sheet',
        dataset_id: upload.dataset_id,
        table_name: upload.table_name,
        content_fingerprint: upload.content_fingerprint,
        columns: Array.isArray(upload.columns) ? upload.columns : [],
        uploads: [],
      }
      sheets.set(sheetId, sheet)
      nodes.push(sheet)
      edges.push({
        id: `chat-sheet:${graphId(identity)}`,
        source: rootId,
        target: sheetId,
        type: 'HAS_SHEET',
      })

      for (const column of sheet.columns) {
        const sqlName = column.sql_name || column.original_name
        if (!sqlName) continue
        const entityId = `entity:${graphId(identity)}:${graphId(sqlName)}`
        entityIds.add(entityId)
        nodes.push({
          id: entityId,
          type: 'Entity',
          label: column.original_name || sqlName,
          dataset_id: upload.dataset_id,
          sql_name: sqlName,
          data_type: column.data_type,
          sheet_id: sheetId,
        })
        edges.push({
          id: `sheet-entity:${graphId(identity)}:${graphId(sqlName)}`,
          source: sheetId,
          target: entityId,
          type: 'HAS_ENTITY',
        })
      }
    }

    sheet.uploads.push(upload)
    const uploadId = `upload:${graphId(upload.upload_id || upload.dataset_id)}`
    const uploadedLabel = upload.uploaded_at
      ? new Date(upload.uploaded_at).toLocaleString()
      : 'Upload time unavailable'
    nodes.push({
      id: uploadId,
      type: 'Upload',
      label: `Uploaded ${uploadedLabel}`,
      dataset_id: upload.dataset_id,
      uploaded_at: upload.uploaded_at,
      filename: upload.filename,
    })
    edges.push({
      id: `sheet-upload:${uploadId}`,
      source: sheetId,
      target: uploadId,
      type: 'UPLOAD_EVENT',
    })
  }

  for (const query of queries) {
    const identity = query.content_fingerprint || query.dataset_id
    const sheetId = `sheet:${graphId(identity)}`
    if (!sheets.has(sheetId)) {
      const sheet = {
        id: sheetId,
        type: 'Dataset',
        label: query.dataset_name || 'Selected sheet',
        dataset_id: query.dataset_id,
        table_name: query.table_name,
        columns: [],
        uploads: [],
      }
      sheets.set(sheetId, sheet)
      nodes.push(sheet)
      edges.push({
        id: `chat-sheet:${graphId(identity)}`,
        source: rootId,
        target: sheetId,
        type: 'HAS_SHEET',
      })
      for (const column of query.read_columns || []) {
        const entityId = `entity:${graphId(identity)}:${graphId(column)}`
        if (entityIds.has(entityId)) continue
        entityIds.add(entityId)
        nodes.push({
          id: entityId,
          type: 'Entity',
          label: column,
          dataset_id: query.dataset_id,
          sql_name: column,
          sheet_id: sheetId,
        })
        edges.push({
          id: `sheet-entity:${graphId(identity)}:${graphId(column)}`,
          source: sheetId,
          target: entityId,
          type: 'HAS_ENTITY',
        })
      }
    }

    const queryId = `query:${graphId(query.query_id)}`
    nodes.push({
      id: queryId,
      type: 'Query',
      label: query.question,
      question: query.question,
      sql: query.sql,
      executed_at: query.executed_at,
      row_count: query.row_count,
      dataset_id: query.dataset_id,
    })
    edges.push({
      id: `query-sheet:${graphId(query.query_id)}`,
      source: sheetId,
      target: queryId,
      type: 'QUERY_ON',
    })

    for (const column of query.read_columns || []) {
      const entityId = `entity:${graphId(identity)}:${graphId(column)}`
      if (entityIds.has(entityId)) {
        edges.push({
          id: `query-entity:${graphId(query.query_id)}:${graphId(column)}`,
          source: queryId,
          target: entityId,
          type: 'USES_ENTITY',
        })
      }
    }

    const resultId = `result:${graphId(query.query_id)}`
    nodes.push({
      id: resultId,
      type: 'Result',
      label: `Results (${query.row_count} rows)`,
      columns: query.result_columns || [],
      rows: query.rows || [],
      row_count: query.row_count,
      executed_at: query.executed_at,
    })
    edges.push({
      id: `query-result:${graphId(query.query_id)}`,
      source: queryId,
      target: resultId,
      type: 'QUERY_RESULT',
      query: query.sql,
    })
  }

  return { nodes, edges }
}
