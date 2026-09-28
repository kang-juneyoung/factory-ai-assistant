import sqlite3

import pytest

from fa import ask as ask_mod
from fa import report as report_mod
from fa import seed, sql_guard
from fa.llm import OfflineLLM


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return seed.build(tmp_path_factory.mktemp("d") / "t.db")


# ---- 1겹: 문자열 검사 ------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "DELETE FROM production",
    "UPDATE items SET item_name='x'",
    "SELECT 1; DROP TABLE items",
    "PRAGMA table_info(items)",
    "ATTACH DATABASE 'x.db' AS x",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM production /* ok */ ; DELETE FROM items",
])
def test_guard_blocks(bad):
    with pytest.raises(sql_guard.UnsafeSQL):
        sql_guard.check(bad)


def test_guard_adds_limit():
    assert "LIMIT 200" in sql_guard.check("SELECT * FROM items")


def test_guard_allows_cte():
    sql = "WITH t AS (SELECT eq_id FROM production) SELECT * FROM t"
    assert sql_guard.check(sql).startswith("WITH")


# ---- 2겹: DB 권한 제한 (문자열 검사를 우회해도 막히는지) -------------------

def test_readonly_connection_blocks_write_even_without_guard(db):
    with pytest.raises(sqlite3.DatabaseError):
        sql_guard.run(db, "DELETE FROM production", checked=False)
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM production").fetchone()[0] > 0


# ---- 자연어 조회 -----------------------------------------------------------

@pytest.mark.parametrize("q", ["최근 7일 설비별 불량률", "불량 유형별 수량", "품목별 생산 실적",
                               "설비 비가동 현황", "납기 남은 작업지시"])
def test_offline_questions(db, q, tmp_path):
    audit = tmp_path / "a.jsonl"
    ans = ask_mod.ask(q, db, OfflineLLM(), audit)
    assert ans.error == ""
    assert ans.rows
    assert audit.read_text(encoding="utf-8").count("\n") == 1


def test_unknown_question_is_reported_not_crashed(db):
    ans = ask_mod.ask("점심 메뉴 추천", db, OfflineLLM())
    assert "정해진 질문" in ans.error


class EvilLLM:
    name = "evil"

    def complete(self, system, user):
        return "```sql\nDELETE FROM production\n```"


def test_malicious_llm_output_is_blocked(db):
    ans = ask_mod.ask("아무거나", db, EvilLLM())
    assert ans.error.startswith("안전 검사에서 차단")


# ---- 리포트 ----------------------------------------------------------------

def test_report_numbers_match_direct_sql(db):
    rep = report_mod.compute(db)
    con = sqlite3.connect(db)
    t, d = con.execute(
        "SELECT SUM(good_qty+defect_qty), SUM(defect_qty) FROM production WHERE work_date=?",
        (rep.day,)).fetchone()
    assert (rep.total, rep.defect) == (t, d)


def test_planted_anomaly_is_detected(db):
    rep = report_mod.compute(db)
    assert rep.day == seed.END_DATE.isoformat()
    assert any("CTG-01" in a for a in rep.alerts)


def test_offline_comment_uses_only_given_numbers(db):
    rep = report_mod.generate(db, OfflineLLM())
    assert rep.unverified_numbers == []


def test_invented_number_is_flagged():
    facts = "전체 불량률: 3.2%"
    assert report_mod.unverified_numbers("불량률 3.2%이고 손실은 480만원", facts) == ["480"]


def test_excel_output(db, tmp_path):
    rep = report_mod.generate(db, OfflineLLM())
    path = report_mod.to_excel(rep, tmp_path / "r.xlsx")
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert wb.sheetnames == ["요약", "설비별", "불량유형", "비가동"]


def test_web_api(db):
    from fastapi.testclient import TestClient

    from fa.web import create_app

    c = TestClient(create_app(db, None, "offline"))
    assert c.get("/").status_code == 200
    r = c.post("/api/ask", json={"question": "설비별 불량률"}).json()
    assert r["rows"] and not r["error"]
    assert c.get("/api/report").json()["alerts"]


class HonestLLM:
    name = "honest"

    def complete(self, system, user):
        return "NO_DATA: 출근·인원 데이터는 DB에 없습니다"


def test_no_data_answer_is_shown_not_substituted(db, tmp_path):
    audit = tmp_path / "a.jsonl"
    ans = ask_mod.ask("출근자 현황 보여줘", db, HonestLLM(), audit)
    assert ans.error.startswith("데이터 없음")
    assert ans.sql == "" and ans.rows == []
    assert "데이터 없음" in audit.read_text(encoding="utf-8")
