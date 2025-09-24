#!/usr/bin/env bash

# 启动 MonkeyOCR 集群管理器与多个 parse_server 实例
# 依赖: Python 3.8+、FastAPI、uvicorn、httpx

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"

NUM_WORKERS="${NUM_WORKERS:-${1:-2}}"
WORKER_BASE_PORT="${WORKER_BASE_PORT:-${2:-7880}}"
MANAGER_PORT="${MANAGER_PORT:-${3:-10008}}"
CONFIG="${CONFIG:-${4:-yolo_model_configs.yaml}}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-${5:-1}}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-10}"
STARTUP_GRACE="${STARTUP_GRACE:-120}"
HEALTH_FAILS_BEFORE_RESTART="${HEALTH_FAILS_BEFORE_RESTART:-3}"
HOST="${HOST:-0.0.0.0}"
WORKER_HOST="${WORKER_HOST:-127.0.0.1}"

echo "🔧 配置:"
echo "  NUM_WORKERS       = ${NUM_WORKERS}"
echo "  WORKER_BASE_PORT  = ${WORKER_BASE_PORT}"
echo "  MANAGER_PORT      = ${MANAGER_PORT}"
echo "  CONFIG            = ${CONFIG}"
echo "  MAX_CONCURRENCY   = ${MAX_CONCURRENCY}"
echo "  HEALTH_INTERVAL   = ${HEALTH_INTERVAL}s"
echo "  STARTUP_GRACE     = ${STARTUP_GRACE}s"
echo "  HEALTH_FAILS      = ${HEALTH_FAILS_BEFORE_RESTART}"
echo "  HOST              = ${HOST}"
echo "  WORKER_HOST       = ${WORKER_HOST}"

cd "$DIR"

PYTHON_CMD="python3"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
  PYTHON_CMD="python"
fi

exec "$PYTHON_CMD" "$DIR/cluster_manager.py" \
  --num-workers "$NUM_WORKERS" \
  --worker-base-port "$WORKER_BASE_PORT" \
  --manager-port "$MANAGER_PORT" \
  --config "$CONFIG" \
  --max-concurrency "$MAX_CONCURRENCY" \
  --health-interval "$HEALTH_INTERVAL" \
  --startup-grace "$STARTUP_GRACE" \
  --health-failures-before-restart "$HEALTH_FAILS_BEFORE_RESTART" \
  --host "$HOST" \
  --worker-host "$WORKER_HOST"


