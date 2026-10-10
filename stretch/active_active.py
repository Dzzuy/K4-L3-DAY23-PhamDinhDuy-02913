"""Deterministic SQLite reconciliation for the optional active-active demo.

Concurrent writes should use globally stable doc_ids. A later ingested_at wins for
matching IDs; equal timestamps use region B as a deterministic tie breaker. This
is a lab policy, not a replacement for causal conflict resolution in production.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
from typing import TypeAlias

Document: TypeAlias = tuple[str, str, bytes, float]


def _read_docs(path: pathlib.Path) -> dict[str, Document]:
    with sqlite3.connect(path) as connection:
        return {
            row[0]: row
            for row in connection.execute(
                "SELECT doc_id, body, embedding, ingested_at FROM docs"
            )
        }


def merge_regions(region_a: pathlib.Path, region_b: pathlib.Path) -> dict[str, int]:
    """Copy the same winning document set to both region databases."""
    a_docs = _read_docs(region_a)
    b_docs = _read_docs(region_b)
    merged: dict[str, Document] = {}
    conflicts = 0
    for doc_id in a_docs.keys() | b_docs.keys():
        left, right = a_docs.get(doc_id), b_docs.get(doc_id)
        if left is not None and right is not None and left != right:
            conflicts += 1
        merged[doc_id] = max(
            ((row, rank) for rank, row in ((0, left), (1, right)) if row is not None),
            key=lambda candidate: (candidate[0][3], candidate[1]),
        )[0]
    for path in (region_a, region_b):
        with sqlite3.connect(path, timeout=10) as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO docs(doc_id, body, embedding, ingested_at) "
                "VALUES (?, ?, ?, ?)",
                merged.values(),
            )
    return {"documents": len(merged), "conflicts_resolved": conflicts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--region-a",
        type=pathlib.Path,
        default=pathlib.Path("state/region-a/vectors.sqlite"),
    )
    parser.add_argument(
        "--region-b",
        type=pathlib.Path,
        default=pathlib.Path("state/region-b/vectors.sqlite"),
    )
    args = parser.parse_args()
    print(json.dumps(merge_regions(args.region_a, args.region_b)))


if __name__ == "__main__":
    main()
