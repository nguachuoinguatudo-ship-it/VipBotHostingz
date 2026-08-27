from __future__ import annotations

import asyncio
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import Database, utc_now


@dataclass(slots=True)
class HostedProcess:
    bot_id: int
    process: asyncio.subprocess.Process
    workdir: Path
    entry_point: Path


class HostingManager:
    def __init__(self, db: Database, data_dir: Path) -> None:
        self.db = db
        self.data_dir = data_dir
        self.bots_dir = data_dir / "bots"
        self.tmp_dir = data_dir / "tmp"
        self.vendor_dir_name = "vendor"
        self.processes: dict[int, HostedProcess] = {}

    def prepare(self) -> None:
        self.bots_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

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

        workdir = source_path if source_path.is_dir() else source_path.parent
        vendor_dir = workdir / self.vendor_dir_name
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        if vendor_dir.exists():
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{vendor_dir}{os.pathsep}{existing}" if existing else str(vendor_dir)

        requirements = workdir / "requirements.txt"
        if requirements.exists():
            await self.install_requirements(workdir, requirements, vendor_dir)

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            entry_point.as_posix(),
            cwd=workdir.as_posix(),
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.processes[bot_id] = HostedProcess(
            bot_id=bot_id,
            process=process,
            workdir=workdir,
            entry_point=entry_point,
        )
        await self.db.update_bot_status(bot_id, "running", process.pid, utc_now())
        return True

    async def stop_bot(self, bot_id: int) -> bool:
        hosted = self.processes.pop(bot_id, None)
        if hosted and hosted.process.returncode is None:
            hosted.process.terminate()
            try:
                await asyncio.wait_for(hosted.process.wait(), timeout=8)
            except TimeoutError:
                hosted.process.kill()
                await hosted.process.wait()
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
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=workdir.as_posix(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await process.wait()

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
