from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0003_board_description_board_members"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="assignee",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_tasks",
                to="accounts.user",
            ),
        ),
    ]
