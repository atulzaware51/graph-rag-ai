import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import KnowledgeGraph from './components/KnowledgeGraph'
import SQLResultChart from './components/SQLResultChart'
import { useCallback, useEffect, useRef, useState } from 'react'

import {
  getDatasets,
  checkHealth,
  uploadDataset,
  askDataset,
  askDatasetSQL,
} from './services/api'

import './App.css'

const navigation = [
  {
    section: 'WORKSPACE',
    items: [
      { id: 'overview', icon: '◫', label: 'Overview' },
      { id: 'datasets', icon: '▤', label: 'Datasets' },
      { id: 'analyst', icon: '✳', label: 'AI Analyst' },
      { id: 'sql', icon: '⌘', label: 'SQL Studio' },
      { id: 'knowledge', icon: '◎', label: 'Knowledge Graph' },
    ],
  },
  {
    section: 'PREFERENCES',
    items: [{ id: 'settings', icon: '⚙', label: 'Settings' }],
  },
]

const features = [
  {
    icon: '▤',
    title: 'Explore datasets',
    text: 'Upload spreadsheets and inspect their structure.',
    target: 'datasets',
  },
  {
    icon: '✳',
    title: 'Ask your data',
    text: 'Get AI-assisted answers grounded in your data.',
    target: 'analyst',
  },
  {
    icon: '⌘',
    title: 'Generate SQL',
    text: 'Turn natural-language questions into SQL queries.',
    target: 'sql',
  },
  {
    icon: '◎',
    title: 'Explore relationships',
    text: 'Discover connections in your knowledge graph.',
    target: 'knowledge',
  },
]



const CHAT_STORAGE_KEY = 'graphsheet-ai-chat-sessions'

function createChatSession() {
  const now = new Date().toISOString()

  return {
    id: crypto.randomUUID(),
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    messages: [],
    uploads: [],
    graphQueries: [],
  }
}

function loadChatSessions() {
  try {
    const saved = localStorage.getItem(CHAT_STORAGE_KEY)

    if (!saved) {
      return [createChatSession()]
    }

    const parsed = JSON.parse(saved)

    if (!Array.isArray(parsed) || parsed.length === 0) {
      return [createChatSession()]
    }

    return parsed
  } catch {
    return [createChatSession()]
  }
}
const MAX_FILE_SIZE = 10 * 1024 * 1024
const ACCEPTED_EXTENSIONS = ['.csv', '.xlsx']

function formatNumber(value) {
  return (Number(value) || 0).toLocaleString()
}

function getFileExtension(filename) {
  return filename.slice(filename.lastIndexOf('.')).toLowerCase()
}

function App() {
 const [chatSessions, setChatSessions] = useState(loadChatSessions)

const [activeChatId, setActiveChatId] = useState(
  () => loadChatSessions()[0]?.id
)

const [chatInput, setChatInput] = useState('')
const [chatLoading, setChatLoading] = useState(false)
const [chatError, setChatError] = useState('')
const [sqlInput, setSqlInput] = useState('')
const [sqlLoading, setSqlLoading] = useState(false)
const [sqlError, setSqlError] = useState('')
const [sqlResult, setSqlResult] = useState(null)
  const [activePage, setActivePage] = useState('overview')
  const [question, setQuestion] = useState('')
  const [datasets, setDatasets] = useState([])
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [apiStatus, setApiStatus] = useState('checking')
  const [error, setError] = useState('')

  const [selectedFile, setSelectedFile] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [uploadError, setUploadError] = useState('')
  const [uploadSuccess, setUploadSuccess] = useState('')
  const fileInputRef = useRef(null)

  const loadDatasets = useCallback(async (showLoading = true) => {
    if (showLoading) {
      setLoading(true)
    } else {
      setRefreshing(true)
    }

    setError('')

    try {
      const datasetList = await getDatasets()
      setDatasets(datasetList)
      setApiStatus('connected')
      return true
    } catch (err) {
      setApiStatus('disconnected')
      setError(err.message || 'Unable to load datasets.')
      return false
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    async function initializeDashboard() {
      try {
        const health = await checkHealth()

        if (cancelled) return

        setApiStatus(
          health.status === 'healthy' ? 'connected' : 'disconnected',
        )
      } catch {
        if (!cancelled) setApiStatus('disconnected')
      }

      if (!cancelled) {
        await loadDatasets(true)
      }
    }

    initializeDashboard()

    return () => {
      cancelled = true
    }
  }, [loadDatasets])

  const pageNames = {
    overview: 'Overview',
    datasets: 'Datasets',
    analyst: 'AI Analyst',
    sql: 'SQL Studio',
    knowledge: 'Knowledge Graph',
    settings: 'Settings',
  }



  // 
  // Save chat history locally whenever a session changes.
useEffect(() => {
  try {
    localStorage.setItem(
      CHAT_STORAGE_KEY,
      JSON.stringify(chatSessions),
    )
  } catch (err) {
    console.error('Unable to save chat history:', err)
  }
}, [chatSessions])

// Find the currently selected chat.
const activeChat =
  chatSessions.find((chat) => chat.id === activeChatId) ??
  chatSessions[0]

// Update a single chat without changing other sessions.
function updateChat(chatId, updater) {
  setChatSessions((current) =>
    current.map((chat) =>
      chat.id === chatId
        ? updater(chat)
        : chat,
    ),
  )
}

// Start a fresh conversation.
function createNewChat() {
  const newChat = createChatSession()

  setChatSessions((current) => [
    newChat,
    ...current,
  ])

  setActiveChatId(newChat.id)
  setChatInput('')
  setChatError('')
  setSqlInput('')
  setSqlError('')
  setSqlResult(null)
  setActivePage('analyst')
}

// Add a message to a specific conversation.
function appendChatMessage(chatId, message) {
  updateChat(chatId, (chat) => {
    const messages = [...chat.messages, message]

    const firstUserMessage = messages.find(
      (item) => item.role === 'user',
    )

    return {
      ...chat,
      messages,
      title:
        chat.title === 'New chat' && firstUserMessage
          ? firstUserMessage.content.slice(0, 48)
          : chat.title,
      updatedAt: new Date().toISOString(),
    }
  })
}

// Choose a dataset explicitly associated with this chat.
// For now, an uploaded dataset is associated through its dataset ID.
function getChatDataset(chat) {
  const datasetId = chat?.datasetId

  if (!datasetId) return null

  return datasets.find(
    (dataset) => dataset.dataset_id === datasetId,
  ) ?? null
}

// Extract readable text from common API response formats.
function getResponseText(data) {
  if (typeof data === 'string') return data

  if (data && typeof data === 'object') {
    const candidates = [
      data.answer,
      data.response,
      data.message,
      data.result,
      data.data?.answer,
      data.data?.response,
    ]

    const text = candidates.find(
      (value) => typeof value === 'string' && value.trim(),
    )

    if (text) return text
  }

  return JSON.stringify(data, null, 2)
}

async function sendChatQuestion(text = chatInput) {
  const query = text.trim()

  if (!query || chatLoading) return

  const chat = activeChat

  if (!chat) {
    setChatError('Create a chat before asking a question.')
    return
  }

  const dataset = getChatDataset(chat)

  if (!dataset) {
    setChatError(
      'This chat has no associated dataset yet. Upload a spreadsheet and select it for this chat first.',
    )
    setActivePage('datasets')
    return
  }

  const chatId = chat.id

  setChatInput('')
  setChatError('')
  setChatLoading(true)

  appendChatMessage(chatId, {
    id: crypto.randomUUID(),
    role: 'user',
    content: query,
    createdAt: new Date().toISOString(),
  })

  try {
    const response = await askDataset(
      dataset.dataset_id,
      query,
    )

    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: getResponseText(response),
      createdAt: new Date().toISOString(),
    })
  } catch (err) {
    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: `Request failed: ${err.message || 'Unable to get an answer.'}`,
      createdAt: new Date().toISOString(),
    })
  } finally {
    setChatLoading(false)
  }
}



async function runSqlQuestion() {
  const questionText = sqlInput.trim()

  if (!questionText || sqlLoading) return

  const dataset = getChatDataset(activeChat)

  if (!dataset) {
    setSqlError(
      'Select a dataset for this chat before running SQL analysis.',
    )
    return
  }

  setSqlLoading(true)
  setSqlError('')
  setSqlResult(null)

  try {
    const response = await askDatasetSQL(
      dataset.dataset_id,
      questionText,
    )

    // The API may return the workflow result inside "result".
    const payload = response?.result ?? response

    if (
      !payload ||
      typeof payload !== 'object' ||
      payload.status !== 'success'
    ) {
      throw new Error(
        payload?.error || 'SQL analysis returned an invalid response.',
      )
    }

    // The SQL service result is nested inside payload.result.
    const queryResult = payload.result

    if (
      !queryResult ||
      typeof queryResult !== 'object' ||
      !Array.isArray(queryResult.columns) ||
      !Array.isArray(queryResult.rows)
    ) {
      throw new Error(
        'SQL analysis returned results in an unexpected format.',
      )
    }

    setSqlResult({
      question: payload.question || questionText,
      sql: payload.sql || '',
      status: queryResult.status || 'success',
      columns: queryResult.columns,
      rows: queryResult.rows,
      rowCount: queryResult.row_count ?? queryResult.rows.length,
    })
    if (activeChat) {
      const upload = (activeChat.uploads || []).find(
        (item) => item.dataset_id === dataset.dataset_id,
      )
      const queryId = crypto.randomUUID()
      updateChat(activeChat.id, (chat) => ({
        ...chat,
        title:
          chat.title === 'New chat'
            ? questionText.slice(0, 48)
            : chat.title,
        graphQueries: [
          ...(chat.graphQueries || []),
          {
            query_id: queryId,
            question: payload.question || questionText,
            sql: payload.sql || '',
            dataset_id: dataset.dataset_id,
            dataset_name: dataset.original_filename,
            table_name: dataset.table_name,
            content_fingerprint: upload?.content_fingerprint,
            read_columns: queryResult.read_columns || [],
            result_columns: queryResult.columns,
            rows: queryResult.rows,
            row_count: queryResult.row_count ?? queryResult.rows.length,
            executed_at: new Date().toISOString(),
          },
        ],
        updatedAt: new Date().toISOString(),
      }))
    }
  } catch (err) {
    setSqlError(
      err.message || 'SQL analysis failed. Please try again.',
    )
  } finally {
    setSqlLoading(false)
  }
}


  const totalRows = datasets.reduce(
    

    (total, dataset) => total + (Number(dataset.row_count) || 0),
    0,
  )

  const totalColumns = datasets.reduce(
    (total, dataset) => total + (Number(dataset.column_count) || 0),
    0,
  )

  function openPage(page) {
    setActivePage(page)
  }

  function handleFileChange(event) {
    const file = event.target.files?.[0]

    setSelectedFile(null)
    setUploadError('')
    setUploadSuccess('')
    setUploadProgress(0)

    if (!file) return

    const extension = getFileExtension(file.name)

    if (!ACCEPTED_EXTENSIONS.includes(extension)) {
      setUploadError('Choose a CSV or XLSX spreadsheet.')
      event.target.value = ''
      return
    }

    if (file.size > MAX_FILE_SIZE) {
      setUploadError('The file exceeds the 10 MB upload limit.')
      event.target.value = ''
      return
    }

    if (file.size === 0) {
      setUploadError('The selected file is empty.')
      event.target.value = ''
      return
    }

    setSelectedFile(file)
  }

  async function handleUpload(event) {
    event.preventDefault()

    if (!selectedFile || uploading) return

    setUploading(true)
    setUploadProgress(0)
    setUploadError('')
    setUploadSuccess('')
    const uploadChatId = activeChat?.id

    try {
      const response = await uploadDataset(selectedFile, (progress) => {
        setUploadProgress(progress)
      })

      setUploadSuccess(
        response.message || `${selectedFile.name} uploaded successfully.`,
      )
      const stored = response.storage
      if (
        !stored ||
        typeof stored.dataset_id !== 'string' ||
        typeof stored.content_fingerprint !== 'string' ||
        !Array.isArray(stored.columns)
      ) {
        throw new Error(
          'The upload completed, but graph metadata was missing from the response.',
        )
      }

      if (uploadChatId) {
        const uploadedAt = new Date().toISOString()
        const filename = stored.filename || selectedFile.name
        const titleFromFilename = filename.replace(/\.[^.]+$/, '')
        updateChat(uploadChatId, (chat) => ({
          ...chat,
          datasetId: stored.dataset_id,
          title:
            chat.title === 'New chat'
              ? titleFromFilename
              : chat.title,
          uploads: [
            ...(chat.uploads || []),
            {
              upload_id: crypto.randomUUID(),
              dataset_id: stored.dataset_id,
              filename,
              table_name: stored.table_name,
              row_count: stored.row_count,
              column_count: stored.column_count,
              content_fingerprint: stored.content_fingerprint,
              columns: stored.columns,
              uploaded_at: uploadedAt,
            },
          ],
          updatedAt: uploadedAt,
        }))
      }
      setSelectedFile(null)

      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      await loadDatasets(false)
    } catch (err) {
      setUploadError(err.message || 'Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  function submitQuestion(event) {
  event.preventDefault()

  const query = question.trim()

  if (!query) return

  setQuestion(query)
  setChatInput(query)
  setActivePage('analyst')
}
  

  function renderDatasetTable() {
    if (loading) {
      return <p className="dataset-message">Loading datasets…</p>
    }

    if (error && datasets.length === 0) {
      return (
        <div className="dataset-message" role="alert">
          <p>{error}</p>
          <button
            className="primary-button"
            onClick={() => loadDatasets(true)}
          >
            Retry loading datasets
          </button>
        </div>
      )
    }

    if (datasets.length === 0) {
      return (
        <div className="dataset-empty">
          <span className="placeholder-icon">▤</span>
          <h3>No datasets yet</h3>
          <p>Upload a CSV or XLSX spreadsheet to get started.</p>
          <button
            className="primary-button"
            onClick={() => fileInputRef.current?.click()}
          >
            Choose spreadsheet
          </button>
        </div>
      )
    }

    return (
      <div className="dataset-table-wrap">
        <table className="dataset-table">
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Rows</th>
              <th>Columns</th>
              <th>Table name</th>
            </tr>
          </thead>
          <tbody>
            {datasets.map((dataset, index) => (
              <tr
                key={
                  dataset.dataset_id ||
                  `${dataset.original_filename}-${index}`
                }
              >
                <td>
                  <div className="dataset-name-cell">
                    <span className="dataset-file-icon">▤</span>
                    <span>
                      <strong>
                        {dataset.original_filename || 'Untitled dataset'}
                      </strong>
                      <small>
                        {dataset.created_at
                          ? new Date(dataset.created_at).toLocaleString()
                          : 'Stored dataset'}
                      </small>
                    </span>
                  </div>
                </td>
                <td>{formatNumber(dataset.row_count)}</td>
                <td>{formatNumber(dataset.column_count)}</td>
                <td>
                  <code>{dataset.table_name || '—'}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => openPage('overview')}
          aria-label="GraphSheet AI overview"
        >
          <span className="brand-mark">G</span>
          <span className="brand-copy">
            <strong>GraphSheet AI</strong>
            <small>DATA INTELLIGENCE</small>
          </span>
        </button>

        <div className="workspace-switcher">
          <span className="workspace-avatar">G</span>
          <span className="workspace-copy">
            <strong>My workspace</strong>
            <small>Personal workspace</small>
          </span>
          <span className="switcher-arrow">⌄</span>
        </div>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {navigation.map((group) => (
            <div className="nav-group" key={group.section}>
              <p className="nav-heading">{group.section}</p>

              {group.items.map((item) => (
                <button
                  key={item.id}
                  className={`nav-item ${
                    activePage === item.id ? 'active' : ''
                  }`}
                  onClick={() => openPage(item.id)}
                  aria-current={activePage === item.id ? 'page' : undefined}
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>
          
        <section className="chat-history">
          <div className="chat-history-heading">
            <span>CHAT HISTORY</span>
            <button
              type="button"
              className="new-chat-button"
              onClick={createNewChat}
              title="Start a new chat"
            >
              + New
            </button>
          </div>

          <div className="chat-history-list">
            {chatSessions.map((chat) => (
              <button
                key={chat.id}
                type="button"
                className={`chat-history-item ${
                  chat.id === activeChatId ? 'active' : ''
                }`}
                onClick={() => {
                  setActiveChatId(chat.id)
                  setChatError('')
                  setSqlError('')
                  setSqlResult(null)
                  setActivePage('analyst')
                }}
                title={chat.title}
              >
                <span aria-hidden="true">◷</span>
                <span className="chat-history-title">
                  {chat.title || 'New chat'}
                </span>
              </button>
            ))}
          </div>
        </section>




        <div className="sidebar-bottom">
          <div className="connection-status">
            <span
              className={`status-dot ${
                apiStatus === 'connected' ? '' : 'status-offline'
              }`}
            />
            <span>
              <strong>
                {apiStatus === 'connected'
                  ? 'API connected'
                  : apiStatus === 'checking'
                    ? 'Checking API…'
                    : 'API unavailable'}
              </strong>
              <small>
                {apiStatus === 'connected'
                  ? 'FastAPI is responding'
                  : apiStatus === 'checking'
                    ? 'Connecting to backend'
                    : 'Check backend and try again'}
              </small>
            </span>
          </div>

          <div className="profile">
            <div className="profile-avatar">A</div>
            <span className="profile-copy">
              <strong>Workspace user</strong>
              <small>Local development</small>
            </span>
            <span className="profile-menu">•••</span>
          </div>
        </div>
      </aside>

      <main className="main-panel">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Workspace</span>
            <span className="breadcrumb-divider">/</span>
            <strong>{pageNames[activePage]}</strong>
          </div>

          <div className="topbar-actions">
            <span className="environment-label">
              <span
                className={`status-dot ${
                  apiStatus === 'connected' ? '' : 'status-offline'
                }`}
              />
              {apiStatus === 'connected'
                ? 'Connected'
                : apiStatus === 'checking'
                  ? 'Connecting'
                  : 'Offline'}
            </span>

            <button
              className="help-button"
              title="Help"
              aria-label="Help"
              onClick={() => openPage('settings')}
            >
              ?
            </button>
          </div>
        </header>

        <div className="page-content">
          {activePage === 'overview' && (
            <>
              <section className="welcome-section">
                <div>
                  <p className="eyebrow">YOUR DATA WORKSPACE</p>
                  <h1>
                    Make your data <span>make sense.</span>
                  </h1>
                  <p className="welcome-description">
                    Explore spreadsheets, ask questions in plain English,
                    generate SQL, and uncover relationships across your data.
                  </p>
                  <button
                    className="primary-button"
                    onClick={() => openPage('datasets')}
                  >
                    Manage datasets <span>→</span>
                  </button>
                </div>

                <div className="welcome-symbol" aria-hidden="true">
                  <div className="symbol-line line-one" />
                  <div className="symbol-line line-two" />
                  <div className="symbol-line line-three" />
                  <span className="symbol-node node-one" />
                  <span className="symbol-node node-two" />
                  <span className="symbol-node node-three" />
                  <span className="symbol-node node-four" />
                  <span className="symbol-core">G</span>
                </div>
              </section>

              {error && (
                <div className="api-error" role="alert">
                  <strong>Unable to load live dashboard data.</strong>
                  <p>{error}</p>
                  <button
                    className="primary-button"
                    onClick={() => loadDatasets(true)}
                  >
                    Retry connection
                  </button>
                </div>
              )}



              <section className="section-block question-section">
                <div className="section-heading">
                  <div>
                    <h2>Ask your data</h2>
                    <p>Start with a question you want to investigate.</p>
                  </div>
                  <span className="ai-label">
                    <span>✳</span> AI ASSISTANT
                  </span>
                </div>

                <form className="question-form" onSubmit={submitQuestion}>
                  <span className="question-icon">✳</span>
                  <input
                    aria-label="Question for your data"
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    placeholder="e.g. What is the average value in my dataset?"
                  />
                  <button type="submit" disabled={!question.trim()}>
                    Ask question <span>→</span>
                  </button>
                </form>

                <p className="form-hint">
                  {datasets.length > 0
                    ? `${datasets.length} datasets available. AI question submission is the next integration step.`
                    : 'Connect a dataset from the Datasets section first.'}
                </p>
              </section>
                {/* -=-=-=-= */}


              <section className="metrics-grid" aria-label="Workspace summary">
                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon blue">▤</span>
                    <span className="metric-label">DATASETS</span>
                  </div>
                  <strong className="metric-value">
                    {loading ? '…' : error ? '—' : datasets.length}
                  </strong>
                  <p>Stored datasets</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon purple">✳</span>
                    <span className="metric-label">AI QUESTIONS</span>
                  </div>
                  <strong className="metric-value">—</strong>
                  <p>Usage tracking not connected</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon green">⌘</span>
                    <span className="metric-label">TOTAL ROWS</span>
                  </div>
                  <strong className="metric-value">
                    {loading ? '…' : error ? '—' : formatNumber(totalRows)}
                  </strong>
                  <p>Across stored datasets</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon amber">◎</span>
                    <span className="metric-label">TOTAL COLUMNS</span>
                  </div>
                  <strong className="metric-value">
                    {loading ? '…' : error ? '—' : formatNumber(totalColumns)}
                  </strong>
                  <p>Across stored datasets</p>
                </article>
              </section>



                {/* ---=-= */}
              <section className="section-block">
                <div className="section-heading">
                  <div>
                    <h2>Get started</h2>
                    <p>Choose a tool to begin exploring your data.</p>
                  </div>
                  <span className="section-caption">4 TOOLS</span>
                </div>

                <div className="feature-grid">
                  {features.map((feature) => (
                    <button
                      className="feature-card"
                      key={feature.target}
                      onClick={() => openPage(feature.target)}
                    >
                      <span className="feature-icon">{feature.icon}</span>
                      <span className="feature-title">{feature.title}</span>
                      <span className="feature-description">
                        {feature.text}
                      </span>
                      <span className="feature-link">
                        Open tool <span>↗</span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>





                      {/* ------=-=--== */}
            </>
          )}

          {activePage === 'datasets' && (
            <>
              <section className="page-intro">
                <p className="eyebrow">DATA MANAGEMENT</p>
                <h1>Your datasets</h1>
                <p>
                  Upload spreadsheets and review the datasets stored in your
                  GraphSheet AI workspace.
                </p>
              </section>

              <section className="section-block upload-panel">
                <div className="section-heading">
                  <div>
                    <h2>Upload a spreadsheet</h2>
                    <p>Supported formats: CSV and XLSX. Maximum size: 10 MB.</p>
                  </div>
                </div>

                <form className="upload-form" onSubmit={handleUpload}>
                  <label className="file-picker-label" htmlFor="dataset-file">
                    <span className="upload-icon">↑</span>
                    <strong>
                      {selectedFile ? selectedFile.name : 'Choose a file'}
                    </strong>
                    <span>
                      {selectedFile
                        ? `${(selectedFile.size / 1024).toFixed(1)} KB`
                        : 'Select a CSV or XLSX spreadsheet from your computer.'}
                    </span>
                  </label>

                  <input
                    ref={fileInputRef}
                    id="dataset-file"
                    type="file"
                    accept=".csv,.xlsx"
                    onChange={handleFileChange}
                    disabled={uploading}
                  />

                  <div className="upload-actions">
                    <button
                      className="secondary-button"
                      type="button"
                      disabled={uploading}
                      onClick={() => fileInputRef.current?.click()}
                    >
                      Browse files
                    </button>
                    <button
                      className="primary-button"
                      type="submit"
                      disabled={!selectedFile || uploading}
                    >
                      {uploading
                        ? 'Uploading…'
                        : 'Upload spreadsheet'}
                    </button>
                  </div>

                  {uploading && (
                    <div className="upload-progress">
                      <div className="upload-progress-heading">
                        <span>Uploading spreadsheet</span>
                        <strong>{uploadProgress}%</strong>
                      </div>
                      <progress
                        value={uploadProgress}
                        max="100"
                        aria-label="Upload progress"
                      />
                    </div>
                  )}

                  {uploadError && (
                    <div className="upload-feedback error" role="alert">
                      <strong>Upload failed</strong>
                      <p>{uploadError}</p>
                    </div>
                  )}

                  {uploadSuccess && (
                    <div className="upload-feedback success" role="status">
                      <strong>Upload completed</strong>
                      <p>{uploadSuccess}</p>
                    </div>
                  )}
                </form>
              </section>

              <section className="section-block">
                <div className="section-heading">
                  <div>
                    <h2>Stored datasets</h2>
                    <p>
                      {loading
                        ? 'Loading stored datasets…'
                        : `${datasets.length} dataset${datasets.length === 1 ? '' : 's'} found`}
                    </p>
                  </div>
                  <button
                    className="secondary-button"
                    onClick={() => loadDatasets(false)}
                    disabled={loading || refreshing}
                  >
                    {refreshing ? 'Refreshing…' : '↻ Refresh'}
                  </button>
                </div>

                {renderDatasetTable()}
              </section>
            </>
          )}

         
{activePage === 'analyst' && (
  <section className="workspace-page analyst-page">
    <div className="workspace-heading">
      <p className="eyebrow">GRAPHSHEET AI</p>
      <h1>Data Analyst</h1>
      <p>Ask questions about your uploaded spreadsheet.</p>
    </div>

    <div className="dataset-selector">
      <label htmlFor="analyst-dataset">Active dataset</label>
      <select
        id="analyst-dataset"
        value={activeChat?.datasetId || ''}
        onChange={(event) => {
          if (!activeChat) return
          updateChat(activeChat.id, (chat) => ({
            ...chat,
            datasetId: event.target.value,
            updatedAt: new Date().toISOString(),
          }))
          setChatError('')
        }}
      >
        <option value="">Select a dataset</option>
        {datasets.map((dataset) => (
          <option
            key={dataset.dataset_id}
            value={dataset.dataset_id}
          >
            {dataset.original_filename} ({dataset.row_count} rows)
          </option>
        ))}
      </select>
    </div>

    <div className="chat-messages" aria-live="polite">
      {!activeChat?.messages?.length && (
        <div className="chat-empty-state">
          <h2>Ask your data a question</h2>
          <p>
            Try asking for a summary, trends, comparisons, or unusual values.
          </p>
        </div>
      )}

      {activeChat?.messages?.map((message) => (
        <article
          key={message.id}
          className={`chat-message ${message.role}`}
        >
          <span className="message-role">
            {message.role === 'user' ? 'You' : 'GraphSheet AI'}
          </span>

          <div className="markdown-content">
  <ReactMarkdown remarkPlugins={[remarkGfm]}>
    {message.content || ''}
  </ReactMarkdown>
</div>
          {/* <p style={{ whiteSpace: 'pre-wrap' }}>{message.content}</p> */}
        </article>
      ))}

      {chatLoading && (
        <p className="loading-message">Analyzing your data...</p>
      )}
    </div>

    {chatError && (
      <p className="error-message" role="alert">{chatError}</p>
    )}

    <form
      className="chat-composer"
      onSubmit={(event) => {
        event.preventDefault()
        sendChatQuestion()
      }}
    >
      <textarea
        value={chatInput}
        onChange={(event) => setChatInput(event.target.value)}
        placeholder="Ask a question about your dataset..."
        rows={3}
        disabled={chatLoading}
      />
      <button
        className="primary-button"
        type="submit"
        disabled={chatLoading || !chatInput.trim()}
      >
        {chatLoading ? 'Analyzing...' : 'Ask question'}
      </button>
    </form>
  </section>
)}


{activePage === 'sql' && (
  <section className="workspace-page sql-page">
    <div className="workspace-heading">
      <p className="eyebrow">GRAPHSHEET AI</p>
      <h1>SQL Studio</h1>
      <p>
        Ask a question and let the backend generate and execute a
        read-only SQL query.
      </p>
    </div>

    <div className="dataset-selector">
      <label>Active dataset</label>
      <p>
        {getChatDataset(activeChat)?.original_filename ||
          'Select a dataset in Analyst first.'}
      </p>

      <button
        type="button"
        className="secondary-button"
        onClick={() => setActivePage('analyst')}
      >
        Choose dataset
      </button>
    </div>

    <form
      className="sql-composer"
      onSubmit={(event) => {
        event.preventDefault()
        runSqlQuestion()
      }}
    >
      <label htmlFor="sql-question">
        What would you like to query?
      </label>

      <textarea
        id="sql-question"
        value={sqlInput}
        onChange={(event) => setSqlInput(event.target.value)}
        placeholder="Example: Show the top 5 products by revenue."
        rows={4}
        disabled={sqlLoading}
      />

      <button
        type="submit"
        className="primary-button"
        disabled={sqlLoading || !sqlInput.trim()}
      >
        {sqlLoading ? 'Running...' : 'Run analysis'}
      </button>
    </form>

    {sqlError && (
      <p className="error-message" role="alert">
        {sqlError}
      </p>
    )}

    {sqlResult && (
      <section className="sql-result">
        <div className="sql-result-heading">
          <div>
            <p className="eyebrow">QUERY EXECUTION</p>
            <h2>Results</h2>
          </div>

          <span className="sql-status">
            {sqlResult.status === 'success'
              ? 'Completed'
              : sqlResult.status}
          </span>
        </div>

        <p className="sql-result-question">
          {sqlResult.question}
        </p>

        <SQLResultChart
          columns={sqlResult.columns}
          rows={sqlResult.rows}
        />

        <div className="sql-code-section">
          <div className="sql-section-heading">
            <h3>Generated SQL</h3>

            {sqlResult.sql && (
              <button
                type="button"
                className="secondary-button"
                onClick={() => {
                  if (navigator.clipboard?.writeText) {
                    navigator.clipboard
                      .writeText(sqlResult.sql)
                      .catch(() => {
                        setSqlError(
                          'Could not copy SQL. Please select and copy it manually.',
                        )
                      })
                  } else {
                    setSqlError(
                      'Clipboard access is unavailable in this browser.',
                    )
                  }
                }}
              >
                Copy SQL
              </button>
            )}
          </div>

          <pre className="sql-code">
            <code>
              {sqlResult.sql || 'No SQL query was returned.'}
            </code>
          </pre>
        </div>

        <div className="sql-data-section">
          <div className="sql-section-heading">
            <h3>Query output</h3>
            <span>
              {sqlResult.rowCount} rows
            </span>
          </div>

          {sqlResult.columns.length === 0 ? (
            <p className="sql-empty-state">
              The query completed without returning tabular columns.
            </p>
          ) : sqlResult.rows.length === 0 ? (
            <p className="sql-empty-state">
              The query returned no matching records.
            </p>
          ) : (
            <div className="sql-table-wrapper">
              <table className="sql-data-table">
                <thead>
                  <tr>
                    {sqlResult.columns.map((column, index) => (
                      <th key={`${column}-${index}`}>
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>

                <tbody>
                  {sqlResult.rows.map((row, rowIndex) => (
                    <tr key={rowIndex}>
                      {sqlResult.columns.map((column, columnIndex) => {
                        const value = row[column]

                        return (
                          <td key={`${column}-${columnIndex}`}>
                            {value === null || value === undefined
                              ? '—'
                              : typeof value === 'object'
                                ? JSON.stringify(value)
                                : String(value)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    )}
  </section>
)}
 

{activePage === 'knowledge' && (
  <KnowledgeGraph
    chat={activeChat}
    onQuery={(datasetId) => {
      if (datasetId && activeChat) {
        updateChat(activeChat.id, (chat) => ({
          ...chat,
          datasetId,
          updatedAt: new Date().toISOString(),
        }))
      }
      setSqlError('')
      setActivePage('sql')
    }}
  />
)}

{activePage !== 'overview' &&
  activePage !== 'datasets' &&
  activePage !== 'analyst' &&
  activePage !== 'sql' &&
  activePage !== 'knowledge' && (
    <section className="placeholder-page">
      <span className="placeholder-icon">
        {navigation.flatMap((group) => group.items)
          .find((item) => item.id === activePage)?.icon || '⚙'}
      </span>
      <p className="eyebrow">GRAPHSHEET AI WORKSPACE</p>
      <h1>{pageNames[activePage]}</h1>
      <p className="placeholder-description">
        This workspace will be connected in a later step.
      </p>
      <button
        className="primary-button"
        onClick={() => openPage('overview')}
      >
        Back to overview
      </button>
    </section>
  )}



          <footer className="page-footer">
            <span>
              GraphSheet AI <span className="footer-dot">·</span> Data
              intelligence workspace
            </span>
            <span>Local development</span>
          </footer>
        </div>
      </main>
    </div>
  )



}

export default App
