"""LLM 연결부.

- ClaudeLLM : ANTHROPIC_API_KEY가 있으면 Claude API 호출 (모델은 FA_MODEL 환경변수)
- OfflineLLM: 키가 없거나 사내망에서 외부 호출이 막힌 경우를 위한 규칙 기반 대체.
              자주 묻는 질문 몇 가지를 정해진 SQL로 바꿔 준다. 시연·테스트도 이 모드로 돈다.

외부 LLM에는 '테이블 구조'와 '집계된 숫자'만 보낸다. 원본 행 데이터는 보내지 않는다.
"""
from __future__ import annotations

import os
import re
from typing import Protocol

SCHEMA_DOC = """\
items(item_code, item_name, category[반도체|디스플레이], customer)
equipment(eq_id, eq_name, process[가공|용접|세정|코팅|검사])
work_orders(wo_no, item_code, plan_qty, due_date 'YYYY-MM-DD', status[진행|완료|지연])
production(id, work_date 'YYYY-MM-DD', wo_no, item_code, eq_id, shift[주간|야간], good_qty, defect_qty)
defects(id, prod_id, work_date, item_code, eq_id, defect_type, qty)
downtime(id, work_date, eq_id, minutes, reason)
불량률 = SUM(defect_qty) * 1.0 / SUM(good_qty + defect_qty)
"""

SQL_SYSTEM = f"""너는 제조 현장 데이터 조회를 돕는 SQLite 전문가다.
사용자의 한국어 질문을 SQLite SELECT 문 하나로 바꿔라.
규칙: SELECT 또는 WITH로 시작하는 한 문장만. 데이터 변경 금지. 결과 컬럼 별칭은 한국어로.
비율(불량률 등)은 100을 곱한 퍼센트 값으로 ROUND(..., 2) 처리하고 별칭 끝에 _퍼센트를 붙여라. 평균 등 소수 결과도 ROUND(..., 2).
질문에 답하는 데 필요한 데이터(테이블·컬럼)가 아래 스키마에 없으면 SQL을 만들지 말고
`NO_DATA: <없는 데이터와 이유 한 줄>` 한 줄만 답하라. 비슷해 보이는 다른 데이터로 대신 답하지 마라.
(예: 출근·인원·급여·재고·원가 데이터는 스키마에 없다)
SQL만 ```sql 블록으로 답하고 설명은 쓰지 마라.
오늘 날짜는 데이터의 마지막 날인 MAX(work_date)로 간주한다.

스키마:
{SCHEMA_DOC}"""

REPORT_SYSTEM = """너는 제조 공장의 품질 담당자다. 아래에 주어진 숫자만 사용해서
일일 품질 리포트의 '종합 의견'을 한국어 3~5문장으로 써라.
주어지지 않은 숫자를 만들어 내지 말고, 원인은 '추정'이라고 표현하라.
마지막 문장은 현장에서 오늘 확인할 조치 한 가지로 끝내라."""


class LLM(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class ClaudeLLM:
    name = "claude"

    def __init__(self, model: str | None = None):
        import anthropic  # 선택 의존성

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("FA_MODEL", "claude-sonnet-4-5")

    def complete(self, system: str, user: str) -> str:
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


# ---- 오프라인(규칙 기반) ---------------------------------------------------

LAST_DAY = "(SELECT MAX(work_date) FROM production)"

OFFLINE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("설비", "불량률"),
     f"""SELECT p.eq_id AS 설비, e.eq_name AS 설비명,
       SUM(p.good_qty + p.defect_qty) AS 생산수량, SUM(p.defect_qty) AS 불량수량,
       ROUND(SUM(p.defect_qty) * 100.0 / SUM(p.good_qty + p.defect_qty), 2) AS 불량률_퍼센트
FROM production p JOIN equipment e ON e.eq_id = p.eq_id
WHERE p.work_date > date({LAST_DAY}, '-7 day')
GROUP BY p.eq_id ORDER BY 불량률_퍼센트 DESC"""),
    (("불량", "유형"),
     f"""SELECT defect_type AS 불량유형, SUM(qty) AS 수량
FROM defects WHERE work_date > date({LAST_DAY}, '-7 day')
GROUP BY defect_type ORDER BY 수량 DESC"""),
    (("품목", "생산"),
     f"""SELECT i.item_code AS 품목코드, i.item_name AS 품목명,
       SUM(p.good_qty) AS 양품수량, SUM(p.defect_qty) AS 불량수량
FROM production p JOIN items i ON i.item_code = p.item_code
WHERE p.work_date > date({LAST_DAY}, '-7 day')
GROUP BY i.item_code ORDER BY 양품수량 DESC"""),
    (("비가동",),
     f"""SELECT d.eq_id AS 설비, d.reason AS 사유, SUM(d.minutes) AS 비가동_분
FROM downtime d WHERE d.work_date > date({LAST_DAY}, '-7 day')
GROUP BY d.eq_id, d.reason ORDER BY 비가동_분 DESC"""),
    (("납기",),
     """SELECT w.wo_no AS 작업지시, w.item_code AS 품목코드, w.due_date AS 납기,
       w.plan_qty AS 계획수량, COALESCE(SUM(p.good_qty), 0) AS 누적양품,
       w.plan_qty - COALESCE(SUM(p.good_qty), 0) AS 잔량, w.status AS 상태
FROM work_orders w LEFT JOIN production p ON p.wo_no = w.wo_no
GROUP BY w.wo_no HAVING 잔량 > 0 ORDER BY w.due_date"""),
]


class OfflineLLM:
    name = "offline"

    def complete(self, system: str, user: str) -> str:
        if system == SQL_SYSTEM:
            return self._to_sql(user)
        if system == REPORT_SYSTEM:
            return self._comment(user)
        raise ValueError("알 수 없는 요청")

    @staticmethod
    def _to_sql(question: str) -> str:
        q = question.replace(" ", "")
        for keywords, sql in OFFLINE_RULES:
            if all(k in q for k in keywords):
                return f"```sql\n{sql}\n```"
        raise LookupError(
            "오프라인 모드에서는 정해진 질문만 답합니다: 설비별 불량률 / 불량 유형 / 품목별 생산 / 비가동 / 납기"
        )

    @staticmethod
    def _comment(facts: str) -> str:
        # 사실 목록에서 경보 줄만 골라 문장으로 만든다(숫자는 그대로 옮김)
        alerts = [ln.strip("- ").strip() for ln in facts.splitlines() if ln.startswith("- 경보")]
        rate = re.search(r"전체 불량률: ([\d.]+)%", facts)
        head = f"오늘 전체 불량률은 {rate.group(1)}%입니다." if rate else "오늘 생산 실적을 집계했습니다."
        if not alerts:
            return head + " 기준을 넘는 이상 징후는 없습니다. 평소대로 초물 검사를 유지하십시오."
        body = " ".join(a.replace("경보: ", "") + "." for a in alerts)
        return f"{head} {body} 원인은 설비 조건 변화로 추정되며 확인이 필요합니다. 오늘 해당 설비의 공정 조건 기록부터 확인하십시오."


def get_llm(prefer: str | None = None) -> LLM:
    prefer = prefer or os.environ.get("FA_LLM", "auto")
    if prefer == "offline":
        return OfflineLLM()
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return ClaudeLLM()
        except ImportError:
            pass
    if prefer == "claude":
        raise RuntimeError("ANTHROPIC_API_KEY 또는 anthropic 패키지가 없습니다")
    return OfflineLLM()
