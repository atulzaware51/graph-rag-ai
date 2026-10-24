# Architecture — GraphSheet AI

## 1. Stack
- Backend: Python, FastAPI
- Database: Existing SQLite storage
- Frontend: Existing React/Vite application
- AI: Existing configured LLM provider
- Data: CSV and XLSX files
- SQL: Existing safe, read-only SQL service

Inspect the repository before changing any technology.

## 2. Main Backend Layers

### Data Ingestion
Reads uploaded files, validates formats, profiles schemas, and stores dataset metadata.

### Knowledge Graph
Builds dataset and column nodes, detects possible relationships, and exposes graph APIs.

### AI Analyst / RAG
Understands user questions and retrieves relevant dataset schemas, graph context, and conversation context.

### Text-to-SQL
Converts a resolved user intent into SQL using real table and column names.

### SQL Execution
Validates and executes permitted read-only queries and returns results.

### Conversation Service
Stores messages, clarifications, selected options, generated SQL, execution status, and result references.

### Conversation Graph Service
Builds a separate graph from persisted conversation events.

## 3. Data Flow

Upload → Profile → Store Metadata → Build Data Graph

Question → Resolve Intent → Retrieve Context → Clarify if Needed → Generate SQL → Validate → Execute → Return Results → Save History → Update Conversation Graph

## 4. Graph Models

Data Knowledge Graph:
- Dataset
- Column
- Potential relationship

Conversation Intelligence Graph:
- Conversation
- Question
- Clarification
- UserChoice
- SQLQuery
- Execution
- ResultTable
- Dataset reference
- Column reference

Example edges:
- HAS_COLUMN
- FOLLOWED_BY
- CLARIFIED_BY
- SELECTED_OPTION
- GENERATED_QUERY
- EXECUTED_AS
- PRODUCED_RESULT
- REFERENCES_DATASET
- USES_COLUMN

## 5. Persistence
Use existing storage and models where possible. Add only necessary tables, fields, indexes, or migrations.

Store result references and metadata in conversation events. Keep large result tables outside the graph.

## 6. API Design
Preserve existing endpoints and response fields. Add separate conversation history and conversation graph endpoints when needed.

The frontend must request the Data Knowledge Graph and Conversation Intelligence Graph independently.

## 7. Key Principle
The graph records relationships and history. The AI workflow resolves intent. The SQL service independently enforces execution safety.