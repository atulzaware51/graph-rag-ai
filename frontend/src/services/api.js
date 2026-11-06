// Centralize HTTP handling and endpoint helpers used by the React workspace.
const API_BASE_URL = ''

async function request(endpoint, options = {}) {
  // Normalize network, response-format, and API errors for the UI.
  let response

  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, options)
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new Error(
      'Cannot connect to the API. Check that FastAPI is running on port 8001.',
    )
  }

  const contentType = response.headers.get('content-type') || ''

  let data

  try {
    data = contentType.includes('application/json')
      ? await response.json()
      : await response.text()
  } catch (error) {
    if (error?.name === 'AbortError') throw error
    throw new Error('The server returned an invalid response.')
  }

  if (!response.ok) {
    const rawDetail =
      typeof data === 'object' && data !== null
        ? data.detail || data.message
        : data
    const errorCode = (
      typeof rawDetail === 'object' && rawDetail !== null
        ? rawDetail.code
        : undefined
    )
    const detail =
      typeof rawDetail === 'object' && rawDetail !== null
        ? rawDetail.message || rawDetail.code
        : rawDetail

    const friendlyMessages = {
      LLM_PROVIDER_NOT_CONFIGURED:
        'Provider configuration error: no usable LLM API key is configured.',
      LLM_PROVIDER_CONFIGURATION_INVALID:
        'Provider configuration error: the selected provider settings are invalid.',
      AUTHENTICATION_FAILED:
        'Provider authentication error: the configured API key was rejected.',
      RATE_LIMITED:
        'Provider rate limit: requests are temporarily limited. Please try again shortly.',
      GRAPH_RETRIEVAL_FAILED:
        'Knowledge Graph unavailable. Chunk-based retrieval may still be used.',
    }
    const error = new Error(
      friendlyMessages[errorCode] || (
        detail
          ? String(detail)
          : `Request failed: HTTP ${response.status}`
      ),
    )
    error.code = errorCode
    throw error
  }

  return data
}

// Check whether the FastAPI backend is running.
export function checkHealth() {
  return request('/health')
}

// Retrieve the available datasets.
export async function getDatasets() {
  // Validate the collection shape before components consume the dataset list.
  const data = await request('/datasets/')

  if (!data || !Array.isArray(data.datasets)) {
    throw new Error('Unexpected response from the datasets API.')
  }

  return data.datasets
}

export async function getConversations() {
  // Fetch conversation summaries for the chat selector.
  const data = await request('/conversations/')
  if (!data || !Array.isArray(data.conversations)) {
    throw new Error('Unexpected response from the conversations API.')
  }
  return data.conversations
}

export async function getConversation(conversationId) {
  // Load a conversation's events so the UI can reconstruct its messages and results.
  const data = await request(
    `/conversations/${encodeURIComponent(conversationId)}`,
  )
  if (!data?.conversation || !Array.isArray(data.conversation.events)) {
    throw new Error('Unexpected response from the conversation API.')
  }
  return data.conversation
}

export async function createConversation(title = 'New chat', conversationId) {
  // Create a persistent conversation, optionally preserving a legacy client ID.
  const data = await request('/conversations/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      ...(conversationId ? { conversation_id: conversationId } : {}),
    }),
  })
  if (!data?.conversation?.conversation_id) {
    throw new Error('The server did not return a conversation ID.')
  }
  return data.conversation
}

export function deleteConversation(conversationId) {
  // Delete server-side conversation history for the selected chat.
  return request(
    `/conversations/${encodeURIComponent(conversationId)}`,
    { method: 'DELETE' },
  )
}

export function addConversationMessage(conversationId, actor, message, metadata = {}) {
  // Append a user or assistant message with structured metadata.
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ actor, message, metadata }),
    },
  )
}

export function addConversationEvent(conversationId, eventType, message, metadata = {}) {
  // Save non-message events such as uploads and SQL result references.
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/events`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: eventType,
        message,
        metadata,
      }),
    },
  )
}

export async function getConversationGraph(conversationId) {
  // Fetch and normalize graph node types for the chat graph visualizer.
  const data = await request(
    `/conversations/${encodeURIComponent(conversationId)}/graph`,
  )
  if (
    !data?.graph ||
    !Array.isArray(data.graph.nodes) ||
    !Array.isArray(data.graph.edges)
  ) {
    throw new Error('Unexpected response from the conversation graph API.')
  }
  return {
    ...data.graph,
    nodes: data.graph.nodes.map((node) => ({
      ...node,
      type: node.type === 'SQLQuery'
        ? 'Query'
        : node.type === 'QueryResult'
          ? 'Result'
          : node.type,
      uploaded_at: node.type === 'Upload' ? node.created_at : undefined,
      executed_at: node.type === 'SQLQuery' ? node.created_at : undefined,
    })),
  }
}

export function updateConversationDatasets(conversationId, datasetIds) {
  // Persist which sheets are allowed as context for this conversation.
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/datasets`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dataset_ids: datasetIds }),
    },
  )
}

export function askConversation(
  conversationId,
  query,
  topK = 5,
  requestId = crypto.randomUUID(),
  signal,
) {
  // Ask the chat-scoped RAG endpoint to answer from selected-sheet evidence.
  if (!conversationId) {
    throw new Error('Create a chat before asking a question.')
  }
  if (!query || !query.trim()) {
    throw new Error('Enter a question before submitting.')
  }
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/ask`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query: query.trim(),
        top_k: topK,
        request_id: requestId,
      }),
      signal,
    },
  )
}

export function askConversationSQL(conversationId, question) {
  // Request one SQL-backed answer scoped to the chat's selected sheets.
  if (!conversationId) {
    throw new Error('Create a chat before running SQL analysis.')
  }
  if (!question || !question.trim()) {
    throw new Error('Enter a question before submitting.')
  }
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/sql`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: question.trim() }),
    },
  )
}

export async function analyzeConversationSQL(
  conversationId,
  question,
  requestId = crypto.randomUUID(),
  mode = 'analysis',
  signal,
) {
  // Request multi-query SQL analysis or code generation and validate the response.
  if (!conversationId) {
    throw new Error('Create a chat before generating an analysis.')
  }
  if (!question || !question.trim()) {
    throw new Error('Enter a question before submitting.')
  }
  const data = await request(
    `/conversations/${encodeURIComponent(conversationId)}/sql-analysis`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question: question.trim(),
        request_id: requestId,
        mode,
      }),
      signal,
    },
  )
  if (!data?.analysis || !Array.isArray(data.analysis.queries)) {
    throw new Error('The server returned an invalid SQL analysis plan.')
  }
  return data
}

export async function getConversationSQLRelationships(conversationId) {
  // Load possible cross-sheet joins and their current approval status.
  const data = await request(
    `/conversations/${encodeURIComponent(conversationId)}/sql-relationships`,
  )
  if (!Array.isArray(data?.relationships)) {
    throw new Error('Unexpected response from SQL relationship analysis.')
  }
  return data.relationships
}

export function approveConversationSQLRelationship(
  conversationId,
  relationshipId,
) {
  // Record explicit user approval before a generated query can use the join.
  return request(
    `/conversations/${encodeURIComponent(conversationId)}/sql-relationships/${encodeURIComponent(relationshipId)}/approval`,
    { method: 'POST' },
  )
}

// Delete a dataset and the backend state associated with its spreadsheet.
export function deleteDataset(datasetId) {
  if (!datasetId) {
    throw new Error('Select a dataset before deleting it.')
  }

  return request(
    `/datasets/${encodeURIComponent(datasetId)}`,
    { method: 'DELETE' },
  )
}

// Retrieve dataset column information.
export function getDatasetSchema(datasetId) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/schema`,
  )
}

// Retrieve a preview of the dataset rows.
export function getDatasetPreview(datasetId) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/preview`,
  )
}

export async function searchDataset(datasetId, query, topK = 5) {
  // Find relevant chunks for a sheet and return only a validated result list.
  if (!datasetId) {
    throw new Error('Select a dataset before viewing its evidence.')
  }
  if (!query || !query.trim()) {
    throw new Error('Enter a question before searching for evidence.')
  }

  const data = await request(
    `/datasets/${encodeURIComponent(datasetId)}/search?query=${encodeURIComponent(query.trim())}&top_k=${encodeURIComponent(topK)}`,
  )
  if (!data || !Array.isArray(data.results)) {
    throw new Error('Unexpected response from the dataset search API.')
  }
  return data.results
}

// Ask the AI Analyst a question about a dataset.
export function askDataset(datasetId, query, topK = 5) {
  if (!datasetId) {
    throw new Error('Select a dataset before asking a question.')
  }

  if (!query || !query.trim()) {
    throw new Error('Enter a question before submitting.')
  }

  return request(
    `/datasets/${encodeURIComponent(datasetId)}/ask`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query: query.trim(),
        top_k: topK,
      }),
    },
  )
}

// Convert a natural-language question into a SQL analysis request.
export function askDatasetSQL(datasetId, question) {
  if (!datasetId) {
    throw new Error('Select a dataset before running SQL analysis.')
  }

  if (!question || !question.trim()) {
    throw new Error('Enter a question before submitting.')
  }

  return request(
    `/datasets/${encodeURIComponent(datasetId)}/ask-sql`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        question: question.trim(),
      }),
    },
  )
}

// Upload a CSV or Excel file and report upload progress.
export function uploadDataset(file, onProgress) {
  if (!file) {
    return Promise.reject(new Error('Select a file to upload.'))
  }

  const allowedExtensions = ['.csv', '.xls', '.xlsx']
  const filename = file.name.toLowerCase()

  if (!allowedExtensions.some((extension) => filename.endsWith(extension))) {
    return Promise.reject(
      new Error('Unsupported file type. Upload a CSV or Excel file.'),
    )
  }

  const MAX_FILE_SIZE = 10 * 1024 * 1024

  if (file.size > MAX_FILE_SIZE) {
    return Promise.reject(
      new Error('File exceeds the 10 MB upload limit.'),
    )
  }

  const formData = new FormData()
  formData.append('file', file)

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()

    xhr.open('POST', `${API_BASE_URL}/documents/upload`)
    xhr.setRequestHeader('Accept', 'application/json')

    xhr.upload.addEventListener('progress', (event) => {
      if (
        event.lengthComputable &&
        typeof onProgress === 'function'
      ) {
        onProgress(
          Math.round((event.loaded / event.total) * 100),
        )
      }
    })

    xhr.addEventListener('load', () => {
      let data

      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        reject(
          new Error('The server returned an invalid upload response.'),
        )
        return
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const detail =
          typeof data.detail === 'string'
            ? data.detail
            : typeof data.message === 'string'
              ? data.message
              : `Upload failed: HTTP ${xhr.status}`

        reject(new Error(detail))
        return
      }

      if (typeof onProgress === 'function') {
        onProgress(100)
      }

      resolve(data)
    })

    xhr.addEventListener('error', () => {
      reject(
        new Error(
          'Cannot connect to the API. Check that FastAPI is running on port 8001.',
        ),
      )
    })

    xhr.addEventListener('abort', () => {
      reject(new Error('The upload was cancelled.'))
    })

    xhr.send(formData)
  })
}


// Retrieve the knowledge graph for visualization.
export async function getKnowledgeGraph(limit = 500) {
  const data = await request(
    `/graph/?limit=${encodeURIComponent(limit)}`,
  )

  if (
    !data ||
    !Array.isArray(data.nodes) ||
    !Array.isArray(data.edges)
  ) {
    throw new Error('Unexpected response from the knowledge graph API.')
  }

  return data
}