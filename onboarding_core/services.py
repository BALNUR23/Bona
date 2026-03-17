from datetime import timedelta

from django.utils import timezone

from accounts.models import Role
from apps.tasks.models import Board, Column, Task


def get_user_default_board(user) -> Board:
    board, _ = Board.objects.get_or_create(
        created_by=user,
        is_personal=True,
        defaults={"name": f"{user.username} board"},
    )
    Column.objects.get_or_create(
        board=board,
        order=1,
        defaults={"name": "New"},
    )
    return board


def ensure_day_task_for_intern(*, user, day):
    if not (getattr(user, "role_id", None) and user.role.name == Role.Name.INTERN):
        return None
    if day.day_number not in {2, 3}:
        return None
    if not getattr(user, "subdivision_id", None):
        return None

    subdivision = user.subdivision
    if not subdivision.is_active:
        return None

    board = get_user_default_board(user)
    column = board.columns.order_by("order", "id").first()
    if column is None:
        column = Column.objects.create(board=board, name="New", order=1)

    today = timezone.localdate()
    due_date = today + timedelta(days=1)
    if day.day_number == 2:
        title = subdivision.day_two_task_title.strip() or f"День 2: {subdivision.name}"
        spec_url = (subdivision.day_two_spec_url or "").strip()
        base_description = subdivision.day_two_task_description.strip()
    else:
        title = subdivision.day_three_task_title.strip() or f"День 3: {subdivision.name}"
        spec_url = (subdivision.day_three_spec_url or "").strip()
        base_description = subdivision.day_three_task_description.strip()

    if spec_url:
        if base_description:
            base_description = f"{base_description}\n\nТЗ: {spec_url}"
        else:
            base_description = f"ТЗ: {spec_url}"

    task, created = Task.objects.get_or_create(
        assignee=user,
        onboarding_day=day,
        defaults={
            "board": board,
            "column": column,
            "title": title,
            "description": base_description,
            "reporter": user.manager if user.manager_id else user,
            "due_date": due_date,
            "priority": Task.Priority.HIGH,
        },
    )
    if created:
        return task

    changed = []
    if task.title != title:
        task.title = title
        changed.append("title")
    if task.description != base_description:
        task.description = base_description
        changed.append("description")
    if task.due_date != due_date:
        task.due_date = due_date
        changed.append("due_date")
    if changed:
        task.save(update_fields=[*changed, "updated_at"])
    return task


def ensure_day_two_task_for_intern(*, user, day):
    return ensure_day_task_for_intern(user=user, day=day)
