import './HighlightedSQL.css'

const SQL_TOKEN_PATTERN =
  /--[^\r\n]*|\/\*[\s\S]*?\*\/|'(?:''|[^'])*'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[[^\]]+\]|\b(?:SELECT|WITH|RECURSIVE|FROM|WHERE|JOIN|INNER|LEFT|RIGHT|FULL|OUTER|CROSS|ON|AS|AND|OR|NOT|NULL|IS|IN|EXISTS|BETWEEN|LIKE|GLOB|ORDER|BY|GROUP|HAVING|LIMIT|OFFSET|DISTINCT|UNION|ALL|CASE|WHEN|THEN|ELSE|END|ASC|DESC|CAST|COLLATE|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TABLE|INDEX|INTO|VALUES|SET|RETURNING|PRAGMA)\b|\b(?:COUNT|SUM|AVG|MIN|MAX|LOWER|UPPER|ROUND|COALESCE|IFNULL|NULLIF|LENGTH|SUBSTR|TRIM|DATE|DATETIME|STRFTIME)\b(?=\s*\()|\b\d+(?:\.\d+)?\b|[=<>!]+|\b[A-Za-z_][A-Za-z0-9_$]*\b/gim

const SQL_KEYWORDS = new Set(
  'SELECT WITH RECURSIVE FROM WHERE JOIN INNER LEFT RIGHT FULL OUTER CROSS ON AS AND OR NOT NULL IS IN EXISTS BETWEEN LIKE GLOB ORDER BY GROUP HAVING LIMIT OFFSET DISTINCT UNION ALL CASE WHEN THEN ELSE END ASC DESC CAST COLLATE INSERT UPDATE DELETE CREATE DROP ALTER TABLE INDEX INTO VALUES SET RETURNING PRAGMA'.split(
    ' ',
  ),
)

const SQL_FUNCTIONS = new Set(
  'COUNT SUM AVG MIN MAX LOWER UPPER ROUND COALESCE IFNULL NULLIF LENGTH SUBSTR TRIM DATE DATETIME STRFTIME'.split(
    ' ',
  ),
)

function getTokenClass(token) {
  const upperToken = token.toUpperCase()

  if (token.startsWith('--') || token.startsWith('/*')) return 'sql-token-comment'
  if (token.startsWith("'")) return 'sql-token-string'
  if (
    token.startsWith('"')
    || token.startsWith('`')
    || token.startsWith('[')
  ) {
    return 'sql-token-identifier'
  }
  if (SQL_KEYWORDS.has(upperToken)) return 'sql-token-keyword'
  if (SQL_FUNCTIONS.has(upperToken)) return 'sql-token-function'
  if (/^\d/.test(token)) return 'sql-token-number'
  if (/^[=<>!]+$/.test(token)) return 'sql-token-operator'
  return 'sql-token-identifier'
}

export default function HighlightedSQL({ code }) {
  const source = String(code ?? '')
  const output = []
  let cursor = 0

  for (const match of source.matchAll(SQL_TOKEN_PATTERN)) {
    const [token] = match
    const start = match.index

    if (start > cursor) output.push(source.slice(cursor, start))
    output.push(
      <span className={getTokenClass(token)} key={`${start}-${token}`}>
        {token}
      </span>,
    )
    cursor = start + token.length
  }

  if (cursor < source.length) output.push(source.slice(cursor))

  return (
    <pre className="highlighted-sql" aria-label="SQL query">
      <code>{output}</code>
    </pre>
  )
}
