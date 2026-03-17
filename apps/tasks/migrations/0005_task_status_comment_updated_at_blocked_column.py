from django.db import migrations, models


STATUS_BY_COLUMN_ORDER = {
    1: "to_do",
    2: "in_progress",
    3: "review",
    4: "done",
    5: "blocked",
}


def populate_task_status_and_blocked_column(apps, schema_editor):
    Board = apps.get_model("tasks", "Board")
    Column = apps.get_model("tasks", "Column")
    Task = apps.get_model("tasks", "Task")

    for board in Board.objects.all().iterator():
        Column.objects.get_or_create(
            board_id=board.id,
            order=5,
            defaults={"name": "Blocked"},
        )

    for task in Task.objects.select_related("column").all().iterator():
        task.status = STATUS_BY_COLUMN_ORDER.get(task.column.order, "to_do")
        task.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0004_task_nullable_assignee"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[
                    ("to_do", "To Do"),
                    ("in_progress", "In Progress"),
                    ("review", "Review"),
                    ("done", "Done"),
                    ("blocked", "Blocked"),
                ],
                default="to_do",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="taskcomment",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=None),
            preserve_default=False,
        ),
        migrations.RunPython(populate_task_status_and_blocked_column, migrations.RunPython.noop),
    ]
