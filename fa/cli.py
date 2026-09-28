"""명령줄 진입점.

  python -m fa init                      샘플 DB 생성 (data/factory.db)
  python -m fa ask "설비별 불량률 알려줘"   자연어 조회
  python -m fa report [--date YYYY-MM-DD] 일일 품질 리포트(md + xlsx)
  python -m fa serve                     웹 화면 (http://127.0.0.1:8090)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import ask as ask_mod
from . import report as report_mod
from . import seed
from .llm import get_llm

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "factory.db"
AUDIT = ROOT / "logs" / "audit.jsonl"
OUT = ROOT / "out"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fa", description="제조 현장 AI 업무 도우미 (포트폴리오 데모)")
    p.add_argument("--llm", choices=["auto", "claude", "offline"], default=None,
                   help="auto: API 키가 있으면 Claude, 없으면 오프라인")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    a = sub.add_parser("ask")
    a.add_argument("question")
    r = sub.add_parser("report")
    r.add_argument("--date")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8090)
    args = p.parse_args(argv)

    if args.cmd == "init":
        DB.parent.mkdir(parents=True, exist_ok=True)
        seed.build(DB)
        print(f"샘플 DB 생성: {DB}")
        return 0

    if not DB.exists():
        print("먼저 `python -m fa init` 으로 샘플 DB를 만드세요.", file=sys.stderr)
        return 1

    if args.cmd == "ask":
        llm = get_llm(args.llm)
        ans = ask_mod.ask(args.question, DB, llm, AUDIT)
        print(f"[{ans.provider}] {ans.elapsed_ms}ms")
        if ans.sql:
            print("\n-- 실행한 SQL\n" + ans.sql + "\n")
        if ans.error:
            print("오류:", ans.error)
            return 2
        print(ask_mod.to_text_table(ans.columns, ans.rows))
        return 0

    if args.cmd == "report":
        llm = get_llm(args.llm)
        rep = report_mod.generate(DB, llm, args.date)
        md = report_mod.to_markdown(rep)
        OUT.mkdir(exist_ok=True)
        md_path = OUT / f"quality_{rep.day}.md"
        md_path.write_text(md, encoding="utf-8")
        xlsx = report_mod.to_excel(rep, OUT / f"quality_{rep.day}.xlsx")
        print(md)
        print(f"저장: {md_path}\n저장: {xlsx}")
        return 0

    if args.cmd == "serve":
        import uvicorn

        from .web import create_app

        uvicorn.run(create_app(DB, AUDIT, args.llm), host="127.0.0.1", port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
