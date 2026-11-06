// Render the validated answer sections, result tables, citations, and follow-ups.
import { useState } from 'react'

function displayValue(value) {
  // Convert database values to safe, readable strings for tables and clipboard output.
  if (value == null || value === '') return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function AnswerTable({ table, onError }) {
  // Keep long result tables compact until the user asks to expand them.
  const [expanded, setExpanded] = useState(false)
  const columns = Array.isArray(table?.columns) ? table.columns : []
  const rows = Array.isArray(table?.rows) ? table.rows : []
  const visibleRows = expanded ? rows : rows.slice(0, 10)

  async function copyTable() {
    // Copy tab-separated values so the result can be pasted into a spreadsheet.
    const text = [
      columns.map((column) => column.label).join('\t'),
      ...rows.map((row) => (
        columns.map((column) => displayValue(row[column.key])).join('\t')
      )),
    ].join('\n')

    try {
      await navigator.clipboard.writeText(text)
    } catch {
      onError?.('Could not copy table data. Please select and copy it manually.')
    }
  }

  if (!columns.length) {
    return <p className="structured-empty">No tabular results were returned.</p>
  }

  return (
    <div className="structured-table-section">
      <div className="structured-table-actions">
        <span>{rows.length} result row{rows.length === 1 ? '' : 's'}</span>
        <button type="button" onClick={copyTable} disabled={!rows.length}>
          Copy table
        </button>
      </div>
      {rows.length ? (
        <div className="structured-table-wrap">
          <table className="structured-table">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column.key}>{column.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, rowIndex) => (
                <tr key={row.id || rowIndex}>
                  {columns.map((column) => {
                    const value = row[column.key]
                    return (
                      <td
                        key={column.key}
                        className={typeof value === 'number' ? 'numeric' : ''}
                        title={displayValue(value)}
                      >
                        {displayValue(value)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="structured-empty">The calculation returned no rows.</p>
      )}
      {rows.length > 10 && (
        <button
          type="button"
          className="structured-expand"
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? 'Show fewer rows' : `Show all ${rows.length} rows`}
        </button>
      )}
    </div>
  )
}

export default function StructuredAnswer({
  answer,
  onFollowUp,
  onError,
  disabled = false,
}) {
  // Leave ordinary text answers to the chat renderer; only render structured payloads here.
  if (!answer || typeof answer !== 'object') return null

  return (
    <section className="structured-answer" aria-label="Structured analysis results">
      {(answer.sections || []).map((section, index) => (
        <section
          className="structured-answer-section"
          key={`${section.title || 'section'}-${index}`}
        >
          <h3>{section.title || `Results ${index + 1}`}</h3>
          {section.description && <p>{section.description}</p>}
          {section.table && (
            <AnswerTable table={section.table} onError={onError} />
          )}
          {(section.subtables || []).map((subtable, subIndex) => (
            <details
              className="structured-subtable"
              key={`${subtable.title || 'subtable'}-${subIndex}`}
              open={subIndex === 0 && section.subtables.length <= 2}
            >
              <summary>{subtable.title || `Breakdown ${subIndex + 1}`}</summary>
              {subtable.description && <p>{subtable.description}</p>}
              <AnswerTable table={subtable.table} onError={onError} />
            </details>
          ))}
        </section>
      ))}

      {answer.key_findings?.length > 0 && (
        <section className="structured-answer-notes">
          <h3>Key findings</h3>
          <ul>
            {answer.key_findings.map((finding, index) => (
              <li key={`${finding}-${index}`}>{finding}</li>
            ))}
          </ul>
        </section>
      )}
      {answer.calculations?.length > 0 && (
        <details className="structured-calculations">
          <summary>Calculation details</summary>
          <ul>
            {answer.calculations.map((calculation, index) => (
              <li key={`${calculation}-${index}`}>{calculation}</li>
            ))}
          </ul>
        </details>
      )}
      {answer.evidence?.length > 0 && (
        <details className="structured-calculations">
          <summary>Evidence references</summary>
          <ul>
            {answer.evidence.map((item, index) => (
              <li key={`${item.source || 'source'}-${index}`}>
                {[item.source, item.sheet_name, item.chunk_id]
                  .filter(Boolean)
                  .join(' · ')}
                {item.source_rows?.length
                  ? ` · Rows ${item.source_rows.join(', ')}`
                  : ''}
                {item.columns?.length
                  ? ` — ${item.columns.join(', ')}`
                  : ''}
                {item.method ? ` (${item.method})` : ''}
              </li>
            ))}
          </ul>
        </details>
      )}
      {answer.follow_up_suggestions?.length > 0 && (
        <section className="structured-followups">
          <h3>Suggested follow-ups</h3>
          <div>
            {answer.follow_up_suggestions.map((suggestion) => (
              <button
                type="button"
                key={suggestion}
                disabled={disabled}
                onClick={() => onFollowUp?.(suggestion)}
              >
                {suggestion}
              </button>
            ))}
          </div>
        </section>
      )}
    </section>
  )
}
