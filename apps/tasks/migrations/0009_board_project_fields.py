from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0008_checklistitem"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="board",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="board",
            name="end_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="board",
            name="responsible_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="responsible_boards",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="board",
            name="status",
            field=models.CharField(
                choices=[
                    ("planning", "Planning"),
                    ("active", "Active"),
                    ("on_hold", "On Hold"),
                    ("completed", "Completed"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="board",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddIndex(
            model_name="board",
            index=models.Index(fields=["status"], name="tasks_board_status_532f31_idx"),
        ),
        migrations.AddIndex(
            model_name="board",
            index=models.Index(fields=["end_date"], name="tasks_board_end_dat_60bf95_idx"),
        ),
    ]
