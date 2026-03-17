import uuid

from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from accounts.models import User
from onboarding_core.models import OnboardingDay


class OnboardingReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Черновик"
        SENT = "SENT", "Отправлен"
        ACCEPTED = "ACCEPTED", "Принят"
        REVISION = "REVISION", "На доработку"
        REJECTED = "REJECTED", "Отклонен"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="onboarding_reports",
        verbose_name="Стажер",
    )
    day = models.ForeignKey(
        OnboardingDay,
        on_delete=models.CASCADE,
        related_name="reports",
        verbose_name="День онбординга",
    )

    did = models.TextField(verbose_name="Что сделал")
    will_do = models.TextField(verbose_name="Что буду делать")
    problems = models.TextField(blank=True, verbose_name="Проблемы")
    report_title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Название отчета",
    )
    report_description = models.TextField(
        blank=True,
        default="",
        verbose_name="Описание отчета",
    )
    github_url = models.URLField(
        blank=True,
        default="",
        verbose_name="Ссылка на работу (GitHub)",
    )
    attachment = models.FileField(
        upload_to="onboarding_reports/",
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(
                allowed_extensions=[
                    "pdf",
                    "doc",
                    "docx",
                    "xls",
                    "xlsx",
                    "txt",
                    "ppt",
                    "pptx",
                    "zip",
                    "rar",
                    "7z",
                    "png",
                    "jpg",
                    "jpeg",
                ]
            )
        ],
        verbose_name="Вложение",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        verbose_name="Статус",
    )
    reviewer_comment = models.TextField(blank=True, verbose_name="Комментарий проверяющего")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def can_be_sent(self) -> bool:
        if self.day and self.day.day_number == 2:
            return bool(
                self.report_title.strip()
                and self.report_description.strip()
                and self.github_url.strip()
            )
        return bool(self.did.strip() and self.will_do.strip())

    def send(self):
        if not self.can_be_sent():
            raise ValueError("Report cannot be sent: required fields are empty")

        self.status = self.Status.SENT
        self.save(update_fields=["status", "updated_at"])
        OnboardingReportLog.objects.create(
            report=self,
            action=OnboardingReportLog.Action.SENT,
            author=self.user,
        )

    def can_be_modified(self) -> bool:
        return self.status in [self.Status.DRAFT, self.Status.REVISION, self.Status.REJECTED]

    def set_status(self, new_status, reviewer=None, comment=None):
        if self.status != self.Status.SENT:
            raise ValueError("Проверять можно только отправленные отчеты")
        if new_status in [self.Status.REVISION, self.Status.REJECTED]:
            if not comment or not comment.strip():
                raise ValueError("Комментарий обязателен для данного статуса")

        self.status = new_status
        if comment:
            self.reviewer_comment = comment.strip()

        self.save(update_fields=["status", "reviewer_comment", "updated_at"])
        OnboardingReportLog.objects.create(
            report=self,
            action=new_status,
            author=reviewer,
        )

        if new_status in [self.Status.REVISION, self.Status.REJECTED]:
            ReportNotification.objects.create(
                report=self,
                text=f"Ваш отчет отправлен на доработку. Комментарий: {self.reviewer_comment}",
            )

    class Meta:
        unique_together = ("user", "day")
        ordering = ["day__day_number"]
        verbose_name = "Отчет по онбордингу"
        verbose_name_plural = "Отчеты по онбордингу"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"{self.user} - День {self.day.day_number}"


class OnboardingReportLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "CREATED", "Создан"
        SENT = "SENT", "Отправлен"
        ACCEPTED = "ACCEPTED", "Принят"
        REVISION = "REVISION", "На доработку"
        REJECTED = "REJECTED", "Отклонен"

    report = models.ForeignKey(
        OnboardingReport,
        on_delete=models.CASCADE,
        related_name="logs",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    author = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class ReportNotification(models.Model):
    report = models.ForeignKey(
        OnboardingReport,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class EmployeeDailyReport(models.Model):
    class BlockerCategory(models.TextChoices):
        TECH_FAILURE = "tech_failure", "Тех. сбой"
        INFO = "info", "Инфо"
        COLLEAGUES = "colleagues", "Коллеги"
        PROCESS = "process", "Процесс"
        OTHER = "other", "Другое"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="employee_daily_reports",
    )
    report_date = models.DateField()
    summary = models.TextField(blank=True, default="")
    started_tasks = models.TextField(blank=True, default="")
    taken_tasks = models.TextField(blank=True, default="")
    completed_tasks = models.TextField(blank=True, default="")
    blockers = models.TextField(blank=True, default="")
    blocker_category = models.CharField(
        max_length=32,
        choices=BlockerCategory.choices,
        blank=True,
        default="",
    )
    is_late = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        created_at = self.created_at
        if self._state.adding or created_at is None:
            created_at = timezone.now()
        if timezone.is_naive(created_at):
            created_at = timezone.make_aware(created_at, timezone.get_current_timezone())

        created_at_local = timezone.localtime(created_at)
        cutoff = created_at_local.replace(hour=10, minute=0, second=0, microsecond=0)
        self.is_late = created_at_local > cutoff

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            update_fields.add("is_late")
            kwargs["update_fields"] = list(update_fields)

        super().save(*args, **kwargs)

    class Meta:
        unique_together = ("user", "report_date")
        ordering = ["-report_date", "-updated_at"]
