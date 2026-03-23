from __future__ import annotations

from typing import Optional

from apps.audit import AuditEvents, log_event


class TasksAuditService:
    @staticmethod
    def _ip(request) -> Optional[str]:
        xff = request.META.get("HTTP_X_FORWARDED_FOR")
        if xff:
            return xff.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @classmethod
    def log_task_created(cls, request, task) -> None:
        log_event(
            action=AuditEvents.TASK_CREATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "assignee_id": task.assignee_id,
                "board_id": task.board_id,
                "column_id": task.column_id,
            },
        )

    @classmethod
    def log_task_updated(cls, request, task, changed_fields: list[str]) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "changed_fields": changed_fields,
            },
        )

    @classmethod
    def log_task_deleted(cls, request, task) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="warning",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "changed_fields": ["deleted"],
            },
        )

    @classmethod
    def log_task_moved(cls, request, task, from_column_id: int, to_column_id: int) -> None:
        log_event(
            action=AuditEvents.TASK_MOVED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "from_column_id": from_column_id,
                "to_column_id": to_column_id,
            },
        )

    @classmethod
    def log_task_comment_added(cls, request, task, comment) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "comment_id": comment.id,
                "changed_fields": ["comment_added"],
            },
        )

    @classmethod
    def log_task_comment_updated(cls, request, task, comment) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "comment_id": comment.id,
                "changed_fields": ["comment_updated"],
            },
        )

    @classmethod
    def log_task_comment_deleted(cls, request, task, comment_id) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "comment_id": comment_id,
                "changed_fields": ["comment_deleted"],
            },
        )

    @classmethod
    def log_subtask_changed(cls, request, task, subtask_id, action_name: str) -> None:
        log_event(
            action=AuditEvents.TASK_UPDATED,
            actor=request.user,
            object_type="task",
            object_id=str(task.id),
            level="info",
            category="content",
            ip_address=cls._ip(request),
            metadata={
                "actor_id": request.user.id,
                "subtask_id": subtask_id,
                "changed_fields": [action_name],
            },
        )
