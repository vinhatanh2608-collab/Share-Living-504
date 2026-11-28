import asyncio
import logging
from time import monotonic

from sqlalchemy.exc import DBAPIError, SQLAlchemyError, TimeoutError as PoolTimeout
from threading import Lock

from pymongo.errors import ConnectionFailure, NetworkTimeout
from pymongo.monitoring import ConnectionPoolListener
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from motor.motor_asyncio import AsyncIOMotorClient
from .config import settings
from ..models import archive  # Register archive tables before create_all.
from ..models.mysql_models import Base
from ..models.living import PERFORMANCE_INDEXES


logger = logging.getLogger(__name__)

def initialize_mysql_schema(connection):
    Base.metadata.create_all(connection)
    inspector = inspect(connection)
    if "note" not in {column["name"] for column in inspector.get_columns("group_payment_proofs")}:
        connection.execute(text("ALTER TABLE group_payment_proofs ADD COLUMN note VARCHAR(300) NULL"))
    group_columns = {column["name"] for column in inspector.get_columns("living_groups")}
    for name, definition in (
        ("auto_monthly_reminder", "BOOLEAN NOT NULL DEFAULT 1"),
        ("reminder_hour", "INTEGER NOT NULL DEFAULT 20"),
        ("last_reminded_period", "VARCHAR(7) NULL"),
    ):
        if name not in group_columns:
            connection.execute(text(f"ALTER TABLE living_groups ADD COLUMN {name} {definition}"))
    if "transfer_amount" not in {column["name"] for column in inspector.get_columns("group_payment_proofs")}:
        connection.execute(text("ALTER TABLE group_payment_proofs ADD COLUMN transfer_amount BIGINT NULL"))
    # create_all skips indexes on tables that already exist.
    for index in PERFORMANCE_INDEXES:
        existing = inspector.get_indexes(index.table.name)
        if not any(item["name"] == index.name for item in existing):
            index.create(connection)


class MongoPoolMetrics(ConnectionPoolListener):
    def __init__(self, capacity):
        self.capacity = capacity
        self.lock = Lock()
        self.checked_out = 0
        self.checkout_failures = 0

    def connection_checked_out(self, event):
        with self.lock:
            self.checked_out += 1

    def connection_checked_in(self, event):
        with self.lock:
            self.checked_out -= 1

    def connection_check_out_failed(self, event):
        with self.lock:
            self.checkout_failures += 1

    def snapshot(self):
        with self.lock:
            return {"checked_out": self.checked_out, "capacity_per_server": self.capacity,
                    "checkout_failures": self.checkout_failures}

    # PyMongo requires listeners to implement all pool event callbacks.
    def ignore(self, event):
        pass

    pool_created = pool_ready = pool_cleared = pool_closed = ignore
    connection_created = connection_ready = connection_closed = connection_check_out_started = ignore


class Resources:
    def __init__(self):
        cfg = settings()
        self.cfg = cfg
        self.engine = self.make_engine(cfg.mysql_url, cfg.mysql_pool_size, cfg.mysql_max_overflow)
        self.sql = async_sessionmaker(self.engine, expire_on_commit=False)
        self.replica = self.replica_sql = None
        self.replica_retry_at = self.replica_failures = 0
        if cfg.database_read_url:
            try:
                self.replica = self.make_engine(cfg.database_read_url, 2, 1)
                self.replica_sql = async_sessionmaker(self.replica, expire_on_commit=False)
            except (ValueError, TypeError, ImportError, SQLAlchemyError):
                logger.warning("Read replica configuration unavailable. Reads will use primary.")
        self.mongo_pool = MongoPoolMetrics(cfg.mongo_pool_size)
        self.mongo_client = AsyncIOMotorClient(cfg.mongo_url, maxPoolSize=cfg.mongo_pool_size, minPoolSize=0,
            maxIdleTimeMS=30000, waitQueueTimeoutMS=2000, serverSelectionTimeoutMS=3000,
            socketTimeoutMS=cfg.db_query_timeout_ms, timeoutMS=cfg.db_query_timeout_ms,
            connectTimeoutMS=2000, tz_aware=True, event_listeners=[self.mongo_pool])
        self.mongo = self.mongo_client[cfg.mongo_database]

    def make_engine(self, url, size, overflow):
        timeout = self.cfg.db_query_timeout_ms
        return create_async_engine(url, pool_pre_ping=True, pool_recycle=900, pool_size=size,
            max_overflow=overflow, pool_timeout=2, hide_parameters=True,
            connect_args={"connect_timeout": 2, "init_command": (
                f"SET SESSION MAX_EXECUTION_TIME={timeout}, SESSION innodb_lock_wait_timeout=5"
            )})

    async def read(self, query):
        """Replay only read-only operations, never write transactions."""
        if self.replica_sql and monotonic() >= self.replica_retry_at:
            try:
                async with asyncio.timeout(self.cfg.db_query_timeout_ms / 1000):
                    async with self.replica_sql() as sql:
                        status = (await sql.execute(text("SHOW REPLICA STATUS"))).mappings().first()
                        if (not status or status["Replica_IO_Running"] != "Yes" or status["Replica_SQL_Running"] != "Yes"
                            or status["Seconds_Behind_Source"] is None or status["Seconds_Behind_Source"] > 5):
                            raise OSError("Replica replication is unavailable or delayed")
                        await sql.execute(text("START TRANSACTION READ ONLY"))
                        result = await query(sql)
                self.replica_failures = 0
                return result
            except (DBAPIError, PoolTimeout, TimeoutError, OSError):
                self.replica_failures += 1
                self.replica_retry_at = monotonic() + min(60, 2 ** min(6, self.replica_failures))
                logger.warning("Read replica unavailable. Falling back to primary.")
        async with self.sql() as sql:
            return await query(sql)

    async def initialize_schema(self):
        async with self.engine.begin() as connection:
            # MySQL DDL commits implicitly. A connection-level lock also covers
            # concurrent API startup and one-off seed processes.
            acquired = await connection.scalar(text("SELECT GET_LOCK('dorm.schema', 30)"))
            if acquired != 1:
                raise RuntimeError("Could not acquire the SQL schema initialization lock")
            try:
                await connection.run_sync(initialize_mysql_schema)
            finally:
                try:
                    await connection.execute(text("SELECT RELEASE_LOCK('dorm.schema')"))
                except BaseException:
                    await connection.invalidate()
                    raise

    async def close(self):
        self.mongo_client.close()
        if self.replica is not None:
            await self.replica.dispose()
        await self.engine.dispose()


async def read_query(resources, query):
    reader = getattr(resources, "read", None)
    if reader:
        return await reader(query)
    async with resources.sql() as sql:
        return await query(sql)


async def retry_startup(operation, label):
    for attempt in range(3):
        try:
            return await operation()
        except (DBAPIError, PoolTimeout, ConnectionFailure, NetworkTimeout, OSError):
            if attempt == 2:
                raise
            logger.warning("%s startup temporarily unavailable. Retrying.", label)
            await asyncio.sleep(.5 * 2**attempt)
