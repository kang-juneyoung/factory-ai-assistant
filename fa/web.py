"""최소 웹 화면: 질문 입력 → SQL·결과 표, 일일 리포트 보기."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import ask as ask_mod
from . import report as report_mod
from .llm import get_llm

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>제조 AI 업무 도우미</title>
<style>
body{font-family:'Malgun Gothic','Apple SD Gothic Neo',sans-serif;max-width:980px;margin:24px auto;padding:0 16px;color:#1f2933}
h1{font-size:22px;margin:0 0 4px}.sub{color:#667;margin-bottom:18px}
input{width:70%;padding:10px;font-size:15px}button{padding:10px 16px;font-size:15px;margin-left:6px}
.chips button{margin:6px 6px 0 0;padding:6px 10px;font-size:13px;background:#eef2f7;border:1px solid #ccd;border-radius:14px}
pre{background:#f6f8fa;padding:10px;overflow:auto;font-size:13px}
table{border-collapse:collapse;margin-top:8px;font-size:14px}td,th{border:1px solid #d0d7de;padding:4px 8px}th{background:#eef2f7}
.err{color:#b00020}.warn{background:#fff4e5;padding:8px;border-left:4px solid #f0a020}
section{margin-top:28px}
</style></head><body>
<h1>제조 AI 업무 도우미</h1><div class="sub">자연어로 생산·품질 데이터 조회 · 일일 품질 리포트 (샘플 데이터)</div>
<input id="q" placeholder="예) 최근 7일 설비별 불량률 알려줘"><button onclick="ask()">조회</button>
<div class="chips">
<button onclick="ex(this)">최근 7일 설비별 불량률</button><button onclick="ex(this)">불량 유형별 수량</button>
<button onclick="ex(this)">품목별 생산 실적</button><button onclick="ex(this)">설비 비가동 현황</button>
<button onclick="ex(this)">납기 남은 작업지시</button></div>
<div id="out"></div>
<section><button onclick="rep()">오늘 품질 리포트 만들기</button><div id="rep"></div></section>
<script>
function esc(s){return String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function table(cols,rows){return '<table><tr>'+cols.map(c=>'<th>'+esc(c)+'</th>').join('')+'</tr>'+
 rows.map(r=>'<tr>'+r.map(v=>'<td>'+esc(v)+'</td>').join('')+'</tr>').join('')+'</table>'}
function ex(b){document.getElementById('q').value=b.textContent;ask()}
async function ask(){const q=document.getElementById('q').value;const o=document.getElementById('out');o.textContent='조회 중...';
 const r=await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:q})});const a=await r.json();
 o.innerHTML='<p>['+esc(a.provider)+'] '+a.elapsed_ms+'ms</p>'+(a.sql?'<pre>'+esc(a.sql)+'</pre>':'')+
 (a.error?'<p class="err">'+esc(a.error)+'</p>':table(a.columns,a.rows))}
async function rep(){const o=document.getElementById('rep');o.textContent='작성 중...';const a=await (await fetch('/api/report')).json();
 let h='<h3>일일 품질 리포트 — '+esc(a.day)+'</h3><p>생산 '+a.total+'개 / 불량 '+a.defect+'개 / 불량률 '+a.rate+'% (직전 7일 평균 '+a.avg7_rate+'%)</p>';
 if(a.alerts.length)h+='<div class="warn">경보: '+a.alerts.map(esc).join('<br>')+'</div>';
 if(a.by_eq.length)h+=table(Object.keys(a.by_eq[0]),a.by_eq.map(Object.values));
 h+='<h4>종합 의견 ('+esc(a.provider)+')</h4><p>'+esc(a.comment)+'</p>';
 if(a.unverified_numbers.length)h+='<div class="warn">확인 필요: 집계표에 없는 숫자 '+a.unverified_numbers.map(esc).join(', ')+'</div>';
 o.innerHTML=h}
</script></body></html>"""


class AskIn(BaseModel):
    question: str


def create_app(db_path: Path, audit_path: Path | None = None, llm_pref: str | None = None) -> FastAPI:
    app = FastAPI(title="제조 AI 업무 도우미")
    llm = get_llm(llm_pref)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return PAGE

    @app.post("/api/ask")
    def api_ask(body: AskIn):
        return asdict(ask_mod.ask(body.question, db_path, llm, audit_path))

    @app.get("/api/report")
    def api_report(date: str | None = None):
        return asdict(report_mod.generate(db_path, llm, date))

    return app
