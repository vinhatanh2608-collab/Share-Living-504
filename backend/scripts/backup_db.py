import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

BACKEND = Path(__file__).resolve().parents[1]
BACKUPS = BACKEND.parent / "backups"
LOCAL = timezone(timedelta(hours=7))
ARCHIVE = re.compile(r"shared_living_(\d{8}_\d{6})\.zip")
logger = logging.getLogger(__name__)


def archives(directory=BACKUPS):
    result = []
    for path in sorted(directory.glob("shared_living_*.zip"), reverse=True):
        match = ARCHIVE.fullmatch(path.name)
        if match and path.is_file() and not path.is_symlink():
            try:
                stamp = datetime.strptime(match[1], "%Y%m%d_%H%M%S").replace(tzinfo=LOCAL)
                result.append({"filename": path.name, "timestamp": stamp.isoformat(), "size": path.stat().st_size})
            except (ValueError, FileNotFoundError):
                continue
    return result


def prune(directory, now, days):
    for item in archives(directory):
        if datetime.fromisoformat(item["timestamp"]) < now - timedelta(days=days):
            (directory / item["filename"]).unlink(missing_ok=True)


@contextmanager
def backup_lock(directory):
    # OS releases the lock on process exit, including after a crash.
    with (directory / ".backup.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def run_command(args, target, env=None):
    with target.open("wb") as output:
        subprocess.run(args, cwd=BACKEND, env=env, stdout=output, stderr=subprocess.DEVNULL,
                       check=True, timeout=300)


def running_services():
    try:
        result = subprocess.run(["docker", "compose", "ps", "--status", "running", "--services"],
                                cwd=BACKEND, capture_output=True, text=True, check=True, timeout=20)
        return set(result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError):
        return set()


def mysql_dump(target, services, config):
    options = ["--single-transaction", "--quick", "--no-tablespaces", "--set-gtid-purged=OFF"]
    if "mysql" in services:
        script = 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysqldump -u "$MYSQL_USER" "$@" -- "$MYSQL_DATABASE"'
        run_command(["docker", "compose", "exec", "-T", "mysql", "sh", "-c", script, "dump", *options], target)
    else:
        url = make_url(config["MYSQL_URL"])
        env = {**os.environ, "MYSQL_PWD": url.password or ""}
        run_command(["mysqldump", *options, "--host", url.host or "127.0.0.1", "--port",
                     str(url.port or 3306), "--user", url.username or "", "--", url.database], target, env)


def create_backup(directory=BACKUPS, *, daily=False):
    config = {**dotenv_values(BACKEND / ".env"), **os.environ}
    days = int(config.get("BACKUP_RETENTION_DAYS") or 14)
    if days < 1:
        raise ValueError("BACKUP_RETENTION_DAYS must be at least 1")
    directory.mkdir(parents=True, exist_ok=True)
    with backup_lock(directory):
        now = datetime.now(LOCAL)
        if daily and (now.hour < 2 or any(datetime.fromisoformat(row["timestamp"]).date() == now.date()
                                          for row in archives(directory))):
            return None
        stamp = now.replace(microsecond=0)
        while (directory / stamp.strftime("shared_living_%Y%m%d_%H%M%S.zip")).exists():
            stamp += timedelta(seconds=1)
        name = stamp.strftime("shared_living_%Y%m%d_%H%M%S.zip")
        with TemporaryDirectory(dir=directory) as scratch:
            tmp = Path(scratch)
            mysql = tmp / "mysql.sql"
            services = running_services()
            mysql_dump(mysql, services, config)
            with mysql.open("rb") as dump:
                tables = sum(line.startswith(b"CREATE TABLE ") for line in dump)
            files = [mysql]
            if "mongo" in services:
                mongo = tmp / "mongo.archive"
                script = ('exec mongodump --archive --authenticationDatabase admin '
                          '--username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --db "$1"')
                run_command(["docker", "compose", "exec", "-T", "mongo", "sh", "-c", script, "dump",
                             config.get("MONGO_DATABASE") or "dorm"], mongo)
                files.append(mongo)
            manifest = {"timestamp": stamp.isoformat(), "table_counts": {"mysql": tables},
                        "mongo_status": "included" if len(files) > 1 else "skipped_not_running",
                        "file_sizes": {file.name: file.stat().st_size for file in files}}
            with ZipFile(tmp / name, "w", ZIP_DEFLATED) as archive:
                for file in files:
                    archive.write(file, file.name)
                archive.writestr("backup_manifest.json", json.dumps(manifest, indent=2))
            (tmp / name).replace(directory / name)
        prune(directory, now, days)
        return {"filename": name, "timestamp": stamp.isoformat(), "size": (directory / name).stat().st_size}


async def backup_forever():
    while True:
        try:
            await asyncio.to_thread(create_backup, daily=True)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            logger.warning("Daily database backup failed (%s)", type(exc).__name__)
        await asyncio.sleep(300)


if __name__ == "__main__":
    try:
        print(json.dumps(create_backup()))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"Backup failed ({type(exc).__name__}). Check Docker and database configuration.") from None
