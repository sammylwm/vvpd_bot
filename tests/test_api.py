import unittest
from datetime import date
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.api import (
    ApiError,
    DeadlinePreview,
    PolytechApi,
    SessionTaskDraft,
    SessionTaskText,
)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.request = self.session.request
        self.api = PolytechApi(self.session, "https://example.com/api/")
        self.day = date(2026, 11, 19)
        self.preview = {
            "group": "261-329",
            "date": "2026-11-19",
            "subject": "Физика",
            "deadline": "Сдать отчёт",
        }

    def respond(self, body, status=200):
        self.request.return_value.__aenter__.return_value = SimpleNamespace(
            ok=status < 400, status=status, json=AsyncMock(return_value=body)
        )

    async def test_schedule_and_deadlines_use_explicit_inclusive_ranges(self):
        params = {"group": "261-329", "start": "2026-11-19", "end": "2026-11-19"}
        self.respond({"lessons": [], "deadlines": []})
        result = await self.api.schedule("261-329", self.day, self.day)
        self.assertEqual(result.lessons, [])
        self.request.assert_called_with(
            "GET", "https://example.com/api/schedule/", params=params
        )

        self.respond([{"id": 42, **self.preview}])
        result = await self.api.deadlines("261-329", self.day, self.day)
        self.assertEqual(result[0].date, self.day)
        self.request.assert_called_with(
            "GET", "https://example.com/api/deadline/", params=params
        )

    async def test_parse_confirm_and_delete_use_documented_payloads(self):
        self.respond(self.preview)
        preview = await self.api.parse("261-329", "Сдать отчёт")
        self.request.assert_called_with(
            "POST",
            "https://example.com/api/deadline/parse/",
            json={"group": "261-329", "text": "Сдать отчёт"},
        )

        self.respond({"status": "created", "deadline": {"id": 42, **self.preview}})
        result = await self.api.confirm(preview)
        self.assertEqual(result.deadline.id, 42)
        self.request.assert_called_with(
            "POST", "https://example.com/api/deadline/confirm/", json=self.preview
        )

        self.respond({"detail": "Запись удалена"})
        await self.api.delete(42)
        self.request.assert_called_with(
            "DELETE", "https://example.com/api/deadline/", params={"id": 42}
        )

    async def test_preserves_api_error_status_and_detail(self):
        self.respond({"detail": "Уточни предмет"}, status=422)
        with self.assertRaises(ApiError) as caught:
            await self.api.parse("261-329", "Сдать отчёт")
        self.assertEqual(caught.exception.status, 422)
        self.assertEqual(caught.exception.detail, "Уточни предмет")

    async def test_session_tasks_use_grouped_responses_and_distinct_post_payloads(self):
        period = {"group": "261-329", "semester": "2026-2"}
        task = {
            **period,
            "id": 42,
            "subject": "Физика",
            "text": "Сдать отчёт",
            "due_date": "2026-12-20",
        }
        subject = {"subject": "Физика", "text": "1. Сдать отчёт", "tasks": [task]}
        self.respond([subject])
        result = await self.api.session_tasks(**period)
        self.assertEqual(result[0].text, subject["text"])
        self.assertEqual(result[0].tasks[0].id, 42)
        self.assertEqual(result[0].tasks[0].due_date, date(2026, 12, 20))
        self.request.assert_called_with(
            "GET", "https://example.com/api/session-task/", params=period
        )

        payload = {**period, "text": "По физике сдать отчёт до 20 декабря"}
        self.respond(subject, status=201)
        parsed = await self.api.add_session_tasks_from_text(SessionTaskText(**payload))
        self.assertEqual(parsed, result[0])
        self.request.assert_called_with(
            "POST", "https://example.com/api/session-task/", json=payload
        )

        self.respond(task, status=201)
        draft = SessionTaskDraft.model_validate(task)
        saved = await self.api.add_session_task(draft)
        self.assertEqual(saved, parsed.tasks[0])
        self.request.assert_called_with(
            "POST",
            "https://example.com/api/session-task/",
            json={key: value for key, value in task.items() if key != "id"},
        )

    async def test_confirmation_sends_repeated_multipart_fields(self):
        self.respond(
            {
                "status": "created",
                "deadline": {
                    "id": 42,
                    **self.preview,
                    "attachments": [
                        {"name": "a.pdf", "size": 3, "url": "https://example.com/a"}
                    ],
                },
            }
        )
        preview = DeadlinePreview.model_validate(self.preview)
        result = await self.api.confirm(
            preview,
            [
                ("a.pdf", "application/pdf", BytesIO(b"abc")),
                ("b.txt", "text/plain", BytesIO(b"def")),
            ],
        )
        self.assertEqual(result.deadline.attachments[0].name, "a.pdf")
        args, kwargs = self.request.call_args
        self.assertEqual(args, ("POST", "https://example.com/api/deadline/confirm/"))
        multipart = kwargs["data"]()
        self.assertTrue(
            multipart.headers["Content-Type"].startswith("multipart/form-data;")
        )
        writer = AsyncMock()
        await multipart.write(writer)
        body = b"".join(call.args[0] for call in writer.write.await_args_list)
        self.assertEqual(body.count(b'name="attachments"'), 2)
        for field in (
            b'name="group"',
            b'name="date"',
            b'name="subject"',
            b'name="deadline"',
            b"abc",
            b"def",
        ):
            self.assertIn(field, body)
