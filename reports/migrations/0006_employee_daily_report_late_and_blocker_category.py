from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0005_alter_onboardingreport_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeedailyreport",
            name="blocker_category",
            field=models.CharField(
                blank=True,
                default="",
                max_length=32,
                choices=[
                    ("tech_failure", "Тех. сбой"),
                    ("info", "Инфо"),
                    ("colleagues", "Коллеги"),
                    ("process", "Процесс"),
                    ("other", "Другое"),
                ],
            ),
        ),
        migrations.AddField(
            model_name="employeedailyreport",
            name="is_late",
            field=models.BooleanField(default=False),
        ),
    ]
