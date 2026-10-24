# Design — GraphSheet AI

## 1. UI Goal
Build a clean, professional spreadsheet-analysis application that is easy to use and understand.

## 2. Main Areas
- Dashboard
- Dataset Upload and Dataset Details
- Data Knowledge Graph
- AI Analyst Chat
- SQL and Result Table
- Conversation History
- Conversation Intelligence Graph

Use the existing frontend structure and navigation where possible.

## 3. Data Knowledge Graph
Display:
- Dataset nodes
- Column nodes
- HAS_COLUMN edges
- Potential cross-dataset relationships

Allow users to inspect node details and relationship evidence.

## 4. Conversation Intelligence Graph
Display the sequence of:
Question → Clarification → User Choice → SQL Query → Execution → Result

Also show connections to the datasets and columns referenced by each query.

Provide useful filters such as conversation, dataset, and node type when practical.

## 5. AI Analyst
Each response should clearly distinguish:
- Answer or explanation
- Clarification required
- Generated SQL
- Execution status
- Result table
- Error or warning

When clarification is needed, show clear options and allow the user to answer in chat.

## 6. Result Table
Show column headers, rows, and empty-result messages. Keep SQL visible and allow users to inspect it.

Do not display a successful result when SQL execution fails.

## 7. Visual Style
- Professional, simple, and readable.
- Neutral backgrounds with restrained accent colors.
- Clear spacing and consistent typography.
- Avoid neon effects, glow, excessive gradients, and clutter.
- Support useful loading, error, and empty states.
- Keep graph labels readable and avoid overlapping nodes where practical.

## 8. Design Rule
Reuse existing components and preserve working screens. Do not redesign unrelated features during this phase.