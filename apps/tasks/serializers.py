from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ErrorDetail

from accounts.models import AuditLog
from common.i18n import request_language, status_label, tr

from .models import Board, ChecklistItem, Column, SubTask, Task, TaskComment


User = get_user_model()


class TaskSerializer(serializers.ModelSerializer):
    assignee_username = serializers.SerializerMethodField()
    reporter_username = serializers.CharField(source="reporter.username", read_only=True)
    column_name = serializers.CharField(source="column.name", read_only=True)
    column_order = serializers.IntegerField(source="column.order", read_only=True)
    board_name = serializers.CharField(source="board.name", read_only=True)
    is_personal_board = serializers.BooleanField(source="board.is_personal", read_only=True)
    board_columns = serializers.SerializerMethodField()
    priority_label = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_overdue = serializers.SerializerMethodField()
    assignee_display = serializers.SerializerMethodField()
    subtasks_total = serializers.SerializerMethodField()
    subtasks_completed = serializers.SerializerMethodField()
    checklist_total = serializers.SerializerMethodField()
    checklist_completed = serializers.SerializerMethodField()
    completion_percentage = serializers.SerializerMethodField()
    can_complete_parent = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            "id",
            "board",
            "board_name",
            "is_personal_board",
            "column",
            "title",
            "description",
            "assignee",
            "assignee_username",
            "assignee_display",
            "reporter",
            "reporter_username",
            "due_date",
            "priority",
            "priority_label",
            "status",
            "status_label",
            "is_overdue",
            "subtasks_total",
            "subtasks_completed",
            "checklist_total",
            "checklist_completed",
            "completion_percentage",
            "can_complete_parent",
            "onboarding_day",
            "column_name",
            "column_order",
            "board_columns",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("reporter", "created_at", "updated_at")

    def get_board_columns(self, obj):
        return [
            {"id": col.id, "name": col.name, "order": col.order}
            for col in obj.board.columns.order_by("order", "id")
        ]

    def get_assignee_username(self, obj):
        return obj.assignee.username if obj.assignee_id and obj.assignee else ""

    def get_priority_label(self, obj):
        return status_label(obj.priority, request_language(self.context.get("request")))

    def get_is_overdue(self, obj):
        if not obj.due_date:
            return False
        if obj.status == Task.Status.DONE:
            return False
        return obj.due_date < timezone.localdate()

    def get_assignee_display(self, obj):
        if obj.assignee_id and obj.assignee:
            return obj.assignee.username
        return "Без исполнителя"

    def get_subtasks_total(self, obj):
        return obj.subtasks.count()

    def get_subtasks_completed(self, obj):
        return obj.subtasks.filter(is_completed=True).count()

    def get_can_complete_parent(self, obj):
        total = obj.subtasks.count()
        if total == 0:
            return False
        return obj.subtasks.filter(is_completed=True).count() == total and obj.status != Task.Status.DONE

    def get_checklist_total(self, obj):
        return obj.checklist_items.count()

    def get_checklist_completed(self, obj):
        return obj.checklist_items.filter(is_completed=True).count()

    def get_completion_percentage(self, obj):
        subtasks_total = obj.subtasks.count()
        checklist_total = obj.checklist_items.count()
        total_items = subtasks_total + checklist_total
        if total_items == 0:
            return 0
        completed_items = (
            obj.subtasks.filter(is_completed=True).count()
            + obj.checklist_items.filter(is_completed=True).count()
        )
        return round((completed_items / total_items) * 100)


class TaskCreateSerializer(serializers.Serializer):
    board_id = serializers.IntegerField(required=False, allow_null=True)
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True)
    assignee_id = serializers.IntegerField(required=False, allow_null=True)
    onboarding_day_id = serializers.UUIDField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, default=Task.Priority.MEDIUM)

    def validate_board_id(self, value):
        if value is None:
            return value
        board = Board.objects.filter(id=value).first()
        if not board:
            request = self.context.get("request")
            raise serializers.ValidationError(
                ErrorDetail(
                    tr("board_not_found", request_language(request)),
                    code="board_not_found",
                )
            )
        return value

    def validate_assignee_id(self, value):
        if value is None:
            return value
        user = User.objects.filter(id=value).first()
        if not user:
            request = self.context.get("request")
            raise serializers.ValidationError(
                ErrorDetail(
                    tr("assignee_not_found", request_language(request)),
                    code="assignee_not_found",
                )
            )
        return value


class TaskMoveSerializer(serializers.Serializer):
    column_id = serializers.IntegerField()

    def validate_column_id(self, value):
        if not Column.objects.filter(id=value).exists():
            request = self.context.get("request")
            raise serializers.ValidationError(
                ErrorDetail(
                    tr("column_not_found", request_language(request)),
                    code="column_not_found",
                )
            )
        return value


class TaskUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    assignee = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    priority = serializers.ChoiceField(choices=Task.Priority.choices, required=False)
    status = serializers.ChoiceField(choices=Task.Status.choices, required=False)


class ProjectMemberSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField(source="role.name", read_only=True)

    class Meta:
        model = User
        fields = ("id", "username", "full_name", "role", "department_id")

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username


class ProjectSerializer(serializers.ModelSerializer):
    members = ProjectMemberSerializer(many=True, read_only=True)
    task_count = serializers.IntegerField(read_only=True)
    completed_task_count = serializers.IntegerField(read_only=True)
    in_progress_task_count = serializers.IntegerField(read_only=True)
    overdue_task_count = serializers.IntegerField(read_only=True)
    progress_percentage = serializers.SerializerMethodField()
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)
    responsible_user_username = serializers.CharField(source="responsible_user.username", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    def get_progress_percentage(self, obj):
        total = getattr(obj, "task_count", 0) or 0
        completed = getattr(obj, "completed_task_count", 0) or 0
        if total <= 0:
            return 0
        return round((completed / total) * 100)

    class Meta:
        model = Board
        fields = (
            "id",
            "name",
            "description",
            "status",
            "status_label",
            "end_date",
            "created_at",
            "updated_at",
            "department",
            "created_by",
            "created_by_username",
            "responsible_user",
            "responsible_user_username",
            "members",
            "task_count",
            "completed_task_count",
            "in_progress_task_count",
            "overdue_task_count",
            "progress_percentage",
        )
        read_only_fields = ("created_by", "created_at", "updated_at")


class ProjectWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    description = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(choices=Board.Status.choices, required=False)
    end_date = serializers.DateField(required=False, allow_null=True)
    responsible_user_id = serializers.IntegerField(required=False, allow_null=True)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=True,
    )

    def validate_responsible_user_id(self, value):
        if value is None:
            return value
        user = User.objects.filter(id=value).first()
        if not user:
            request = self.context.get("request")
            raise serializers.ValidationError(
                ErrorDetail(
                    tr("assignee_not_found", request_language(request)),
                    code="assignee_not_found",
                )
            )
        return value


class TaskCommentSerializer(serializers.ModelSerializer):
    author_username = serializers.CharField(source="author.username", read_only=True)

    class Meta:
        model = TaskComment
        fields = ("id", "task", "author", "author_username", "text", "created_at", "updated_at")
        read_only_fields = ("task", "author", "created_at", "updated_at")


class TaskCommentCreateSerializer(serializers.Serializer):
    text = serializers.CharField(allow_blank=False, trim_whitespace=True)


class TaskHistorySerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = ("id", "action", "level", "category", "actor_username", "created_at")


class SubTaskSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = SubTask
        fields = (
            "id",
            "task",
            "title",
            "is_completed",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("task", "created_by", "created_at", "updated_at")


class SubTaskCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)


class SubTaskUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    is_completed = serializers.BooleanField(required=False)


class ChecklistItemSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source="created_by.username", read_only=True)

    class Meta:
        model = ChecklistItem
        fields = (
            "id",
            "task",
            "title",
            "is_completed",
            "created_by",
            "created_by_username",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("task", "created_by", "created_at", "updated_at")


class ChecklistItemCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)


class ChecklistItemUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    is_completed = serializers.BooleanField(required=False)


class ProjectReportTaskSerializer(serializers.ModelSerializer):
    assignee_username = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    is_overdue = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            "id",
            "title",
            "assignee_username",
            "status",
            "status_label",
            "priority",
            "due_date",
            "is_overdue",
        )

    def get_assignee_username(self, obj):
        return obj.assignee.username if obj.assignee_id and obj.assignee else ""

    def get_is_overdue(self, obj):
        if not obj.due_date:
            return False
        if obj.status == Task.Status.DONE:
            return False
        return obj.due_date < timezone.localdate()
