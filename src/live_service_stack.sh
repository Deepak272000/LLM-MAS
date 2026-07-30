#!/bin/bash

wait_for_tcp_port() {
    local host="$1"
    local port="$2"
    local name="$3"
    local timeout_secs="${4:-60}"
    local deadline=$((SECONDS + timeout_secs))

    while [ $SECONDS -lt $deadline ]; do
        if "$PYTHON" - "$host" "$port" >/dev/null 2>&1 <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
sock = socket.socket()
sock.settimeout(1.0)
try:
    sock.connect((host, port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
raise SystemExit(0)
PY
        then
            echo "  ${name}: READY on ${host}:${port}"
            return 0
        fi
    done

    echo "  ERROR: ${name} did not become ready on ${host}:${port} within ${timeout_secs}s"
    return 1
}

start_background_process() {
    local name="$1"
    local workdir="$2"
    local logfile="$3"
    shift 3

    mkdir -p "$(dirname "$logfile")"
    (
        cd "$workdir" || exit 1
        "$@"
    ) >"$logfile" 2>&1 &
    local pid=$!
    LIVE_SERVICE_PIDS+=("$pid")
    echo "  ${name}: PID ${pid} (log: ${logfile})"
}

cleanup_live_services() {
    if [ ${#LIVE_SERVICE_PIDS[@]} -eq 0 ]; then
        return
    fi

    for pid in "${LIVE_SERVICE_PIDS[@]}"; do
        if kill -0 "$pid" >/dev/null 2>&1; then
            kill "$pid" >/dev/null 2>&1 || true
            wait "$pid" >/dev/null 2>&1 || true
        fi
    done
}

ensure_recommendation_python_deps() {
    echo "  Ensuring recommendation service Python dependencies ..."
    "$PYTHON" -m pip install -q -r "${SRCDIR}/recommendationservice/requirements.txt"
}

ensure_adservice_built() {
    local ad_bin="${SRCDIR}/adservice/build/install/hipstershop/bin/AdService"
    if [ -x "$ad_bin" ]; then
        echo "  adservice: existing build found"
        return 0
    fi

    echo "  adservice: building installDist ..."
    (
        cd "${SRCDIR}/adservice" || exit 1
        chmod +x ./gradlew
        ./gradlew installDist
    )
}

start_productcatalog_service() {
    local port="${PRODUCT_CATALOG_PORT:-3550}"
    local logfile="${LOGDIR}/productcatalogservice_${SLURM_JOB_ID:-local}.log"

    echo "  Starting productcatalogservice ..."
    start_background_process \
        "productcatalogservice" \
        "${SRCDIR}/productcatalogservice" \
        "$logfile" \
        env \
        PRODUCT_CATALOG_JSON="${SRCDIR}/productcatalogservice/products.json" \
        GRPC_PORT="$port" \
        PYTHONPATH="${SRCDIR}:${SRCDIR}/productcatalogservice" \
        "$PYTHON" server.py
    wait_for_tcp_port 127.0.0.1 "$port" "productcatalogservice" 60 || return 1
}

start_recommendation_service() {
    local rec_port="${RECOMMENDATION_PORT:-8080}"
    local catalog_port="${PRODUCT_CATALOG_PORT:-3550}"
    local logfile="${LOGDIR}/recommendationservice_${SLURM_JOB_ID:-local}.log"

    ensure_recommendation_python_deps
    echo "  Starting recommendationservice ..."
    start_background_process \
        "recommendationservice" \
        "${SRCDIR}/recommendationservice" \
        "$logfile" \
        env \
        PORT="$rec_port" \
        PRODUCT_CATALOG_SERVICE_ADDR="127.0.0.1:${catalog_port}" \
        DISABLE_PROFILER=1 \
        ENABLE_TRACING=0 \
        PYTHONPATH="${SRCDIR}:${SRCDIR}/recommendationservice" \
        "$PYTHON" recommendation_server.py
    wait_for_tcp_port 127.0.0.1 "$rec_port" "recommendationservice" 60 || return 1
}

start_adservice() {
    local port="${ADSERVICE_PORT:-9555}"
    local logfile="${LOGDIR}/adservice_${SLURM_JOB_ID:-local}.log"

    ensure_adservice_built
    echo "  Starting adservice ..."
    start_background_process \
        "adservice" \
        "${SRCDIR}/adservice" \
        "$logfile" \
        env \
        PORT="$port" \
        "${SRCDIR}/adservice/build/install/hipstershop/bin/AdService"
    wait_for_tcp_port 127.0.0.1 "$port" "adservice" 90 || return 1
}

start_required_live_services() {
    local mode="$1"

    LIVE_SERVICE_PIDS=()
    trap cleanup_live_services EXIT

    case "$mode" in
        recommendation)
            start_productcatalog_service
            start_recommendation_service
            ;;
        adservice)
            start_adservice
            ;;
        boundary|all)
            start_productcatalog_service
            start_recommendation_service
            start_adservice
            ;;
    esac
}