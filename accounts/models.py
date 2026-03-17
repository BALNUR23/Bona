import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .managers import UserManager


def validate_photo_size(value):
    max_size = 2 * 1024 * 1024  
    if value.size > max_size:
        raise ValidationError("Максимальный размер фото — 2MB.")


# ================= RBAC =================
class Permission(models.Model):
    codename = models.CharField("Код разрешения", max_length=100, unique=True)
    module = models.CharField("Модуль", max_length=100, blank=True, default="")
    description = models.TextField("Описание", blank=True)

    class Meta:
        verbose_name = "Разрешение"
        verbose_name_plural = "Разрешения"

    def __str__(self):
        return self.codename


class Role(models.Model):
    class Name(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "SuperAdmin"
        ADMINISTRATOR = "ADMINISTRATOR", "Administrator"
        ADMIN = "ADMIN", "Admin"
        TEAMLEAD = "TEAMLEAD", "TeamLead"
        EMPLOYEE = "EMPLOYEE", "Employee"
        INTERN = "INTERN", "Intern"

    class Level(models.IntegerChoices):
        INTERN = 10, "Intern"
        EMPLOYEE = 20, "Employee"
        TEAMLEAD = 30, "TeamLead"
        ADMINISTRATOR = 40, "Administrator"
        ADMIN = 50, "Admin"
        SUPER_ADMIN = 60, "SuperAdmin"

    name = models.CharField("Название", max_length=50, unique=True)
    level = models.PositiveSmallIntegerField(
        "Уровень",
        choices=Level.choices,
        default=Level.INTERN,
    )
    description = models.TextField("Описание", blank=True)
    permissions = models.ManyToManyField(Permission, blank=True, verbose_name="Разрешения")

    class Meta:
        verbose_name = "Роль"
        verbose_name_plural = "Роли"

    def __str__(self):
        return self.name


# ================= Reference Tables =================
class Department(models.Model):
    name = models.CharField("Название", max_length=150, unique=True)
    comment = models.TextField("Комментарий", blank=True, default="")
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Родительский отдел",
    )
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Отдел"
        verbose_name_plural = "Отделы"

    def clean(self):
        super().clean()
        if self.parent_id and self.parent_id == self.id:
            raise ValidationError("Отдел не может быть родителем сам себе.")

    def __str__(self):
        return self.name


class Position(models.Model):
    name = models.CharField("Название", max_length=150, unique=True)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Должность"
        verbose_name_plural = "Должности"

    def __str__(self):
        return self.name


class DepartmentSubdivision(models.Model):
    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="subdivisions",
        verbose_name="Отдел",
    )
    name = models.CharField("Название", max_length=150)
    day_two_task_title = models.CharField("Заголовок задачи 2-го дня", max_length=255, blank=True)
    day_two_task_description = models.TextField("Описание задачи 2-го дня", blank=True)
    day_two_spec_url = models.URLField("Ссылка на ТЗ/документ", blank=True)
    day_three_task_title = models.CharField("Заголовок задачи 3-го дня", max_length=255, blank=True)
    day_three_task_description = models.TextField("Описание задачи 3-го дня", blank=True)
    day_three_spec_url = models.URLField("Ссылка на ТЗ/документ для 3-го дня", blank=True)
    is_active = models.BooleanField("Активен", default=True)

    class Meta:
        verbose_name = "Подотдел"
        verbose_name_plural = "Подотделы"
        unique_together = ("department", "name")
        ordering = ["department__name", "name"]

    def __str__(self):
        return f"{self.department.name}: {self.name}"


# ================= User =================
class User(AbstractUser):
    role = models.ForeignKey(Role, on_delete=models.PROTECT, verbose_name="Системная роль")
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Отдел",
    )
    position = models.ForeignKey(
        Position,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Должность",
    )
    manager = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_members",
        verbose_name="Руководитель",
    )
    subdivision = models.ForeignKey(
        DepartmentSubdivision,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="Подотдел",
    )

    custom_position = models.CharField("Своя должность", max_length=150, blank=True)

    telegram = models.CharField("Telegram", max_length=100, blank=True)
    phone = models.CharField("Телефон", max_length=50, blank=True)
    current_hourly_rate = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
    )
    photo = models.ImageField(
        "Фото",
        upload_to="users/",
        null=True,
        blank=True,
        validators=[
            FileExtensionValidator(allowed_extensions=["jpg", "jpeg", "png", "webp"]),
            validate_photo_size,
        ],
    )

    objects = UserManager()

    is_blocked = models.BooleanField("Заблокирован", default=False)
    failed_login_attempts = models.PositiveIntegerField("Неудачных входов", default=0)
    lockout_until = models.DateTimeField("Блокировка до", null=True, blank=True)
    intern_onboarding_started_at = models.DateTimeField(null=True, blank=True)
    intern_onboarding_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def has_permission(self, codename: str) -> bool:
        if not self.is_authenticated:
            return False

        if not self.role:
            return False

        return self.role.permissions.filter(codename=codename).exists()

    def has_any_permission(self, codenames: list[str]) -> bool:
        if not self.is_authenticated or not self.role:
            return False

        return self.role.permissions.filter(codename__in=codenames).exists()

    def has_all_permissions(self, codenames: list[str]) -> bool:
        if not self.is_authenticated or not self.role:
            return False

        user_codes = set(self.role.permissions.values_list("codename", flat=True))
        return set(codenames).issubset(user_codes)

    @property
    def is_admin_like(self) -> bool:
        if not self.role_id:
            return False
        return self.role.name in {Role.Name.ADMINISTRATOR, Role.Name.ADMIN}

    @property
    def can_manage_team(self) -> bool:
        if self.is_admin_like:
            return True
        if not self.role_id:
            return False
        return self.role.name == Role.Name.TEAMLEAD

    def save(self, *args, **kwargs):
        previous_role_name = None
        if self.pk:
            previous_role_name = (
                type(self)
                .objects.filter(pk=self.pk)
                .values_list("role__name", flat=True)
                .first()
            )

        super().save(*args, **kwargs)

        # If a teamlead is demoted to another role, clear manager for their team.
        if previous_role_name == Role.Name.TEAMLEAD and (
            not self.role_id or self.role.name != Role.Name.TEAMLEAD
        ):
            type(self).objects.filter(manager_id=self.pk).update(manager=None)


# ================= Security =================
class AuditLog(models.Model):
    """
    Primary audit storage backend.
    Use apps.audit.log_event as unified entrypoint for new writes.
    """

    class Level(models.TextChoices):
        INFO = "info", "Info"
        WARNING = "warning", "Warning"
        ERROR = "error", "Error"
        CRITICAL = "critical", "Critical"

    class Category(models.TextChoices):
        AUTH = "auth", "Authentication"
        USER = "user", "User management"
        SECURITY = "security", "Security"
        CONTENT = "content", "Content"
        SYSTEM = "system", "System"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Пользователь",
    )

    action = models.CharField("Действие", max_length=255)
    object_type = models.CharField("Тип объекта", max_length=100, blank=True)
    object_id = models.CharField("ID объекта", max_length=100, blank=True)

    level = models.CharField(
        "Уровень",
        max_length=20,
        choices=Level.choices,
        default=Level.INFO,
    )

    category = models.CharField(
        "Категория",
        max_length=50,
        choices=Category.choices,
        default=Category.SYSTEM,
    )

    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Журнал аудита"
        verbose_name_plural = "Журнал аудита"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["level"]),
            models.Index(fields=["category"]),
            models.Index(fields=["created_at"]),
            models.Index(fields=["user"]),
        ]

    @classmethod
    def log(
        cls,
        action,
        user=None,
        object_type="",
        object_id="",
        level=Level.INFO,
        category=Category.SYSTEM,
        ip_address=None,
    ):
        return cls.objects.create(
            user=user,
            action=action,
            object_type=object_type,
            object_id=object_id,
            level=level,
            category=category,
            ip_address=ip_address,
        )

    def __str__(self):
        return f"[{self.level.upper()}] {self.action}"


class LoginHistory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Пользователь",
    )
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)
    user_agent = models.TextField("User Agent", blank=True)
    success = models.BooleanField("Успешно", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "История входа"
        verbose_name_plural = "История входов"


class PasswordResetToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Пользователь",
    )
    token = models.UUIDField("Токен", default=uuid.uuid4, unique=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    is_used = models.BooleanField("Использован", default=False)

    class Meta:
        verbose_name = "Токен сброса пароля"
        verbose_name_plural = "Токены сброса пароля"

    def is_expired(self):
        return timezone.now() > self.created_at + timezone.timedelta(hours=24)


class UserSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Пользователь",
    )
    session_key = models.CharField("Ключ сессии", max_length=255)
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)
    expires_at = models.DateTimeField("Истекает")

    class Meta:
        verbose_name = "Сессия пользователя"
        verbose_name_plural = "Сессии пользователей"


class TwoFactorCode(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Пользователь",
    )
    code = models.CharField("Код", max_length=6)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    is_used = models.BooleanField("Использован", default=False)

    class Meta:
        verbose_name = "Код двухфакторной аутентификации"
        verbose_name_plural = "Коды двухфакторной аутентификации"

    def is_expired(self):
        return timezone.now() > self.created_at + timedelta(minutes=5)


class PromotionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="promotion_requests",
        verbose_name="Пользователь",
    )
    requested_role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="promotion_requests",
        verbose_name="Запрошенная роль",
    )
    reason = models.TextField("Причина", blank=True)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_promotion_requests",
        verbose_name="Проверил",
    )
    review_comment = models.TextField("Комментарий проверки", blank=True)
    reviewed_at = models.DateTimeField("Дата проверки", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Заявка на повышение"
        verbose_name_plural = "Заявки на повышение"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.user_id} -> {self.requested_role.name} ({self.status})"
