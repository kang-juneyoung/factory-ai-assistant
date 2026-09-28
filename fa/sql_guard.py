"""AI가 만든 SQL을 실행하기 전에 거치는 안전장치.

두 겹으로 막는다.
1) 문자열 검사  : SELECT/WITH 한 문장만, 금지 키워드·허용 외 테이블 차단, LIMIT 강제
2) DB 권한 제한 : 읽기 전용으로 열고, sqlite authorizer로 읽기 외 동작을 DB 엔진에서 거부
1번을 우회하는 SQL이 나와도 2번에서 막힌다. 테스트에서 둘을 따로 검증한다.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

ALLOWED_TABLES = {"items", "equipment", "work_orders", "production", "defects", "downtime"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate)\b",
    re.IGNORECASE,
)
MAX_ROWS = 200


class UnsafeSQL(ValueError):
    """실행하면 안 되는 SQL."""


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)


def extract_sql(text: str) -> str:
    """LLM 응답에서 SQL만 꺼낸다(```sql 블록이 있으면 그 안쪽)."""
    m = re.search(r"```(?:sql)?\s*(.+?)```", text, flags=re.S | re.I)
    return (m.group(1) if m else text).strip()


def check(sql: str) -> str:
    """검사를 통과하면 실행할 SQL(LIMIT 보정)을 돌려주고, 아니면 UnsafeSQL."""
    body = _strip_comments(sql).strip().rstrip(";").strip()
    if not body:
        raise UnsafeSQL("빈 SQL")
    if ";" in body:
        raise UnsafeSQL("여러 문장은 실행하지 않습니다")
    if not re.match(r"^(select|with)\b", body, re.I):
        raise UnsafeSQL("조회(SELECT)만 허용합니다")
    if FORBIDDEN.search(body):
        raise UnsafeSQL("변경·관리 명령이 포함되어 있습니다")
    # FROM/JOIN 뒤 테이블 이름 확인 (CTE 이름은 허용)
    ctes = {m.lower() for m in re.findall(r"(\w+)\s+as\s*\(", body, re.I)}
    for name in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w]*)", body, re.I):
        if name.lower() not in ALLOWED_TABLES | ctes:
            raise UnsafeSQL(f"허용되지 않은 테이블: {name}")
    if not re.search(r"\blimit\s+\d+", body, re.I):
        body = f"{body}\nLIMIT {MAX_ROWS}"
    return body


def _authorizer(action, arg1, arg2, dbname, source):
    allowed = {sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION}
    # CTE/서브쿼리 처리에 쓰이는 RECURSIVE 코드도 읽기 동작
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
    if recursive is not None:
        allowed.add(recursive)
    return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY


def connect_readonly(db_path: str | Path) -> sqlite3.Connection:
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.set_authorizer(_authorizer)
    return con


def run(db_path: str | Path, sql: str, *, checked: bool = True) -> tuple[list[str], list[tuple]]:
    """SQL을 읽기 전용으로 실행해 (컬럼명, 행) 반환. checked=False는 테스트용."""
    final = check(sql) if checked else sql
    con = connect_readonly(db_path)
    try:
        cur = con.execute(final)
        cols = [c[0] for c in cur.description or []]
        rows = cur.fetchmany(MAX_ROWS)
    finally:
        con.close()
    return cols, rows
