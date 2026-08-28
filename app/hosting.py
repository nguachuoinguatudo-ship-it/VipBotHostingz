from __future__ import annotations

import asyncio
import os
import shutil
import sys
import zipfile
import contextlib
import signal
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .db import Database, utc_now


@dataclass(slots=True)
class HostedProcess:
    bot_id: int
    process: asyncio.subprocess.Process
    workdir: Path
    entry_point: Path
    log_file: BinaryIO


def _apply_resource_limits(memory_limit_mb: int, cpu_limit_seconds: int) -> None:
    try:
        import resource
    except ImportError:
        return
    if memory_limit_mb > 0:
        limit = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    if cpu_limit_seconds > 0:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_seconds, cpu_limit_seconds))


def tail_log(path: Path, lines: int = 60) -> str:
    if not path.exists():
        return "Log belum tersedia."
    content = path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:]) or "Log masih kosong."


class HostingManager:
    def __init__(self, db: Database, data_dir: Path) -> None:
        self.db = db
        self.data_dir = data_dir
        self.bots_dir = data_dir / "bots"
        self.tmp_dir = data_dir / "tmp"
        self.vendor_dir_name = "vendor"
        self.processes: dict[int, HostedProcess] = {}
        self._stop_requested: set[int] = set()
        self.auto_restart = True
        self.memory_limit_mb = 512
        self.cpu_limit_seconds = 0
        self.backend = "local"
        self.docker_image = "python:3.12-slim"

    def prepare(self) -> None:
        self.bots_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def configure(
        self,
        auto_restart: bool,
        memory_limit_mb: int,
        cpu_limit_seconds: int,
        backend: str = "local",
        docker_image: str = "python:3.12-slim",
    ) -> None:
        self.auto_restart = auto_restart
        self.memory_limit_mb = memory_limit_mb
        self.cpu_limit_seconds = cpu_limit_seconds
        self.backend = backend
        self.docker_image = docker_image

    async def restart_running_bots(self) -> None:
        bots = await self.db.list_all_bots()
        for bot in bots:
            if bot["status"] == "running":
                source = Path(bot["source_path"])
                entry = Path(bot["entry_point"])
                if source.exists() and entry.exists():
                    await self.start_bot(int(bot["id"]), source, entry, resume=True)

    async def start_bot(self, bot_id: int, source_path: Path, entry_point: Path, resume: bool = False) -> bool:
        if bot_id in self.processes:
            return True

        source_path = source_path.resolve()
        entry_point = entry_point.resolve()
        workdir = source_path if source_path.is_dir() else source_path.parent
        if self.backend == "docker":
            return await self._start_docker(bot_id, workdir, entry_point)
        vendor_dir = workdir / self.vendor_dir_name
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if vendor_dir.exists():
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{vendor_dir}{os.pathsep}{existing}" if existing else str(vendor_dir)

        requirements = workdir / "requirements.txt"
        if not requirements.exists():
            nested_requirements = sorted(entry_point.parent.rglob("requirements.txt"))
            requirements = nested_requirements[0] if nested_requirements else requirements
        if requirements.exists():
            await self.install_requirements(workdir, requirements, vendor_dir)

        log_path = workdir / "runtime.log"
        self._rotate_log(log_path)
        log_file = log_path.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-u",
                entry_point.as_posix(),
                cwd=workdir.as_posix(),
                env=env,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=lambda: _apply_resource_limits(self.memory_limit_mb, self.cpu_limit_seconds),
            )
        except Exception:
            log_file.close()
            raise
        self.processes[bot_id] = HostedProcess(
            bot_id=bot_id,
            process=process,
            workdir=workdir,
            entry_point=entry_point,
            log_file=log_file,
        )
        await self.db.update_bot_status(bot_id, "running", process.pid, utc_now())
        await asyncio.sleep(0.5)
        if process.returncode is not None:
            self.processes.pop(bot_id, None)
            log_file.close()
            await self.db.update_bot_status(bot_id, "stopped")
            raise RuntimeError(f"Bot berhenti saat start:\n{tail_log(log_path)}")
        asyncio.create_task(self._watch_process(self.processes[bot_id]))
        return True

    async def _start_docker(self, bot_id: int, workdir: Path, entry_point: Path) -> bool:
        relative_entry = entry_point.relative_to(workdir).as_posix()
        requirements = workdir / "requirements.txt"
        if not requirements.exists():
            nested = sorted(entry_point.parent.rglob("requirements.txt"))
            requirements = nested[0] if nested else requirements
        install = ""
        if requirements.exists():
            relative_requirements = requirements.relative_to(workdir).as_posix()
            install = f"python -m pip install --no-cache-dir -r {shlex.quote(relative_requirements)} && "
        container_name = f"viphosting_bot_{bot_id}"
        command = [
            "docker", "run", "--rm", "--name", container_name,
            "--memory", f"{self.memory_limit_mb}m", "--pids-limit", "128",
            "-v", f"{workdir.as_posix()}:/app:rw", "-w", "/app",
            "-e", "PYTHONUNBUFFERED=1", self.docker_image, "sh", "-lc",
            f"{install}exec python -u {shlex.quote(relative_entry)}",
        ]
        if self.cpu_limit_seconds > 0:
            command[7:7] = ["--cpus", str(max(0.1, self.cpu_limit_seconds / 60))]
        log_path = workdir / "runtime.log"
        self._rotate_log(log_path)
        log_file = log_path.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=log_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception:
            log_file.close()
            raise
        self.processes[bot_id] = HostedProcess(bot_id, process, workdir, entry_point, log_file)
        await self.db.update_bot_status(bot_id, "running", process.pid, utc_now())
        await asyncio.sleep(1)
        if process.returncode is not None:
            self.processes.pop(bot_id, None)
            log_file.close()
            await self.db.update_bot_status(bot_id, "stopped")
            raise RuntimeError(f"Container berhenti saat start:\n{tail_log(log_path)}")
        asyncio.create_task(self._watch_process(self.processes[bot_id]))
        return True

    async def _watch_process(self, hosted: HostedProcess) -> None:
        return_code = await hosted.process.wait()
        current = self.processes.get(hosted.bot_id)
        if current is not hosted:
            return
        self.processes.pop(hosted.bot_id, None)
        with contextlib.suppress(Exception):
            hosted.log_file.close()
        if hosted.bot_id in self._stop_requested:
            self._stop_requested.discard(hosted.bot_id)
            await self.db.update_bot_status(hosted.bot_id, "stopped")
            return
        await self.db.update_bot_status(hosted.bot_id, "crashed")
        if self.auto_restart and return_code != 0:
            await asyncio.sleep(2)
            row = await self.db.get_bot(hosted.bot_id)
            if row and row["status"] == "crashed":
                await self.start_bot(hosted.bot_id, Path(row["source_path"]), Path(row["entry_point"]), resume=True)

    def _rotate_log(self, path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
        if path.exists() and path.stat().st_size >= max_bytes:
            rotated = path.with_suffix(path.suffix + ".1")
            with contextlib.suppress(OSError):
                rotated.unlink()
            path.rename(rotated)

    async def stop_bot(self, bot_id: int) -> bool:
        hosted = self.processes.pop(bot_id, None)
        if hosted and hosted.process.returncode is None:
            self._stop_requested.add(bot_id)
            if self.backend == "docker":
                with contextlib.suppress(Exception):
                    stop_process = await asyncio.create_subprocess_exec(
                        "docker", "stop", "-t", "8", f"viphosting_bot_{bot_id}",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(stop_process.wait(), timeout=10)
            hosted.process.terminate()
            try:
                await asyncio.wait_for(hosted.process.wait(), timeout=8)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(hosted.process.pid, signal.SIGKILL)
                await hosted.process.wait()
        if hosted:
            hosted.log_file.close()
        await self.db.update_bot_status(bot_id, "stopped")
        return True

    async def stop_all(self) -> None:
        for bot_id in list(self.processes):
            await self.stop_bot(bot_id)
        await self.db.stop_all_bots()

    async def delete_bot(self, bot_id: int) -> None:
        await self.stop_bot(bot_id)
        bot_row = await self.db.get_bot(bot_id)
        bot_dir = Path(bot_row["source_path"]) if bot_row else self.bots_dir / str(bot_id)
        if bot_dir.exists():
            shutil.rmtree(bot_dir, ignore_errors=True)
        await self.db.delete_bot(bot_id)

    async def install_requirements(self, workdir: Path, requirements: Path, vendor_dir: Path) -> None:
        vendor_dir.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--upgrade",
            "--ignore-installed",
            "--target",
            vendor_dir.as_posix(),
            "-r",
            requirements.as_posix(),
        ]
        install_log_path = workdir / "install.log"
        install_log = install_log_path.open("ab", buffering=0)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workdir.as_posix(),
                stdout=install_log,
                stderr=asyncio.subprocess.STDOUT,
            )
            await process.wait()
        finally:
            install_log.close()
        if process.returncode != 0:
            raise RuntimeError(f"Gagal memasang requirements:\n{tail_log(install_log_path)}")

    async def save_uploaded_bot(
        self,
        owner_id: int,
        filename: str,
        payload: bytes,
    ) -> tuple[int, Path, Path, str]:
        safe_name = filename.replace("/", "_").replace("\\", "_")
        bot_id_dir = self.bots_dir / str(owner_id) / f"{datetime.now(timezone.utc).timestamp():.0f}_{safe_name}"
        bot_id_dir.mkdir(parents=True, exist_ok=True)

        kind = "zip" if safe_name.lower().endswith(".zip") else "py"
        source_root = bot_id_dir

        if kind == "zip":
            archive = bot_id_dir / safe_name
            archive.write_bytes(payload)
            with zipfile.ZipFile(archive) as zip_file:
                zip_file.extractall(bot_id_dir)
            entry_point = self.find_entry_point(bot_id_dir)
            if entry_point is None:
                raise ValueError("ZIP tidak menemukan file .py utama")
            source_path = entry_point
        else:
            source_path = bot_id_dir / "main.py"
            source_path.write_bytes(payload)
            entry_point = source_path

        bot_id = await self.db.create_bot(
            owner_id=owner_id,
            name=safe_name.rsplit(".", 1)[0],
            kind=kind,
            source_path=source_root.as_posix(),
            entry_point=entry_point.as_posix(),
        )
        return bot_id, source_root, entry_point, kind

    def find_entry_point(self, root: Path) -> Path | None:
        preferred = ["main.py", "bot.py", "app.py", "__main__.py"]
        for name in preferred:
            path = root / name
            if path.exists():
                return path
        py_files = sorted(root.rglob("*.py"))
        return py_files[0] if py_files else None

    def process_is_running(self, bot_id: int) -> bool:
        hosted = self.processes.get(bot_id)
        return bool(hosted and hosted.process.returncode is None)
