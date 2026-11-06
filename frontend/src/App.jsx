// Coordinate the main workspace: dataset upload, persistent chats, and analyst views.
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import KnowledgeGraph from './components/KnowledgeGraph'
import OverviewGraphs from './components/OverviewGraphs'
import ChatContextPanel from './components/ChatContextPanel'
import HighlightedSQL from './components/HighlightedSQL'
import StructuredAnswer from './components/StructuredAnswer'
import SQLAnalysisResults from './components/SQLAnalysisResults'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './components/ChatWorkspace.css'

import {
  getDatasets,
  getConversations,
  getConversation,
  createConversation,
  deleteConversation,
  addConversationMessage,
  addConversationEvent,
  updateConversationDatasets,
  askConversation,
  analyzeConversationSQL,
  approveConversationSQLRelationship,
  checkHealth,
  uploadDataset,
  deleteDataset,
} from './services/api'

import './App.css'

const navigation = [
  {
    section: 'WORKSPACE',
    items: [
      { id: 'overview', icon: '◫', label: 'Overview' },
      { id: 'datasets', icon: '▤', label: 'Datasets' },
      { id: 'analyst', icon: '✳', label: 'AI Analyst' },
      { id: 'knowledge', icon: '◎', label: 'Knowledge Graph' },
    ],
  },
  // {
  //   section: 'PREFERENCES',
  //   items: [{ id: 'settings', icon: '⚙', label: 'Settings' }],
  // },
]

// Describe the feature cards shown when users first enter the workspace.
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
    title: 'Analyze with SQL',
    text: 'Generate multiple safe SQL result tables from a natural-language request.',
    target: 'analyst',
  },
  {
    icon: '◎',
    title: 'Explore relationships',
    text: 'Discover connections in your knowledge graph.',
    target: 'knowledge',
  },
]



const CHAT_STORAGE_KEY = 'graphsheet-ai-chat-sessions'
const EMPTY_SOURCES = []

function createChatSession() {
  // Give client-side chat state a complete shape before server persistence begins.
  const now = new Date().toISOString()

  return {
    id: crypto.randomUUID(),
    title: 'New chat',
    createdAt: now,
    updatedAt: now,
    messages: [],
    uploads: [],
    graphQueries: [],
    selectedDatasetIds: [],
    datasetId: null,
  }
}

function loadChatSessions() {
  // Restore older browser-cached chats so they can be migrated into the API store.
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

function conversationToChat(conversation) {
  // Convert API events back to the UI model, joining stored result references to SQL events.
  const events = conversation.events || []
  const selectedDatasetIds = conversation.selected_dataset_ids || (
    conversation.dataset_id ? [conversation.dataset_id] : []
  )
  const resultsByQueryId = new Map(
    events
      .filter((event) => event.event_type === 'result_reference')
      .map((event) => [event.metadata?.query_id, event.metadata || {}])
      .filter(([queryId]) => queryId),
  )
  const uploads = events
    .filter((event) => event.event_type === 'upload')
    .map((event) => ({
      ...event.metadata,
      upload_id: event.metadata?.upload_id || event.event_id,
      uploaded_at: event.created_at,
    }))
  const graphQueries = events
    .filter((event) => event.event_type === 'sql')
    .map((event) => ({
      ...event.metadata,
      ...(resultsByQueryId.get(event.event_id) || {}),
      query_id: event.event_id,
      sql: event.metadata?.sql || '',
      question: event.metadata?.question || '',
      executed_at: event.created_at,
    }))

  return {
    id: conversation.conversation_id,
    title: conversation.title || 'New chat',
    createdAt: conversation.created_at,
    updatedAt: conversation.updated_at,
    datasetId: selectedDatasetIds[0] || null,
    selectedDatasetIds,
    messages: events.flatMap((event) => {
      if (event.event_type === 'message') {
        return [{
          id: event.event_id,
          role: event.actor,
          content: event.message || '',
          intent: event.metadata?.intent,
          question: event.metadata?.question || '',
          requestId: event.metadata?.request_id,
          sources: event.metadata?.sources || [],
          structuredAnswer: event.metadata?.response?.structured_answer || null,
          sql: event.metadata?.response?.sql || null,
          sqlResult: event.metadata?.response?.sql_result || null,
          sqlSources: event.metadata?.response?.sql_sources || [],
          sqlAnalysis:
            event.metadata?.sql_analysis
            || event.metadata?.response?.sql_analysis
            || null,
          sqlMode: event.metadata?.mode || 'analysis',
          graphWarning: event.metadata?.response?.graph_warning,
          status: event.metadata?.status || 'success',
          createdAt: event.created_at,
        }]
      }

      if (
        event.event_type === 'sql_studio_prompt'
        && event.metadata?.surface === 'sql_studio'
      ) {
        return [{
          id: event.event_id,
          role: 'user',
          intent: 'sql_analysis',
          content: event.message || '',
          question: event.message || '',
          createdAt: event.created_at,
        }]
      }

      if (
        event.event_type === 'sql_studio_analysis'
        && event.metadata?.surface === 'sql_studio'
        && event.metadata?.analysis
      ) {
        const analysis = event.metadata.analysis
        return [{
          id: event.event_id,
          role: 'assistant',
          intent: 'sql_analysis',
          content: analysis.clarification
            || `${analysis.analysis_title || 'SQL analysis'}: ${analysis.queries?.length || 0} result tables generated.`,
          question: analysis.original_prompt || '',
          sqlAnalysis: analysis,
          status: analysis.status || 'success',
          createdAt: event.created_at,
        }]
      }

      if (event.event_type === 'sql') {
        const result = resultsByQueryId.get(event.event_id)
        const failed = event.metadata?.status === 'failed'
        return [{
          id: event.event_id,
          role: 'assistant',
          intent: 'sql',
          content: failed
            ? event.message || 'SQL analysis failed.'
            : `SQL generated and executed. ${result?.row_count ?? 0} rows returned.`,
          sql: event.metadata?.sql || '',
          sqlResult: result
            ? {
                columns: result.columns || [],
                rows: result.rows || [],
                rowCount: result.row_count ?? result.rows?.length ?? 0,
              }
            : null,
          question: event.metadata?.question || '',
          status: failed ? 'failed' : 'success',
          createdAt: event.created_at,
        }]
      }

      return []
    }),
    uploads,
    graphQueries,
  }
}
const MAX_FILE_SIZE = 10 * 1024 * 1024
const ACCEPTED_EXTENSIONS = ['.csv', '.xls', '.xlsx']

function formatNumber(value) {
  // Use locale-aware formatting for row counts and other displayed totals.
  return (Number(value) || 0).toLocaleString()
}

function getDatasetsForChat(chat, datasets) {
  // Resolve a chat's selected IDs to the current dataset records.
  const datasetIds = chat?.selectedDatasetIds || (
    chat?.datasetId ? [chat.datasetId] : []
  )
  return datasetIds
    .map((datasetId) => datasets.find(
      (dataset) => dataset.dataset_id === datasetId,
    ))
    .filter(Boolean)
}

function getFileExtension(filename) {
  // Normalize the suffix so upload validation is case-insensitive.
  return filename.slice(filename.lastIndexOf('.')).toLowerCase()
}

function getSpreadsheetIcon(filename = '') {
  // Choose a compact icon by spreadsheet format for dataset lists.
  const extension = getFileExtension(filename)

  if (extension === '.xlsx' || extension === '.xls') {
    return '▦'
  }

  if (extension === '.csv') {
    return '▤'
  }

  return '▧'
}

function truncateTableName(name = '', maxLength = 24) {
  // Keep long generated table names readable without losing their identifying ends.
  if (name.length <= maxLength) return name

  const startLength = Math.floor((maxLength - 3) / 2)
  const endLength = maxLength - 3 - startLength

  return `${name.slice(0, startLength)}...${name.slice(-endLength)}`
}

function App() {
  // Own application-wide UI state and connect views to the backend services.
 const [chatSessions, setChatSessions] = useState(loadChatSessions)

const [activeChatId, setActiveChatId] = useState(
  () => loadChatSessions()[0]?.id
)
const [contextPanelOpen, setContextPanelOpen] = useState(true)
const [copiedTableName, setCopiedTableName] = useState('')
const [datasetToDelete, setDatasetToDelete] = useState(null)
const [datasetDeleteLoading, setDatasetDeleteLoading] = useState(false)
const [chatInput, setChatInput] = useState('')
const [chatLoading, setChatLoading] = useState(false)
const [chatError, setChatError] = useState('')
const [chatRetry, setChatRetry] = useState(null)
const activeChatRequestRef = useRef(null)
const [sqlLoading, setSqlLoading] = useState(false)
const [sqlLoadingMode, setSqlLoadingMode] = useState('analysis')
const [datasetSearch, setDatasetSearch] = useState('')
const [datasetPickerOpen, setDatasetPickerOpen] = useState(false)
const [sourceToolsOpen, setSourceToolsOpen] = useState(false)
const [contextTab, setContextTab] = useState('evidence')
  const [activePage, setActivePage] = useState('analyst')
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
  const [deletingDatasetId, setDeletingDatasetId] = useState('')
  const [datasetDeleteError, setDatasetDeleteError] = useState('')
  const fileInputRef = useRef(null)
  const chatMessagesRef = useRef(null)
  const shouldAutoScrollRef = useRef(true)

  const loadDatasets = useCallback(async (showLoading = true) => {
    // Keep initial loading and user-triggered refresh indicators distinct.
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
      // Check API readiness first, then fetch datasets only while this view is mounted.
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

  useEffect(() => {
    let cancelled = false

    async function loadPersistentChats() {
      // Restore server conversations and migrate any legacy browser-only sessions.
      try {
        const legacyChats = loadChatSessions()
        let conversations = await getConversations()
        const knownIds = new Set(
          conversations.map((conversation) => conversation.conversation_id),
        )
        const availableDatasets = await getDatasets()
        const availableDatasetIds = new Set(
          availableDatasets.map((dataset) => dataset.dataset_id),
        )

        for (const legacyChat of legacyChats) {
          if (knownIds.has(legacyChat.id)) continue

          const created = await createConversation(
            legacyChat.title || 'New chat',
            legacyChat.id,
          )
          knownIds.add(created.conversation_id)

          const legacySelection = legacyChat.selectedDatasetIds ||
            (legacyChat.datasetId ? [legacyChat.datasetId] : [])
          const selectedDatasetIds = legacySelection
            .filter((datasetId) => availableDatasetIds.has(datasetId))
            .slice(0, 5)
          if (selectedDatasetIds.length) {
            await updateConversationDatasets(
              created.conversation_id,
              selectedDatasetIds,
            )
          }

          for (const upload of legacyChat.uploads || []) {
            if (!availableDatasetIds.has(upload.dataset_id)) continue
            await addConversationEvent(
              created.conversation_id,
              'upload',
              upload.filename || 'Uploaded spreadsheet',
              upload,
            )
          }
          for (const message of legacyChat.messages || []) {
            await addConversationMessage(
              created.conversation_id,
              message.role || 'user',
              message.content || '',
              { legacy_message_id: message.id },
            )
          }
          for (const query of legacyChat.graphQueries || []) {
            const queryEvent = await addConversationEvent(
              created.conversation_id,
              'sql',
              'Generated SQL',
              {
                ...query,
                dataset_id: query.dataset_id,
                question: query.question,
                sql: query.sql,
              },
            )
            await addConversationEvent(
              created.conversation_id,
              'execution',
              'SQL execution completed',
              {
                query_id: queryEvent.event.event_id,
                status: 'success',
              },
            )
            await addConversationEvent(
              created.conversation_id,
              'result_reference',
              `Query returned ${query.row_count || 0} rows`,
              {
                ...query,
                query_id: queryEvent.event.event_id,
                result_reference: `result:${queryEvent.event.event_id}`,
              },
            )
          }
        }

        conversations = await getConversations()
        const loadedChats = await Promise.all(
          conversations.map((item) => getConversation(item.conversation_id)),
        )

        if (cancelled) return
        const nextChats = loadedChats.map(conversationToChat)
        setChatSessions(nextChats)
        setActiveChatId((current) =>
          nextChats.some((chat) => chat.id === current)
            ? current
            : nextChats[0]?.id,
        )
      } catch (err) {
        if (!cancelled) {
          setChatError(
            err.message || 'Unable to load persistent chat history.',
          )
        }
      }
    }

    loadPersistentChats()
    return () => {
      cancelled = true
    }
  }, [])

  const pageNames = {
    overview: 'Overview',
    datasets: 'Datasets',
    analyst: 'AI Analyst',
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
useEffect(() => {
  if (!shouldAutoScrollRef.current || !chatMessagesRef.current) return
  chatMessagesRef.current.scrollTop = chatMessagesRef.current.scrollHeight
}, [
  activeChat?.id,
  activeChat?.messages?.length,
  chatLoading,
  sqlLoading,
])
const activeChatDatasets = useMemo(
  () => getDatasetsForChat(activeChat, datasets),
  [activeChat, datasets],
)
const filteredDatasets = useMemo(() => {
  const search = datasetSearch.trim().toLowerCase()
  if (!search) return datasets
  return datasets.filter((dataset) => [
    dataset.original_filename,
    dataset.sheet_name,
    dataset.sheet,
    dataset.table_name,
  ].some((value) => (
    typeof value === 'string' && value.toLowerCase().includes(search)
  )))
}, [datasetSearch, datasets])
const latestAnswerMessage = [...(activeChat?.messages || [])]
  .reverse()
  .find((message) => message.role === 'assistant' && Array.isArray(message.sources))
const latestSQLQuery = activeChat?.graphQueries?.at(-1)
const evidenceQuestion = latestAnswerMessage?.question
  || [...(activeChat?.messages || [])]
    .reverse()
    .find((message) => message.role === 'user')?.content
  || ''

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
async function createNewChat() {
  // Persist the new conversation and carry forward only still-existing selected sheets.
  let newChat
  try {
    const persistedChat = await createConversation()
    newChat = conversationToChat(persistedChat)
    const selectedDatasetIds = (activeChat?.selectedDatasetIds || []).filter(
      (datasetId) => datasets.some((dataset) => dataset.dataset_id === datasetId),
    )
    if (selectedDatasetIds.length) {
      const selection = await updateConversationDatasets(
        newChat.id,
        selectedDatasetIds,
      )
      newChat = conversationToChat(selection.conversation)
    }
  } catch (err) {
    setChatError(err.message || 'Unable to create a persistent chat.')
    return
  }

  setChatSessions((current) => [
    newChat,
    ...current,
  ])

  shouldAutoScrollRef.current = true
  setActiveChatId(newChat.id)
  setChatInput('')
  setChatError('')
  setActivePage('analyst')
}

async function deleteChatSession(chatId) {
  // Require confirmation, delete on the server, then update local chat selection.
  const chat = chatSessions.find((item) => item.id === chatId)
  if (!chat || !window.confirm(`Delete "${chat.title || 'New chat'}" and its chat history?`)) {
    return
  }

  try {
    await deleteConversation(chatId)
  } catch (err) {
    setChatError(err.message || 'Unable to delete this chat.')
    return
  }

  const remainingChats = chatSessions.filter((item) => item.id !== chatId)
  try {
    localStorage.setItem(
      CHAT_STORAGE_KEY,
      JSON.stringify(remainingChats),
    )
  } catch (err) {
    setChatError(
      `Chat was deleted, but the local history cache could not be updated: ${err.message}`,
    )
  }
  setChatSessions(remainingChats)
  if (activeChatId === chatId) {
    if (remainingChats.length) {
      setActiveChatId(remainingChats[0].id)
    } else {
      setActiveChatId('')
      await createNewChat()
    }
    setChatInput('')
    setChatError('')
  }
}

async function deleteDatasetAndRelatedData(dataset) {
  // Remove the dataset through the API and discard chats that depended on it.
  const filename = dataset.original_filename || 'this dataset'
  if (
    !window.confirm(
      `Delete "${filename}" and its graph, related chats, queries, and stored data? This cannot be undone.`,
    )
  ) {
    return
  }

  setDeletingDatasetId(dataset.dataset_id)
  setDatasetDeleteError('')

  try {
    await deleteDataset(dataset.dataset_id)

    const relatedChatIds = new Set(
      chatSessions
        .filter(
          (chat) =>
            chat.datasetId === dataset.dataset_id ||
            (chat.selectedDatasetIds || []).includes(dataset.dataset_id) ||
            (chat.uploads || []).some(
              (upload) => upload.dataset_id === dataset.dataset_id,
            ) ||
            (chat.graphQueries || []).some(
              (query) => query.dataset_id === dataset.dataset_id,
            ),
        )
        .map((chat) => chat.id),
    )
    const remainingChats = chatSessions.filter(
      (chat) => !relatedChatIds.has(chat.id),
    )
    setChatSessions(remainingChats)
    if (relatedChatIds.has(activeChatId)) {
      setChatInput('')
      setChatError('')
      if (remainingChats.length) {
        setActiveChatId(remainingChats[0].id)
      } else {
        setActiveChatId('')
        await createNewChat()
      }
    }

    await loadDatasets(false)
  } catch (err) {
    setDatasetDeleteError(
      err.message || `Could not delete ${filename}.`,
    )
  } finally {
    setDeletingDatasetId('')
  }
}

// Add a message to a specific conversation.
function appendChatMessage(chatId, message) {
  const messageList = chatMessagesRef.current
  shouldAutoScrollRef.current = !messageList
    || messageList.scrollHeight - messageList.scrollTop - messageList.clientHeight < 100
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

async function saveChatDatasetSelection(datasetId, selected) {
  // Save sheet selection to the conversation and enforce the five-sheet limit.
  if (!activeChat) return
  const currentIds = activeChat.selectedDatasetIds || []
  const nextIds = selected
    ? [...currentIds, datasetId]
    : currentIds.filter((item) => item !== datasetId)

  if (nextIds.length > 5) {
    setChatError('A chat can use at most five sheets.')
    return
  }

  try {
    await updateConversationDatasets(activeChat.id, nextIds)
    updateChat(activeChat.id, (chat) => ({
      ...chat,
      datasetId: nextIds[0] || null,
      selectedDatasetIds: nextIds,
      updatedAt: new Date().toISOString(),
    }))
    setChatError('')
  } catch (err) {
    setChatError(err.message || 'Unable to save the selected sheets.')
  }
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

async function sendChatQuestion(text = chatInput, retryRequestId) {
  // Persist a user question, request an evidence-grounded answer, and retain retry identity.
  const query = text.trim()

  if (!query || chatLoading) return

  const chat = activeChat

  if (!chat) {
    setChatError('Create a chat before asking a question.')
    return
  }

  const selectedDatasets = getDatasetsForChat(chat, datasets)

  if (!selectedDatasets.length) {
    setChatError(
      'This chat has no associated dataset yet. Upload a spreadsheet and select it for this chat first.',
    )
    setActivePage('datasets')
    return
  }

  const chatId = chat.id
  const requestId = retryRequestId || (
    chatRetry?.chatId === chatId && chatRetry.query === query
      ? chatRetry.requestId
      : crypto.randomUUID()
  )
  const controller = new AbortController()
  activeChatRequestRef.current = {
    controller,
    chatId,
    kind: 'answer',
    query,
    requestId,
  }

  setChatInput('')
  setChatError('')
  setChatLoading(true)

  if (!chat.messages?.some((message) => (
    message.role === 'user' && message.requestId === requestId
  ))) {
    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'user',
      content: query,
      question: query,
      requestId,
      createdAt: new Date().toISOString(),
    })
  }

  let answerReceived = false
  let answer = ''
  try {
    const response = await askConversation(
      chatId,
      query,
      5,
      requestId,
      controller.signal,
    )
    answerReceived = true
    setChatRetry(null)
    answer = getResponseText(response)
    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: answer,
      question: response.question || query,
      requestId,
      sources: response.sources || [],
      structuredAnswer: response.structured_answer || null,
      sql: response.sql || null,
      sqlResult: response.sql_result || null,
      sqlSources: response.sql_sources || [],
      graphRetrieval: response.graph_retrieval || {},
      graphWarning: response.graph_warning || '',
      status: response.status || 'success',
      createdAt: new Date().toISOString(),
    })
  } catch (err) {
    if (controller.signal.aborted) return

    if (answerReceived) {
      appendChatMessage(chatId, {
        id: crypto.randomUUID(),
        role: 'assistant',
        content: answer,
        createdAt: new Date().toISOString(),
      })
      setChatError(
        `The answer was generated but could not be saved to chat history: ${err.message}`,
      )
      return
    }

    const failureMessage =
      `Request failed: ${err.message || 'Unable to get an answer.'}`
    setChatRetry({ chatId, query, requestId })
    setChatInput(query)
    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      content: failureMessage,
      question: query,
      requestId,
      status: 'failed',
      createdAt: new Date().toISOString(),
    })
    try {
      await addConversationMessage(chatId, 'assistant', failureMessage, {
        dataset_ids: selectedDatasets.map((dataset) => dataset.dataset_id),
        status: 'failed',
        question: query,
        request_id: requestId,
      })
    } catch (historyError) {
      setChatError(
        `The answer failed and could not be saved: ${historyError.message}`,
      )
    }
  } finally {
    if (activeChatRequestRef.current?.controller === controller) {
      activeChatRequestRef.current = null
      setChatLoading(false)
    }
  }
}

function stopCurrentRequest() {
  const activeRequest = activeChatRequestRef.current
  if (!activeRequest) return

  activeChatRequestRef.current = null
  activeRequest.controller.abort()
  setChatLoading(false)
  setSqlLoading(false)
  appendChatMessage(activeRequest.chatId, {
    id: crypto.randomUUID(),
    role: 'assistant',
    intent: activeRequest.kind === 'sql'
      ? 'sql_analysis'
      : undefined,
    content: activeRequest.kind === 'sql'
      ? 'SQL analysis stopped.'
      : 'Answer generation stopped.',
    question: activeRequest.query,
    requestId: activeRequest.requestId,
    status: 'cancelled',
    createdAt: new Date().toISOString(),
  })
}

async function runSqlQuestion(question = chatInput, mode = 'analysis') {
  // Request chat-scoped SQL analysis or code generation and append its result to history.
  const questionText = question.trim()

  if (!questionText || sqlLoading) return

  const chat = activeChat
  if (!chat) {
    setChatError('Create a chat before generating an analysis.')
    return
  }

  const selectedDatasets = getDatasetsForChat(activeChat, datasets)

  if (!selectedDatasets.length) {
    setChatError(
      'Select a dataset for this chat before running SQL analysis.',
    )
    return
  }

  const chatId = chat.id
  const requestId = crypto.randomUUID()
  const controller = new AbortController()
  activeChatRequestRef.current = {
    controller,
    chatId,
    kind: 'sql',
    query: questionText,
    requestId,
  }
  setSqlLoading(true)
  setSqlLoadingMode(mode)
  setChatError('')
  setChatInput('')
  appendChatMessage(chatId, {
    id: crypto.randomUUID(),
    role: 'user',
    content: questionText,
    question: questionText,
    requestId,
    intent: mode === 'code_generation'
      ? 'sql_code_generation'
      : 'sql_analysis',
    sqlMode: mode,
    createdAt: new Date().toISOString(),
  })

  try {
    const response = await analyzeConversationSQL(
      chatId,
      questionText,
      requestId,
      mode,
      controller.signal,
    )
    const analysis = response.analysis
    const completedCount = analysis.queries.filter(
      (query) => query.execution_status === 'success',
    ).length
    const content = analysis.clarification
      || (analysis.mode === 'code_generation'
        ? `${analysis.analysis_title}: SQL code generated; none executed.`
        : `${analysis.analysis_title}: ${completedCount} of ${analysis.queries.length} result tables completed.`)
    updateChat(chatId, (current) => ({
      ...current,
      title: current.title === 'New chat'
        ? questionText.slice(0, 48)
        : current.title,
      updatedAt: new Date().toISOString(),
    }))
    appendChatMessage(chatId, {
      id: response.events?.assistant || crypto.randomUUID(),
      role: 'assistant',
      intent: mode === 'code_generation'
        ? 'sql_code_generation'
        : 'sql_analysis',
      sqlMode: mode,
      content,
      question: questionText,
      requestId,
      sqlAnalysis: analysis,
      status: analysis.status || 'success',
      createdAt: new Date().toISOString(),
    })
  } catch (err) {
    if (controller.signal.aborted) return

    const errorMessage = err.message || 'SQL analysis failed. Please try again.'
    setChatError(errorMessage)
    appendChatMessage(chatId, {
      id: crypto.randomUUID(),
      role: 'assistant',
      intent: mode === 'code_generation'
        ? 'sql_code_generation'
        : 'sql_analysis',
      sqlMode: mode,
      content: `SQL analysis failed: ${errorMessage}`,
      question: questionText,
      requestId,
      status: 'failed',
      createdAt: new Date().toISOString(),
    })
  } finally {
    if (activeChatRequestRef.current?.controller === controller) {
      activeChatRequestRef.current = null
      setSqlLoading(false)
    }
  }
}

async function approveAndRetrySQLRelationship(relationship, question) {
  // Record explicit join approval before asking Analyst to generate the query again.
  if (!activeChat?.id) return
  try {
    await approveConversationSQLRelationship(
      activeChat.id,
      relationship.relationship_id,
    )
    await runSqlQuestion(question)
  } catch (err) {
    setChatError(err.message || 'Could not approve this sheet relationship.')
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
    // Keep navigation changes inside the single-page application.
    setActivePage(page)
  }

  function handleFileChange(event) {
    // Reject unsupported or oversized uploads before sending them to the backend.
    const file = event.target.files?.[0]

    setSelectedFile(null)
    setUploadError('')
    setUploadSuccess('')
    setUploadProgress(0)

    if (!file) return

    setSourceToolsOpen(true)
    const extension = getFileExtension(file.name)

    if (!ACCEPTED_EXTENSIONS.includes(extension)) {
      setUploadError('Choose a CSV, XLS, or XLSX spreadsheet.')
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
    // Upload a validated file, associate its sheets with the active chat, then refresh data.
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

      const uploadMessage =
        response.message || `${selectedFile.name} uploaded successfully.`
      setUploadSuccess(uploadMessage)
      const stored = response.storage
      if (
        !stored ||
        typeof stored.dataset_id !== 'string' ||
        typeof stored.content_fingerprint !== 'string' ||
        !Array.isArray(stored.columns)
      ) {
        throw new Error(
          'The upload completed, but dataset details were missing from the response.',
        )
      }

      if (uploadChatId) {
        const uploadedAt = new Date().toISOString()
        const storedSheets = Array.isArray(stored.sheets) && stored.sheets.length
          ? stored.sheets
          : [stored]
        if (storedSheets.some((sheet) => (
          typeof sheet.dataset_id !== 'string' ||
          typeof sheet.content_fingerprint !== 'string' ||
          !Array.isArray(sheet.columns)
        ))) {
          throw new Error('The server returned incomplete worksheet details.')
        }

        const filename = stored.filename || selectedFile.name
        const titleFromFilename = filename.replace(/\.[^.]+$/, '')
        const uploadRecords = storedSheets.map((sheet) => ({
          upload_id: crypto.randomUUID(),
          dataset_id: sheet.dataset_id,
          filename: sheet.filename || filename,
          sheet_name: sheet.sheet_name || null,
          table_name: sheet.table_name,
          row_count: sheet.row_count,
          column_count: sheet.column_count,
          content_fingerprint: sheet.content_fingerprint,
          columns: sheet.columns,
          duplicate: Boolean(sheet.duplicate),
          uploaded_at: uploadedAt,
        }))
        const uploadChat = chatSessions.find(
          (chat) => chat.id === uploadChatId,
        )
        const selectedDatasetIds = uploadChat?.selectedDatasetIds || (
          uploadChat?.datasetId ? [uploadChat.datasetId] : []
        )
        const nextSelectedDatasetIds = [...new Set([
          ...selectedDatasetIds,
          ...uploadRecords.map((record) => record.dataset_id),
        ])].slice(0, 5)
        const unselectedSheetCount = uploadRecords.filter(
          (record) => !nextSelectedDatasetIds.includes(record.dataset_id),
        ).length

        await updateConversationDatasets(
          uploadChatId,
          nextSelectedDatasetIds,
        )
        for (const uploadRecord of uploadRecords) {
          await addConversationEvent(
            uploadChatId,
            'upload',
            uploadRecord.sheet_name
              ? `${filename} - ${uploadRecord.sheet_name}`
              : filename,
            uploadRecord,
          )
        }
        updateChat(uploadChatId, (chat) => ({
          ...chat,
          selectedDatasetIds: nextSelectedDatasetIds,
          datasetId: nextSelectedDatasetIds[0] || null,
          title:
            chat.title === 'New chat'
              ? titleFromFilename
              : chat.title,
          uploads: [
            ...(chat.uploads || []),
            ...uploadRecords,
          ],
          updatedAt: uploadedAt,
        }))
        setUploadSuccess(
          unselectedSheetCount
            ? `${uploadMessage} ${unselectedSheetCount} sheet${unselectedSheetCount === 1 ? '' : 's'} could not be selected because chats can use at most five sheets.`
            : uploadMessage,
        )
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
  // Route the landing-page question into the analyst chat composer.
  event.preventDefault()

  const query = question.trim()

  if (!query) return

  setQuestion(query)
  setChatInput(query)
  setActivePage('analyst')
}
  

  function renderSheetManager(layout) {
    // Render current sheet chips and the searchable, capped sheet selector.
    const searchId = `${layout}-sheet-search`

    return (
      <>
        <div className="selected-sheet-strip" aria-label="Selected sheets">
          {activeChatDatasets.length ? (
            activeChatDatasets.map((dataset) => {
              const label = [
                dataset.original_filename || 'Uploaded spreadsheet',
                dataset.sheet_name,
              ].filter(Boolean).join(' · ')
              return (
                <span
                  className="selected-sheet-chip"
                  key={dataset.dataset_id}
                  title={label}
                >
                  <span>{label}</span>
                  <button
                    type="button"
                    aria-label={`Remove ${label}`}
                    onClick={() => saveChatDatasetSelection(
                      dataset.dataset_id,
                      false,
                    )}
                  >
                    ×
                  </button>
                </span>
              )
            })
          ) : (
            <span className="selected-sheet-empty">No sheets selected</span>
          )}
        </div>
        {datasetPickerOpen && (
          <div
            className="sheet-picker-popover"
            id="spreadsheet-picker"
            role="region"
            aria-label="Search and select spreadsheets"
          >
            <div className="sheet-picker-heading">
              <strong>Choose existing sheets</strong>
              <div>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploading}
                >
                  + Upload spreadsheet
                </button>
                <button
                  type="button"
                  className="sheet-picker-done"
                  onClick={() => {
                    setDatasetPickerOpen(false)
                    setDatasetSearch('')
                  }}
                >
                  Done
                </button>
              </div>
            </div>
            <label className="sheet-picker-search" htmlFor={searchId}>
              <span>Search files and sheets</span>
              <input
                id={searchId}
                type="search"
                value={datasetSearch}
                onChange={(event) => setDatasetSearch(event.target.value)}
                placeholder="Search CSV or worksheet name"
              />
            </label>
            {filteredDatasets.length ? (
              <div className="sheet-picker-list">
                {filteredDatasets.map((dataset) => {
                  const selected = activeChat?.selectedDatasetIds?.includes(
                    dataset.dataset_id,
                  ) || false
                  const atLimit =
                    (activeChat?.selectedDatasetIds?.length || 0) >= 5
                  const label = [
                    dataset.original_filename || 'Uploaded spreadsheet',
                    dataset.sheet_name || dataset.sheet,
                  ].filter(Boolean).join(' · ')
                  return (
                    <label
                      className={`sheet-picker-option ${selected ? 'selected' : ''}`}
                      key={dataset.dataset_id}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={!selected && atLimit}
                        onChange={(event) => saveChatDatasetSelection(
                          dataset.dataset_id,
                          event.target.checked,
                        )}
                      />
                      <span>
                        <strong>{label}</strong>
                        <small>
                          {formatNumber(dataset.row_count)} rows ·{' '}
                          {formatNumber(dataset.column_count)} columns
                        </small>
                      </span>
                    </label>
                  )
                })}
              </div>
            ) : (
              <p className="analyst-no-sources">
                {datasets.length
                  ? `No files or sheets match “${datasetSearch}”.`
                  : 'No saved spreadsheets yet. Upload a CSV, XLS, or XLSX file.'}
              </p>
            )}
          </div>
        )}
      </>
    )
  }

  function renderDatasetTable() {
    // Render dataset loading, error, empty, and populated states for the dataset page.
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
          <p>Upload a CSV or Excel spreadsheet to get started.</p>
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
              <th>Actions</th>
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
                <td>
                  <button
                    className="danger-button"
                    type="button"
                    onClick={() => deleteDatasetAndRelatedData(dataset)}
                    disabled={Boolean(deletingDatasetId)}
                  >
                    {deletingDatasetId === dataset.dataset_id
                      ? 'Deleting…'
                      : 'Delete'}
                  </button>
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
                  className={`nav-item ${activePage === item.id ? 'active' : ''}`}
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
              <div
                key={chat.id}
                className={`chat-history-row ${
                  chat.id === activeChatId ? 'active' : ''
                }`}
              >
                <button
                  type="button"
                  className="chat-history-item"
                  onClick={() => {
                    shouldAutoScrollRef.current = true
                    setActiveChatId(chat.id)
                    setChatError('')
                    setActivePage('analyst')
                  }}
                  title={chat.title}
                >
                  <span aria-hidden="true">◷</span>
                  <span className="chat-history-title">
                    {chat.title || 'New chat'}
                  </span>
                </button>
                <button
                  type="button"
                  className="chat-delete-button"
                  onClick={() => deleteChatSession(chat.id)}
                  aria-label={`Delete ${chat.title || 'New chat'}`}
                  title="Delete chat and its history"
                >
                  ×
                </button>
              </div>
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
              {/* <small>
                {apiStatus === 'connected'
                  ? 'FastAPI is responding'
                  : apiStatus === 'checking'
                    ? 'Connecting to backend'
                    : 'Check backend and try again'}
              </small> */}
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

      <input
        ref={fileInputRef}
        id="dataset-file"
        className="shared-file-input"
        type="file"
        accept=".csv,.xls,.xlsx"
        onChange={handleFileChange}
        disabled={uploading}
        aria-label="Choose a CSV or Excel spreadsheet"
      />

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

        <div className={`page-content ${activePage === 'analyst' ? 'chat-page-content' : ''}`}>
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
                    ? `${datasets.length} datasets available. Select sheets in AI Analyst, then submit your question.`
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
                  <strong
                    className={`metric-value ${loading ? 'is-loading' : ''}`}
                    aria-busy={loading}
                  >
                    {loading ? '…' : error ? '—' : datasets.length}
                  </strong>
                  <p>Stored datasets</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon purple">✳</span>
                    <span className="metric-label">AI QUESTIONING</span>
                  </div>
                  <strong className="metric-value"> </strong>
                  <p>Ask AI for insights</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon green">⌘</span>
                    <span className="metric-label">TOTAL ROWS</span>
                  </div>
                  <strong
                    className={`metric-value ${loading ? 'is-loading' : ''}`}
                    aria-busy={loading}
                  >
                    {loading ? '…' : error ? '—' : formatNumber(totalRows)}
                  </strong>
                  <p>Across stored datasets</p>
                </article>

                <article className="metric-card">
                  <div className="metric-top">
                    <span className="metric-icon amber">◎</span>
                    <span className="metric-label">TOTAL COLUMNS</span>
                  </div>
                  <strong
                    className={`metric-value ${loading ? 'is-loading' : ''}`}
                    aria-busy={loading}
                  >
                    {loading ? '…' : error ? '—' : formatNumber(totalColumns)}
                  </strong>
                  <p>Across stored datasets</p>
                </article>
              </section>

              <section className="section-block">
                <div className="section-heading">
                  <div>
                    <h2>Uploaded sheet graphs</h2>
                    <p>
                      Graphs are grouped by uploaded sheet and show only the
                      sheet and its entity nodes.
                    </p>
                  </div>
                </div>
                <OverviewGraphs datasets={datasets} />
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
              <section className="page-intro dataset-page-intro">
                <p className="eyebrow">DATA MANAGEMENT</p>
                <h1>Your datasets</h1>
                <p>
                  Upload spreadsheets and review the datasets stored in your
                  GraphSheet AI workspace.
                </p>
              </section>

              <section className="section-block upload-panel dataset-page-section dataset-upload-section">
                <div className="section-heading">
                  <div>
                    <h2>Upload a spreadsheet</h2>
                    <p>Supported formats: CSV, XLS, and XLSX. Maximum size: 10 MB.</p>
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
                        : 'Select a CSV or Excel spreadsheet from your computer.'}
                    </span>
                  </label>

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
                        ? uploadProgress >= 100
                          ? 'Processing…'
                          : 'Uploading…'
                        : 'Upload spreadsheet'}
                    </button>
                  </div>

                  {uploading && (
                    <div className="upload-progress">
                      <div className="upload-progress-heading">
                        {uploadProgress >= 100 ? (
                          <span>Processing worksheets and indexing data…</span>
                        ) : (
                          <>
                            <span>Uploading spreadsheet</span>
                            <strong>{uploadProgress}%</strong>
                          </>
                        )}
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

              <section className="section-block dataset-page-section dataset-list-section">
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

                {datasetDeleteError && (
                  <div className="upload-feedback error" role="alert">
                    <strong>Dataset deletion failed</strong>
                    <p>{datasetDeleteError}</p>
                  </div>
                )}

                {renderDatasetTable()}
              </section>
            </>
          )}

         
{activePage === 'analyst' && (
  <section className="analyst-workspace">
    <div className="analyst-main">
      <header className="analyst-header">
        <div>
          <h1>Ask your data</h1>
          <p>
            Upload a spreadsheet or choose a saved file, then ask a question.
          </p>
        </div>
        <div className="analyst-chat-name">
          <span className="chat-presence-dot" />
          {activeChat?.title || 'New chat'}
        </div>
      </header>

      <div
        className="chat-messages"
        aria-live="polite"
        ref={chatMessagesRef}
        onScroll={(event) => {
          const element = event.currentTarget
          shouldAutoScrollRef.current =
            element.scrollHeight - element.scrollTop - element.clientHeight < 100
        }}
      >
        {!activeChat?.messages?.length && (
          <div className="chat-empty-state">
            <div className="chat-welcome-icon">✳</div>
            <h2>
              {activeChatDatasets.length
                ? 'What would you like to know?'
                : 'Choose a spreadsheet to begin'}
            </h2>
            <p>
              {activeChatDatasets.length
                ? 'Ask for a summary, comparison, calculation, or trend.'
                : 'Choose a saved spreadsheet or upload one from the controls below.'}
            </p>
            <div className="chat-suggestion-list">
              {[
                'Summarize the selected sheets',
                'Compare totals across sheets',
                'Show the most important trends',
              ].map((suggestion) => (
                <button
                  type="button"
                  key={suggestion}
                  onClick={() => setChatInput(suggestion)}
                  disabled={!activeChatDatasets.length}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {activeChat?.messages?.map((message) => (
          <article
            key={message.id}
            className={`chat-message ${message.role} ${message.intent === 'sql' ? 'sql-chat-message' : ''}`}
          >
            <span className="message-role">
              <span className="message-avatar">
                {message.role === 'user' ? 'Y' : '✳'}
              </span>
              {message.role === 'user' ? 'You' : 'GraphSheet AI'}
              {['sql', 'sql_analysis', 'sql_code_generation'].includes(message.intent) && (
                <span className="intent-badge">SQL</span>
              )}
            </span>

            <div className="markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content || ''}
              </ReactMarkdown>
              {message.role === 'assistant' && message.structuredAnswer && (
                <StructuredAnswer
                  answer={message.structuredAnswer}
                  onFollowUp={(suggestion) => sendChatQuestion(suggestion)}
                  onError={setChatError}
                  disabled={chatLoading || sqlLoading}
                />
              )}
              {message.role === 'assistant' &&
                !message.sql &&
                message.sqlSources?.length > 0 && (
                  <p className="chat-sql-sources">
                    Calculated from:{' '}
                    {message.sqlSources.map((source) => (
                      [
                        source.filename,
                        source.sheet_name,
                      ].filter(Boolean).join(' — ')
                    )).join(', ')}
                  </p>
                )}
              {message.role === 'assistant' && message.sqlAnalysis && (
                <SQLAnalysisResults
                  analysis={message.sqlAnalysis}
                  onApproveRelationship={(relationship) => (
                    approveAndRetrySQLRelationship(
                      relationship,
                      message.question,
                    )
                  )}
                />
              )}
              {message.sql && (
                <details className="chat-sql-details">
                  <summary>View generated SQL and query result</summary>
                  {message.sqlSources?.length > 0 && (
                    <p className="chat-sql-sources">
                      Source sheets:{' '}
                      {message.sqlSources.map((source) => (
                        [
                          source.filename,
                          source.sheet_name,
                        ].filter(Boolean).join(' — ')
                      )).join(', ')}
                    </p>
                  )}
                  <div className="chat-sql-actions">
                    <button
                      type="button"
                      onClick={() => {
                        if (!navigator.clipboard?.writeText) {
                          setChatError('Clipboard access is unavailable in this browser.')
                          return
                        }
                        navigator.clipboard.writeText(message.sql).catch(() => {
                          setChatError('Could not copy SQL. Please select and copy it manually.')
                        })
                      }}
                    >
                      Copy SQL
                    </button>
                  </div>
                  <HighlightedSQL code={message.sql} />
                  {message.sqlResult?.columns?.length > 0 && (
                    <div className="chat-sql-table-wrap">
                      <table className="chat-sql-table">
                        <thead>
                          <tr>
                            {message.sqlResult.columns.map((column) => (
                              <th key={column}>{column}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {(message.sqlResult.rows || []).slice(0, 10).map((row, rowIndex) => (
                            <tr key={`${message.id}-${rowIndex}`}>
                              {message.sqlResult.columns.map((column) => (
                                <td key={`${message.id}-${rowIndex}-${column}`}>
                                  {row[column] == null ? '—' : String(row[column])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </details>
              )}
              {message.role === 'assistant' && message.sources?.length > 0 && (
                <div className="answer-evidence-summary">
                  <span aria-hidden="true">✣</span>
                  Answer grounded in {message.sources.length} evidence chunk
                  {message.sources.length === 1 ? '' : 's'}
                  <button
                    type="button"
                    onClick={() => setContextTab('evidence')}
                  >
                    View evidence
                  </button>
                </div>
              )}
              {message.role === 'assistant' && message.graphWarning && (
                <p className="chat-graph-warning" role="status">
                  {message.graphWarning}
                </p>
              )}
              {message.role === 'assistant' &&
                message.status === 'failed' &&
                message.question &&
                message.requestId && (
                  <button
                    type="button"
                    className="secondary-button chat-retry-button"
                    disabled={chatLoading || sqlLoading}
                    onClick={() => (
                      ['sql_analysis', 'sql_code_generation'].includes(message.intent)
                        ? runSqlQuestion(
                          message.question,
                          (message.sqlAnalysis?.mode || message.sqlMode) === 'code_generation'
                            ? 'code_generation'
                            : 'analysis',
                        )
                        : sendChatQuestion(message.question, message.requestId)
                    )}
                  >
                    Retry question
                  </button>
                )}
            </div>
          </article>
        ))}

        {chatLoading && (
          <p className="loading-message">Analyzing selected sheets…</p>
        )}
        {sqlLoading && (
          <p className="loading-message">
            {sqlLoadingMode === 'code_generation'
              ? 'Generating SQL code…'
              : 'Generating and safely running SQL…'}
          </p>
        )}
        {chatError && <p className="error-message" role="alert">{chatError}</p>}
      </div>

      <form
        className="chat-composer"
        onSubmit={(event) => {
          event.preventDefault()
          sendChatQuestion()
        }}
      >
        <div
          className="chat-source-panel"
          id="chat-source-panel"
          hidden={!sourceToolsOpen}
        >
          <div className="chat-source-toolbar-actions">
            <button
              type="button"
              className="secondary-button chat-source-button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              <span aria-hidden="true">＋</span> Upload file
            </button>
            <button
              type="button"
              className="secondary-button chat-source-button"
              onClick={() => setDatasetPickerOpen((open) => !open)}
              disabled={uploading}
              aria-expanded={datasetPickerOpen}
              aria-controls="spreadsheet-picker"
            >
              <span aria-hidden="true">▤</span> Select files
            </button>
          </div>
          {renderSheetManager('analyst')}

          {selectedFile && (
            <div className="analyst-selected-file">
              <div className="analyst-selected-file-copy">
                <span className="analyst-file-icon" aria-hidden="true">▤</span>
                <span>
                  <strong>{selectedFile.name}</strong>
                  <small>{(selectedFile.size / 1024).toFixed(1)} KB · Ready to upload</small>
                </span>
              </div>
              <button
                type="button"
                className="analyst-remove-file"
                onClick={() => {
                  setSelectedFile(null)
                  setUploadError('')
                  if (fileInputRef.current) fileInputRef.current.value = ''
                }}
                disabled={uploading}
              >
                Remove
              </button>
              <button
                type="button"
                className="primary-button analyst-upload-button"
                onClick={handleUpload}
                disabled={uploading}
              >
                {uploading
                  ? uploadProgress >= 100
                    ? 'Processing worksheets…'
                    : `Uploading ${uploadProgress}%…`
                  : uploadError ? 'Retry upload' : 'Upload and select'}
              </button>
            </div>
          )}

          {uploading && (
            <progress
              className="analyst-upload-progress"
              value={uploadProgress}
              max="100"
              aria-label="Upload progress"
            />
          )}
          {uploadError && (
            <p className="analyst-source-feedback error" role="alert">
              {uploadError}
            </p>
          )}
          {uploadSuccess && (
            <p className="analyst-source-feedback success" role="status">
              {uploadSuccess}
            </p>
          )}
        </div>

        <textarea
          value={chatInput}
          onChange={(event) => setChatInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          placeholder="Ask a question about your selected sheets…"
          rows={2}
          disabled={chatLoading || sqlLoading}
          aria-label="Ask a question about selected sheets"
        />
        <div className="chat-composer-actions">
          <span>Enter to ask · Shift + Enter for a new line</span>
          <div>
            <button
              type="button"
              className={`chat-attachment-toggle ${sourceToolsOpen ? 'active' : ''}`}
              onClick={() => {
                setSourceToolsOpen((open) => !open)
                if (sourceToolsOpen) setDatasetPickerOpen(false)
              }}
              disabled={uploading}
              aria-label={sourceToolsOpen ? 'Hide file selection controls' : 'Show file selection controls'}
              aria-expanded={sourceToolsOpen}
              aria-controls="chat-source-panel"
              title={sourceToolsOpen ? 'Hide spreadsheet controls' : 'Attach or select spreadsheets'}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="m21.4 11.1-8.5 8.5a5.7 5.7 0 0 1-8.1-8.1l9.2-9.2a3.8 3.8 0 0 1 5.4 5.4l-9.2 9.2a1.9 1.9 0 0 1-2.7-2.7l8.5-8.5" />
              </svg>
              <span className="chat-attachment-label">Files</span>
              <span className="chat-attachment-count">
                {activeChatDatasets.length}
              </span>
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={chatLoading || sqlLoading || !chatInput.trim() || !activeChatDatasets.length}
              onClick={() => runSqlQuestion(chatInput, 'analysis')}
            >
              {sqlLoading ? 'Working…' : '⌘ Analyze with SQL'}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={chatLoading || sqlLoading || !chatInput.trim() || !activeChatDatasets.length}
              onClick={() => runSqlQuestion(chatInput, 'code_generation')}
            >
              Generate SQL code
            </button>
            {chatLoading || sqlLoading ? (
              <button
                className="chat-stop-button"
                type="button"
                onClick={stopCurrentRequest}
                aria-label="Stop the current response"
                title="Stop the current response"
              >
                <span aria-hidden="true">■</span> Stop
              </button>
            ) : (
              <button
                className="primary-button"
                type="submit"
                disabled={!chatInput.trim() || !activeChatDatasets.length}
              >
                Ask question ↑
              </button>
            )}
          </div>
        </div>
        {!activeChatDatasets.length && (
          <p className="chat-selection-hint">
            Select at least one spreadsheet or upload a file to ask a question.
          </p>
        )}
      </form>
    </div>

    <ChatContextPanel
      chatId={activeChat?.id}
      datasets={activeChatDatasets}
      question={evidenceQuestion}
      sources={latestAnswerMessage?.sources || EMPTY_SOURCES}
      sqlQuery={latestSQLQuery}
      tab={contextTab}
      onTabChange={setContextTab}
      onManageSheets={() => openPage('datasets')}
    />
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
      setActivePage('analyst')
    }}
  />
)}

{activePage !== 'overview' &&
activePage !== 'datasets' &&
activePage !== 'analyst' &&
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
