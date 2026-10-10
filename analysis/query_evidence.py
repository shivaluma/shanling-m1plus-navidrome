#!/usr/bin/env python3
"""Reproduce shipped album grouping semantics with synthetic host data only."""
import hashlib
import json
import pathlib
import sqlite3

base = pathlib.Path(__file__).resolve().parent
binary = (base / "rootfs/release/bin/demo").read_bytes()
offset = 0x34BAD8
template = binary[offset:].split(b"\0", 1)[0].decode("ascii")
assert template == "select id,album,songer,fileurl from %s group by album COLLATE NOCASE"
db = sqlite3.connect(":memory:")
db.execute("create table songtable(id integer, album text, songer text, albumsonger text, fileurl text)")
db.executemany("insert into songtable values(?,?,?,?,?)", [
    (1, "Greatest Hits", "Artist A", "Artist A", "/synthetic/a.flac"),
    (2, "Greatest Hits", "Artist B", "Artist B", "/synthetic/b.flac"),
])
query = template % "songtable"
shipped_rows = db.execute(query).fetchall()
candidate_query = "select album,albumsonger,count(*) from songtable group by album COLLATE NOCASE, albumsonger COLLATE NOCASE"
candidate_rows = db.execute(candidate_query).fetchall()
assert len(shipped_rows) == 1 and len(candidate_rows) == 2
result = dict(
    evidence="Synthetic host SQLite experiment using query bytes from the supplied firmware; no device execution.",
    binary_sha256=hashlib.sha256(binary).hexdigest(), source_file_offset=hex(offset),
    sqlite_version=sqlite3.sqlite_version, input_rows=2,
    shipped_query=query, shipped_result=shipped_rows,
    candidate_query=candidate_query, candidate_result=candidate_rows,
    limitation="Candidate is illustrative, not a complete firmware fix. Album drilldown, artwork, blank tags, callback column order and queue identity still need integration.",
)
encoded = json.dumps(result, indent=2) + "\n"
(base / "query-evidence.json").write_text(encoded)
print(encoded, end="")
