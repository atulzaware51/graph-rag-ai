# Rules — GraphSheet AI

## 1. Before Coding
- Inspect existing code, tests, APIs, and database schema.
- Follow current project patterns.
- Make small, focused changes.
- Explain the plan before major architectural changes.

## 2. Preserve Existing Work
- Do not reset or delete the SQLite database.
- Do not remove working features or tests.
- Do not break existing API response fields.
- Do not replace libraries or frameworks without a clear reason.
- Add migrations safely when the schema changes.

## 3. SQL Safety
- Allow only the existing approved read-only SQL operations.
- Preserve SELECT/WITH restrictions and all current safety checks.
- Never execute raw, unvalidated LLM output.
- Validate table and column names against the real schema.
- Reject writes, deletes, schema changes, and other unsafe operations.
- Never weaken SQL validation to make a test pass.

## 4. AI and Clarification
- Use the actual schema and relevant graph context.
- Ask for clarification when multiple materially different interpretations exist.
- Do not ask unnecessary questions when intent is clear.
- Resume the pending conversation after clarification.
- General questions must not automatically generate SQL.
- Never invent columns, tables, query results, or relationships.

## 5. Graph Rules
- Keep both graph views separate.
- Use stable IDs and prevent duplicate nodes and edges.
- Link to existing dataset and column IDs.
- Distinguish potential relationships from verified relationships.
- Do not create false relationships just to increase edge counts.
- Do not store each result row as a graph node.

## 6. Conversation History
- Preserve chronological order and links between events.
- Store original questions, clarifications, choices, SQL, status, and result references.
- Do not silently overwrite earlier user decisions.
- Keep conversations isolated from one another.
- Do not store API keys or unnecessary sensitive information.

## 7. Testing
- Add tests for new behavior.
- Run targeted tests after each meaningful change.
- Run the full backend suite and frontend production build before completion.
- Fix the root cause of failures instead of deleting tests or weakening assertions.
- Report exact commands and results.

## 8. Coding Agent Report
At completion, report:
- Files changed
- Database changes
- APIs added or modified
- Tests and build results
- Known limitations
- Recommended next step

Do not claim success for checks that were not actually run.