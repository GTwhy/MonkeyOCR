#!/usr/bin/env python3
"""
MonkeyOCR Cluster Manager

功能:
- 启动多个 parse_server 实例(通过 start_server.sh)
- 提供统一入口端口(反向代理/轮询转发 /parse 与 /parse_batch 请求)
- 定期健康检查与故障自动拉起
- 管理页面: 显示工作进程状态与队列情况

使用示例:
  python cluster_manager.py \
    --num-workers 2 \
    --worker-base-port 7861 \
    --manager-port 9000 \
    --config yolo_model_configs.yaml \
    --max-concurrency 1

或使用 start_cluster.sh 包装脚本。
"""

import os
import sys
import time
import json
import signal
import asyncio
import logging
import subprocess
import socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import uvicorn
import httpx
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from contextlib import asynccontextmanager


logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(asctime)s - %(message)s")
logger = logging.getLogger("cluster_manager")


@dataclass
class WorkerProcess:
    worker_id: int
    port: int
    process: Optional[subprocess.Popen] = None
    status: str = "starting"  # starting | healthy | unhealthy | stopped
    last_ok_ts: float = field(default_factory=lambda: 0.0)
    restarted_times: int = 0
    current_requests: int = 0
    total_requests: int = 0
    log_path: Optional[str] = None
    # 记录最近一次启动时间，用于冷启动宽限期判断
    start_ts: float = field(default_factory=lambda: time.time())
    # 连续健康检查失败次数，用于避免单次网络抖动导致的误重启
    consecutive_failures: int = 0
    # 绑定的 GPU 索引；None 表示 CPU 或未指定
    gpu_index: Optional[int] = None


class ClusterManager:
    def __init__(
        self,
        num_workers: int,
        worker_base_port: int,
        manager_port: int,
        config_path: str,
        max_concurrency_per_worker: int = 1,
        health_interval_seconds: float = 10.0,
        startup_grace_seconds: float = 60.0,
        health_failures_before_restart: int = 3,
        worker_host: str = "127.0.0.1",
        manager_host: str = "0.0.0.0",
    ) -> None:
        self.num_workers = num_workers
        self.worker_base_port = worker_base_port
        self.manager_port = manager_port
        self.config_path = config_path
        self.max_concurrency_per_worker = max_concurrency_per_worker
        self.health_interval_seconds = health_interval_seconds
        # 冷启动宽限期：在此时间内健康检查失败不触发重启
        self.startup_grace_seconds = startup_grace_seconds
        # 连续失败阈值：达到后才执行重启
        self.health_failures_before_restart = max(1, int(health_failures_before_restart))
        self.worker_host = worker_host
        self.manager_host = manager_host

        self.workers: List[WorkerProcess] = []
        self._rr_index: int = 0
        self._assign_lock = asyncio.Lock()
        self._pending_queue_len: int = 0
        self._shutdown: bool = False

        self._client: Optional[httpx.AsyncClient] = None
        # GPU 分配顺序（按显存占用从低到高）
        self._gpu_order: List[int] = []

    # -------------------------- Worker Lifecycle -------------------------- #
    def _start_one_worker(self, worker_id: int, port: int, gpu_index: Optional[int] = None) -> WorkerProcess:
        """启动一个 parse_server 实例，通过 start_server.sh 脚本。"""
        monkey_dir = os.path.dirname(os.path.abspath(__file__))
        log_dir = os.path.join(monkey_dir, "cluster_logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"worker_{port}.log")

        # 启动前先确保端口空闲，如被占用则尝试终止占用进程
        try:
            if self._is_port_open(self.worker_host, port):
                logger.warning(f"端口 {self.worker_host}:{port} 已被占用，尝试释放...")
                self._kill_port_processes(port, exclude_pids=[])
                self._wait_until_port_closed(self.worker_host, port, timeout_seconds=15.0)
        except Exception as _:
            # 忽略释放失败，仍尝试启动；后续健康检查会兜底
            pass

        env = os.environ.copy()
        env.update(
            {
                "HOST": self.worker_host,
                "PORT": str(port),
                "CONFIG": self.config_path,
                "SERVER_TYPE": "api",
                "WORKERS": "1",
                "PYTHONUNBUFFERED": "1",
            }
        )
        # 为子进程明确绑定 GPU，避免并发自选导致全部落在同一块 GPU
        if gpu_index is not None:
            env["CUDA_DEVICE"] = str(gpu_index)
        # 统一输出目录到仓库 backend/outputs，避免在不可写路径下创建
        repo_root = os.path.abspath(os.path.join(monkey_dir, os.pardir))
        backend_outputs = os.path.join(repo_root, "backend", "outputs")
        os.makedirs(backend_outputs, exist_ok=True)
        env["BACKEND_OUTPUT_DIR"] = backend_outputs

        # 使用 bash 显式执行，确保脚本兼容性
        cmd = ["bash", "start_server.sh"]

        stdout_fd = open(log_path, "ab", buffering=0)
        stderr_fd = stdout_fd  # 合并输出

        logger.info(
            f"启动 Worker#{worker_id} -> port={port}, gpu={'cpu' if gpu_index is None else gpu_index}"
        )
        proc = subprocess.Popen(
            cmd,
            cwd=monkey_dir,
            env=env,
            stdout=stdout_fd,
            stderr=stderr_fd,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )

        worker = WorkerProcess(
            worker_id=worker_id,
            port=port,
            process=proc,
            status="starting",
            last_ok_ts=0.0,
            restarted_times=0,
            current_requests=0,
            total_requests=0,
            log_path=log_path,
            start_ts=time.time(),
            consecutive_failures=0,
            gpu_index=gpu_index,
        )
        return worker

    def _stop_one_worker(self, worker: WorkerProcess, kill: bool = False) -> None:
        if worker.process is None:
            worker.status = "stopped"
            return
        try:
            if hasattr(os, "killpg") and worker.process.pid:
                try:
                    os.killpg(os.getpgid(worker.process.pid), signal.SIGKILL if kill else signal.SIGTERM)
                except Exception:
                    pass
            else:
                worker.process.terminate()
                if kill:
                    worker.process.kill()
        except Exception:
            pass
        worker.status = "stopped"
        # 等待端口释放，避免立即重启时端口仍被占用
        try:
            self._wait_until_port_closed(self.worker_host, worker.port, timeout_seconds=15.0)
        except Exception:
            pass

    def _is_port_open(self, host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except Exception:
            return False

    def _wait_until_port_closed(self, host: str, port: int, timeout_seconds: float = 15.0) -> None:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not self._is_port_open(host, port):
                return
            time.sleep(0.2)

    def _find_pids_listening(self, port: int) -> List[int]:
        """使用 ss 查询监听该端口的进程 PID 列表。"""
        try:
            out = subprocess.check_output(["ss", "-lntp"], text=True, stderr=subprocess.DEVNULL)
        except Exception:
            return []
        pids: List[int] = []
        for line in out.splitlines():
            if f":{port} " in line or line.endswith(f":{port}") or f":{port}\n" in line:
                # 解析 users:((("proc",pid=1234,fd=...))
                if "users:(" in line:
                    users_part = line.split("users:(", 1)[1]
                    # 提取所有 pid=xxxx
                    tokens = users_part.replace(")", " ").replace(",", " ").split()
                    for tok in tokens:
                        if tok.startswith("pid="):
                            pid_str = tok.split("=", 1)[1]
                            if pid_str.isdigit():
                                pids.append(int(pid_str))
        # 去重
        return sorted(set(pids))

    def _kill_port_processes(self, port: int, exclude_pids: List[int]) -> None:
        """终止占用端口的进程，先 TERM 后 KILL。"""
        pids = [pid for pid in self._find_pids_listening(port) if pid not in set(exclude_pids or [])]
        if not pids:
            return
        logger.warning(f"终止占用端口 {port} 的进程: {pids}")
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        # 等待短暂时间以释放端口
        t0 = time.time()
        while time.time() - t0 < 5.0:
            if not self._is_port_open(self.worker_host, port):
                break
            time.sleep(0.2)
        if self._is_port_open(self.worker_host, port):
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
            # 再等待释放
            self._wait_until_port_closed(self.worker_host, port, timeout_seconds=10.0)

    def _detect_gpus_sorted_by_mem(self) -> List[int]:
        """使用 nvidia-smi 获取按显存占用(升序)排序的 GPU 索引列表。
        失败时返回空列表，表示不进行显式分配。
        """
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return []

        pairs: List[Tuple[int, int]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            # 兼容 "0, 123" 或 "0,123"
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                idx = int(parts[0])
                used = int(parts[1])
                pairs.append((idx, used))
        if not pairs:
            return []
        # 先按显存占用升序，再按索引升序
        pairs.sort(key=lambda x: (x[1], x[0]))
        return [idx for idx, _ in pairs]

    def _log_contains_address_in_use(self, log_path: Optional[str]) -> bool:
        if not log_path or not os.path.isfile(log_path):
            return False
        try:
            with open(log_path, "rb") as f:
                try:
                    f.seek(-8192, os.SEEK_END)
                except Exception:
                    pass
                data = f.read().decode(errors="ignore").lower()
            return ("address already in use" in data) or ("errno 98" in data)
        except Exception:
            return False

    def _restart_worker(self, worker: WorkerProcess) -> None:
        port = worker.port
        wid = worker.worker_id
        self._stop_one_worker(worker, kill=True)
        # 如端口仍被占用，尝试主动释放
        try:
            if self._is_port_open(self.worker_host, port):
                self._kill_port_processes(port, exclude_pids=[worker.process.pid] if worker.process else [])
        except Exception:
            pass
        # 再次确认端口关闭
        try:
            self._wait_until_port_closed(self.worker_host, port, timeout_seconds=15.0)
        except Exception:
            pass
        # 重启时保持原 GPU 分配，避免在不同 GPU 间来回漂移
        new_worker = self._start_one_worker(wid, port, gpu_index=worker.gpu_index)
        new_worker.restarted_times = worker.restarted_times + 1
        # 原位替换
        idx = self.workers.index(worker)
        self.workers[idx] = new_worker

    async def start_all_workers(self) -> None:
        self.workers = []
        # 启动前侦测一次 GPU，并按显存占用升序排列
        self._gpu_order = self._detect_gpus_sorted_by_mem()
        for i in range(self.num_workers):
            port = self.worker_base_port + i
            gpu_index: Optional[int] = None
            if self._gpu_order:
                # 若 Worker 数量超过 GPU 数量，则按顺序循环复用
                gpu_index = self._gpu_order[i % len(self._gpu_order)]
            worker = self._start_one_worker(i, port, gpu_index=gpu_index)
            self.workers.append(worker)

    async def stop_all_workers(self) -> None:
        for w in list(self.workers):
            self._stop_one_worker(w, kill=True)

    # -------------------------- Health Checking -------------------------- #
    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0, write=60.0, connect=5.0))
        return self._client

    async def check_worker_health(self, worker: WorkerProcess) -> bool:
        """调用 /health 检查健康，或检查子进程是否已退出。"""
        # 子进程是否仍在
        if worker.process and (worker.process.poll() is not None):
            worker.status = "stopped"
            worker.consecutive_failures += 1
            # 若日志中出现端口占用，主动释放
            if self._log_contains_address_in_use(worker.log_path):
                try:
                    self._kill_port_processes(worker.port, exclude_pids=[])
                except Exception:
                    pass
            return False

        try:
            client = await self._ensure_client()
            url = f"http://{self.worker_host}:{worker.port}/health"
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "healthy":
                    worker.status = "healthy"
                    worker.last_ok_ts = time.time()
                    worker.consecutive_failures = 0
                    return True
        except Exception:
            worker.consecutive_failures += 1

        worker.status = "unhealthy"
        return False

    async def health_loop(self) -> None:
        """周期性健康检查 & 自动拉起。"""
        while not self._shutdown:
            for worker in list(self.workers):
                # 冷启动宽限期内，容忍健康检查失败
                within_grace = False
                if worker.process and (worker.process.poll() is None):
                    within_grace = (time.time() - worker.start_ts) < self.startup_grace_seconds

                ok = await self.check_worker_health(worker)
                if not ok:
                    if within_grace:
                        # 在宽限期内且子进程仍在运行，认为仍在启动中，暂不重启
                        if worker.status != "starting":
                            worker.status = "starting"
                        logger.info(
                            f"Worker#{worker.worker_id}@{worker.port} 启动中(宽限{int(self.startup_grace_seconds)}s)，暂不重启"
                        )
                    else:
                        if worker.consecutive_failures < self.health_failures_before_restart:
                            logger.warning(
                                f"Worker#{worker.worker_id}@{worker.port} 健康检查失败 {worker.consecutive_failures}/"
                                f"{self.health_failures_before_restart}，暂不重启"
                            )
                        else:
                            logger.warning(f"检测到 Worker#{worker.worker_id}@{worker.port} 连续失败，执行重启...")
                            try:
                                self._restart_worker(worker)
                            except Exception as e:
                                logger.exception(f"重启 Worker 失败: {e}")
            await asyncio.sleep(self.health_interval_seconds)

    # -------------------------- Request Assignment ----------------------- #
    def _choose_next_worker(self) -> Optional[WorkerProcess]:
        if not self.workers:
            return None
        num = len(self.workers)
        for _ in range(num):
            worker = self.workers[self._rr_index % num]
            self._rr_index = (self._rr_index + 1) % num
            if worker.status == "healthy" and worker.current_requests < self.max_concurrency_per_worker:
                return worker
        return None

    async def acquire_worker(self, wait_interval: float = 0.2) -> WorkerProcess:
        """轮询选择可用 Worker；若无空闲则排队等待。"""
        added_to_queue = False
        while True:
            async with self._assign_lock:
                worker = self._choose_next_worker()
                if worker is not None:
                    worker.current_requests += 1
                    worker.total_requests += 1
                    if added_to_queue and self._pending_queue_len > 0:
                        self._pending_queue_len -= 1
                    return worker
                # 没有可用，首次进入等待则记录排队
                if not added_to_queue:
                    self._pending_queue_len += 1
                    added_to_queue = True
            await asyncio.sleep(wait_interval)

    async def release_worker(self, worker: WorkerProcess) -> None:
        async with self._assign_lock:
            if worker.current_requests > 0:
                worker.current_requests -= 1

    # -------------------------- Proxy Logic ------------------------------ #
    async def proxy_parse(
        self,
        file: UploadFile,
        task: Optional[str],
        output_format: str,
    ) -> Response:
        worker = await self.acquire_worker()
        try:
            client = await self._ensure_client()
            target_url = f"http://{self.worker_host}:{worker.port}/parse"

            file_bytes = await file.read()
            files = {
                "file": (file.filename or "uploaded", file_bytes, file.content_type or "application/octet-stream"),
            }
            data = {}
            if task is not None:
                data["task"] = task
            if output_format:
                data["output_format"] = output_format

            resp = await client.post(target_url, files=files, data=data)

            ct = resp.headers.get("content-type", "application/json")
            if "application/zip" in ct:
                content = resp.content
                headers = {k: v for k, v in resp.headers.items() if k.lower() in {"content-type", "content-disposition"}}
                return Response(content=content, status_code=resp.status_code, headers=headers)
            else:
                # 默认 JSON
                return JSONResponse(status_code=resp.status_code, content=resp.json())
        finally:
            await self.release_worker(worker)

    async def proxy_parse_batch(self, files: List[UploadFile], task: Optional[str]) -> Response:
        worker = await self.acquire_worker()
        try:
            client = await self._ensure_client()
            target_url = f"http://{self.worker_host}:{worker.port}/parse_batch"

            multipart_files: List[Tuple[str, Tuple[str, bytes, str]]] = []
            for f in files:
                content = await f.read()
                multipart_files.append((
                    "files",
                    (f.filename or "uploaded", content, f.content_type or "application/octet-stream"),
                ))

            data = {}
            if task is not None:
                data["task"] = task

            resp = await client.post(target_url, files=multipart_files, data=data)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
        finally:
            await self.release_worker(worker)

    async def proxy_parse_download(self, file: UploadFile, convert_table: Optional[bool]) -> Response:
        """转发到 worker 的 /parse/download，返回 ZIP 文件。
        注意：SERVER_TYPE=api 的 worker 接口签名为 (file, convert_table: bool)。
        """
        worker = await self.acquire_worker()
        try:
            client = await self._ensure_client()
            target_url = f"http://{self.worker_host}:{worker.port}/parse/download"

            file_bytes = await file.read()
            files = {
                "file": (file.filename or "uploaded", file_bytes, file.content_type or "application/octet-stream"),
            }
            data = {}
            if convert_table is not None:
                # FastAPI 会把表单布尔解析为字符串 'true'/'false'
                data["convert_table"] = str(bool(convert_table)).lower()
            else:
                data["convert_table"] = "true"

            resp = await client.post(target_url, files=files, data=data)
            # 直接透传 ZIP 内容与关键响应头
            content = resp.content
            headers = {k: v for k, v in resp.headers.items() if k.lower() in {"content-type", "content-disposition"}}
            return Response(content=content, status_code=resp.status_code, headers=headers)
        finally:
            await self.release_worker(worker)

    # -------------------------- Metrics ---------------------------------- #
    def get_metrics(self) -> Dict[str, object]:
        return {
            "manager_port": self.manager_port,
            "num_workers_target": self.num_workers,
            "max_concurrency_per_worker": self.max_concurrency_per_worker,
            "pending_queue_len": self._pending_queue_len,
            "workers": [
                {
                    "worker_id": w.worker_id,
                    "port": w.port,
                    "status": w.status,
                    "pid": w.process.pid if w.process else None,
                    "last_ok_ts": w.last_ok_ts,
                    "current_requests": w.current_requests,
                    "total_requests": w.total_requests,
                    "restarted_times": w.restarted_times,
                    "log_path": w.log_path,
                    "gpu_index": w.gpu_index,
                }
                for w in self.workers
            ],
        }


# ------------------------------ FastAPI App ------------------------------ #
def build_app(manager: ClusterManager) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Startup
        await manager.start_all_workers()
        # 后台健康检查任务
        health_task = asyncio.create_task(manager.health_loop())
        try:
            yield
        finally:
            manager._shutdown = True
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            await manager.stop_all_workers()
            if manager._client is not None:
                await manager._client.aclose()

    app = FastAPI(title="MonkeyOCR Cluster Manager", lifespan=lifespan)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        m = manager.get_metrics()
        rows = []
        for w in m["workers"]:
            rows.append(
                f"<tr>"
                f"<td>{w['worker_id']}</td>"
                f"<td>{w['port']}</td>"
                f"<td>{w.get('gpu_index') if w.get('gpu_index') is not None else '-'}" + "</td>"
                f"<td>{w['status']}</td>"
                f"<td>{w['pid']}</td>"
                f"<td>{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(w['last_ok_ts'])) if w['last_ok_ts'] else '-'}" + "</td>"
                f"<td>{w['current_requests']}</td>"
                f"<td>{w['total_requests']}</td>"
                f"<td>{w['restarted_times']}</td>"
                f"<td>{w['log_path'] or ''}</td>"
                f"</tr>"
            )
        table_html = """
        <table border="1" cellspacing="0" cellpadding="6">
          <thead>
            <tr>
              <th>WorkerID</th>
              <th>Port</th>
              <th>GPU</th>
              <th>Status</th>
              <th>PID</th>
              <th>Last OK</th>
              <th>In-Flight</th>
              <th>Total</th>
              <th>Restarts</th>
              <th>Log</th>
            </tr>
          </thead>
          <tbody>
            {rows}
          </tbody>
        </table>
        """.replace("{rows}", "\n".join(rows))

        html = f"""
        <html>
          <head>
            <meta charset="utf-8"/>
            <title>MonkeyOCR Cluster Manager</title>
          </head>
          <body>
            <h2>MonkeyOCR 集群管理</h2>
            <div>Manager Port: {m['manager_port']}</div>
            <div>目标 Worker 数: {m['num_workers_target']}</div>
            <div>每 Worker 最大并发: {m['max_concurrency_per_worker']}</div>
            <div>排队请求数: <b>{m['pending_queue_len']}</b></div>
            <hr/>
            {table_html}
          </body>
        </html>
        """
        return html

    @app.get("/metrics")
    async def metrics() -> Dict[str, object]:
        return manager.get_metrics()

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/parse")
    async def proxy_parse(
        file: UploadFile = File(...),
        task: Optional[str] = Form(None),
        output_format: str = Form("json"),
    ) -> Response:
        return await manager.proxy_parse(file=file, task=task, output_format=output_format)

    @app.post("/parse_batch")
    async def proxy_parse_batch(
        files: List[UploadFile] = File(...),
        task: Optional[str] = Form(None),
    ) -> Response:
        return await manager.proxy_parse_batch(files=files, task=task)

    # 统一下载端口：/parse/download -> 轮询到后端 worker
    @app.post("/parse/download")
    async def proxy_parse_download(
        file: UploadFile = File(...),
        convert_table: Optional[bool] = Form(True),
    ) -> Response:
        return await manager.proxy_parse_download(file=file, convert_table=convert_table)

    return app


def _parse_args(argv: Optional[List[str]] = None):
    import argparse

    parser = argparse.ArgumentParser(description="MonkeyOCR Cluster Manager")
    parser.add_argument("--num-workers", type=int, default=int(os.getenv("NUM_WORKERS", "2")))
    parser.add_argument("--worker-base-port", type=int, default=int(os.getenv("WORKER_BASE_PORT", "7861")))
    parser.add_argument("--manager-port", type=int, default=int(os.getenv("MANAGER_PORT", "9000")))
    parser.add_argument("--config", type=str, default=os.getenv("CONFIG", "yolo_model_configs.yaml"))
    parser.add_argument("--max-concurrency", type=int, default=int(os.getenv("MAX_CONCURRENCY", "1")))
    parser.add_argument("--health-interval", type=float, default=float(os.getenv("HEALTH_INTERVAL", "10")))
    parser.add_argument("--startup-grace", type=float, default=float(os.getenv("STARTUP_GRACE", "60")))
    parser.add_argument(
        "--health-failures-before-restart",
        type=int,
        default=int(os.getenv("HEALTH_FAILS_BEFORE_RESTART", "3")),
    )
    parser.add_argument("--host", type=str, default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--worker-host", type=str, default=os.getenv("WORKER_HOST", "127.0.0.1"))
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    manager = ClusterManager(
        num_workers=args.num_workers,
        worker_base_port=args.worker_base_port,
        manager_port=args.manager_port,
        config_path=args.config,
        max_concurrency_per_worker=args.max_concurrency,
        health_interval_seconds=args.health_interval,
        startup_grace_seconds=args.startup_grace,
        health_failures_before_restart=args.health_failures_before_restart,
        worker_host=args.worker_host,
        manager_host=args.host,
    )

    app = build_app(manager)
    uvicorn.run(app, host=args.host, port=args.manager_port, reload=False)


if __name__ == "__main__":
    main()


