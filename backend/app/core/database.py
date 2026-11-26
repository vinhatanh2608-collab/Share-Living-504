from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from motor.motor_asyncio import AsyncIOMotorClient
from .config import settings
from ..models.mysql_models import Base
from ..models.living import PERFORMANCE_INDEXES


def initialize_mysql_schema(connection):
    Base.metadata.create_all(connection)
    inspector = inspect(connection)
    # create_all skips indexes on tables that already exist.
    for index in PERFORMANCE_INDEXES:
        existing = inspector.get_indexes(index.table.name)
        if not any(item["name"] == index.name for item in existing):
            index.create(connection)


class Resources:
    def __init__(self):
        cfg = settings()
        self.engine = create_async_engine(cfg.mysql_url, pool_pre_ping=True,
                                          pool_recycle=900, pool_size=5, max_overflow=5,
                                          hide_parameters=True)
        self.sql = async_sessionmaker(self.engine, expire_on_commit=False)
        self.mongo_client = AsyncIOMotorClient(cfg.mongo_url, serverSelectionTimeoutMS=4000,
                                              socketTimeoutMS=6000, tz_aware=True)
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
