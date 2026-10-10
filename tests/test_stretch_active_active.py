"""Boundaries for the optional active-active routing and merge policy."""

import importlib
import sqlite3

from stretch.active_active import merge_regions


def _db(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE docs(doc_id TEXT PRIMARY KEY, body TEXT, embedding BLOB, ingested_at REAL)"
        )
        connection.executemany("INSERT INTO docs VALUES (?, ?, ?, ?)", rows)
    return path


def test_merge_preserves_unique_docs_and_resolves_concurrent_updates(tmp_path):
    a = _db(
        tmp_path / "a.sqlite",
        [
            ("shared", "old", b"a", 10.0),
            ("a-only", "A", b"a", 11.0),
            ("tie", "A", b"a", 12.0),
        ],
    )
    b = _db(
        tmp_path / "b.sqlite",
        [
            ("shared", "new", b"b", 13.0),
            ("b-only", "B", b"b", 11.0),
            ("tie", "B", b"b", 12.0),
        ],
    )
    result = merge_regions(a, b)
    assert result == {"documents": 4, "conflicts_resolved": 2}
    with sqlite3.connect(a) as left, sqlite3.connect(b) as right:
        left_rows = left.execute(
            "SELECT doc_id, body FROM docs ORDER BY doc_id"
        ).fetchall()
        right_rows = right.execute(
            "SELECT doc_id, body FROM docs ORDER BY doc_id"
        ).fetchall()
    assert (
        left_rows
        == right_rows
        == [("a-only", "A"), ("b-only", "B"), ("shared", "new"), ("tie", "B")]
    )
    assert merge_regions(a, b)["conflicts_resolved"] == 0


def test_active_active_split_is_balanced(monkeypatch):
    monkeypatch.setenv("EDGE_ROUTING_MODE", "active-active")
    from edge import proxy

    module = importlib.reload(proxy)
    try:
        assert [module.resolve() for _ in range(10)] == ["a", "b"] * 5
        assert module.edge_state()["requests_by_region"] == {"a": 5, "b": 5}
    finally:
        monkeypatch.setenv("EDGE_ROUTING_MODE", "active-passive")
        importlib.reload(proxy)
