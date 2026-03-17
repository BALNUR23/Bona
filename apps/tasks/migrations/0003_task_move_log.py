from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0002_task_onboarding_day_taskcomment_taskattachment"),
    ]

    operations = [
        migrations.CreateModel(
            name="TaskMoveLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="task_move_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "from_column",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="move_logs_from",
                        to="tasks.column",
                    ),
                ),
                (
                    "task",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="move_logs",
                        to="tasks.task",
                    ),
                ),
                (
                    "to_column",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="move_logs_to",
                        to="tasks.column",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="taskmovelog",
            index=models.Index(fields=["actor"], name="tasks_taskm_actor_i_6b94c2_idx"),
        ),
        migrations.AddIndex(
            model_name="taskmovelog",
            index=models.Index(fields=["task"], name="tasks_taskm_task_i_7d6b72_idx"),
        ),
        migrations.AddIndex(
            model_name="taskmovelog",
            index=models.Index(fields=["created_at"], name="tasks_taskm_created_3f2d64_idx"),
        ),
    ]
