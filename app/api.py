from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app import repositories as repo
from app import services
from app.db import get_db
from app.models import LoginSession, User
from app.schemas import (
    Credentials,
    Priority,
    RefreshIn,
    Status,
    TaskCreate,
    TaskOut,
    TaskPage,
    TaskUpdate,
    TokenPair,
    UserOut,
)
from app.security import decode_token, unauthorized

router = APIRouter(prefix="/api/v1")
Db = Annotated[AsyncSession, Depends(get_db)]
bearer = HTTPBearer(auto_error=False)


async def current_session(
    db: Db, token: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
):
    if not token:
        raise unauthorized()
    claims = decode_token(token.credentials, "access")
    session = await repo.login_session(db, claims["sid"])
    if (
        not session
        or session.revoked
        or session.user_id != claims["sub"]
        or services.is_expired(session.expires_at)
    ):
        raise unauthorized()
    return session


Session = Annotated[LoginSession, Depends(current_session)]


@router.post("/auth/register", response_model=UserOut, status_code=201)
async def register(data: Credentials, db: Db):
    return await services.register(db, data)


@router.post("/auth/login", response_model=TokenPair)
async def login(data: Credentials, db: Db):
    return await services.login(db, data)


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh(data: RefreshIn, db: Db):
    return await services.refresh(db, data.refresh_token)


@router.post("/auth/logout", status_code=204)
async def logout(session: Session, db: Db):
    # Refresh and logout use the same lock; logout cannot be undone by rotation.
    locked = await repo.login_session(db, session.id, lock=True)
    locked.revoked = True
    await db.commit()
    return Response(status_code=204)


@router.get("/users/me", response_model=UserOut)
async def me(session: Session, db: Db):
    return await db.get(User, session.user_id)


def require_version(if_match: Annotated[str | None, Header()] = None) -> int:
    if if_match is None:
        raise HTTPException(428, 'Supply If-Match with the task ETag, such as "1"')
    if not (
        len(if_match) >= 3
        and if_match[0] == if_match[-1] == '"'
        and if_match[1:-1].isascii()
        and if_match[1:-1].isdigit()
    ):
        raise HTTPException(400, 'If-Match must be a quoted version, such as "1"')
    if len(if_match) > 12:
        raise HTTPException(400, "Invalid task version")
    return int(if_match[1:-1])


Version = Annotated[int, Depends(require_version)]


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def create_task(data: TaskCreate, session: Session, db: Db, response: Response):
    task = await services.create_task(db, session.user_id, data)
    response.headers["ETag"] = f'"{task.version}"'
    response.headers["Location"] = f"/api/v1/tasks/{task.id}"
    return task


@router.get("/tasks", response_model=TaskPage)
async def list_tasks(
    session: Session,
    db: Db,
    status: Status | None = None,
    priority: Priority | None = None,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    overdue: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
):
    return await repo.list_tasks(db, session.user_id, status, priority, q, overdue, limit, offset)


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: UUID, session: Session, db: Db, response: Response):
    task = await services.get_task(db, session.user_id, str(task_id))
    response.headers["ETag"] = f'"{task.version}"'
    return task


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: UUID, data: TaskUpdate, session: Session, db: Db, version: Version, response: Response
):
    task = await services.update_task(db, session.user_id, str(task_id), data, version)
    response.headers["ETag"] = f'"{task.version}"'
    return task


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task(task_id: UUID, session: Session, db: Db, version: Version):
    await services.delete_task(db, session.user_id, str(task_id), version)
    return Response(status_code=204)
