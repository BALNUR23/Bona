from django.conf import settings
from django.db import migrations, models


def add_board_creators_as_members(apps, schema_editor):
    Board = apps.get_model("tasks", "Board")
    for board in Board.objects.all().iterator():
        if board.created_by_id:
            board.members.add(board.created_by_id)


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0002_task_onboarding_day_taskcomment_taskattachment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="description",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="board",
            name="members",
            field=models.ManyToManyField(blank=True, related_name="task_boards", to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(add_board_creators_as_members, migrations.RunPython.noop),
    ]
