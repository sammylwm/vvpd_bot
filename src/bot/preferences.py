from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    ForeignKey,
    MetaData,
    String,
    Table,
    Time,
    func,
    select,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from settings.config import config


@dataclass(frozen=True)
class ProfileSettings:
    telegram_id: int
    group: str
    reminders_enabled: bool = True
    reminder_time: time = time(8)
    last_reminder_date: date | None = None


@dataclass(frozen=True)
class TopicSettings:
    topic_id: int | None = None
    active: bool = True


metadata = MetaData()
groups = Table(
    "groups",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("name", String(64), nullable=False, unique=True),
)
users = Table(
    "users",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("telegram_id", BigInteger, nullable=False),
    Column("group_id", ForeignKey("groups.id"), nullable=False),
    Column("reminders_enabled", Boolean, nullable=False, server_default=true()),
    Column("reminder_time", Time, nullable=False, server_default="08:00:00"),
    Column("last_reminder_date", Date),
)
topics = Table(
    "telegramchats",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("topic_id", BigInteger),
    Column("active", Boolean, nullable=False, server_default=true()),
)
profile_query = (
    select(
        users.c.telegram_id,
        groups.c.name.label("group"),
        users.c.reminders_enabled,
        users.c.reminder_time,
        users.c.last_reminder_date,
    )
    .select_from(users.join(groups))
    .order_by(users.c.id)
)


class PreferenceStore:
    def __init__(self):
        self.engine = create_async_engine(config.database.url, pool_pre_ping=True)
        self.sessions = async_sessionmaker(self.engine)

    async def initialize(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            await connection.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS reminders_enabled boolean NOT NULL DEFAULT true, "
                "ADD COLUMN IF NOT EXISTS reminder_time time NOT NULL DEFAULT '08:00:00', "
                "ADD COLUMN IF NOT EXISTS last_reminder_date date"
            )
            await connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS users_telegram_id_idx ON users (telegram_id)"
            )

    async def profile(self, owner: int) -> ProfileSettings | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    profile_query.where(users.c.telegram_id == owner).limit(1)
                )
            ).first()
        return ProfileSettings(*row) if row else None

    async def set_group(self, owner: int, group: str) -> None:
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(owner)))
            await session.execute(
                insert(groups)
                .values(name=group)
                .on_conflict_do_nothing(index_elements=[groups.c.name])
            )
            group_id = await session.scalar(
                select(groups.c.id).where(groups.c.name == group)
            )
            result = await session.execute(
                update(users)
                .where(users.c.telegram_id == owner)
                .values(group_id=group_id)
            )
            if not result.rowcount:
                await session.execute(
                    insert(users).values(telegram_id=owner, group_id=group_id)
                )

    async def update_profile(self, owner: int, **values) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(users).where(users.c.telegram_id == owner).values(**values)
            )

    async def move_profile(self, old_id: int, new_id: int) -> None:
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(new_id)))
            destination = await session.scalar(
                select(users.c.id).where(users.c.telegram_id == new_id).limit(1)
            )
            if destination is None:
                await session.execute(
                    update(users)
                    .where(users.c.telegram_id == old_id)
                    .values(telegram_id=new_id)
                )
            await session.execute(
                update(topics).where(topics.c.id == old_id).values(active=False)
            )

    async def due_profiles(self, today: date, at: time) -> list[ProfileSettings]:
        first_profiles = select(func.min(users.c.id)).group_by(users.c.telegram_id)
        async with self.sessions() as session:
            rows = await session.execute(
                profile_query.where(
                    users.c.id.in_(first_profiles),
                    users.c.reminders_enabled,
                    users.c.reminder_time == at,
                    users.c.last_reminder_date.is_(None)
                    | (users.c.last_reminder_date < today),
                )
            )
            return [ProfileSettings(*row) for row in rows]

    async def topic(self, chat_id: int) -> TopicSettings | None:
        async with self.sessions() as session:
            row = (
                await session.execute(
                    select(topics.c.topic_id, topics.c.active).where(
                        topics.c.id == chat_id
                    )
                )
            ).first()
        return TopicSettings(*row) if row else None

    async def set_topic(self, chat_id: int, **values) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                insert(topics)
                .values(id=chat_id, **values)
                .on_conflict_do_update(
                    index_elements=[topics.c.id],
                    set_=values,
                )
            )

    async def pending_topics(self) -> list[int]:
        async with self.sessions() as session:
            return list(
                await session.scalars(
                    select(topics.c.id).where(
                        topics.c.active, topics.c.topic_id.is_(None)
                    )
                )
            )


preferences = PreferenceStore()


async def group_for_account(telegram_id: int) -> str | None:
    profile = await preferences.profile(telegram_id)
    return profile.group if profile else None
