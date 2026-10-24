
/**
 * GraphSheet AI — API service
 *
 * Centralizes communication between the React frontend
 * and the FastAPI backend.
 */

const API_BASE_URL = '';

/**
 * Send an HTTP request and return parsed JSON.
 * Provides a readable error when the backend rejects a request.
 */
async function request(endpoint, options = {}) {
  let response;

  try {
    response = await fetch(`${API_BASE_URL}${endpoint}`, options);
  } catch {
    throw new Error(
      'Cannot connect to GraphSheet AI API. ' +
        'Check that the backend is running on port 8000.'
    );
  }

  const contentType = response.headers.get('content-type') || '';

  let data;

  if (contentType.includes('application/json')) {
    data = await response.json();
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    const detail =
      typeof data === 'object' && data !== null
        ? data.detail || data.message
        : data;

    throw new Error(
      detail
        ? String(detail)
        : `Request failed with status ${response.status}.`
    );
  }

  return data;
}

/**
 * Check backend health.
 */
export function checkHealth() {
  return request('/health');
}

/**
 * Retrieve stored datasets.
 *
 * Expected response:
 * {
 *   status: "success",
 *   count: number,
 *   datasets: [...]
 * }
 */
export async function getDatasets() {
  const data = await request('/datasets/');

  if (!Array.isArray(data.datasets)) {
    throw new Error('The dataset API returned an unexpected response.');
  }

  return data.datasets;
}

/**
 * Retrieve a dataset's schema.
 */
export function getDatasetSchema(datasetId) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/schema`
  );
}

/**
 * Retrieve a dataset preview.
 */
export function getDatasetPreview(datasetId) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/preview`
  );
}

/**
 * Ask a question using retrieval-augmented generation (RAG).
 */
export function askDataset(datasetId, query, topK = 5) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/ask`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        query,
        top_k: topK,
      }),
    }
  );
}

/**
 * Ask a question using natural-language-to-SQL.
 */
export function askDatasetSQL(datasetId, question) {
  return request(
    `/datasets/${encodeURIComponent(datasetId)}/ask-sql`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ question }),
    }
  );
}





export function uploadDataset(file, onProgress) {
  const formData = new FormData()
  formData.append('file', file)

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()

    xhr.open('POST', '/documents/upload')

    xhr.upload.addEventListener('progress', (event) => {
      if (event.lengthComputable && typeof onProgress === 'function') {
        const percent = Math.round((event.loaded / event.total) * 100)
        onProgress(percent)
      }
    })

    xhr.addEventListener('load', () => {
      let data

      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        reject(new Error('The server returned an invalid response.'))
        return
      }

      if (xhr.status < 200 || xhr.status >= 300) {
        const detail = data.detail || data.message
        reject(new Error(detail || `Upload failed: HTTP ${xhr.status}`))
        return
      }

      resolve(data)
    })

    xhr.addEventListener('error', () => {
      reject(new Error('Cannot connect to the API. Check that FastAPI is running.'))
    })

    xhr.addEventListener('abort', () => {
      reject(new Error('The upload was cancelled.'))
    })

    xhr.send(formData)
  })
}
