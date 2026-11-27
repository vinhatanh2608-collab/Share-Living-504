from threading import Lock

from pymongo.monitoring import ConnectionPoolListener
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from motor.motor_asyncio import AsyncIOMotorClient
from .config import settings
from ..models.mysql_models import Base
from ..models.living import PERFORMANCE_INDEXES


def initialize_mysql_schema(connection):
    Base.metadata.create_all(connection)
    inspector = inspect(connection)
    if "note" not in {column["name"] for column in inspector.get_columns("group_payment_proofs")}:
        connection.execute(text("ALTER TABLE group_payment_proofs ADD COLUMN note VARCHAR(300) NULL"))
    # create_all skips indexes on tables that already exist.
    for index in PERFORMANCE_INDEXES:
        existing = inspector.get_indexes(index.table.name)
        if not any(item["name"] == index.name for item in existing):
            index.create(connection)


class MongoPoolMetrics(ConnectionPoolListener):
    def __init__(self):
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
            return {"checked_out": self.checked_out, "capacity_per_server": 20,
                    "checkout_failures": self.checkout_failures}

    # PyMongo requires listeners to implement all pool event callbacks.
    def ignore(self, event):
        pass

    pool_created = pool_ready = pool_cleared = pool_closed = ignore
    connection_created = connection_ready = connection_closed = connection_check_out_started = ignore


class Resources:
    def __init__(self):
        cfg = settings()
        self.engine = create_async_engine(cfg.mysql_url, pool_pre_ping=True,
                                          pool_recycle=1800, pool_size=10, max_overflow=15, pool_timeout=5,
                                          hide_parameters=True)
        self.sql = async_sessionmaker(self.engine, expire_on_commit=False)
        self.mongo_pool = MongoPoolMetrics()
        self.mongo_client = AsyncIOMotorClient(cfg.mongo_url, maxPoolSize=20, minPoolSize=2, maxIdleTimeMS=30000, waitQueueTimeoutMS=5000, serverSelectionTimeoutMS=5000,
                                              socketTimeoutMS=10000, connectTimeoutMS=3000, tz_aware=True,
                                              event_listeners=[self.mongo_pool])
        self.mongo = self.mongo_client[cfg.mongo_database]

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
        await self.engine.dispose()
