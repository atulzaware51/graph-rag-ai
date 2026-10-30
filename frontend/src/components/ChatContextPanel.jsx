import { useEffect, useMemo, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import useElementWidth from '../hooks/useElementWidth'
import {
  getConversationGraph,
  getDatasetPreview,
  searchDataset,
} from '../services/api'
import './ChatContextPanel.css'

function getFilename(dataset) {
  return dataset?.original_filename || 'Uploaded sheet'
}

export default function ChatContextPanel({
  chatId,
  datasets,
  question,
  sources,
  sqlQuery,
  tab,
  onTabChange,
  onManageSheets,
}) {
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [previews, setPreviews] = useState({})
  const [evidence, setEvidence] = useState([])
  const [activeDatasetId, setActiveDatasetId] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [graphCanvasRef, graphCanvasWidth] = useElementWidth()

  const datasetIds = useMemo(
    () => datasets.map((dataset) => dataset.dataset_id),
    [datasets],
  )
  const datasetKey = datasetIds.join(',')
  const sourceIds = useMemo(
    () => [...new Set(
      sources
        .filter((source) => datasetIds.includes(source.dataset_id))
        .map((source) => source.dataset_id)
        .filter(Boolean),
    )],
    [datasetIds, sources],
  )
  const sourceKey = sourceIds.join(',')
  const evidenceKey = sources.map((source) => source.chunk_id).join(',')

  useEffect(() => {
    let active = true

    async function loadContext() {
      if (!chatId) {
        setGraph({ nodes: [], edges: [] })
        setPreviews({})
        setEvidence([])
        setLoading(false)
        return
      }

      setLoading(true)
      setError('')
      const failures = []
      const previewResults = await Promise.all(
        datasets.map(async (dataset) => {
          try {
            const response = await getDatasetPreview(dataset.dataset_id)
            return [dataset.dataset_id, response.rows || []]
          } catch (loadError) {
            failures.push(loadError.message)
            return [dataset.dataset_id, []]
          }
        }),
      )

      let nextGraph = { nodes: [], edges: [] }
      try {
        const response = await getConversationGraph(chatId)
        nextGraph = {
          nodes: response.nodes || [],
          edges: response.edges || [],
        }
      } catch (loadError) {
        failures.push(loadError.message)
      }

      const evidenceResults = await Promise.all(
        sourceIds.map(async (datasetId) => {
          try {
            return await searchDataset(datasetId, question, 10)
          } catch (loadError) {
            failures.push(loadError.message)
            return []
          }
        }),
      )
      const allowedChunkIds = new Set(
        sources
          .filter((source) => datasetIds.includes(source.dataset_id))
          .map((source) => source.chunk_id)
          .filter(Boolean),
      )
      const nextEvidence = evidenceResults
        .flat()
        .filter((item) => allowedChunkIds.has(item.chunk_id))

      if (active) {
        setPreviews(Object.fromEntries(previewResults))
        setGraph(nextGraph)
        setEvidence(nextEvidence)
        setActiveDatasetId((current) =>
          datasetIds.includes(current) ? current : (sourceIds[0] || datasetIds[0] || ''),
        )
        setError(failures.length ? failures[0] : '')
        setLoading(false)
      }
    }

    loadContext()
    return () => {
      active = false
    }
  }, [
    chatId,
    datasetKey,
    datasetIds,
    datasets,
    question,
    sourceIds,
    sourceKey,
    sources,
    evidenceKey,
  ])

  const visibleGraph = useMemo(() => {
    const selectedIds = new Set(datasetIds)
    const nodes = graph.nodes.filter((node) => (
      node.type === 'Chat'
      || selectedIds.has(node.dataset_id)
      || (Array.isArray(node.dataset_ids)
        && node.dataset_ids.some((datasetId) => selectedIds.has(datasetId)))
    ))
    const visibleIds = new Set(nodes.map((node) => node.id))
    const links = graph.edges
      .filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
      .map((edge) => ({ ...edge }))

    return { nodes, links }
  }, [datasetIds, graph])

  const activeDataset = datasets.find(
    (dataset) => dataset.dataset_id === activeDatasetId,
  )
  const activePreview = previews[activeDatasetId] || []
  const displayedEvidence = evidence.filter(
    (item) => item.dataset_id === activeDatasetId,
  )

  return (
    <aside className="chat-context-panel" aria-label="Chat evidence and graph">
      <div className="context-tabs" role="tablist" aria-label="Context views">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'evidence'}
          className={tab === 'evidence' ? 'active' : ''}
          onClick={() => onTabChange('evidence')}
        >
          <span aria-hidden="true">▤</span> Evidence
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'graph'}
          className={tab === 'graph' ? 'active' : ''}
          onClick={() => onTabChange('graph')}
        >
          <span aria-hidden="true">✣</span> Graph
        </button>
      </div>

      {error && (
        <p className="context-error" role="status">
          Some context could not be loaded: {error}
        </p>
      )}

      {tab === 'evidence' ? (
        <div className="context-scroll">
          <section className="context-section">
            <div className="context-section-heading">
              <h3>Used sheets</h3>
              <button type="button" onClick={onManageSheets}>Manage</button>
            </div>
            {datasets.length ? (
              <div className="used-sheet-list">
                {datasets.map((dataset) => {
                  const wasUsed = sourceIds.includes(dataset.dataset_id)
                  return (
                    <button
                      type="button"
                      className={`used-sheet ${dataset.dataset_id === activeDatasetId ? 'selected' : ''}`}
                      key={dataset.dataset_id}
                      onClick={() => setActiveDatasetId(dataset.dataset_id)}
                    >
                      <span className="sheet-file-icon" aria-hidden="true">▤</span>
                      <span className="used-sheet-copy">
                        <strong>{getFilename(dataset)}</strong>
                        <small>
                          {dataset.column_count} columns · {Number(dataset.row_count).toLocaleString()} rows
                        </small>
                      </span>
                      {wasUsed && <span className="evidence-check" title="Cited as evidence">✓</span>}
                    </button>
                  )
                })}
              </div>
            ) : (
              <div className="context-empty">
                <p>No sheets selected for this chat.</p>
                <button type="button" onClick={onManageSheets}>Choose sheets</button>
              </div>
            )}
          </section>

          <section className="context-section">
            <div className="context-section-heading">
              <h3>Supporting evidence</h3>
              <span>{sources.length} chunk{sources.length === 1 ? '' : 's'}</span>
            </div>
            {!sources.length ? (
              <p className="context-muted">
                Ask a question to see which sheet evidence supports the answer.
              </p>
            ) : loading ? (
              <p className="context-muted">Loading cited evidence…</p>
            ) : displayedEvidence.length ? (
              <div className="evidence-list">
                {displayedEvidence.map((item) => (
                  <article className="evidence-card" key={item.chunk_id}>
                    <header>
                      <strong>Chunk {(item.chunk_index ?? 0) + 1}</strong>
                      <span>Score {item.score}</span>
                    </header>
                    <pre>{item.text}</pre>
                    {item.graph_matches?.length > 0 && (
                      <div className="evidence-matches">
                        {item.graph_matches.map((match) => (
                          <span key={`${item.chunk_id}-${match.sql_name}`}>
                            {match.column || match.sql_name}
                          </span>
                        ))}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            ) : (
              <p className="context-muted">
                Evidence references are saved, but their text could not be loaded.
              </p>
            )}
          </section>

          <section className="context-section">
            <div className="context-section-heading">
              <h3>Sheet preview</h3>
              {activeDataset && <span>{getFilename(activeDataset)}</span>}
            </div>
            {activePreview.length ? (
              <div className="context-preview-table">
                <table>
                  <thead>
                    <tr>
                      {Object.keys(activePreview[0]).map((column) => (
                        <th key={column}>{column}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {activePreview.slice(0, 5).map((row, rowIndex) => (
                      <tr key={`${activeDatasetId}-${rowIndex}`}>
                        {Object.keys(activePreview[0]).map((column) => (
                          <td key={`${rowIndex}-${column}`}>
                            {row[column] == null ? '—' : String(row[column])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="context-muted">
                {loading ? 'Loading sheet preview…' : 'Select a sheet to preview its rows.'}
              </p>
            )}
          </section>

          {sqlQuery?.sql && (
            <section className="context-section">
              <div className="context-section-heading">
                <h3>Latest generated SQL</h3>
              </div>
              <pre className="context-sql">{sqlQuery.sql}</pre>
              <p className="context-muted">
                {sqlQuery.row_count ?? sqlQuery.rows?.length ?? 0} rows returned
              </p>
            </section>
          )}
        </div>
      ) : (
        <div className="context-graph-view">
          <section className="context-section">
            <div className="context-section-heading">
              <h3>Selected-sheet graph</h3>
              <span>{visibleGraph.nodes.length} nodes</span>
            </div>
            {loading ? (
              <p className="context-muted">Loading graph…</p>
            ) : visibleGraph.nodes.length ? (
              <div className="context-graph-canvas" ref={graphCanvasRef}>
                <ForceGraph2D
                  graphData={visibleGraph}
                  nodeId="id"
                  width={graphCanvasWidth}
                  height={280}
                  nodeRelSize={5}
                  nodeColor={(node) => (
                    node.type === 'Chat' ? '#7489ee'
                      : node.type === 'Dataset' ? '#22b981'
                        : node.type === 'SQLQuery' ? '#9857e8'
                          : '#58a0e8'
                  )}
                  nodeLabel={(node) => `${node.type}: ${node.label || node.id}`}
                  linkColor={() => '#a9bbd4'}
                  linkWidth={1.5}
                  linkDirectionalArrowLength={4}
                  linkDirectionalArrowRelPos={1}
                  cooldownTicks={40}
                  enableNodeDrag={false}
                />
              </div>
            ) : (
              <p className="context-muted">No graph nodes for this chat yet.</p>
            )}
          </section>
          <section className="context-section">
            <div className="context-section-heading">
              <h3>Relationships</h3>
              <span>{visibleGraph.links.length}</span>
            </div>
            {visibleGraph.links.length ? (
              <ul className="context-relationship-list">
                {visibleGraph.links.slice(0, 8).map((edge) => {
                  const source = visibleGraph.nodes.find((node) => node.id === edge.source)
                  const target = visibleGraph.nodes.find((node) => node.id === edge.target)
                  return (
                    <li key={edge.id}>
                      <span>{source?.label || source?.type || 'Node'}</span>
                      <small>{edge.type}</small>
                      <span>{target?.label || target?.type || 'Node'}</span>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="context-muted">Relationships appear when sheets are selected.</p>
            )}
          </section>
        </div>
      )}
    </aside>
  )
}
