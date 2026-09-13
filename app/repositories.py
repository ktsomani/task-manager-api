from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LoginSession, Task, User, now


async def user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email))


async def login_session(db: AsyncSession, session_id: str, lock: bool = False):
    statement = select(LoginSession).where(LoginSession.id == session_id)
    if lock:
        statement = statement.with_for_update()
    return await db.scalar(statement)


async def owned_task(db, owner_id, task_id, lock=False):
    stmt = select(Task).where(Task.owner_id == owner_id, Task.id == task_id)
    if lock:
        stmt = stmt.with_for_update()
    return await db.scalar(stmt)


async def list_tasks(db, owner_id, status, priority, q, overdue, limit, offset):
    filters = [Task.owner_id == owner_id]
    if status:
        filters.append(Task.status == status)
    if priority:
        filters.append(Task.priority == priority)
    if q:
        filters.append(Task.title.icontains(q, autoescape=True))
    if overdue:
        filters.extend([Task.due_at < now(), Task.status != "done"])
    total = await db.scalar(select(func.count()).select_from(Task).where(*filters))
    items = list(
        await db.scalars(
            select(Task)
            .where(*filters)
            .order_by(Task.created_at.desc(), Task.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
