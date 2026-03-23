from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tasks", "0009_board_project_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="board",
            name="status",
            field=models.CharField(
                choices=[
                    ("planning", "Planning"),
                    ("active", "Active"),
                    ("on_hold", "On Hold"),
                    ("completed", "Completed"),
                    ("archived", "Archived"),
                ],
                default="active",
                max_length=20,
            ),
        ),
    ]
