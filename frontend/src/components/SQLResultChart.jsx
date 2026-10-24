import { useState } from 'react'

function toNumericValue(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return null
}

export default function SQLResultChart({ columns, rows }) {
  const [selectedValueColumn, setSelectedValueColumn] = useState('')
  const [selectedCategoryColumn, setSelectedCategoryColumn] = useState('')

  const numericColumns = columns.filter((column) =>
    rows.some((row) => toNumericValue(row[column]) !== null),
  )

  if (numericColumns.length === 0 || rows.length === 0) return null

  const singleRowMetrics = rows.length === 1 && numericColumns.length > 1
  const valueColumn = numericColumns.includes(selectedValueColumn)
    ? selectedValueColumn
    : numericColumns[0]
  const categoryColumns = columns.filter((column) => column !== valueColumn)
  const categoryColumn = categoryColumns.includes(selectedCategoryColumn)
    ? selectedCategoryColumn
    : categoryColumns[0]

  const chartRows = singleRowMetrics
    ? numericColumns.map((column) => ({
        label: column,
        value: toNumericValue(rows[0][column]),
      }))
    : rows
        .map((row, index) => ({
          label: categoryColumn
            ? String(row[categoryColumn] ?? `Row ${index + 1}`)
            : `Row ${index + 1}`,
          value: toNumericValue(row[valueColumn]),
        }))
        .filter((row) => row.value !== null)
        .slice(0, 20)

  if (chartRows.length === 0) return null

  const minimum = Math.min(0, ...chartRows.map((row) => row.value))
  const maximum = Math.max(0, ...chartRows.map((row) => row.value))
  const range = maximum - minimum || 1
  const plotStart = 250
  const plotWidth = 540
  const zeroX = plotStart + ((0 - minimum) / range) * plotWidth
  const rowHeight = 34
  const chartHeight = 52 + chartRows.length * rowHeight

  return (
    <section className="sql-chart-section" aria-label="Query result chart">
      <div className="sql-section-heading">
        <h3>Visualized results</h3>
        <span>Bar chart</span>
      </div>

      {!singleRowMetrics && (
        <div className="sql-chart-controls">
          {numericColumns.length > 1 && (
            <label>
              Value
              <select
                value={valueColumn}
                onChange={(event) =>
                  setSelectedValueColumn(event.target.value)
                }
              >
                {numericColumns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
          )}
          {categoryColumns.length > 0 && (
            <label>
              Category
              <select
                value={categoryColumn}
                onChange={(event) =>
                  setSelectedCategoryColumn(event.target.value)
                }
              >
                {categoryColumns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
      )}

      <div className="sql-chart-scroll">
        <svg
          className="sql-result-chart"
          viewBox={`0 0 860 ${chartHeight}`}
          role="img"
          aria-label={`Bar chart of ${singleRowMetrics ? 'numeric result columns' : valueColumn} by ${singleRowMetrics ? 'column' : categoryColumn || 'row'}`}
        >
          <line
            x1={zeroX}
            x2={zeroX}
            y1="30"
            y2={chartHeight - 12}
            className="sql-chart-axis"
          />
          {chartRows.map((row, index) => {
            const y = 38 + index * rowHeight
            const valueX = plotStart + ((row.value - minimum) / range) * plotWidth
            const barX = Math.min(zeroX, valueX)
            const barWidth = Math.max(1, Math.abs(valueX - zeroX))

            return (
              <g key={`${row.label}-${index}`}>
                <title>{`${row.label}: ${row.value}`}</title>
                <text
                  x="8"
                  y={y + 5}
                  className="sql-chart-label"
                >
                  {row.label.length > 32
                    ? `${row.label.slice(0, 29)}…`
                    : row.label}
                </text>
                <rect
                  x={barX}
                  y={y - 8}
                  width={barWidth}
                  height="16"
                  rx="3"
                  className="sql-chart-bar"
                />
                <text
                  x={valueX + (row.value < 0 ? -7 : 7)}
                  y={y + 5}
                  textAnchor={row.value < 0 ? 'end' : 'start'}
                  className="sql-chart-value"
                >
                  {row.value.toLocaleString()}
                </text>
              </g>
            )
          })}
        </svg>
      </div>
      {!singleRowMetrics && rows.length > chartRows.length && (
        <p className="sql-chart-note">
          Showing the first {chartRows.length} rows from the query result.
        </p>
      )}
    </section>
  )
}
