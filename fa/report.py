"""일일 품질 리포트 자동 생성.

원칙: 숫자는 코드(SQL)가 계산하고, LLM은 그 숫자로 '의견 문장'만 쓴다.
LLM이 쓴 문장에 사실 목록에 없는 숫자가 들어 있으면 경고를 붙인다(숫자 검증).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .llm import LLM, REPORT_SYSTEM
from .sql_guard import connect_readonly

ALERT_MIN_RATE = 2.0      # 불량률 이 값(%) 이상이면서
ALERT_RATIO = 1.5         # 최근 7일 평균의 1.5배 이상이면 경보


@dataclass
class Report:
    day: str
    total: int = 0
    defect: int = 0
    rate: float = 0.0
    avg7_rate: float = 0.0
    by_eq: list[dict] = field(default_factory=list)
    by_type: list[dict] = field(default_factory=list)
    downtime: list[dict] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    comment: str = ""
    unverified_numbers: list[str] = field(default_factory=list)
    provider: str = ""


def _rate(defect: int, total: int) -> float:
    return round(defect * 100.0 / total, 2) if total else 0.0


def compute(db_path: str | Path, day: str | None = None) -> Report:
    con = connect_readonly(db_path)
    try:
        day = day or con.execute("SELECT MAX(work_date) FROM production").fetchone()[0]
        rep = Report(day=day)
        total, defect = con.execute(
            "SELECT COALESCE(SUM(good_qty+defect_qty),0), COALESCE(SUM(defect_qty),0)"
            " FROM production WHERE work_date = ?", (day,)).fetchone()
        rep.total, rep.defect, rep.rate = total, defect, _rate(defect, total)

        t7, d7 = con.execute(
            "SELECT COALESCE(SUM(good_qty+defect_qty),0), COALESCE(SUM(defect_qty),0) FROM production"
            " WHERE work_date < ? AND work_date >= date(?, '-7 day')", (day, day)).fetchone()
        rep.avg7_rate = _rate(d7, t7)

        # 설비별: 오늘 vs 직전 7일
        base = {
            eq: _rate(d, t) for eq, t, d in con.execute(
                "SELECT eq_id, SUM(good_qty+defect_qty), SUM(defect_qty) FROM production"
                " WHERE work_date < ? AND work_date >= date(?, '-7 day') GROUP BY eq_id", (day, day))
        }
        for eq, name, t, d in con.execute(
            "SELECT p.eq_id, e.eq_name, SUM(good_qty+defect_qty), SUM(defect_qty)"
            " FROM production p JOIN equipment e ON e.eq_id=p.eq_id"
            " WHERE work_date = ? GROUP BY p.eq_id ORDER BY p.eq_id", (day,)):
            r, b = _rate(d, t), base.get(eq, 0.0)
            rep.by_eq.append({"설비": eq, "설비명": name, "생산": t, "불량": d, "불량률": r, "7일평균": b})
            if r >= ALERT_MIN_RATE and (b == 0 or r >= b * ALERT_RATIO):
                rep.alerts.append(f"{eq}({name}) 불량률 {r}% (7일 평균 {b}%)")

        rep.by_type = [
            {"불량유형": t, "수량": q} for t, q in con.execute(
                "SELECT defect_type, SUM(qty) FROM defects WHERE work_date = ?"
                " GROUP BY defect_type ORDER BY 2 DESC", (day,))
        ]
        rep.downtime = [
            {"설비": e, "사유": r, "분": m} for e, r, m in con.execute(
                "SELECT eq_id, reason, minutes FROM downtime WHERE work_date = ? ORDER BY minutes DESC", (day,))
        ]
    finally:
        con.close()
    return rep


def facts_text(rep: Report) -> str:
    """LLM에 넘기는 사실 목록. 여기에 있는 숫자만 문장에 쓸 수 있다."""
    lines = [
        f"기준일: {rep.day}",
        f"전체 생산: {rep.total}개, 불량: {rep.defect}개, 전체 불량률: {rep.rate}%",
        f"직전 7일 평균 불량률: {rep.avg7_rate}%",
    ]
    lines += [f"설비 {e['설비']}: 생산 {e['생산']}개, 불량률 {e['불량률']}% (7일 평균 {e['7일평균']}%)" for e in rep.by_eq]
    lines += [f"불량유형 {t['불량유형']}: {t['수량']}개" for t in rep.by_type]
    lines += [f"비가동 {d['설비']}: {d['사유']} {d['분']}분" for d in rep.downtime]
    lines += [f"- 경보: {a}" for a in rep.alerts]
    return "\n".join(lines)


_NUM = re.compile(r"\d+(?:\.\d+)?")


def unverified_numbers(text: str, facts: str) -> list[str]:
    """문장 속 숫자 중 사실 목록에 없는 것. (날짜·1자리 서수는 제외)"""
    allowed = set(_NUM.findall(facts))
    out = []
    for n in _NUM.findall(text):
        if n in allowed or (len(n) == 1 and "." not in n):
            continue
        out.append(n)
    return out


def generate(db_path: str | Path, llm: LLM, day: str | None = None) -> Report:
    rep = compute(db_path, day)
    facts = facts_text(rep)
    rep.provider = llm.name
    rep.comment = llm.complete(REPORT_SYSTEM, facts).strip()
    rep.unverified_numbers = unverified_numbers(rep.comment, facts)
    return rep


def to_markdown(rep: Report) -> str:
    md = [f"# 일일 품질 리포트 — {rep.day}", ""]
    md.append(f"- 생산 **{rep.total}**개 / 불량 **{rep.defect}**개 / 불량률 **{rep.rate}%** (직전 7일 평균 {rep.avg7_rate}%)")
    md.append(f"- 경보 {len(rep.alerts)}건" + ("" if not rep.alerts else ": " + "; ".join(rep.alerts)))
    md += ["", "## 설비별", "", "| 설비 | 설비명 | 생산 | 불량 | 불량률(%) | 7일 평균(%) |", "|---|---|---:|---:|---:|---:|"]
    md += [f"| {e['설비']} | {e['설비명']} | {e['생산']} | {e['불량']} | {e['불량률']} | {e['7일평균']} |" for e in rep.by_eq]
    md += ["", "## 불량 유형", "", "| 유형 | 수량 |", "|---|---:|"]
    md += [f"| {t['불량유형']} | {t['수량']} |" for t in rep.by_type] or ["| - | 0 |"]
    md += ["", "## 비가동", "", "| 설비 | 사유 | 분 |", "|---|---|---:|"]
    md += [f"| {d['설비']} | {d['사유']} | {d['분']} |" for d in rep.downtime] or ["| - | - | 0 |"]
    md += ["", f"## 종합 의견 (작성: {rep.provider})", "", rep.comment]
    if rep.unverified_numbers:
        md += ["", f"> ⚠ 확인 필요: 의견에 집계표에 없는 숫자가 있습니다 → {', '.join(rep.unverified_numbers)}"]
    return "\n".join(md) + "\n"


def to_excel(rep: Report, path: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "요약"
    ws.append(["기준일", rep.day])
    ws.append(["생산", rep.total])
    ws.append(["불량", rep.defect])
    ws.append(["불량률(%)", rep.rate])
    ws.append(["직전 7일 평균(%)", rep.avg7_rate])
    ws.append(["경보", "; ".join(rep.alerts) or "없음"])
    ws.append(["종합 의견", rep.comment])
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 90

    def sheet(title: str, rows: list[dict]):
        s = wb.create_sheet(title)
        if not rows:
            s.append(["데이터 없음"])
            return
        s.append(list(rows[0].keys()))
        for c in s[1]:
            c.font = Font(bold=True)
        for r in rows:
            s.append(list(r.values()))
        return s

    s = sheet("설비별", rep.by_eq)
    if s is not None:
        red = PatternFill("solid", fgColor="FFD7D7")
        for row in s.iter_rows(min_row=2):
            rate, avg = row[4].value, row[5].value
            if rate >= ALERT_MIN_RATE and (avg == 0 or rate >= avg * ALERT_RATIO):
                for c in row:
                    c.fill = red
    sheet("불량유형", rep.by_type)
    sheet("비가동", rep.downtime)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
