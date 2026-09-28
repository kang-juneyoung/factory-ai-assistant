"""샘플 제조 DB 생성.

반도체·디스플레이 장비 부품 공장을 가정한 가상 데이터입니다.
실제 회사·고객 데이터는 한 줄도 들어 있지 않습니다.
같은 seed로 만들면 항상 같은 데이터가 나오므로 테스트 결과가 재현됩니다.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE items (
    item_code   TEXT PRIMARY KEY,
    item_name   TEXT NOT NULL,
    category    TEXT NOT NULL,          -- 반도체 / 디스플레이
    customer    TEXT NOT NULL           -- 가상 고객사
);
CREATE TABLE equipment (
    eq_id       TEXT PRIMARY KEY,
    eq_name     TEXT NOT NULL,
    process     TEXT NOT NULL           -- 가공 / 용접 / 세정 / 코팅 / 검사
);
CREATE TABLE work_orders (
    wo_no       TEXT PRIMARY KEY,
    item_code   TEXT NOT NULL REFERENCES items(item_code),
    plan_qty    INTEGER NOT NULL,
    due_date    TEXT NOT NULL,          -- YYYY-MM-DD
    status      TEXT NOT NULL           -- 진행 / 완료 / 지연
);
CREATE TABLE production (
    id          INTEGER PRIMARY KEY,
    work_date   TEXT NOT NULL,
    wo_no       TEXT NOT NULL REFERENCES work_orders(wo_no),
    item_code   TEXT NOT NULL REFERENCES items(item_code),
    eq_id       TEXT NOT NULL REFERENCES equipment(eq_id),
    shift       TEXT NOT NULL,          -- 주간 / 야간
    good_qty    INTEGER NOT NULL,
    defect_qty  INTEGER NOT NULL
);
CREATE TABLE defects (
    id          INTEGER PRIMARY KEY,
    prod_id     INTEGER NOT NULL REFERENCES production(id),
    work_date   TEXT NOT NULL,
    item_code   TEXT NOT NULL,
    eq_id       TEXT NOT NULL,
    defect_type TEXT NOT NULL,
    qty         INTEGER NOT NULL
);
CREATE TABLE downtime (
    id          INTEGER PRIMARY KEY,
    work_date   TEXT NOT NULL,
    eq_id       TEXT NOT NULL REFERENCES equipment(eq_id),
    minutes     INTEGER NOT NULL,
    reason      TEXT NOT NULL
);
CREATE INDEX ix_prod_date ON production(work_date);
CREATE INDEX ix_def_date ON defects(work_date);
"""

ITEMS = [
    ("SH-450", "샤워헤드 450", "반도체", "가상고객A"),
    ("CL-300", "챔버 라이너 300", "반도체", "가상고객A"),
    ("EC-120", "정전척 커버 120", "반도체", "가상고객B"),
    ("MS-G8", "마스크 지지대 G8", "디스플레이", "가상고객C"),
    ("SP-G6", "스테이지 플레이트 G6", "디스플레이", "가상고객C"),
]

EQUIPMENT = [
    ("CNC-01", "5축 가공기 1호", "가공"),
    ("CNC-02", "5축 가공기 2호", "가공"),
    ("WLD-01", "TIG 용접기 1호", "용접"),
    ("CLN-01", "초음파 세정기 1호", "세정"),
    ("CTG-01", "용사 코팅기 1호", "코팅"),
]

# 공정별로 자주 나오는 불량 유형
DEFECT_TYPES = {
    "가공": ["치수불량", "버(Burr)", "스크래치"],
    "용접": ["용접기공", "변형", "스크래치"],
    "세정": ["이물", "얼룩"],
    "코팅": ["코팅박리", "두께편차", "이물"],
}

DOWNTIME_REASONS = ["공구 교체", "설비 알람", "자재 대기", "정기 점검", "치공구 조정"]

END_DATE = date(2026, 9, 25)   # 샘플 데이터 마지막 날
DAYS = 30


def build(db_path: str | Path, seed: int = 7) -> Path:
    """샘플 DB를 새로 만든다. 기존 파일은 덮어쓴다."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    rng = random.Random(seed)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO items VALUES (?,?,?,?)", ITEMS)
    con.executemany("INSERT INTO equipment VALUES (?,?,?)", EQUIPMENT)

    start = END_DATE - timedelta(days=DAYS - 1)
    # 품목마다 작업지시 2건
    wos = []
    for i, (code, *_rest) in enumerate(ITEMS):
        for k in range(2):
            wo = f"WO-2609-{i * 2 + k + 1:03d}"
            due = start + timedelta(days=12 + k * 14 + i)
            wos.append((wo, code, rng.randint(300, 600), due.isoformat(), "진행"))
    con.executemany("INSERT INTO work_orders VALUES (?,?,?,?,?)", wos)

    prod_id = 0
    for d in range(DAYS):
        day = start + timedelta(days=d)
        if day.weekday() == 6:          # 일요일 휴무
            continue
        for wo, code, *_ in wos:
            wo_idx = int(wo[-3:])
            # 앞쪽 작업지시는 월 초반, 뒤쪽은 후반에 생산
            if (wo_idx % 2 == 1) != (d < DAYS // 2):
                continue
            for eq_id, _name, process in EQUIPMENT:
                if rng.random() < 0.55:
                    continue
                for shift in ("주간", "야간"):
                    if shift == "야간" and rng.random() < 0.5:
                        continue
                    total = rng.randint(8, 25)
                    rate = 0.02
                    # 의도적으로 심어 둔 이상 징후: 마지막 3일 CTG-01 코팅 불량 급증
                    if eq_id == "CTG-01" and day >= END_DATE - timedelta(days=2):
                        rate = 0.12
                    defect = sum(1 for _ in range(total) if rng.random() < rate)
                    prod_id += 1
                    con.execute(
                        "INSERT INTO production VALUES (?,?,?,?,?,?,?,?)",
                        (prod_id, day.isoformat(), wo, code, eq_id, shift, total - defect, defect),
                    )
                    # 불량 수량을 유형별로 나눠 기록
                    remain = defect
                    types = DEFECT_TYPES.get(process, ["기타"])
                    while remain > 0:
                        q = rng.randint(1, remain)
                        dtype = types[0] if (eq_id == "CTG-01" and rate > 0.1) else rng.choice(types)
                        con.execute(
                            "INSERT INTO defects (prod_id, work_date, item_code, eq_id, defect_type, qty)"
                            " VALUES (?,?,?,?,?,?)",
                            (prod_id, day.isoformat(), code, eq_id, dtype, q),
                        )
                        remain -= q
        for eq_id, *_ in EQUIPMENT:
            if rng.random() < 0.3:
                con.execute(
                    "INSERT INTO downtime (work_date, eq_id, minutes, reason) VALUES (?,?,?,?)",
                    (day.isoformat(), eq_id, rng.choice([15, 20, 30, 45, 60, 90]), rng.choice(DOWNTIME_REASONS)),
                )
    # 납기 지난 작업지시는 '지연'으로
    con.execute("UPDATE work_orders SET status='지연' WHERE due_date < ?", (END_DATE.isoformat(),))
    con.commit()
    con.close()
    return db_path
