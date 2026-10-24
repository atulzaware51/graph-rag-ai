# PRD — GraphSheet AI

## 1. Project
GraphSheet AI is an AI-powered spreadsheet analysis platform. Users upload CSV/XLSX files, explore their data through a Knowledge Graph, ask questions in natural language, and receive SQL queries and result tables.

## 2. Main Features
- Upload and inspect CSV/XLSX files.
- Detect columns, data types, and dataset schemas.
- Build a Knowledge Graph of datasets and columns.
- Detect possible relationships between columns.
- Ask questions using RAG and an AI Analyst.
- Convert clear questions into SQL.
- Ask clarification questions when multiple SQL interpretations are possible.
- Execute safe, read-only SQL and display result tables.
- Save conversation history and query decisions.
- Display a separate Conversation Intelligence Graph.

## 3. Graphs
### Data Knowledge Graph
Shows datasets, columns, and potential relationships between datasets.

### Conversation Intelligence Graph
Shows questions, clarification steps, user choices, generated SQL, execution outcomes, result references, and related datasets/columns.

Both graphs must remain separate views but use shared dataset and column IDs.

## 4. Question Workflow
1. User uploads a spreadsheet.
2. System profiles its schema and updates the Data Knowledge Graph.
3. User asks a question.
4. AI determines whether it is a data question, general question, ambiguous request, or unsupported/unsafe request.
5. For clear data questions, generate SQL.
6. For ambiguous questions, ask the user to clarify.
7. Use the clarification response to resolve the intended query.
8. Validate and execute read-only SQL.
9. Display SQL, explanation, and result table.
10. Save the conversation events and update the Conversation Intelligence Graph.

## 5. Clarification Example
User: "Show the best-performing products."

AI: "Which metric should define performance?"
- Total revenue
- Quantity sold
- Average selling price

Generate SQL after the user selects an option.

## 6. General Questions
General questions must not automatically trigger SQL generation. Answer them normally when appropriate.

## 7. Success Criteria
- Existing uploads, APIs, graph views, and tests continue to work.
- SQL is generated from the actual schema.
- Ambiguous questions are clarified before execution.
- SQL execution remains read-only.
- Conversation history is persistent and correctly linked.
- Both graphs display accurate nodes and relationships.
- Errors and empty results are handled clearly.