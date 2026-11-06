# GraphSheet AI Mentor Demo Checklist

## Before the session

- [ ] Keep the application local; do not bind the backend to a public or
      untrusted network.
- [ ] Configure the local Neo4j Aura connection and verify connectivity without
      showing credentials on screen.
- [ ] Configure the local Gemini key/model and make a real, non-sensitive
      prompt to verify the provider.
- [ ] Run the backend suite and frontend lint/build:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest backend\tests -q
  npm --prefix frontend run lint
  npm --prefix frontend run build
  ```

- [ ] Start the backend from the repository root:

  ```powershell
  .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
  ```

- [ ] Start the React/Vite frontend in a second terminal:

  ```powershell
  npm --prefix frontend run dev
  ```

- [ ] Verify `http://127.0.0.1:8001/health` reports healthy and open the
      intended UI at `http://localhost:5173/`.
- [ ] Note that `http://127.0.0.1:8001/app/` is the separate framework-free
      static interface; confirm which UI will be shown.

## Sample files

Create these files in a temporary demo folder. Do not use a real customer
spreadsheet for the demo.

`customers.csv`

```csv
customer_id,customer_name,city
1,Rahul,Pune
2,Priya,Mumbai
3,Amit,Nashik
```

`orders.csv`

```csv
order_id,customer_id,amount
101,1,1000
102,1,1500
103,2,2000
104,3,500
```

## Mentor demonstration sequence

1. Show the empty/current dataset list, then upload both CSV files.
2. Open each dataset and show its row count, columns, and bounded preview.
3. Open the graph view. Show `Dataset`, `Column`, and evidence nodes and the
   `customer_id` cross-sheet **candidate**. Explain that inference is
   heuristic, not foreign-key verification.
4. Create a new chat and select both datasets.
5. Ask: **"Which customers account for total order amount across these
   sheets?"**
6. Show that evidence is sourced from both selected sheets and explain how
   the candidate graph relationship guides retrieval. Do not treat graph
   evidence as proof of row values.
7. Show the generated answer and its source references. If Gemini is not
   connected, stop and call the answer path blocked rather than showing a
   mocked answer as live.
8. In SQL Studio, ask for total sales by customer. Review the generated SQL
   before highlighting the result:

   | Customer | Expected total |
   |---|---:|
   | Amit | 500 |
   | Priya | 2,000 |
   | Rahul | 2,500 |
   | **Total** | **5,000** |

9. Show dataset/chat selection isolation and refresh/reload persistence only
   after those UI behaviors have been verified in the running browser.
10. Finish by distinguishing local test results, mocked provider tests, and
    genuinely live Neo4j/Gemini checks.

## Stop conditions

- If Neo4j is unavailable, state that live graph persistence/query is
  unverified; do not claim the Neo4j path works.
- If Gemini is unavailable, do not claim a live AI answer or live Text-to-SQL
  result.
- Do not use the production SQLite database for demo uploads.
