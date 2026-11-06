// Show one compact dataset-and-column graph card per uploaded sheet.
import { useEffect, useMemo, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { getKnowledgeGraph } from '../services/api'
import useElementWidth from '../hooks/useElementWidth'
import './OverviewGraphs.css'

function getEndpointId(endpoint) {
  // Graph APIs may return edge endpoints as IDs or expanded node objects.
  return typeof endpoint === 'object' ? endpoint?.id : endpoint
}

function DatasetGraph({ dataset, graph }) {
  // Restrict the shared graph payload to this sheet before rendering its overview.
  const [graphCanvasRef, graphCanvasWidth] = useElementWidth()
  const datasetGraph = useMemo(() => {
    const nodes = graph.nodes.filter(
      (node) =>
        node.dataset_id === dataset.dataset_id &&
        ['Dataset', 'Column', 'Entity'].includes(node.type),
    )
    const nodeIds = new Set(nodes.map((node) => node.id))
    const links = graph.edges
      .filter((edge) => {
        const source = getEndpointId(edge.source)
        const target = getEndpointId(edge.target)
        return nodeIds.has(source) && nodeIds.has(target)
      })
      .map((edge) => ({
        ...edge,
        source: getEndpointId(edge.source),
        target: getEndpointId(edge.target),
      }))

    return { nodes, links }
  }, [dataset.dataset_id, graph])

  const entityCount = datasetGraph.nodes.filter(
    (node) => node.type === 'Column' || node.type === 'Entity',
  ).length

  return (
    <article className="overview-graph-card">
      <header className="overview-graph-heading">
        <div>
          <h3>{dataset.original_filename || 'Untitled dataset'}</h3>
          <time dateTime={dataset.created_at}>
            Uploaded{' '}
            {dataset.created_at
              ? new Date(dataset.created_at).toLocaleString()
              : 'time unavailable'}
          </time>
        </div>
        <span className="overview-entity-count">
          {entityCount} {entityCount === 1 ? 'entity' : 'entities'}
        </span>
      </header>

      <div
        className="overview-graph-canvas"
        ref={graphCanvasRef}
        role="img"
        aria-label={`Graph for ${dataset.original_filename || 'dataset'} with ${entityCount} entity nodes`}
      >
        {datasetGraph.nodes.length ? (
          <ForceGraph2D
            graphData={datasetGraph}
            nodeId="id"
            width={graphCanvasWidth}
            height={220}
            nodeRelSize={6}
            nodeColor={(node) => node.type === 'Dataset' ? '#3b82f6' : '#059669'}
            nodeCanvasObject={(node, context, globalScale) => {
              const isDataset = node.type === 'Dataset'
              const radius = isDataset ? 8 : 5

              context.beginPath()
              context.arc(node.x, node.y, radius, 0, 2 * Math.PI)
              context.fillStyle = isDataset ? '#3b82f6' : '#059669'
              context.fill()
              context.font = `${isDataset ? '600 ' : ''}${11 / globalScale}px sans-serif`
              context.textAlign = 'left'
              context.textBaseline = 'middle'
              context.fillStyle = '#17324d'
              context.fillText(
                String(node.label ?? node.id),
                node.x + radius + 3,
                node.y,
              )
            }}
            linkColor={() => '#8aa8c8'}
            linkWidth={1.2}
            cooldownTicks={60}
            enableNodeDrag={false}
            enableZoomInteraction={false}
            enablePanInteraction={false}
          />
        ) : (
          <p>No entity nodes are available for this sheet.</p>
        )}
      </div>
      <footer className="overview-graph-legend">
        <span><i className="overview-graph-dot sheet" /> Sheet</span>
        <span><i className="overview-graph-dot entity" /> Entity</span>
      </footer>
    </article>
  )
}

export default function OverviewGraphs({ datasets }) {
  // Load the dataset graph once and let each card display its own subgraph.
  const [graph, setGraph] = useState({ nodes: [], edges: [] })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const datasetKey = datasets.map((dataset) => dataset.dataset_id).join(',')

  useEffect(() => {
    let active = true

    async function loadGraph() {
      if (!datasetKey) {
        setGraph({ nodes: [], edges: [] })
        setError('')
        setLoading(false)
        return
      }

      setLoading(true)
      setError('')
      try {
        const result = await getKnowledgeGraph(5000)
        if (active) {
          setGraph({
            nodes: result.nodes,
            edges: result.edges,
          })
        }
      } catch (err) {
        if (active) {
          setError(err.message || 'Unable to load uploaded sheet graphs.')
        }
      } finally {
        if (active) setLoading(false)
      }
    }

    loadGraph()
    return () => {
      active = false
    }
  }, [datasetKey])

  if (loading) {
    return <p className="overview-graphs-status">Loading sheet graphs…</p>
  }

  if (error) {
    return (
      <p className="overview-graphs-status error" role="alert">
        {error}
      </p>
    )
  }

  if (datasets.length === 0) {
    return (
      <p className="overview-graphs-status">
        Upload a CSV or XLSX sheet to see its graph and entity nodes here.
      </p>
    )
  }

  return (
    <div className="overview-graphs-grid">
      {datasets.map((dataset) => (
        <DatasetGraph
          key={dataset.dataset_id}
          dataset={dataset}
          graph={graph}
        />
      ))}
    </div>
  )
}
