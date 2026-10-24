import assert from 'node:assert/strict'
import test from 'node:test'
import { buildChatGraph } from '../src/components/chatGraph.js'

const sharedFingerprint = 'same-sheet-content'

test('groups duplicate sheet uploads and preserves each upload time', () => {
  const graph = buildChatGraph({
    id: 'chat-1',
    title: 'Monthly sales review',
    uploads: [
      {
        upload_id: 'upload-1',
        dataset_id: 'dataset-1',
        filename: 'sales.csv',
        content_fingerprint: sharedFingerprint,
        uploaded_at: '2026-10-25T01:00:00.000Z',
        columns: [{ original_name: 'Customer ID', sql_name: 'customer_id' }],
      },
      {
        upload_id: 'upload-2',
        dataset_id: 'dataset-2',
        filename: 'sales.xlsx',
        content_fingerprint: sharedFingerprint,
        uploaded_at: '2026-10-25T02:00:00.000Z',
        columns: [{ original_name: 'Customer ID', sql_name: 'customer_id' }],
      },
    ],
  })

  assert.equal(graph.nodes.filter((node) => node.type === 'Dataset').length, 1)
  assert.equal(graph.nodes.filter((node) => node.type === 'Entity').length, 1)
  assert.equal(graph.nodes.filter((node) => node.type === 'Upload').length, 2)
  assert.equal(graph.nodes[0].label, 'Monthly sales review')
  assert.ok(
    graph.edges.every(
      (edge) =>
        graph.nodes.some((node) => node.id === edge.source) &&
        graph.nodes.some((node) => node.id === edge.target),
    ),
  )
})

test('links query lineage, SQL text, and result data to the chat sheet graph', () => {
  const sql = 'SELECT customer_id FROM dataset_customers'
  const rows = [{ customer_id: 1001 }]
  const graph = buildChatGraph({
    id: 'chat-2',
    title: 'Customer lookup',
    uploads: [
      {
        upload_id: 'upload-1',
        dataset_id: 'dataset-1',
        filename: 'customers.csv',
        content_fingerprint: sharedFingerprint,
        uploaded_at: '2026-10-25T01:00:00.000Z',
        columns: [
          { original_name: 'Customer ID', sql_name: 'customer_id' },
          { original_name: 'Name', sql_name: 'name' },
        ],
      },
    ],
    graphQueries: [
      {
        query_id: 'query-1',
        dataset_id: 'dataset-1',
        content_fingerprint: sharedFingerprint,
        question: 'Show the customer ID',
        sql,
        read_columns: ['customer_id'],
        result_columns: ['customer_id'],
        rows,
        row_count: 1,
      },
    ],
  })

  const query = graph.nodes.find((node) => node.type === 'Query')
  const result = graph.nodes.find((node) => node.type === 'Result')
  const usedEntity = graph.edges.find((edge) => edge.type === 'USES_ENTITY')
  const resultEdge = graph.edges.find((edge) => edge.type === 'QUERY_RESULT')

  assert.equal(query.sql, sql)
  assert.equal(result.rows, rows)
  assert.equal(usedEntity.target, 'entity:same-sheet-content:customer_id')
  assert.equal(resultEdge.query, sql)
  assert.equal(resultEdge.source, query.id)
  assert.equal(resultEdge.target, result.id)
})

test('keeps different sheets separate and scopes each chat graph by chat ID', () => {
  const chat = {
    id: 'chat-a',
    title: 'Separate sheets',
    uploads: [
      {
        upload_id: 'upload-a',
        dataset_id: 'dataset-a',
        filename: 'customers.csv',
        content_fingerprint: 'customer-sheet',
        uploaded_at: '2026-10-25T01:00:00.000Z',
        columns: [{ original_name: 'ID', sql_name: 'id' }],
      },
      {
        upload_id: 'upload-b',
        dataset_id: 'dataset-b',
        filename: 'orders.csv',
        content_fingerprint: 'orders-sheet',
        uploaded_at: '2026-10-25T02:00:00.000Z',
        columns: [{ original_name: 'Order ID', sql_name: 'order_id' }],
      },
    ],
  }
  const firstGraph = buildChatGraph(chat)
  const secondGraph = buildChatGraph({ ...chat, id: 'chat-b' })

  assert.equal(
    firstGraph.nodes.filter((node) => node.type === 'Dataset').length,
    2,
  )
  assert.notEqual(firstGraph.nodes[0].id, secondGraph.nodes[0].id)
  assert.equal(
    firstGraph.edges.filter((edge) => edge.type === 'HAS_SHEET').length,
    2,
  )
})
