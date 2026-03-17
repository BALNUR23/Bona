from __future__ import annotations

from django.db.models import Q

from accounts.access_policy import AccessPolicy
from accounts.models import Role, User
from .models import Board, SubTask, TaskComment


class TaskPolicy:
    @staticmethod
    def is_admin_like(user) -> bool:
        return AccessPolicy.is_admin_like(user)

    @staticmethod
    def is_department_admin(user) -> bool:
        return AccessPolicy.is_admin(user)

    @classmethod
    def can_manage_team(cls, actor) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            return True
        return AccessPolicy.is_teamlead(actor)

    @classmethod
    def can_assign_task(cls, actor, assignee) -> bool:
        if cls.is_admin_like(actor):
            return True
        # Any authenticated user can create a task for themselves
        if actor.id == assignee.id:
            return True
        return AccessPolicy.is_teamlead(actor) and assignee.manager_id == actor.id

    @classmethod
    def can_view_task(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            return True
        if task.assignee_id == actor.id or task.reporter_id == actor.id:
            return True
        return AccessPolicy.is_teamlead(actor) and task.assignee_id and task.assignee and task.assignee.manager_id == actor.id

    @classmethod
    def can_edit_task(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            return True
        if task.status == "done":
            return False
        if task.reporter_id == actor.id:
            return True
        return AccessPolicy.is_teamlead(actor) and task.assignee_id and task.assignee and task.assignee.manager_id == actor.id

    @classmethod
    def can_manage_comment(cls, actor, comment: TaskComment) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            return True
        if comment.author_id == actor.id:
            return True
        return cls.can_edit_task(actor, comment.task)

    @classmethod
    def can_manage_subtask(cls, actor, subtask: SubTask) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            return True
        if subtask.created_by_id == actor.id:
            return True
        return cls.can_edit_task(actor, subtask.task)

    @classmethod
    def project_member_queryset(cls, actor):
        qs = User.objects.filter(
            is_active=True,
            role__name__in=[Role.Name.TEAMLEAD, Role.Name.EMPLOYEE, Role.Name.INTERN],
        )
        if cls.is_admin_like(actor):
            if actor.department_id:
                return qs.filter(department_id=actor.department_id)
            return qs
        if AccessPolicy.is_teamlead(actor):
            return qs.filter(Q(id=actor.id) | Q(manager_id=actor.id))
        return qs.filter(id=actor.id)

    @classmethod
    def visible_project_boards(cls, actor):
        if not actor or not actor.is_authenticated:
            return None
        qs = (
            Board.objects.filter(is_personal=False)
            .select_related("created_by", "department")
            .prefetch_related("members")
        )
        if cls.is_admin_like(actor):
            if actor.department_id:
                return qs.filter(Q(department_id=actor.department_id) | Q(department__isnull=True))
            return qs
        if AccessPolicy.is_teamlead(actor):
            return qs.filter(Q(created_by=actor) | Q(members=actor)).distinct()
        return qs.filter(members=actor).distinct()

    @classmethod
    def can_view_board(cls, actor, board) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            if actor.department_id and board.department_id and board.department_id != actor.department_id:
                return False
            return True
        if board.is_personal:
            return board.created_by_id == actor.id
        if board.created_by_id == actor.id:
            return True
        return board.members.filter(id=actor.id).exists()

    @classmethod
    def can_manage_board(cls, actor, board) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_admin_like(actor):
            if actor.department_id and board.department_id and board.department_id != actor.department_id:
                return False
            return True
        if not AccessPolicy.is_teamlead(actor):
            return False
        return board.created_by_id == actor.id

    @classmethod
    def can_create_project(cls, actor) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        return cls.is_admin_like(actor) or AccessPolicy.is_teamlead(actor)

    @classmethod
    def can_assign_task_in_board(cls, actor, board, assignee) -> bool:
        if not cls.can_view_board(actor, board):
            return False
        if board.is_personal:
            return cls.can_assign_task(actor, assignee)
        if not board.members.filter(id=assignee.id).exists():
            return False
        if cls.is_admin_like(actor):
            return True
        return board.created_by_id == actor.id or actor.id == assignee.id
