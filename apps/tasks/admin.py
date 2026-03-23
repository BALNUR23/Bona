from django import forms
from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import Board, ChecklistItem, Column, SubTask, Task, TaskComment


class ColumnInline(admin.TabularInline):
    model = Column
    extra = 0
    ordering = ("order", "id")


class TaskAdminForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            return cleaned

        title = (cleaned.get("title") or "").strip()
        board = cleaned.get("board")
        assignee = cleaned.get("assignee")
        reporter = cleaned.get("reporter")
        due_date = cleaned.get("due_date")

        if not title or not board or not reporter:
            return cleaned

        duplicate_exists = Task.objects.filter(
            board=board,
            title=title,
            assignee=assignee,
            reporter=reporter,
            due_date=due_date,
        ).exists()
        if duplicate_exists:
            raise ValidationError("Такая задача уже существует. Повторное создание заблокировано.")

        return cleaned


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "is_personal", "department", "created_by")
    list_filter = ("is_personal", "department")
    search_fields = ("name", "description", "created_by__username")
    filter_horizontal = ("members",)
    inlines = [ColumnInline]


@admin.register(Column)
class ColumnAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "board", "order")
    list_filter = ("board",)
    search_fields = ("name", "board__name")
    autocomplete_fields = ("board",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    form = TaskAdminForm
    list_display = ("id", "title", "board", "assignee", "reporter", "status", "priority", "due_date", "created_at")
    list_filter = ("status", "priority", "board", "board__department")
    search_fields = ("title", "description", "assignee__username", "reporter__username")
    list_select_related = ("board", "column", "assignee", "reporter", "onboarding_day")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "board":
            kwargs["queryset"] = Board.objects.order_by("name", "id")
        elif db_field.name == "column":
            kwargs["queryset"] = Column.objects.select_related("board").order_by("board__name", "order", "id")
        elif db_field.name in {"assignee", "reporter"}:
            kwargs["queryset"] = (
                kwargs.get("queryset") or db_field.remote_field.model.objects.all()
            ).order_by("username")
        elif db_field.name == "onboarding_day":
            kwargs["queryset"] = (
                kwargs.get("queryset") or db_field.remote_field.model.objects.all()
            ).order_by("day_number", "position", "id")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(TaskComment)
class TaskCommentAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "author", "created_at", "updated_at")
    search_fields = ("task__title", "author__username", "text")
    autocomplete_fields = ("task", "author")


@admin.register(SubTask)
class SubTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "title", "is_completed", "created_by", "created_at")
    list_filter = ("is_completed",)
    search_fields = ("task__title", "title", "created_by__username")
    autocomplete_fields = ("task", "created_by")


@admin.register(ChecklistItem)
class ChecklistItemAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "title", "is_completed", "created_by", "created_at")
    list_filter = ("is_completed",)
    search_fields = ("task__title", "title", "created_by__username")
    autocomplete_fields = ("task", "created_by")
