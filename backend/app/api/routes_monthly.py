from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictBool

from .deps import Actor
from .routes_living import approved_member
from ..models.living import LivingGroup
from ..services.monthly_reminders import LOCAL, project_monthly, queue_month

router = APIRouter(prefix="/groups/{group_id}", tags=["group-reminders"])


class ReminderSettings(BaseModel):
    auto_monthly_reminder: StrictBool


async def owner_group(sql, group_id, actor_id):
    group = await sql.get(LivingGroup, group_id, with_for_update=True)
    await approved_member(sql, group_id, actor_id, lock=True)
    if not group or group.owner_id != actor_id:
        raise HTTPException(403, "Group owner approval required")
    return group


@router.patch("/reminder-settings")
async def reminder_settings(group_id: int, data: ReminderSettings, request: Request, actor: Actor):
    async with request.app.state.resources.sql.begin() as sql:
        group = await owner_group(sql, group_id, actor.id)
        group.auto_monthly_reminder = data.auto_monthly_reminder
        return {"auto_monthly_reminder": group.auto_monthly_reminder, "reminder_hour": group.reminder_hour,
                "last_reminded_period": group.last_reminded_period}


@router.post("/reminders/run-monthly")
async def run_monthly(group_id: int, request: Request, actor: Actor):
    resources = request.app.state.resources
    async with resources.sql.begin() as sql:
        group = await owner_group(sql, group_id, actor.id)
        result = await queue_month(sql, group, datetime.now(LOCAL), manual=True)
    if result["status"] == "sent":
        await project_monthly(resources, request.app.state.ws, (group_id, result["period"]))
    return result
