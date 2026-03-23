from datetime import timedelta
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access_policy import AccessPolicy
from accounts.models import AuditLog, Role, User
from onboarding_core.models import OnboardingDay
from work_schedule.models import WeeklyWorkPlan
from .audit import TasksAuditService
from .models import Board, ChecklistItem, Column, SubTask, Task, TaskComment
from .policies import TaskPolicy
from .serializers import (
    ProjectSerializer,
    ProjectReportTaskSerializer,
    ProjectWriteSerializer,
    ChecklistItemCreateSerializer,
    ChecklistItemSerializer,
    ChecklistItemUpdateSerializer,
    SubTaskCreateSerializer,
    SubTaskSerializer,
    SubTaskUpdateSerializer,
    TaskCommentCreateSerializer,
    TaskCommentSerializer,
    TaskCreateSerializer,
    TaskHistorySerializer,
    TaskMoveSerializer,
    TaskSerializer,
    TaskUpdateSerializer,
)


MANDATORY_WEEKLY_PLAN_TASK_TITLE = "Сделать график работы на следующую неделю"
DEFAULT_COLUMNS = (
    (1, "To Do"),
    (2, "In Progress"),
    (3, "Review"),
    (4, "Done"),
    (5, "Blocked"),
)

STATUS_TO_COLUMN_ORDER = {
    Task.Status.TO_DO: 1,
    Task.Status.IN_PROGRESS: 2,
    Task.Status.REVIEW: 3,
    Task.Status.DONE: 4,
    Task.Status.BLOCKED: 5,
}

COLUMN_ORDER_TO_STATUS = {value: key for key, value in STATUS_TO_COLUMN_ORDER.items()}


def _with_project_stats(qs):
    today = timezone.localdate()
    return qs.annotate(
        task_count=Count("tasks", distinct=True),
        completed_task_count=Count(
            "tasks",
            filter=Q(tasks__status=Task.Status.DONE),
            distinct=True,
        ),
        in_progress_task_count=Count(
            "tasks",
            filter=Q(tasks__status=Task.Status.IN_PROGRESS),
            distinct=True,
        ),
        overdue_task_count=Count(
            "tasks",
            filter=Q(tasks__due_date__lt=today) & ~Q(tasks__status=Task.Status.DONE),
            distinct=True,
        ),
    )


def _ensure_default_columns(board: Board) -> None:
    for order, name in DEFAULT_COLUMNS:
        Column.objects.get_or_create(
            board=board,
            order=order,
            defaults={"name": name},
        )


def get_user_default_board(user) -> Board:
    board, _ = Board.objects.get_or_create(
        created_by=user,
        is_personal=True,
        defaults={"name": f"{user.username} board"},
    )
    if not board.members.filter(id=user.id).exists():
        board.members.add(user)
    _ensure_default_columns(board)
    return board


def _resolve_project_members(*, actor, member_ids):
    allowed_qs = TaskPolicy.project_member_queryset(actor)
    allowed_ids = set(allowed_qs.values_list("id", flat=True))
    invalid_ids = sorted(set(member_ids) - allowed_ids)
    return allowed_qs, invalid_ids


def _apply_task_filters(qs, params):
    qs = qs.annotate(
        priority_rank=Case(
            When(priority=Task.Priority.CRITICAL, then=Value(0)),
            When(priority=Task.Priority.HIGH, then=Value(1)),
            When(priority=Task.Priority.MEDIUM, then=Value(2)),
            When(priority=Task.Priority.LOW, then=Value(3)),
            default=Value(99),
            output_field=IntegerField(),
        )
    )
    assignee_id = params.get("assignee_id")
    if assignee_id:
        qs = qs.filter(assignee_id=assignee_id)

    priority = params.get("priority")
    if priority:
        qs = qs.filter(priority=priority)

    column_id = params.get("column_id")
    if column_id:
        qs = qs.filter(column_id=column_id)

    status_value = params.get("status")
    if status_value:
        qs = qs.filter(status=status_value)

    is_overdue = params.get("is_overdue")
    if is_overdue in {"true", "1"}:
        qs = qs.filter(due_date__lt=timezone.localdate()).exclude(status=Task.Status.DONE)
    elif is_overdue in {"false", "0"}:
        qs = qs.exclude(due_date__lt=timezone.localdate()).exclude(due_date__isnull=True)

    due_date_from = params.get("due_date_from")
    if due_date_from:
        qs = qs.filter(due_date__gte=due_date_from)

    due_date_to = params.get("due_date_to")
    if due_date_to:
        qs = qs.filter(due_date__lte=due_date_to)

    board_id = params.get("board_id")
    if board_id:
        qs = qs.filter(board_id=board_id)

    ordering = params.get("ordering", "priority")
    if ordering == "due_date":
        return qs.order_by("due_date", "priority_rank", "-updated_at")
    if ordering == "-due_date":
        return qs.order_by("-due_date", "priority_rank", "-updated_at")
    if ordering == "-priority":
        return qs.order_by("-priority_rank", "-updated_at")
    return qs.order_by("priority_rank", "-updated_at")


def _next_monday(today):
    days_ahead = (7 - today.weekday()) % 7
    return today + timedelta(days=days_ahead or 7)


def _find_duplicate_task(*, board, title, assignee, reporter, due_date):
    normalized_title = (title or "").strip()
    if not normalized_title:
        return None
    return (
        Task.objects.filter(
            board=board,
            title=normalized_title,
            assignee=assignee,
            reporter=reporter,
            due_date=due_date,
        )
        .order_by("-created_at", "-id")
        .first()
    )


def _ensure_weekly_plan_task_for_user(*, assignee, reporter):
    next_week_start = _next_monday(timezone.localdate())
    has_plan = WeeklyWorkPlan.objects.filter(user=assignee, week_start=next_week_start).exists()
    if has_plan:
        return None

    exists_task = Task.objects.filter(
        assignee=assignee,
        title=MANDATORY_WEEKLY_PLAN_TASK_TITLE,
        due_date=next_week_start,
    ).exists()
    if exists_task:
        return None

    board = get_user_default_board(assignee)
    column = board.columns.order_by("order", "id").first()
    if column is None:
        column = Column.objects.create(board=board, name="Новые", order=1)
    return Task.objects.create(
        board=board,
        column=column,
        title=MANDATORY_WEEKLY_PLAN_TASK_TITLE,
        description=f"Заполнить и отправить недельный график на неделю с {next_week_start.isoformat()}",
        assignee=assignee,
        reporter=reporter,
        due_date=next_week_start,
        priority=Task.Priority.HIGH,
        status=Task.Status.TO_DO,
    )


class TaskMyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        reporter = request.user.manager if request.user.manager_id else request.user
        auto_task = _ensure_weekly_plan_task_for_user(assignee=request.user, reporter=reporter)
        if auto_task is not None:
            TasksAuditService.log_task_created(request, auto_task)
        qs = Task.objects.filter(assignee=request.user)
        qs = _apply_task_filters(qs, request.query_params)
        qs = qs.select_related("assignee", "reporter", "column", "board")
        return Response(TaskSerializer(qs, many=True, context={"request": request}).data)


class TaskTeamAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not TaskPolicy.can_manage_team(request.user):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        if TaskPolicy.is_full_admin(request.user):
            team_users = User.objects.filter(
                is_active=True,
                role__name__in=[Role.Name.ADMIN, Role.Name.TEAMLEAD, Role.Name.EMPLOYEE],
            )
            qs = Task.objects.all()
        elif TaskPolicy.is_department_head(request.user):
            if request.user.department_id:
                team_users = User.objects.filter(
                    is_active=True,
                    department_id=request.user.department_id,
                    role__name__in=[Role.Name.ADMIN, Role.Name.TEAMLEAD, Role.Name.EMPLOYEE],
                )
                qs = Task.objects.filter(assignee__department_id=request.user.department_id)
            else:
                team_users = User.objects.none()
                qs = Task.objects.none()
        else:
            team_users = request.user.team_members.filter(is_active=True)
            qs = Task.objects.filter(assignee__manager=request.user)

        for user in team_users:
            auto_task = _ensure_weekly_plan_task_for_user(assignee=user, reporter=request.user)
            if auto_task is not None:
                TasksAuditService.log_task_created(request, auto_task)

        qs = _apply_task_filters(qs, request.query_params)
        qs = qs.select_related("assignee", "reporter", "column", "board")
        return Response(TaskSerializer(qs, many=True, context={"request": request}).data)


class TaskCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = TaskCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        assignee_id = serializer.validated_data.get("assignee_id")
        if assignee_id is None:
            assignee = request.user
        else:
            assignee = get_object_or_404(User, id=assignee_id)
        board_id = serializer.validated_data.get("board_id")
        if board_id:
            board = get_object_or_404(Board.objects.prefetch_related("members", "columns"), id=board_id)
            if not TaskPolicy.can_assign_task_in_board(request.user, board, assignee):
                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        else:
            if not TaskPolicy.can_assign_task(request.user, assignee):
                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
            board = get_user_default_board(assignee)
        _ensure_default_columns(board)
        column = board.columns.order_by("order", "id").first()
        onboarding_day = None
        onboarding_day_id = serializer.validated_data.get("onboarding_day_id")
        if onboarding_day_id:
            onboarding_day = get_object_or_404(OnboardingDay, id=onboarding_day_id)
        duplicate = _find_duplicate_task(
            board=board,
            title=serializer.validated_data["title"],
            assignee=assignee,
            reporter=request.user,
            due_date=serializer.validated_data.get("due_date"),
        )
        if duplicate is not None:
            return Response(
                {
                    "detail": "Такая задача уже существует.",
                    "task_id": duplicate.id,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        task = Task.objects.create(
            board=board,
            column=column,
            title=serializer.validated_data["title"],
            description=serializer.validated_data.get("description", ""),
            assignee=assignee,
            reporter=request.user,
            onboarding_day=onboarding_day,
            due_date=serializer.validated_data.get("due_date"),
            priority=serializer.validated_data.get("priority", Task.Priority.MEDIUM),
            status=COLUMN_ORDER_TO_STATUS.get(column.order, Task.Status.TO_DO),
        )
        TasksAuditService.log_task_created(request, task)
        return Response(TaskSerializer(task, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskAssigneesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if TaskPolicy.is_full_admin(user):
            qs = User.objects.filter(
                is_active=True,
                role__name__in=[Role.Name.ADMIN, Role.Name.TEAMLEAD, Role.Name.EMPLOYEE],
            )
        elif TaskPolicy.is_department_head(user):
            if user.department_id:
                qs = User.objects.filter(
                    is_active=True,
                    department_id=user.department_id,
                    role__name__in=[Role.Name.ADMIN, Role.Name.TEAMLEAD, Role.Name.EMPLOYEE],
                )
            else:
                qs = User.objects.none()
        elif AccessPolicy.is_teamlead(user):
            qs = User.objects.filter(Q(id=user.id) | Q(manager_id=user.id), is_active=True)
        else:
            qs = User.objects.filter(id=user.id, is_active=True)

        qs = qs.select_related("role").order_by("first_name", "last_name", "username", "id")
        payload = [
            {
                "id": item.id,
                "username": item.username,
                "full_name": f"{item.first_name} {item.last_name}".strip() or item.username,
                "role": item.role.name if item.role_id else "",
                "department_id": item.department_id,
            }
            for item in qs
        ]
        return Response(payload)


class TaskDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        return Response(TaskSerializer(task, context={"request": request}).data)

    def patch(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "column", "board"), pk=pk)
        if not TaskPolicy.can_edit_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = TaskUpdateSerializer(data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        changed_fields = []
        data = serializer.validated_data
        if "title" in data:
            task.title = data["title"]
            changed_fields.append("title")
        if "description" in data:
            task.description = data["description"]
            changed_fields.append("description")
        if "due_date" in data:
            task.due_date = data["due_date"]
            changed_fields.append("due_date")
        if "priority" in data:
            task.priority = data["priority"]
            changed_fields.append("priority")
        if "assignee" in data:
            task.assignee = get_object_or_404(User, id=data["assignee"]) if data["assignee"] else None
            changed_fields.append("assignee")
        if "status" in data:
            status_value = data["status"]
            target_order = STATUS_TO_COLUMN_ORDER.get(status_value)
            if target_order is not None:
                target_column = task.board.columns.filter(order=target_order).first()
                if target_column is None:
                    target_column = Column.objects.create(
                        board=task.board,
                        order=target_order,
                        name=dict(DEFAULT_COLUMNS).get(target_order, status_value),
                    )
                task.column = target_column
                task.status = status_value
                changed_fields.extend(["status", "column"])
        task.save()
        if changed_fields:
            TasksAuditService.log_task_updated(request, task, changed_fields)
        return Response(TaskSerializer(task, context={"request": request}).data)

    def delete(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "column", "board"), pk=pk)
        if not TaskPolicy.can_delete_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        task_id = task.id
        TasksAuditService.log_task_deleted(request, task)
        task.delete()
        return Response({"detail": "Task deleted.", "task_id": task_id}, status=status.HTTP_200_OK)


class TaskMoveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "column"), pk=pk)
        if not TaskPolicy.can_edit_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = TaskMoveSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        new_column = get_object_or_404(Column, id=serializer.validated_data["column_id"])
        if new_column.board_id != task.board_id:
            return Response(
                {"detail": "Column belongs to another board."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_column_id = task.column_id
        task.column = new_column
        task.status = COLUMN_ORDER_TO_STATUS.get(new_column.order, task.status)
        task.save(update_fields=["column", "status", "updated_at"])
        TasksAuditService.log_task_moved(request, task, old_column_id, task.column_id)
        return Response(TaskSerializer(task, context={"request": request}).data)


class ProjectListCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = TaskPolicy.visible_project_boards(request.user)
        qs = _with_project_stats(qs).order_by("name", "id")
        return Response(ProjectSerializer(qs, many=True, context={"request": request}).data)

    def post(self, request):
        if not TaskPolicy.can_create_project(request.user):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = ProjectWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        member_ids = serializer.validated_data.get("member_ids", [])
        allowed_qs, invalid_ids = _resolve_project_members(actor=request.user, member_ids=member_ids)
        if invalid_ids:
            return Response(
                {"detail": "Some selected members are outside your scope.", "member_ids": invalid_ids},
                status=status.HTTP_400_BAD_REQUEST,
            )

        board = Board.objects.create(
            name=serializer.validated_data["name"],
            description=serializer.validated_data.get("description", ""),
            is_personal=False,
            created_by=request.user,
            responsible_user=get_object_or_404(User, id=serializer.validated_data["responsible_user_id"])
            if serializer.validated_data.get("responsible_user_id")
            else request.user,
            status=serializer.validated_data.get("status", Board.Status.ACTIVE),
            end_date=serializer.validated_data.get("end_date"),
            department=request.user.department,
        )
        member_ids_set = set(member_ids)
        member_ids_set.add(request.user.id)
        members = allowed_qs.filter(id__in=member_ids_set)
        board.members.set(members)
        _ensure_default_columns(board)
        board = (
            _with_project_stats(
                Board.objects.select_related("created_by", "department")
            .prefetch_related("members")
            )
            .get(id=board.id)
        )
        return Response(ProjectSerializer(board, context={"request": request}).data, status=status.HTTP_201_CREATED)


class ProjectDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        board = get_object_or_404(
            _with_project_stats(
                Board.objects.filter(is_personal=False)
            .select_related("created_by", "department")
            .prefetch_related("members")
            ),
            pk=pk,
        )
        if not TaskPolicy.can_view_board(request.user, board):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        return Response(ProjectSerializer(board, context={"request": request}).data)

    def patch(self, request, pk):
        board = get_object_or_404(
            Board.objects.filter(is_personal=False).prefetch_related("members"),
            pk=pk,
        )
        if not TaskPolicy.can_manage_board(request.user, board):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        serializer = ProjectWriteSerializer(data=request.data, context={"request": request}, partial=True)
        serializer.is_valid(raise_exception=True)

        updated_fields = []
        if "name" in serializer.validated_data:
            board.name = serializer.validated_data["name"]
            updated_fields.append("name")
        if "description" in serializer.validated_data:
            board.description = serializer.validated_data["description"]
            updated_fields.append("description")
        if "status" in serializer.validated_data:
            board.status = serializer.validated_data["status"]
            updated_fields.append("status")
        if "end_date" in serializer.validated_data:
            board.end_date = serializer.validated_data["end_date"]
            updated_fields.append("end_date")
        if "responsible_user_id" in serializer.validated_data:
            responsible_user_id = serializer.validated_data["responsible_user_id"]
            board.responsible_user = (
                get_object_or_404(User, id=responsible_user_id)
                if responsible_user_id
                else None
            )
            updated_fields.append("responsible_user")
        if updated_fields:
            board.save(update_fields=updated_fields)

        if "member_ids" in serializer.validated_data:
            member_ids = serializer.validated_data["member_ids"]
            allowed_qs, invalid_ids = _resolve_project_members(actor=request.user, member_ids=member_ids)
            if invalid_ids:
                return Response(
                    {"detail": "Some selected members are outside your scope.", "member_ids": invalid_ids},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            member_ids_set = set(member_ids)
            member_ids_set.add(request.user.id)
            next_members_qs = allowed_qs.filter(id__in=member_ids_set)
            next_member_ids = set(next_members_qs.values_list("id", flat=True))
            removed_member_ids = set(board.members.values_list("id", flat=True)) - next_member_ids
            board.members.set(next_members_qs)
            if removed_member_ids:
                Task.objects.filter(board=board, assignee_id__in=removed_member_ids).update(assignee=None)

        board = (
            _with_project_stats(
                Board.objects.select_related("created_by", "department")
            .prefetch_related("members")
            )
            .get(id=board.id)
        )
        return Response(ProjectSerializer(board, context={"request": request}).data)


class ProjectTasksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        board = get_object_or_404(Board.objects.filter(is_personal=False).prefetch_related("members"), pk=pk)
        if not TaskPolicy.can_view_board(request.user, board):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        qs = Task.objects.filter(board=board)
        qs = _apply_task_filters(qs, request.query_params)
        qs = qs.select_related("assignee", "reporter", "column", "board")
        return Response(TaskSerializer(qs, many=True, context={"request": request}).data)


class ProjectReportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        board = get_object_or_404(
            _with_project_stats(
                Board.objects.filter(is_personal=False)
                .select_related("created_by", "department", "responsible_user")
                .prefetch_related("members")
            ),
            pk=pk,
        )
        if not TaskPolicy.can_view_board(request.user, board):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        tasks_qs = (
            Task.objects.filter(board=board)
            .select_related("assignee", "reporter", "column", "board")
            .order_by("id")
        )
        today = timezone.localdate()
        overdue_qs = tasks_qs.filter(due_date__lt=today).exclude(status=Task.Status.DONE)

        total_count = getattr(board, "task_count", 0) or 0
        completed_count = getattr(board, "completed_task_count", 0) or 0
        progress_percentage = round((completed_count / total_count) * 100) if total_count else 0

        status_counts = {
            "to_do": tasks_qs.filter(status=Task.Status.TO_DO).count(),
            "in_progress": tasks_qs.filter(status=Task.Status.IN_PROGRESS).count(),
            "review": tasks_qs.filter(status=Task.Status.REVIEW).count(),
            "done": tasks_qs.filter(status=Task.Status.DONE).count(),
            "blocked": tasks_qs.filter(status=Task.Status.BLOCKED).count(),
        }

        member_names = [
            member.get_full_name() or member.username
            for member in board.members.all().order_by("id")
        ]
        responsible_name = None
        if board.responsible_user_id:
            responsible_name = board.responsible_user.get_full_name() or board.responsible_user.username

        assignee_stats = []
        for member in board.members.all().order_by("id"):
            member_tasks = tasks_qs.filter(assignee=member)
            member_total = member_tasks.count()
            member_done = member_tasks.filter(status=Task.Status.DONE).count()
            member_in_progress = member_tasks.filter(status=Task.Status.IN_PROGRESS).count()
            member_review = member_tasks.filter(status=Task.Status.REVIEW).count()
            member_blocked = member_tasks.filter(status=Task.Status.BLOCKED).count()
            member_progress = round((member_done / member_total) * 100) if member_total else 0
            assignee_stats.append(
                {
                    "name": member.get_full_name() or member.username,
                    "username": member.username,
                    "total": member_total,
                    "done": member_done,
                    "in_progress": member_in_progress,
                    "review": member_review,
                    "blocked": member_blocked,
                    "progress": member_progress,
                }
            )

        overdue_lines = [
            f"- {task.title} ({task.assignee.username if task.assignee_id else 'Без исполнителя'}, срок: {task.due_date or '—'})"
            for task in overdue_qs
        ]
        status_lines = [
            f"- К выполнению: {status_counts['to_do']}",
            f"- В работе: {status_counts['in_progress']}",
            f"- На проверке: {status_counts['review']}",
            f"- Выполнено: {status_counts['done']}",
            f"- Заблокировано: {status_counts['blocked']}",
        ]
        assignee_lines = [
            f"- {item['name']}: всего {item['total']}, выполнено {item['done']}, в работе {item['in_progress']}, на проверке {item['review']}, прогресс {item['progress']}%"
            for item in assignee_stats
        ]
        template_report = "\n".join(
            [
                "Название проекта",
                board.name,
                "",
                board.created_at.strftime("%d.%m.%Y") if board.created_at else "—",
                "─",
                "В команде:",
                ", ".join(member_names) if member_names else "—",
                "",
                "О проекте",
                board.description or "Описание проекта пока не добавлено.",
                "",
                "Насколько проект готов",
                f"Статус проекта: {board.get_status_display()}",
                f"Руководитель проекта: {responsible_name or '—'}",
                f"Общее количество задач: {total_count}",
                f"Процент выполнения: {progress_percentage}%",
                "Задачи по статусам:",
                *status_lines,
                "",
                "Эффективность членов команды",
                *(assignee_lines or ["- Пока нет участников проекта"]),
                "",
                "Просроченные задачи",
                f"Количество просроченных задач: {overdue_qs.count()}",
                "Список:",
                *(overdue_lines or ["- Нет просроченных задач"]),
            ]
        )

        payload = {
            "project": ProjectSerializer(board, context={"request": request}).data,
            "total_task_count": total_count,
            "completed_task_count": completed_count,
            "progress_percentage": progress_percentage,
            "status_counts": status_counts,
            "overdue_task_count": overdue_qs.count(),
            "overdue_tasks": ProjectReportTaskSerializer(
                overdue_qs,
                many=True,
                context={"request": request},
            ).data,
            "assignee_stats": assignee_stats,
            "template_report": template_report,
        }
        return Response(payload)


class TaskCommentsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        comments = task.comments.select_related("author")
        return Response(TaskCommentSerializer(comments, many=True, context={"request": request}).data)

    def post(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = TaskCommentCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        comment = TaskComment.objects.create(
            task=task,
            author=request.user,
            text=serializer.validated_data["text"],
        )
        TasksAuditService.log_task_comment_added(request, task, comment)
        return Response(TaskCommentSerializer(comment, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskCommentDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, comment_id):
        comment = get_object_or_404(
            TaskComment.objects.select_related("task", "author", "task__column", "task__assignee"),
            pk=comment_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_comment(request.user, comment):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = TaskCommentCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        comment.text = serializer.validated_data["text"]
        comment.save(update_fields=["text", "updated_at"])
        TasksAuditService.log_task_comment_updated(request, comment.task, comment)
        return Response(TaskCommentSerializer(comment, context={"request": request}).data)

    def delete(self, request, pk, comment_id):
        comment = get_object_or_404(
            TaskComment.objects.select_related("task", "author", "task__column", "task__assignee"),
            pk=comment_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_comment(request.user, comment):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        task = comment.task
        comment_pk = comment.id
        comment.delete()
        TasksAuditService.log_task_comment_deleted(request, task, comment_pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        history = AuditLog.objects.filter(object_type="task", object_id=str(task.id)).select_related("user")
        return Response(TaskHistorySerializer(history, many=True, context={"request": request}).data)


class TaskSubTasksAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        subtasks = task.subtasks.select_related("created_by")
        return Response(SubTaskSerializer(subtasks, many=True, context={"request": request}).data)

    def post(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_manage_task_items(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = SubTaskCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        subtask = SubTask.objects.create(
            task=task,
            title=serializer.validated_data["title"],
            created_by=request.user,
        )
        TasksAuditService.log_subtask_changed(request, task, subtask.id, "subtask_created")
        return Response(SubTaskSerializer(subtask, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskSubTaskDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, subtask_id):
        subtask = get_object_or_404(
            SubTask.objects.select_related("task", "task__assignee", "task__column", "created_by"),
            pk=subtask_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_subtask(request.user, subtask):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = SubTaskUpdateSerializer(data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        changed = []
        if "title" in serializer.validated_data:
            subtask.title = serializer.validated_data["title"]
            changed.append("title")
        if "is_completed" in serializer.validated_data:
            subtask.is_completed = serializer.validated_data["is_completed"]
            changed.append("is_completed")
        if changed:
            subtask.save(update_fields=changed + ["updated_at"])
            TasksAuditService.log_subtask_changed(request, subtask.task, subtask.id, "subtask_updated")
        return Response(SubTaskSerializer(subtask, context={"request": request}).data)

    def delete(self, request, pk, subtask_id):
        subtask = get_object_or_404(
            SubTask.objects.select_related("task", "task__assignee", "task__column", "created_by"),
            pk=subtask_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_subtask(request.user, subtask):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        task = subtask.task
        deleted_id = subtask.id
        subtask.delete()
        TasksAuditService.log_subtask_changed(request, task, deleted_id, "subtask_deleted")
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskChecklistAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_view_task(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        items = task.checklist_items.select_related("created_by")
        return Response(ChecklistItemSerializer(items, many=True, context={"request": request}).data)

    def post(self, request, pk):
        task = get_object_or_404(Task.objects.select_related("assignee", "reporter", "column", "board"), pk=pk)
        if not TaskPolicy.can_manage_task_items(request.user, task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ChecklistItemCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        item = ChecklistItem.objects.create(
            task=task,
            title=serializer.validated_data["title"],
            created_by=request.user,
        )
        TasksAuditService.log_subtask_changed(request, task, item.id, "checklist_item_created")
        return Response(ChecklistItemSerializer(item, context={"request": request}).data, status=status.HTTP_201_CREATED)


class TaskChecklistDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk, item_id):
        item = get_object_or_404(
            ChecklistItem.objects.select_related("task", "task__assignee", "task__column", "created_by"),
            pk=item_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_task_items(request.user, item.task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        serializer = ChecklistItemUpdateSerializer(data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        changed = []
        if "title" in serializer.validated_data:
            item.title = serializer.validated_data["title"]
            changed.append("title")
        if "is_completed" in serializer.validated_data:
            item.is_completed = serializer.validated_data["is_completed"]
            changed.append("is_completed")
        if changed:
            item.save(update_fields=changed + ["updated_at"])
            TasksAuditService.log_subtask_changed(request, item.task, item.id, "checklist_item_updated")
        return Response(ChecklistItemSerializer(item, context={"request": request}).data)

    def delete(self, request, pk, item_id):
        item = get_object_or_404(
            ChecklistItem.objects.select_related("task", "task__assignee", "task__column", "created_by"),
            pk=item_id,
            task_id=pk,
        )
        if not TaskPolicy.can_manage_task_items(request.user, item.task):
            return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
        task = item.task
        deleted_id = item.id
        item.delete()
        TasksAuditService.log_subtask_changed(request, task, deleted_id, "checklist_item_deleted")
        return Response(status=status.HTTP_204_NO_CONTENT)
