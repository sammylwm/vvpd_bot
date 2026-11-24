from datetime import date
from typing import BinaryIO, Literal

from aiohttp import ClientError, ClientSession, FormData
from pydantic import BaseModel, Field, TypeAdapter


class ApiError(Exception):
    def __init__(self, status: int, detail: str = ""):
        super().__init__(detail or f"Polytech Plan API error ({status})")
        self.status = status
        self.detail = detail


class DeadlinePreview(BaseModel):
    group: str
    date: date
    subject: str
    deadline: str


class Attachment(BaseModel):
    name: str
    size: int
    url: str


class Deadline(DeadlinePreview):
    id: int
    attachments: list[Attachment] = Field(default_factory=list)


class Confirmation(BaseModel):
    status: Literal["created", "updated", "unchanged"]
    deadline: Deadline


class Schedule(BaseModel):
    lessons: list[dict]


class SessionTaskText(BaseModel):
    group: str
    semester: str
    text: str


class SessionTaskDraft(SessionTaskText):
    subject: str
    due_date: date | None = None


class SessionTask(SessionTaskDraft):
    id: int


class SessionSubject(BaseModel):
    subject: str
    text: str
    tasks: list[SessionTask]


class PolytechApi:
    def __init__(self, session: ClientSession, base_url: str):
        self.session = session
        self.base_url = base_url.rstrip("/").removesuffix("/api") + "/api"

    async def request(self, method: str, path: str, response_type, **kwargs):
        try:
            async with self.session.request(
                method, self.base_url + path, **kwargs
            ) as response:
                if not response.ok:
                    try:
                        body = await response.json()
                    except (ValueError, ClientError):
                        body = None
                    detail = body.get("detail") if isinstance(body, dict) else None
                    raise ApiError(
                        response.status, detail if isinstance(detail, str) else ""
                    )
                return TypeAdapter(response_type).validate_python(await response.json())
        except (ClientError, TimeoutError, ValueError) as exc:
            raise ApiError(0) from exc

    @staticmethod
    def period(group: str, start: date, end: date) -> dict[str, str]:
        return {"group": group, "start": start.isoformat(), "end": end.isoformat()}

    async def schedule(self, group: str, start: date, end: date) -> Schedule:
        return await self.request(
            "GET", "/schedule/", Schedule, params=self.period(group, start, end)
        )

    async def deadlines(self, group: str, start: date, end: date) -> list[Deadline]:
        return await self.request(
            "GET", "/deadline/", list[Deadline], params=self.period(group, start, end)
        )

    async def parse(self, group: str, text: str) -> DeadlinePreview:
        return await self.request(
            "POST",
            "/deadline/parse/",
            DeadlinePreview,
            json={"group": group, "text": text},
        )

    async def confirm(
        self,
        preview: DeadlinePreview,
        files: list[tuple[str, str, BinaryIO]] | None = None,
    ) -> Confirmation:
        fields = preview.model_dump(mode="json")
        if not files:
            return await self.request(
                "POST", "/deadline/confirm/", Confirmation, json=fields
            )
        form = FormData()
        for name, value in fields.items():
            form.add_field(name, value)
        for filename, content_type, stream in files:
            form.add_field(
                "attachments", stream, filename=filename, content_type=content_type
            )
        return await self.request("POST", "/deadline/confirm/", Confirmation, data=form)

    async def delete(self, deadline_id: int) -> None:
        await self.request(
            "DELETE", "/deadline/", dict[str, str], params={"id": deadline_id}
        )

    async def session_tasks(self, group: str, semester: str) -> list[SessionSubject]:
        return await self.request(
            "GET",
            "/session-task/",
            list[SessionSubject],
            params={"group": group, "semester": semester},
        )

    async def add_session_task(self, task: SessionTaskDraft) -> SessionTask:
        return await self.request(
            "POST", "/session-task/", SessionTask, json=task.model_dump(mode="json")
        )

    async def add_session_tasks_from_text(
        self, task: SessionTaskText
    ) -> SessionSubject:
        return await self.request(
            "POST", "/session-task/", SessionSubject, json=task.model_dump(mode="json")
        )
