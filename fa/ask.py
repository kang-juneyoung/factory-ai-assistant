"""자연어 질문 → SQL → 결과. 모든 요청은 감사 로그(JSONL)에 남긴다."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sql_guard
from .llm import LLM, SQL_SYSTEM


@dataclass
class Answer:
    question: str
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    error: str = ""
    provider: str = ""
    elapsed_ms: int = 0


def ask(question: str, db_path: str | Path, llm: LLM, audit_path: str | Path | None = None) -> Answer:
    t0 = time.perf_counter()
    ans = Answer(question=question, provider=llm.name)
    try:
        raw = llm.complete(SQL_SYSTEM, question)
        ans.sql = sql_guard.check(sql_guard.extract_sql(raw))
        ans.columns, ans.rows = sql_guard.run(db_path, ans.sql)
    except sql_guard.UnsafeSQL as e:
        ans.error = f"안전 검사에서 차단: {e}"
    except LookupError as e:
        ans.error = str(e)
    except Exception as e:  # DB 오류 등은 사용자에게 그대로 보여 준다
        ans.error = f"{type(e).__name__}: {e}"
    ans.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if audit_path:
        _audit(audit_path, ans)
    return ans


def _audit(path: str | Path, ans: Answer) -> None:
    rec = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": ans.provider,
        "question": ans.question,
        "sql": ans.sql,
        "rows": len(ans.rows),
        "error": ans.error,
        "ms": ans.elapsed_ms,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def to_text_table(columns: list[str], rows: list[tuple]) -> str:
    if not columns:
        return "(결과 없음)"
    data = [columns] + [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(_w(row[i]) for row in data) for i in range(len(columns))]
    lines = []
    for n, row in enumerate(data):
        lines.append("  ".join(v + " " * (widths[i] - _w(v)) for i, v in enumerate(row)))
        if n == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _w(s: str) -> int:
    """터미널 표시 폭(한글은 2칸)."""
    return sum(2 if ord(ch) > 0x1100 else 1 for ch in s)
