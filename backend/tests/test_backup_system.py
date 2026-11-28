import gzip
import json
from datetime import datetime, timedelta
from zipfile import ZipFile

from app.api import routes_monthly
from scripts import backup_db
from test_living import account, client  # noqa: F401


def test_archive_and_retention(tmp_path, monkeypatch):
    old = datetime.now(backup_db.LOCAL) - timedelta(days=15)
    expired = tmp_path / old.strftime("shared_living_%Y%m%d_%H%M%S.zip")
    expired.write_bytes(b"expired")
    unrelated = tmp_path / "other.zip"
    unrelated.write_bytes(b"other")
    monkeypatch.setenv("BACKUP_RETENTION_DAYS", "14")
    monkeypatch.setattr(backup_db, "running_services", lambda: {"mysql", "mongo"})
    monkeypatch.setattr(backup_db, "mysql_dump",
                        lambda path, *_: path.write_bytes(b"CREATE TABLE example ();\n"))
    monkeypatch.setattr(backup_db, "run_command", lambda args, path: path.write_bytes(gzip.compress(b"mongo archive")))
    item = backup_db.create_backup(tmp_path)
    with ZipFile(tmp_path / item["filename"]) as archive:
        assert set(archive.namelist()) == {"mysql.sql", "mongo.archive.gz", "backup_manifest.json"}
        manifest = json.loads(archive.read("backup_manifest.json"))
        assert manifest["table_counts"] == {"mysql": 1}
        assert gzip.decompress(archive.read("mongo.archive.gz")) == b"mongo archive"
        assert manifest["file_sizes"]["mongo.archive.gz"] == archive.getinfo("mongo.archive.gz").file_size
    assert not expired.exists()
    assert unrelated.exists()
    assert backup_db.archives(tmp_path) == [item]


def test_system_owner_required(client, monkeypatch):
    from app.core.config import settings

    _, owner = account(client, settings().owner_username)
    _, other = account(client, "backup-other")
    item = {"filename": "test.zip", "timestamp": "2026-01-01T02:00:00+07:00", "size": 42}
    monkeypatch.setattr(routes_monthly.backup_db, "archives", lambda *args: [] if args else [item])
    monkeypatch.setattr(routes_monthly.backup_db, "create_backup", lambda: item)
    url = "/api/v1/system/backups"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=other).status_code == 403
    assert client.post(url + "/run", headers=other).status_code == 403
    assert client.get(url, headers=owner).json() == [item]
    response = client.post(url + "/run", headers=owner)
    assert response.status_code == 201
    assert response.json() == item
