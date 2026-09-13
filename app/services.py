import hmac
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app import repositories as repo
from app.config import get_settings
from app.models import LoginSession, Task, User, new_id, now
from app.schemas import Credentials, TaskCreate, TaskUpdate, TokenPair
from app.security import (
    access_token,
    decode_token,
    digest,
    encode_token,
    hash_password,
    unauthorized,
    verify_password,
)


@lru_cache
def dummy_hash() -> str:
    return hash_password("dummy-password-for-timing")


async def register(db: AsyncSession, data: Credentials) -> User:
    user = User(
        email=str(data.email), password_hash=await run_in_threadpool(hash_password, data.password)
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "Email already registered") from exc
    return user


def pair(session: LoginSession) -> TokenPair:
    refresh = encode_token(session.user_id, session.id, "refresh", session.expires_at)
    session.refresh_hash = digest(refresh)
    return TokenPair(
        access_token=access_token(session.user_id, session.id),
        refresh_token=refresh,
        expires_in=get_settings().access_token_minutes * 60,
    )


async def login(db: AsyncSession, data: Credentials) -> TokenPair:
    user = await repo.user_by_email(db, str(data.email))
    hashed = user.password_hash if user else await run_in_threadpool(dummy_hash)
    valid = await run_in_threadpool(verify_password, data.password, hashed)
    if not user or not valid:
        raise unauthorized()
    session = LoginSession(
        id=new_id(),
        user_id=user.id,
        revoked=False,
        expires_at=datetime.now(UTC) + timedelta(days=get_settings().refresh_token_days),
    )
    tokens = pair(session)
    db.add(session)
    await db.commit()
    return tokens


def is_expired(value: datetime) -> bool:
    return value.replace(tzinfo=UTC) <= datetime.now(UTC)


async def refresh(db: AsyncSession, token: str) -> TokenPair:
    claims = decode_token(token, "refresh")
    session = await repo.login_session(db, claims["sid"], lock=True)
    if (
        not session
        or session.revoked
        or session.user_id != claims["sub"]
        or is_expired(session.expires_at)
    ):
        raise unauthorized()
    if not hmac.compare_digest(session.refresh_hash, digest(token)):
        # Reuse of a rotated token revokes the entire login session.
        session.revoked = True
        await db.commit()
        raise unauthorized()
    tokens = pair(session)
    await db.commit()
    return tokens


async def get_task(db: AsyncSession, owner_id: str, task_id: str, lock=False) -> Task:
    task = await repo.owned_task(db, owner_id, task_id, lock=lock)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


async def create_task(db: AsyncSession, owner_id: str, data: TaskCreate) -> Task:
    task = Task(owner_id=owner_id, **data.model_dump())
    if task.status == "done":
        task.completed_at = now()
    db.add(task)
    await db.commit()
    return task


async def update_task(
    db: AsyncSession, owner_id: str, task_id: str, data: TaskUpdate, version: int
) -> Task:
    task = await get_task(db, owner_id, task_id, lock=True)
    if task.version != version:
        raise HTTPException(412, "Task changed; reload it before saving")
    changes = data.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] != task.status:
        task.completed_at = now() if changes["status"] == "done" else None
    for key, value in changes.items():
        setattr(task, key, value)
    task.updated_at = now()
    task.version += 1
    await db.commit()
    return task


async def delete_task(db: AsyncSession, owner_id: str, task_id: str, version: int):
    task = await get_task(db, owner_id, task_id, lock=True)
    if task.version != version:
        raise HTTPException(412, "Task changed; reload it before deleting")
    await db.delete(task)
    await db.commit()
