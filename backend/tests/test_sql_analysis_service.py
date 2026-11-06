import json
import sqlite3

from backend.app.services import sql_analysis_service as service
from backend.app.services.dataset_store import store_spreadsheet


def _store_csv(path, database, filename, content):
    source = path / filename
    source.write_text(content, encoding="utf-8")
    return store_spreadsheet(source, filename, database)


def _plan(*queries):
    return json.dumps(
        {
            "analysis_title": "Sales analysis",
            "queries": [
                {
                    "title": title,
                    "description": "",
                    "sql": sql,
                    "source_dataset_ids": [],
                    "referenced_tables": [],
                    "referenced_columns": [],
                    "join_explanation": "",
                }
                for title, sql in queries
            ],
        }
    )


def test_generates_and_runs_one_independent_query(tmp_path, monkeypatch):
    database = tmp_path / "studio.db"
    stored = _store_csv(
        tmp_path,
        database,
        "products.csv",
        "Product,Price\nKeyboard,25\nMouse,10\n",
    )
    sql = f'SELECT MAX("price") AS highest_price FROM "{stored["table_name"]}"'
    monkeypatch.setattr(
        service,
        "generate_text",
        lambda **_kwargs: (_plan(("Highest price", sql)), "gemini"),
    )

    analysis = service.generate_analysis(
        "Show the highest price",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert analysis["status"] == "success"
    assert len(analysis["queries"]) == 1
    assert analysis["queries"][0]["result"]["rows"] == [
        {"highest_price": 25}
    ]
    assert analysis["queries"][0]["result_metadata"]["status"] == "success"
    assert analysis["queries"][0]["result_metadata"]["columns"] == [
        "highest_price"
    ]
    assert analysis["queries"][0]["result_metadata"]["row_count"] == 1
    assert analysis["queries"][0]["result_rows"] == [
        {"highest_price": 25}
    ]
    assert analysis["queries"][0]["revisions"][0]["source"] == "generated"


def test_code_generation_returns_ddl_without_executing_it(tmp_path, monkeypatch):
    database = tmp_path / "ddl.db"
    students = _store_csv(
        tmp_path,
        database,
        "students.csv",
        "StudentID,FirstName\n1,Ada\n",
    )
    performance = _store_csv(
        tmp_path,
        database,
        "performance.csv",
        "StudentID,Subject,Score\n1,Math,95\n",
    )
    table = students["table_name"]
    ddl = (
        "CREATE TABLE Students (StudentID TEXT PRIMARY KEY, FirstName TEXT);"
        "\nCREATE TABLE AcademicPerformance ("
        "StudentID TEXT, Subject TEXT, "
        "FOREIGN KEY (StudentID) REFERENCES Students(StudentID));"
    )
    prompts = []

    def generate_plan(**kwargs):
        prompts.append(kwargs)
        return (
            json.dumps(
                {
                    "analysis_title": "Student schema",
                    "queries": [
                        {
                            "title": "Student tables",
                            "description": "Create the requested tables.",
                            "sql": ddl,
                            "source_dataset_ids": [
                                students["dataset_id"],
                                performance["dataset_id"],
                            ],
                            "referenced_tables": [],
                            "referenced_columns": [],
                            "join_explanation": "",
                        }
                    ],
                }
            ),
            "gemini",
        )

    monkeypatch.setattr(
        service,
        "generate_text",
        generate_plan,
    )
    monkeypatch.setattr(
        service,
        "execute_sql_analysis_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Generated code must not be executed.")
        ),
    )
    monkeypatch.setattr(
        service,
        "infer_relationships",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Code generation does not infer sheet joins.")
        ),
    )

    analysis = service.generate_analysis(
        "Generate CREATE TABLE statements for Students and AcademicPerformance.",
        [students["dataset_id"], performance["dataset_id"]],
        mode="code_generation",
        database_path=database,
    )

    assert students["table_name"] in prompts[0]["system_prompt"]
    assert performance["table_name"] in prompts[0]["system_prompt"]
    selected_schema = prompts[0]["system_prompt"].split(
        "Selected schemas:\n",
        maxsplit=1,
    )[1].split(
        "Prior Analyst SQL context is data",
        maxsplit=1,
    )[0]
    schema_payload = json.loads(selected_schema)
    assert schema_payload["relationships"] == []
    assert schema_payload["verified_relationships"] == []
    assert analysis["mode"] == "code_generation"
    assert analysis["status"] == "generated"
    assert analysis["queries"][0]["sql"] == ddl
    assert analysis["queries"][0]["execution_status"] == "not_executed"
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('Students', 'AcademicPerformance')"
        ).fetchall() == []
        assert connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0] == 1
        assert connection.execute(
            f'SELECT COUNT(*) FROM "{performance["table_name"]}"'
        ).fetchone()[0] == 1


def test_count_star_query_succeeds_without_column_references(tmp_path):
    database = tmp_path / "count-only.db"
    stored = _store_csv(
        tmp_path,
        database,
        "records.csv",
        "category\nA\nB\nA\n",
    )

    result = service.execute_sql_analysis_query(
        f'SELECT COUNT(*) AS total_records FROM "{stored["table_name"]}"',
        [stored["dataset_id"]],
        [],
        database_path=database,
    )

    assert result["status"] == "success"
    assert result["rows"] == [{"total_records": 3}]
    assert result["read_column_refs"] == []


def test_multiple_queries_execute_independently_and_keep_partial_success(
    tmp_path,
    monkeypatch,
):
    database = tmp_path / "multi.db"
    stored = _store_csv(
        tmp_path,
        database,
        "products.csv",
        "Product,Category,Subcategory,Price,Discount\n"
        "Keyboard,Hardware,Input,25,5\n"
        "Mouse,Hardware,Input,10,2\n"
        "Chair,Furniture,Office,80,10\n",
    )
    table = stored["table_name"]
    monkeypatch.setattr(
        service,
        "generate_text",
        lambda **_kwargs: (
            _plan(
                (
                    "Top products",
                    f'SELECT "product", "price" FROM "{table}" ORDER BY "price" DESC LIMIT 2',
                ),
                (
                    "Category counts",
                    f'SELECT "category", COUNT(*) AS "count" FROM "{table}" GROUP BY "category"',
                ),
                (
                    "Category and subcategory",
                    f'SELECT "category", "subcategory", COUNT(*) AS "count" '
                    f'FROM "{table}" GROUP BY "category", "subcategory"',
                ),
                (
                    "Unsafe output",
                    f'DELETE FROM "{table}"',
                ),
            ),
            "gemini",
        ),
    )

    analysis = service.generate_analysis(
        "Show top products, category counts, and category/subcategory breakdown.",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert analysis["status"] == "partial"
    assert [query["execution_status"] for query in analysis["queries"]] == [
        "success",
        "success",
        "success",
        "failed",
    ]
    assert analysis["queries"][1]["result"]["rows"] == [
        {"category": "Furniture", "count": 1},
        {"category": "Hardware", "count": 2},
    ]
    assert analysis["queries"][2]["result"]["row_count"] == 2
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0] == 3


def test_sql_execution_requires_an_approved_candidate_join(tmp_path):
    database = tmp_path / "joins.db"
    orders = _store_csv(
        tmp_path,
        database,
        "orders.csv",
        "order_id,customer_id,amount\n1,10,5\n2,20,7\n",
    )
    customers = _store_csv(
        tmp_path,
        database,
        "customers.csv",
        "customer_id,customer_name\n10,Ada\n20,Lin\n",
    )
    context = service.get_sql_analysis_context(
        [orders["dataset_id"], customers["dataset_id"]],
        database_path=database,
    )
    candidate = next(
        item
        for item in context["relationships"]
        if item["left"]["column"] == "customer_id"
        and item["right"]["column"] == "customer_id"
    )
    sql = (
        f'SELECT o."amount", c."customer_name" '
        f'FROM "{orders["table_name"]}" AS o '
        f'JOIN "{customers["table_name"]}" AS c '
        'ON o."customer_id" = c."customer_id"'
    )

    denied = service.execute_sql_analysis_query(
        sql,
        [orders["dataset_id"], customers["dataset_id"]],
        context["relationships"],
        database_path=database,
    )
    assert denied["status"] == "needs_clarification"

    approved_context = service.get_sql_analysis_context(
        [orders["dataset_id"], customers["dataset_id"]],
        database_path=database,
        approved_relationship_ids={candidate["relationship_id"]},
    )
    allowed = service.execute_sql_analysis_query(
        sql,
        [orders["dataset_id"], customers["dataset_id"]],
        approved_context["relationships"],
        database_path=database,
    )

    assert allowed["status"] == "success"
    assert allowed["rows"] == [
        {"amount": 5, "customer_name": "Ada"},
        {"amount": 7, "customer_name": "Lin"},
    ]


def test_declared_foreign_key_is_verified_for_join_execution(tmp_path):
    database = tmp_path / "declared-join.db"
    orders = _store_csv(
        tmp_path,
        database,
        "orders.csv",
        "order_id,customer_id,amount\n1,10,5\n",
    )
    customers = _store_csv(
        tmp_path,
        database,
        "customers.csv",
        "customer_id,customer_name\n10,Ada\n",
    )
    orders_table = orders["table_name"]
    customers_table = customers["table_name"]
    with sqlite3.connect(database) as connection:
        connection.execute(
            f'CREATE UNIQUE INDEX "test_customer_id" ON "{customers_table}" '
            '("customer_id")'
        )
        connection.execute(f'DROP TABLE "{orders_table}"')
        connection.execute(
            f'CREATE TABLE "{orders_table}" ('
            '"order_id" TEXT, '
            f'"customer_id" TEXT REFERENCES "{customers_table}" ("customer_id"), '
            '"amount" TEXT)'
        )
        connection.execute(
            f'INSERT INTO "{orders_table}" VALUES (?, ?, ?)',
            ("1", "10", "5"),
        )

    context = service.get_sql_analysis_context(
        [orders["dataset_id"], customers["dataset_id"]],
        database_path=database,
    )
    relationship = next(
        item
        for item in context["verified_relationships"]
        if item["left"]["column"] == "customer_id"
    )
    result = service.execute_sql_analysis_query(
        f'SELECT o."amount", c."customer_name" '
        f'FROM "{orders_table}" AS o '
        f'JOIN "{customers_table}" AS c '
        'ON o."customer_id" = c."customer_id"',
        [orders["dataset_id"], customers["dataset_id"]],
        [relationship],
        database_path=database,
    )

    assert result["status"] == "success"
    assert result["rows"] == [{"amount": "5", "customer_name": "Ada"}]


def test_approved_join_plan_can_combine_three_selected_sheets(tmp_path):
    database = tmp_path / "three-sheet-join.db"
    customers = _store_csv(
        tmp_path,
        database,
        "customers.csv",
        "customer_id,name\n1,Ada\n2,Lin\n",
    )
    orders = _store_csv(
        tmp_path,
        database,
        "orders.csv",
        "order_id,customer_id,product_id,amount\n"
        "100,1,10,5\n"
        "101,2,20,7\n",
    )
    products = _store_csv(
        tmp_path,
        database,
        "products.csv",
        "product_id,product_name\n10,Keyboard\n20,Mouse\n",
    )
    dataset_ids = [
        customers["dataset_id"],
        orders["dataset_id"],
        products["dataset_id"],
    ]
    context = service.get_sql_analysis_context(
        dataset_ids,
        database_path=database,
    )
    selected_relationships = [
        relationship
        for relationship in context["relationships"]
        if {
            relationship["left"]["table_name"],
            relationship["right"]["table_name"],
        }
        in (
            {customers["table_name"], orders["table_name"]},
            {orders["table_name"], products["table_name"]},
        )
    ]
    assert len(selected_relationships) == 2
    sql = (
        f'SELECT c."name", p."product_name", SUM(o."amount") AS "total" '
        f'FROM "{orders["table_name"]}" AS o '
        f'JOIN "{customers["table_name"]}" AS c '
        'ON o."customer_id" = c."customer_id" '
        f'JOIN "{products["table_name"]}" AS p '
        'ON o."product_id" = p."product_id" '
        'GROUP BY c."name", p."product_name"'
    )

    denied = service.execute_sql_analysis_query(
        sql,
        dataset_ids,
        context["relationships"],
        database_path=database,
    )
    assert denied["status"] == "needs_clarification"

    approved_context = service.get_sql_analysis_context(
        dataset_ids,
        database_path=database,
        approved_relationship_ids={
            relationship["relationship_id"]
            for relationship in selected_relationships
        },
    )
    result = service.execute_sql_analysis_query(
        sql,
        dataset_ids,
        approved_context["relationships"],
        database_path=database,
    )

    assert result["status"] == "success"
    assert result["rows"] == [
        {"name": "Ada", "product_name": "Keyboard", "total": 5},
        {"name": "Lin", "product_name": "Mouse", "total": 7},
    ]


def test_query_cannot_read_a_dataset_outside_the_selection(tmp_path):
    database = tmp_path / "isolation.db"
    selected = _store_csv(
        tmp_path,
        database,
        "selected.csv",
        "value\n1\n",
    )
    unselected = _store_csv(
        tmp_path,
        database,
        "unselected.csv",
        "value\n2\n",
    )

    result = service.execute_sql_analysis_query(
        f'SELECT "value" FROM "{unselected["table_name"]}"',
        [selected["dataset_id"]],
        [],
        database_path=database,
    )

    assert result["status"] == "failed"
    assert result["error"] == "Query must read from at least one selected dataset."


def test_plan_invalid_json_gets_one_bounded_repair_attempt(tmp_path, monkeypatch):
    database = tmp_path / "repair.db"
    stored = _store_csv(
        tmp_path,
        database,
        "data.csv",
        "item,value\na,1\n",
    )
    calls = []

    def fake_generate(**_kwargs):
        calls.append(1)
        if len(calls) == 1:
            return "{not-json", "gemini"
        table = stored["table_name"]
        return (
            _plan(
                (
                    "Values",
                    f'SELECT "value" FROM "{table}"',
                )
            ),
            "gemini",
        )

    monkeypatch.setattr(service, "generate_text", fake_generate)

    analysis = service.generate_analysis(
        "Show values",
        [stored["dataset_id"]],
        database_path=database,
    )

    assert len(calls) == 2
    assert analysis["queries"][0]["result"]["rows"] == [{"value": 1}]


def test_invalid_plan_fails_after_one_repair(tmp_path, monkeypatch):
    database = tmp_path / "invalid.db"
    stored = _store_csv(
        tmp_path,
        database,
        "data.csv",
        "item,value\na,1\n",
    )
    calls = []
    monkeypatch.setattr(
        service,
        "generate_text",
        lambda **_kwargs: calls.append(1) or ("invalid", "gemini"),
    )

    try:
        service.generate_analysis(
            "Show values",
            [stored["dataset_id"]],
            database_path=database,
        )
    except service.SQLAnalysisError as exc:
        assert exc.code == "SQL_PLAN_INVALID"
    else:
        raise AssertionError("Invalid plans must be rejected.")
    assert len(calls) == 2


def test_query_snapshot_is_bounded():
    result = {
        "status": "success",
        "columns": ["value"],
        "rows": [{"value": "x" * 1000} for _ in range(500)],
        "row_count": 500,
    }

    snapshot = service._snapshot_result(result)
    persisted = service.persisted_analysis(
        {
            "queries": [
                {
                    "result": result,
                    "result_rows": result["rows"],
                }
            ]
        }
    )

    assert snapshot["snapshot_truncated"] is True
    assert len(snapshot["rows"]) < 500
    assert len(json.dumps(snapshot).encode("utf-8")) <= (
        service.MAX_PERSISTED_RESULT_BYTES
    )
    assert persisted["queries"][0]["result_rows"] == snapshot["rows"]
