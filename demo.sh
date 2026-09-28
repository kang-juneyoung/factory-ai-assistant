#!/usr/bin/env bash
# 면접 시연용 단축 명령
#   ./demo.sh          웹 화면 시작 + 브라우저 열기 (= start)
#   ./demo.sh stop     종료
#   ./demo.sh reset    샘플 DB 다시 만들기
#   ./demo.sh offline  API 키가 있어도 오프라인 모드로 시작
# API 키는 프로젝트 폴더의 .env 파일에 한 줄로 넣어 두면 자동으로 읽습니다:
#   ANTHROPIC_API_KEY=sk-ant-...
set -e
cd "$(dirname "$0")"
PORT=8090
PY=.venv/bin/python
PIDFILE=logs/server.pid
[ -x "$PY" ] || { echo "가상환경이 없습니다: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f .env ] && { set -a; . ./.env; set +a; }
mkdir -p logs

stop() {
  if [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null; then echo "서버 종료"; fi
  rm -f "$PIDFILE"
}

start() {
  mode=${1:-auto}
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo "이미 실행 중입니다. 다시 시작하려면 ./demo.sh stop 후 실행하세요."; exit 0
  fi
  [ -f data/factory.db ] || "$PY" -m fa init
  if [ "$mode" = auto ]; then
    if [ -n "$ANTHROPIC_API_KEY" ]; then mode=claude; else mode=offline; fi
  fi
  "$PY" -m fa --llm "$mode" serve --port $PORT > logs/server.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 2
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then echo "시작 실패 — logs/server.log 확인"; tail -5 logs/server.log; exit 1; fi
  echo "모드: $mode  |  http://127.0.0.1:$PORT  (끄기: ./demo.sh stop)"
  [ "$(uname)" = Darwin ] && open "http://127.0.0.1:$PORT" || true
}

case "${1:-start}" in
  start)   start auto ;;
  offline) start offline ;;
  stop)    stop ;;
  reset)   stop; rm -f data/factory.db; "$PY" -m fa init ;;
  *)       sed -n '2,9p' "$0" ;;
esac
