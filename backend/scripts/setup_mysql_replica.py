"""Seed an empty Compose replica from a consistent primary snapshot."""
from secrets import token_hex
import subprocess
from tempfile import TemporaryFile

from scripts.backup_db import BACKEND


def mysql(service, args, *, stdin=None, stdout=None):
    command = ["docker", "compose", "--profile", "ha", "exec", "-T", service,
               "sh", "-c", 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec "$@"', "mysql-ha", *args]
    return subprocess.run(command, cwd=BACKEND, stdin=stdin, stdout=stdout, check=True, timeout=600)


def statement(service, sql):
    with TemporaryFile() as stream:
        stream.write(sql.encode())
        stream.seek(0)
        mysql(service, ["mysql", "-uroot"], stdin=stream)


def main():
    with TemporaryFile() as status:
        mysql("mysql-replica", ["mysql", "-uroot", "-N", "-e", "SHOW REPLICA STATUS"], stdout=status)
        if status.tell():
            print("Replica already configured. Use SHOW REPLICA STATUS to inspect recovery.")
            return
    with TemporaryFile() as count:
        mysql("mysql-replica", ["mysql", "-uroot", "-N", "-e",
              "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='dorm'"], stdout=count)
        count.seek(0)
        if count.read().strip() != b"0":
            raise SystemExit("Replica dorm database is not empty. Stop and review the volume before seeding.")

    password = token_hex(16)  # MySQL replication passwords are capped at 32 characters.
    statement("mysql", f"""CREATE USER IF NOT EXISTS 'living_replica'@'%' IDENTIFIED BY '{password}';
ALTER USER 'living_replica'@'%' IDENTIFIED BY '{password}';
GRANT REPLICATION SLAVE ON *.* TO 'living_replica'@'%';
""")
    with TemporaryFile() as dump:
        mysql("mysql", ["mysqldump", "-uroot", "--single-transaction", "--no-tablespaces",
              "--set-gtid-purged=ON", "--databases", "dorm"], stdout=dump)
        dump.seek(0)
        mysql("mysql-replica", ["mysql", "-uroot"], stdin=dump)
    statement("mysql-replica", f"""SET SESSION sql_log_bin=0;
GRANT REPLICATION CLIENT ON *.* TO 'dorm'@'%';
CHANGE REPLICATION SOURCE TO SOURCE_HOST='mysql', SOURCE_USER='living_replica',
SOURCE_PASSWORD='{password}', SOURCE_AUTO_POSITION=1, GET_SOURCE_PUBLIC_KEY=1;
START REPLICA;
""")
    print("Replica started. Confirm both replication threads are running before enabling DATABASE_READ_URL.")


if __name__ == "__main__":
    main()
