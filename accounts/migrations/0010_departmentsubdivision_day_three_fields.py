from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0009_set_backend_spec_url"),
    ]

    operations = [
        migrations.AddField(
            model_name="departmentsubdivision",
            name="day_three_task_title",
            field=models.CharField(blank=True, max_length=255, verbose_name="Заголовок задачи 3-го дня"),
        ),
        migrations.AddField(
            model_name="departmentsubdivision",
            name="day_three_task_description",
            field=models.TextField(blank=True, verbose_name="Описание задачи 3-го дня"),
        ),
        migrations.AddField(
            model_name="departmentsubdivision",
            name="day_three_spec_url",
            field=models.URLField(blank=True, verbose_name="Ссылка на ТЗ/документ для 3-го дня"),
        ),
    ]
