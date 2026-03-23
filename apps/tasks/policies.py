from __future__ import annotations

from django.db.models import Q

from accounts.access_policy import AccessPolicy
from accounts.models import Role, User
from .models import Board, SubTask, TaskComment


class TaskPolicy:
    @staticmethod
    def is_full_admin(user) -> bool:
        return AccessPolicy.is_super_admin(user) or AccessPolicy.is_administrator(user)

    @staticmethod
    def is_admin_like(user) -> bool:
        return AccessPolicy.is_admin_like(user)

    @staticmethod
    def is_department_admin(user) -> bool:
        return AccessPolicy.is_admin(user)

    @staticmethod
    def is_department_head(user) -> bool:
        return AccessPolicy.is_admin(user)

    @staticmethod
    def is_teamlead(user) -> bool:
        return AccessPolicy.is_teamlead(user)

    @staticmethod
    def is_employee(user) -> bool:
        return AccessPolicy.is_employee(user)

    @staticmethod
    def is_intern(user) -> bool:
        return AccessPolicy.is_intern(user)

    @classmethod
    def can_manage_assignments(cls, actor) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        return cls.is_full_admin(actor) or cls.is_department_head(actor) or cls.is_teamlead(actor)

    @classmethod
    def scoped_members_queryset(cls, actor):
        qs = User.objects.filter(is_active=True).exclude(role__name=Role.Name.INTERN)
        if cls.is_full_admin(actor):
            return qs
        if cls.is_department_head(actor):
            if not actor.department_id:
                return qs.none()
            return qs.filter(department_id=actor.department_id)
        if cls.is_teamlead(actor):
            return qs.filter(Q(id=actor.id) | Q(manager_id=actor.id))
        if cls.is_employee(actor):
            return qs.filter(Q(id=actor.id) | Q(manager_id=actor.manager_id))
        return qs.filter(id=actor.id)

    @classmethod
    def can_manage_team(cls, actor) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        return cls.can_manage_assignments(actor)

    @classmethod
    def can_assign_task(cls, actor, assignee) -> bool:
        if cls.is_full_admin(actor):
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and assignee.department_id == actor.department_id)
        if cls.is_teamlead(actor):
            return actor.id == assignee.id or assignee.manager_id == actor.id
        # Any authenticated user can create a task for themselves
        if actor.id == assignee.id:
            return True
        return False

    @classmethod
    def can_view_task(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if task.assignee_id == actor.id or task.reporter_id == actor.id:
            return True
        if task.board_id and cls.can_view_board(actor, task.board):
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and task.assignee_id and task.assignee and task.assignee.department_id == actor.department_id)
        if cls.is_teamlead(actor):
            return bool(task.assignee_id and task.assignee and task.assignee.manager_id == actor.id)
        return False

    @classmethod
    def can_edit_task(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if task.status == "done":
            return False
        if task.reporter_id == actor.id:
            return True
        if task.assignee_id == actor.id:
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and task.assignee_id and task.assignee and task.assignee.department_id == actor.department_id)
        return cls.is_teamlead(actor) and task.assignee_id and task.assignee and task.assignee.manager_id == actor.id

    @classmethod
    def can_delete_task(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if task.reporter_id == actor.id:
            return True
        if task.board and task.board.is_personal and task.assignee_id == actor.id:
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and task.assignee_id and task.assignee and task.assignee.department_id == actor.department_id)
        return cls.is_teamlead(actor) and task.assignee_id and task.assignee and task.assignee.manager_id == actor.id

    @classmethod
    def can_manage_task_items(cls, actor, task) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if task.reporter_id == actor.id:
            return True
        if task.assignee_id == actor.id:
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and task.assignee_id and task.assignee and task.assignee.department_id == actor.department_id)
        return cls.is_teamlead(actor) and task.assignee_id and task.assignee and task.assignee.manager_id == actor.id

    @classmethod
    def can_manage_comment(cls, actor, comment: TaskComment) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if comment.author_id == actor.id:
            return True
        return cls.can_edit_task(actor, comment.task)

    @classmethod
    def can_manage_subtask(cls, actor, subtask: SubTask) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if subtask.created_by_id == actor.id:
            return True
        return cls.can_manage_task_items(actor, subtask.task)

    @classmethod
    def project_member_queryset(cls, actor):
        qs = User.objects.filter(is_active=True).filter(
            Q(role__name__in=[Role.Name.TEAMLEAD, Role.Name.EMPLOYEE, Role.Name.ADMIN]) | Q(id=actor.id)
        )
        if cls.is_full_admin(actor):
            return qs
        if cls.is_department_head(actor):
            if not actor.department_id:
                return qs.none()
            return qs.filter(department_id=actor.department_id)
        if cls.is_teamlead(actor):
            return qs.filter(Q(id=actor.id) | Q(manager_id=actor.id))
        if cls.is_employee(actor):
            return qs.filter(id=actor.id)
        return qs.none()

    @classmethod
    def visible_project_boards(cls, actor):
        if not actor or not actor.is_authenticated:
            return None
        qs = (
            Board.objects.filter(is_personal=False)
            .select_related("created_by", "department")
            .prefetch_related("members")
        )
        if cls.is_full_admin(actor):
            return qs
        if cls.is_department_head(actor):
            if not actor.department_id:
                return qs.none()
            return qs.filter(
                Q(department_id=actor.department_id)
                | Q(members__department_id=actor.department_id)
            ).distinct()
        if cls.is_teamlead(actor):
            return qs.filter(Q(created_by=actor) | Q(members=actor)).distinct()
        if cls.is_employee(actor):
            return qs.filter(members=actor).distinct()
        return qs.none()

    @classmethod
    def can_view_board(cls, actor, board) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if board.is_personal:
            return board.created_by_id == actor.id
        if cls.is_department_head(actor):
            return bool(actor.department_id and (board.department_id == actor.department_id or board.members.filter(department_id=actor.department_id).exists()))
        if board.created_by_id == actor.id:
            return True
        return board.members.filter(id=actor.id).exists()

    @classmethod
    def can_manage_board(cls, actor, board) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        if cls.is_full_admin(actor):
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and board.department_id == actor.department_id)
        if not cls.is_teamlead(actor):
            return False
        return board.created_by_id == actor.id

    @classmethod
    def can_create_project(cls, actor) -> bool:
        if not actor or not actor.is_authenticated:
            return False
        return cls.is_full_admin(actor) or cls.is_department_head(actor) or cls.is_teamlead(actor)

    @classmethod
    def can_assign_task_in_board(cls, actor, board, assignee) -> bool:
        if not cls.can_view_board(actor, board):
            return False
        if board.is_personal:
            return cls.can_assign_task(actor, assignee)
        if not board.members.filter(id=assignee.id).exists():
            return False
        if cls.is_full_admin(actor):
            return True
        if cls.is_department_head(actor):
            return bool(actor.department_id and assignee.department_id == actor.department_id)
        if cls.is_teamlead(actor):
            return board.created_by_id == actor.id or assignee.manager_id == actor.id or actor.id == assignee.id
        return actor.id == assignee.id
