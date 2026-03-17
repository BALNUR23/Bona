from django.db import migrations


DESIGNER_SPEC_URL = "https://docs.google.com/document/d/1oDmyN3zNdSqSQVCs8OZv3JrBtQyRmdO8t4UXLH93lpU/edit?usp=sharing"
FRONTEND_DAY3_SPEC_URL = "https://docs.google.com/document/d/1ogmAwK6shKmZkdJXp5jgR9waQrETVel_VcPOqUMl6pU/edit?usp=sharing"


def _find_or_create_it_department(Department):
    aliases = ["IT", "Айти", "Разработ", "Тех"]
    qs = Department.objects.all()
    for alias in aliases:
        dept = qs.filter(name__icontains=alias).first()
        if dept:
            return dept
    dept, _ = Department.objects.get_or_create(name="IT департамент", defaults={"is_active": True})
    return dept


def _find_or_create_subdivision(DepartmentSubdivision, department, primary_name, aliases):
    names = [primary_name, *aliases]
    for name in names:
        existing = DepartmentSubdivision.objects.filter(name__iexact=name).first()
        if existing:
            return existing

    for alias in aliases:
        existing = DepartmentSubdivision.objects.filter(name__icontains=alias).first()
        if existing:
            return existing

    subdivision, _ = DepartmentSubdivision.objects.get_or_create(
        department=department,
        name=primary_name,
        defaults={"is_active": True},
    )
    return subdivision


def _update_day_two(subdivision, *, title, description, spec_url=""):
    subdivision.day_two_task_title = title
    subdivision.day_two_task_description = description
    subdivision.day_two_spec_url = spec_url
    if not subdivision.is_active:
        subdivision.is_active = True
    subdivision.save(
        update_fields=[
            "day_two_task_title",
            "day_two_task_description",
            "day_two_spec_url",
            "is_active",
        ]
    )


def _update_day_three(subdivision, *, title, description, spec_url=""):
    subdivision.day_three_task_title = title
    subdivision.day_three_task_description = description
    subdivision.day_three_spec_url = spec_url
    if not subdivision.is_active:
        subdivision.is_active = True
    subdivision.save(
        update_fields=[
            "day_three_task_title",
            "day_three_task_description",
            "day_three_spec_url",
            "is_active",
        ]
    )


def forwards(apps, schema_editor):
    Department = apps.get_model("accounts", "Department")
    DepartmentSubdivision = apps.get_model("accounts", "DepartmentSubdivision")

    it_department = _find_or_create_it_department(Department)

    backend = _find_or_create_subdivision(
        DepartmentSubdivision,
        it_department,
        "Бэкенд разработчик",
        aliases=["Backend", "Бэкенд", "Backend разработчик"],
    )
    frontend = _find_or_create_subdivision(
        DepartmentSubdivision,
        it_department,
        "Фронтенд разработчик",
        aliases=["Frontend", "Фронтенд", "Frontend разработчик"],
    )
    designer = _find_or_create_subdivision(
        DepartmentSubdivision,
        it_department,
        "Дизайнер",
        aliases=["Designer", "UI/UX дизайнер", "UI UX дизайнер", "UI/UX Designer"],
    )
    project_manager = _find_or_create_subdivision(
        DepartmentSubdivision,
        it_department,
        "Проект менеджер",
        aliases=["Project Manager", "Менеджер проекта", "PM"],
    )

    _update_day_two(
        designer,
        title="День 2: Дизайн-задача",
        description=(
            "Выполнить задание по дизайн-ТЗ. "
            "Итог загрузить в отчет: укажите ссылку на GitHub (если использовался) и приложите файл макета/прототипа при необходимости."
        ),
        spec_url=DESIGNER_SPEC_URL,
    )

    _update_day_two(
        project_manager,
        title="День 2: ТЗ на сайт",
        description=(
            "Написать ТЗ на создание сайта. Тематика, сложность и интересные фичи - на усмотрение сотрудника."
        ),
        spec_url="",
    )

    _update_day_three(
        backend,
        title="День 3: Бэкенд разработка",
        description=(
            "Реализовать парсер аукционов квартир с bankrotbaza.ru (Selenium + парсинг HTML), "
            "собрать ключевые поля карточек и сохранить результат в Excel. "
            "В отчете обязательно укажите ссылку на GitHub с выполненной работой и приложите файл выгрузки (если требуется). "
            "ТЗ: файл 'ТЗ для банкрот базы, уровень 2.pdf'."
        ),
        spec_url="",
    )

    _update_day_three(
        frontend,
        title="День 3: Фронтенд разработка",
        description=(
            "Выполнить фронтенд-задачу по ТЗ 3-го дня. "
            "В отчете обязательно укажите ссылку на GitHub с выполненной работой и приложите файл (если требуется)."
        ),
        spec_url=FRONTEND_DAY3_SPEC_URL,
    )


def backwards(apps, schema_editor):
    # Backward migration is intentionally no-op for seeded task content.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_departmentsubdivision_day_three_fields"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
