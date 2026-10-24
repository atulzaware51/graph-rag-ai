// ```javascript
const API_BASE_URL = ''

async function request(endpoint, options = {}) {
  let response

  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, options)
  } catch {
    throw new Error(
      'Cannot connect to the API. Check that FastAPI is running on port 8000.',
    )
  }

  const contentType = response.headers.get('content-type') || ''

  let data

  try {
    data = contentType.includes('application/json')
      ? await response.json()
      : await response.text()
  } catch {
    throw new Error('The server returned an invalid response.')
  }

  if (!response.ok) {
    const detail =
      typeof data === 'object' && data !== null
        ? data.detail || data.message
        : data

    throw new Error(
      detail
        ? String(detail)
        : `Request failed: HTTP ${response.status}`,
    )
  }

  return data
}

// Check whether the FastAPI backend is running.
export function checkHealth() {
  return request('/health')
}

// Retrieve the available datasets.
export async function getDatasets() {
  const data = await request('/datasets/')

  if (!data || !Array.isArray(data.datasets)) {
    throw new Error('Unexpected response from the datasets API.')
  }

  return data.datasets
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

  const allowedExtensions = ['.csv', '.xlsx']
  const filename = file.name.toLowerCase()

  if (!allowedExtensions.some((extension) => filename.endsWith(extension))) {
    return Promise.reject(
      new Error('Unsupported file type. Upload a CSV or XLSX file.'),
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
          'Cannot connect to the API. Check that FastAPI is running on port 8000.',
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