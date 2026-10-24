
import { useCallback, useEffect, useMemo, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { getKnowledgeGraph } from '../services/api'
import { buildChatGraph } from './chatGraph'
import './KnowledgeGraph.css'

export default function KnowledgeGraph({ chat, onQuery }) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedNode, setSelectedNode] = useState(null)
  const [search, setSearch] = useState('')
  const [edgeFilter, setEdgeFilter] = useState('ALL')

  useEffect(() => {
    let active = true

    async function loadGraph() {
      try {
        setLoading(true)
        setError('')

        const result = chat
          ? buildChatGraph(chat)
          : await getKnowledgeGraph(500)

        if (active) {
          setGraph({
            nodes: result.nodes ?? [],
            edges: result.edges ?? [],
          })
          setSelectedNode(null)
        }
      } catch (err) {
        if (active) {
          setError(err.message || 'Unable to load the knowledge graph.')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    loadGraph()

    return () => {
      active = false
    }
  }, [chat])

  const visibleGraph = useMemo(() => {
    const term = search.trim().toLowerCase()

    const nodes = graph.nodes.filter((node) => {
      const matchesSearch =
        !term ||
        String(node.label ?? '').toLowerCase().includes(term) ||
        String(node.type ?? '').toLowerCase().includes(term) ||
        String(node.dataset_id ?? '').toLowerCase().includes(term)

      return matchesSearch
    })

    const nodeIds = new Set(nodes.map((node) => node.id))

    const edges = graph.edges.filter((edge) => {
      const sourceId =
        typeof edge.source === 'object' ? edge.source.id : edge.source
      const targetId =
        typeof edge.target === 'object' ? edge.target.id : edge.target

      const matchesNodes = nodeIds.has(sourceId) && nodeIds.has(targetId)
      const matchesType = edgeFilter === 'ALL' || edge.type === edgeFilter

      return matchesNodes && matchesType
    })

    return {
      nodes,
      links: edges.map((edge) => ({
        ...edge,
        source:
          typeof edge.source === 'object' ? edge.source.id : edge.source,
        target:
          typeof edge.target === 'object' ? edge.target.id : edge.target,
      })),
    }
  }, [graph, search, edgeFilter])

  const sheetCount = graph.nodes.filter(
    (node) => node.type === 'Dataset',
  ).length
  const entityCount = graph.nodes.filter(
    (node) => node.type === 'Entity' || node.type === 'Column',
  ).length
  const queryCount = graph.nodes.filter(
    (node) => node.type === 'Query',
  ).length
  const timelineEvents = graph.nodes
    .filter((node) => node.type === 'Upload' || node.type === 'Query')
    .sort(
      (left, right) =>
        new Date(left.uploaded_at || left.executed_at) -
        new Date(right.uploaded_at || right.executed_at),
    )

  const handleNodeClick = useCallback((node) => {
    setSelectedNode(node)
  }, [])

  const paintNode = useCallback(
    (node, ctx, globalScale) => {
      const isDataset = node.type === 'Dataset'
      const isMain = node.type === 'Chat'
      const isSelected = selectedNode?.id === node.id
      const radius = isMain ? 10 : isDataset ? 8 : 5

      ctx.beginPath()
      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI)
      ctx.fillStyle = isMain
        ? '#1d4ed8'
        : isDataset
          ? '#2563eb'
          : node.type === 'Query'
            ? '#7c3aed'
            : node.type === 'Result'
              ? '#16a34a'
              : node.type === 'Upload'
                ? '#c2410c'
                : '#0f766e'
      ctx.fill()

      if (isSelected) {
        ctx.lineWidth = 2 / globalScale
        ctx.strokeStyle = '#111827'
        ctx.stroke()
      }

      if (isMain || isDataset || node.type === 'Query' || node.type === 'Result' || globalScale > 1.5) {
        const label = String(node.label ?? node.id)
        ctx.font = `${isMain || isDataset ? '600 ' : ''}${12 / globalScale}px sans-serif`
        ctx.textAlign = 'left'
        ctx.textBaseline = 'middle'
        ctx.fillStyle = '#1f2937'
        ctx.fillText(label, node.x + radius + 3, node.y)
      }
    },
    [selectedNode],
  )

  if (loading) {
    return (
      <section className="kg-page">
        <div className="kg-status">Loading knowledge graph…</div>
      </section>
    )
  }

  if (error) {
    return (
      <section className="kg-page">
        <div className="kg-status kg-error">
          <h3>Could not load knowledge graph</h3>
          <p>{error}</p>
          <button onClick={() => window.location.reload()}>
            Reload page
          </button>
        </div>
      </section>
    )
  }

  return (
    <section className="kg-page">
      <header className="kg-header">
        <div>
          <h2>{chat?.title || 'Knowledge Graph Explorer'}</h2>
          <p>
            Chat graph: uploaded sheets, their entities, queries, and results.
          </p>
        </div>

        <button
          className="kg-reset"
          onClick={() => {
            setSearch('')
            setEdgeFilter('ALL')
            setSelectedNode(null)
          }}
        >
          Clear filters
        </button>
      </header>

      <div className="kg-stats">
        <div className="kg-stat">
          <span>Sheets</span>
          <strong>{sheetCount}</strong>
        </div>
        <div className="kg-stat">
          <span>Entities</span>
          <strong>{entityCount}</strong>
        </div>
        <div className="kg-stat">
          <span>Queries</span>
          <strong>{queryCount}</strong>
        </div>
      </div>

      <div className="kg-toolbar">
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search sheets, entities, queries…"
          aria-label="Search graph nodes"
        />

        <select
          value={edgeFilter}
          onChange={(event) => setEdgeFilter(event.target.value)}
          aria-label="Filter relationship type"
        >
          <option value="ALL">All relationships</option>
          <option value="HAS_SHEET">Chat → Sheet</option>
          <option value="HAS_ENTITY">Sheet → Entity</option>
          <option value="UPLOAD_EVENT">Upload timeline</option>
          <option value="QUERY_ON">Sheet → Query</option>
          <option value="USES_ENTITY">Query → Entity</option>
          <option value="QUERY_RESULT">Query → Result</option>
        </select>
      </div>

      <div className="kg-legend">
        <span><i className="kg-dot kg-dataset-dot" /> Sheet</span>
        <span><i className="kg-dot kg-column-dot" /> Entity</span>
        <span><i className="kg-dot kg-query-dot" /> Query</span>
        <span><i className="kg-dot kg-result-dot" /> Result</span>
        <span>Click a node to inspect its details</span>
      </div>

      {chat && (
        <div className="kg-chat-label">
          Graph for chat: <strong>{chat.title || 'New chat'}</strong>
        </div>
      )}

      {timelineEvents.length > 0 && (
        <ol className="kg-timeline" aria-label="Chat activity timeline">
          {timelineEvents.map((event) => {
            const timestamp = event.uploaded_at || event.executed_at
            return (
            <li key={event.id} className={`kg-timeline-${event.type.toLowerCase()}`}>
              <time dateTime={timestamp}>
                {timestamp
                  ? new Date(timestamp).toLocaleString()
                  : 'Time unavailable'}
              </time>
              <span>
                {event.type === 'Upload'
                  ? `Upload: ${event.filename}`
                  : `Query: ${event.question}`}
              </span>
            </li>
            )
          })}
        </ol>
      )}

      <div className="kg-workspace">
        <div className="kg-canvas">
          {visibleGraph.nodes.length ? (
            <ForceGraph2D
              graphData={visibleGraph}
              nodeId="id"
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node, color, ctx) => {
                ctx.fillStyle = color
                ctx.beginPath()
                ctx.arc(node.x, node.y, node.type === 'Chat' ? 13 : node.type === 'Dataset' ? 11 : 8, 0, 2 * Math.PI)
                ctx.fill()
              }}
              linkColor={(link) => {
                if (link.type === 'QUERY_RESULT') return '#16a34a'
                if (link.type === 'USES_ENTITY') return '#d97706'
                if (link.type === 'UPLOAD_EVENT') return '#c2410c'
                if (link.type === 'QUERY_ON') return '#7c3aed'
                return '#94a3b8'
              }}
              linkWidth={(link) => {
                if (link.type === 'QUERY_RESULT') return 2.5
                if (link.type === 'USES_ENTITY' || link.type === 'QUERY_ON') return 2
                return 1.25
              }}
              linkDirectionalArrowLength={5}
              linkDirectionalArrowRelPos={1}
              onNodeClick={handleNodeClick}
              cooldownTicks={100}
              enableNodeDrag
              enableZoomInteraction
              enablePanInteraction
            />
          ) : (
            <div className="kg-empty">
              No graph nodes match the current filters.
            </div>
          )}
        </div>

        <aside className="kg-details">
          <h3>Node details</h3>

          {selectedNode ? (
            <dl>
              <dt>Name</dt>
              <dd>{selectedNode.label ?? '—'}</dd>

              <dt>Type</dt>
              <dd>{selectedNode.type ?? '—'}</dd>

              {selectedNode.data_type && (
                <>
                  <dt>Data type</dt>
                  <dd>{selectedNode.data_type}</dd>
                </>
              )}

              {selectedNode.sql && (
                <>
                  <dt>Executed SQL</dt>
                  <dd><pre className="kg-sql">{selectedNode.sql}</pre></dd>
                </>
              )}

              {selectedNode.question && (
                <>
                  <dt>Question</dt>
                  <dd>{selectedNode.question}</dd>
                </>
              )}

              {selectedNode.uploaded_at && (
                <>
                  <dt>Uploaded</dt>
                  <dd>{new Date(selectedNode.uploaded_at).toLocaleString()}</dd>
                </>
              )}

              {Array.isArray(selectedNode.rows) && (
                <>
                  <dt>Query results ({selectedNode.row_count ?? selectedNode.rows.length} rows)</dt>
                  <dd>
                    {selectedNode.rows.length
                      ? <pre className="kg-result-data">{JSON.stringify(selectedNode.rows, null, 2)}</pre>
                      : 'No rows returned.'}
                  </dd>
                </>
              )}

              {selectedNode.table_name && (
                <>
                  <dt>Table name</dt>
                  <dd>{selectedNode.table_name}</dd>
                </>
              )}

              {selectedNode.dataset_id && (
                <>
                  <dt>Dataset ID</dt>
                  <dd className="kg-mono">{selectedNode.dataset_id}</dd>
                </>
              )}

              {selectedNode.status && (
                <>
                  <dt>Status</dt>
                  <dd>{selectedNode.status}</dd>
                </>
              )}

              {selectedNode.confidence != null && (
                <>
                  <dt>Confidence</dt>
                  <dd>{selectedNode.confidence}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="kg-muted">
              Select a node in the graph to inspect it here.
            </p>
          )}

          {onQuery && (selectedNode?.type === 'Chat' || selectedNode?.type === 'Dataset') && (
            <button
              type="button"
              className="kg-query-button"
              onClick={() =>
                onQuery(
                  selectedNode.type === 'Dataset'
                    ? selectedNode.dataset_id
                    : chat?.datasetId,
                )
              }
            >
              Ask a query from this {selectedNode.type === 'Dataset' ? 'sheet' : 'chat'}
            </button>
          )}
        </aside>
      </div>
    </section>
  )
}
