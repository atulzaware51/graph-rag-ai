// Present generated SQL, execution results, and any join approvals requested by Analyst.
import { useState } from 'react'
import HighlightedSQL from './HighlightedSQL'
import './SQLAnalysisResults.css'

const SOURCE_COLUMNS = new Set([
  'dataset',
  'dataset_id',
  'sheet',
  'sheet_name',
  'source',
  'table',
  'table_name',
])

function formatCell(value, column, sourceLabels) {
  // Replace internal dataset/table IDs with the filenames users recognize.
  if (value === null || value === undefined) return '—'
  if (
    typeof value === 'string'
    && SOURCE_COLUMNS.has(String(column).toLowerCase())
    && sourceLabels.has(value)
  ) {
    return sourceLabels.get(value)
  }
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function SQLAnalysisResults({ analysis, onApproveRelationship }) {
  // Normalize SQL-analysis data once so multiple query results render consistently.
  const [copyError, setCopyError] = useState('')

  if (!analysis) return null

  const relationships = analysis.mode === 'code_generation'
    ? []
    : analysis.relationships || []
  const datasets = Array.isArray(analysis.datasets) ? analysis.datasets : []
  const sourceLabels = new Map()
  for (const dataset of datasets) {
    if (!dataset || typeof dataset !== 'object') continue
    const filename = dataset.filename
    if (typeof filename !== 'string' || !filename) continue
    const label = dataset.sheet_name
      ? `${filename} — ${dataset.sheet_name}`
      : filename
    for (const identifier of [dataset.dataset_id, dataset.table_name]) {
      if (typeof identifier === 'string') sourceLabels.set(identifier, label)
    }
  }
  const hasUnapprovedRelationship = relationships.some(
    (relationship) => relationship.status === 'heuristic_candidate',
  )

  async function copySQL(sql) {
    // Offer a clear fallback when the browser cannot write to the clipboard.
    setCopyError('')
    if (!navigator.clipboard?.writeText) {
      setCopyError('Clipboard access is unavailable in this browser.')
      return
    }
    try {
      await navigator.clipboard.writeText(sql)
    } catch {
      setCopyError('Could not copy SQL. Please select and copy it manually.')
    }
  }

  return (
    <section className="analyst-sql-analysis" aria-label="SQL analysis results">
      {datasets.length > 0 && (
        <p className="analyst-sql-sources">
          Source sheets:{' '}
          {datasets.map((dataset, index) => (
            <span key={dataset.dataset_id || `${dataset.filename}-${index}`}>
              {dataset.filename || 'Selected sheet'}
              {dataset.sheet_name ? ` — ${dataset.sheet_name}` : ''}
              {index < datasets.length - 1 ? ', ' : ''}
            </span>
          ))}
        </p>
      )}
      {analysis.mode === 'code_generation' && (
        <p className="analyst-sql-code-note" role="status">
          Generated SQL code only. It was not executed against your uploaded sheets.
        </p>
      )}
      {analysis.clarification && (
        <p className="analyst-sql-clarification" role="status">
          {analysis.clarification}
        </p>
      )}

      {analysis.queries?.map((query, index) => (
        <article className="analyst-sql-query" key={query.query_id || index}>
          <header>
            <div>
              <span className="analyst-sql-query-number">
                {analysis.mode === 'code_generation' ? 'Code' : 'Result'} {index + 1}
              </span>
              <h3>{query.title || `Query ${index + 1}`}</h3>
              {query.description && <p>{query.description}</p>}
            </div>
            <span className={`analyst-sql-status ${query.execution_status || 'failed'}`}>
              {(query.execution_status || 'failed').replaceAll('_', ' ')}
            </span>
          </header>

          {query.error && (
            <p className="analyst-sql-error" role="alert">{query.error}</p>
          )}

          {query.sql && (
            <details
              className="analyst-sql-code"
              open={analysis.mode === 'code_generation'}
            >
              <summary>
                {analysis.mode === 'code_generation' ? 'View generated SQL' : 'View SQL'}
              </summary>
              <div className="analyst-sql-copy-row">
                <button type="button" onClick={() => copySQL(query.sql)}>
                  Copy SQL
                </button>
              </div>
              <HighlightedSQL code={query.sql} />
            </details>
          )}

          {query.execution_status === 'success' && (
            query.result?.columns?.length ? (
              <>
                <p className="analyst-sql-row-count">
                  {Number(query.result.row_count || 0).toLocaleString()} rows
                </p>
                {query.result.rows?.length ? (
                  <div className="analyst-sql-table-wrap">
                    <table>
                      <thead>
                        <tr>
                          {query.result.columns.map((column, columnIndex) => (
                            <th key={`${column}-${columnIndex}`}>{column}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {query.result.rows.map((row, rowIndex) => (
                          <tr key={`${query.query_id || index}-${rowIndex}`}>
                            {query.result.columns.map((column, columnIndex) => (
                              <td key={`${column}-${columnIndex}`}>
                                {formatCell(row[column], column, sourceLabels)}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="analyst-sql-empty">The query returned no matching rows.</p>
                )}
              </>
            ) : (
              <p className="analyst-sql-empty">The query returned no tabular columns.</p>
            )
          )}
        </article>
      ))}

      {hasUnapprovedRelationship && (
        <section className="analyst-sql-relationships" aria-label="Possible sheet joins">
          <h3>Possible sheet relationships</h3>
          <p>Approve a safe candidate before asking Analyst to join those sheets.</p>
          {relationships.map((relationship) => (
            <div className="analyst-sql-relationship" key={relationship.relationship_id}>
              <span>
                {relationship.left.filename}.{relationship.left.column}
                {' ↔ '}
                {relationship.right.filename}.{relationship.right.column}
                <small>
                  {relationship.relationship_type.replaceAll('_', ' ')}
                  {relationship.status === 'verified_declared'
                    ? ' · declared foreign key'
                    : ` · ${Math.round((relationship.confidence || 0) * 100)}% heuristic confidence`}
                </small>
              </span>
              {relationship.status === 'verified_declared' ? (
                <strong>Verified</strong>
              ) : relationship.status === 'user_approved' ? (
                <strong>Approved</strong>
              ) : relationship.join_safe ? (
                <button
                  type="button"
                  onClick={() => onApproveRelationship?.(relationship)}
                >
                  Approve and retry
                </button>
              ) : (
                <strong>Unsafe many-to-many</strong>
              )}
            </div>
          ))}
        </section>
      )}
      {copyError && <p className="analyst-sql-error" role="alert">{copyError}</p>}
    </section>
  )
}

export default SQLAnalysisResults
