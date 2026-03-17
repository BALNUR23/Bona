from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from accounts.models import Department, DepartmentSubdivision, Permission, Role, User
from apps.tasks.models import Task
from onboarding_core.models import OnboardingDay, OnboardingProgress
from reports.models import OnboardingReport
from regulations.models import (
    Regulation,
    RegulationAcknowledgement,
    RegulationFeedback,
    RegulationReadProgress,
    RegulationQuiz,
    RegulationQuizAttempt,
)


class OnboardingFlowTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.role, _ = Role.objects.get_or_create(
            name=Role.Name.INTERN,
            defaults={"level": Role.Level.INTERN},
        )
        self.user = User.objects.create_user(
            username="intern",
            password="StrongPass123!",
            role=self.role,
        )
        self.day1 = OnboardingDay.objects.create(day_number=1, title="Day 1", is_active=True)
        self.day2 = OnboardingDay.objects.create(day_number=2, title="Day 2", is_active=True)
        self.client.force_authenticate(user=self.user)

    def test_cannot_complete_second_day_without_selected_role_and_report(self):
        response = self.client.post(f"/api/v1/onboarding/days/{self.day2.id}/complete/")
        self.assertEqual(response.status_code, 409)

    def test_can_complete_second_day_after_role_selected_and_report_sent(self):
        department = Department.objects.create(name="IT")
        subdivision = DepartmentSubdivision.objects.create(
            department=department,
            name="Backend",
            is_active=True,
        )
        self.user.department = department
        self.user.subdivision = subdivision
        self.user.save(update_fields=["department", "subdivision"])
        OnboardingProgress.objects.create(
            user=self.user,
            day=self.day1,
            status=OnboardingProgress.Status.DONE,
            completed_at=timezone.now(),
        )
        OnboardingReport.objects.create(
            user=self.user,
            day=self.day2,
            did="implemented parser",
            will_do="send PR",
            report_title="Fedresurs parser",
            report_description="Implemented parser by spec",
            github_url="https://github.com/example/repo/pull/1",
            status=OnboardingReport.Status.SENT,
        )

        response = self.client.post(f"/api/v1/onboarding/days/{self.day2.id}/complete/")
        self.assertEqual(response.status_code, 200)
        progress = OnboardingProgress.objects.get(user=self.user, day=self.day2)
        self.assertEqual(progress.status, OnboardingProgress.Status.DONE)

    def test_cannot_complete_first_day_without_mandatory_regulations_ack(self):
        reg = Regulation.objects.create(
            title="Mandatory Day 1",
            type=Regulation.RegulationType.LINK,
            external_url="https://example.com/day1",
            language=Regulation.Language.RU,
            is_active=True,
            is_mandatory_on_day_one=True,
        )
        response = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("missing_regulations", response.data)
        self.assertEqual(str(reg.id), response.data["missing_regulations"][0]["id"])

    def test_can_complete_first_day_after_mandatory_ack(self):
        reg = Regulation.objects.create(
            title="Mandatory Day 1",
            type=Regulation.RegulationType.LINK,
            external_url="https://example.com/day1",
            language=Regulation.Language.RU,
            is_active=True,
            is_mandatory_on_day_one=True,
        )
        RegulationAcknowledgement.objects.create(
            user=self.user,
            regulation=reg,
            user_full_name="intern",
            regulation_title=reg.title,
        )
        RegulationReadProgress.objects.create(
            user=self.user,
            regulation=reg,
            is_read=True,
        )
        RegulationFeedback.objects.create(
            user=self.user,
            regulation=reg,
            text="ok",
        )
        quiz = RegulationQuiz.objects.create(regulation=reg, title="Day 1 quiz", is_active=True)
        RegulationQuizAttempt.objects.create(user=self.user, quiz=quiz, score_percent=100, passed=True)
        response = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(response.status_code, 200)

    def test_cannot_complete_first_day_until_read_feedback_and_quiz_done(self):
        reg = Regulation.objects.create(
            title="Day 1 flow",
            type=Regulation.RegulationType.LINK,
            external_url="https://example.com/day1-flow",
            language=Regulation.Language.RU,
            is_active=True,
            is_mandatory_on_day_one=True,
        )
        self.day1.regulations.add(reg)
        RegulationAcknowledgement.objects.create(
            user=self.user,
            regulation=reg,
            user_full_name="intern",
            regulation_title=reg.title,
        )

        response = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(response.status_code, 409)
        self.assertIn("missing_steps", response.data)

        RegulationReadProgress.objects.create(user=self.user, regulation=reg, is_read=True)
        RegulationFeedback.objects.create(user=self.user, regulation=reg, text="ok")
        quiz = RegulationQuiz.objects.create(regulation=reg, title="Day 1 flow quiz", is_active=True)
        RegulationQuizAttempt.objects.create(user=self.user, quiz=quiz, score_percent=100, passed=True)

        second = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(second.status_code, 200)

    def test_complete_day_is_idempotent(self):
        first = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        second = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data["status"], OnboardingProgress.Status.DONE)

    def test_overview_contains_locked_statuses(self):
        response = self.client.get("/api/v1/onboarding/overview/")
        self.assertEqual(response.status_code, 200)

        statuses = {item["status"] for item in response.data["days"]}
        self.assertIn("LOCKED", statuses)

    @patch("onboarding_core.views.OnboardingAuditService.log_day_completed")
    def test_complete_logs_audit_payload(self, log_day_completed):
        response = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(response.status_code, 200)

        log_day_completed.assert_called_once()
        _, kwargs = log_day_completed.call_args
        self.assertFalse(kwargs["idempotent"])

    @patch("onboarding_core.views.OnboardingAuditService.log_day_completed")
    def test_idempotent_complete_logs_once_with_idempotent_flag(self, log_day_completed):
        self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        log_day_completed.reset_mock()

        response = self.client.post(f"/api/v1/onboarding/days/{self.day1.id}/complete/")
        self.assertEqual(response.status_code, 200)

        log_day_completed.assert_called_once()
        _, kwargs = log_day_completed.call_args
        self.assertTrue(kwargs["idempotent"])

    @patch("onboarding_core.views.OnboardingAuditService.log_overview_viewed")
    def test_overview_logs_audit_once(self, log_overview_viewed):
        response = self.client.get("/api/v1/onboarding/overview/")
        self.assertEqual(response.status_code, 200)

        log_overview_viewed.assert_called_once()
        _, kwargs = log_overview_viewed.call_args
        self.assertEqual(kwargs["total_days"], 2)
        self.assertEqual(kwargs["completed_days"], 0)


class OnboardingAdminAuditTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.permission, _ = Permission.objects.get_or_create(
            codename="reports_review",
            defaults={"module": "onboarding"},
        )
        self.role, _ = Role.objects.get_or_create(
            name=Role.Name.ADMIN,
            defaults={"level": Role.Level.ADMIN},
        )
        self.role.permissions.add(self.permission)

        self.user = User.objects.create_user(
            username="admin_u",
            password="StrongPass123!",
            role=self.role,
        )
        self.client.force_authenticate(user=self.user)

        self.day = OnboardingDay.objects.create(day_number=10, title="Day 10", is_active=True)
        OnboardingProgress.objects.create(user=self.user, day=self.day, status=OnboardingProgress.Status.IN_PROGRESS)

    @patch("onboarding_core.views.OnboardingAuditService.log_progress_viewed_admin")
    def test_admin_progress_list_logs_audit_once(self, log_progress_viewed_admin):
        response = self.client.get("/api/v1/onboarding/admin/onboarding/progress/?status=in_progress")
        self.assertEqual(response.status_code, 200)

        log_progress_viewed_admin.assert_called_once()
        args, _ = log_progress_viewed_admin.call_args
        self.assertEqual(args[1]["status"], "in_progress")


class OnboardingDayDetailTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.role, _ = Role.objects.get_or_create(
            name=Role.Name.INTERN,
            defaults={"level": Role.Level.INTERN},
        )
        self.user = User.objects.create_user(
            username="intern_detail",
            password="StrongPass123!",
            role=self.role,
        )
        self.department = Department.objects.create(name="IT detail")
        self.subdivision = DepartmentSubdivision.objects.create(
            department=self.department,
            name="Frontend",
            day_three_task_title="Day 3 Front",
            day_three_task_description="Build UI",
            day_three_spec_url="https://example.com/front-day3",
            is_active=True,
        )
        self.user.department = self.department
        self.user.subdivision = self.subdivision
        self.user.save(update_fields=["department", "subdivision"])
        self.day1 = OnboardingDay.objects.create(day_number=1, title="Day 1", is_active=True)
        self.day2 = OnboardingDay.objects.create(day_number=2, title="Day 2", is_active=True)
        self.day3 = OnboardingDay.objects.create(day_number=3, title="Day 3", is_active=True)
        self.reg = Regulation.objects.create(
            title="Reg Day 1",
            type=Regulation.RegulationType.LINK,
            external_url="https://example.com/day1-reg",
            language=Regulation.Language.RU,
            is_active=True,
            is_mandatory_on_day_one=True,
        )
        self.day1.regulations.add(self.reg)
        self.client.force_authenticate(user=self.user)

    def test_day_detail_returns_regulations_for_day(self):
        response = self.client.get(f"/api/v1/onboarding/days/{self.day1.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("regulations", response.data)
        self.assertEqual(len(response.data["regulations"]), 1)
        self.assertEqual(str(self.reg.id), str(response.data["regulations"][0]["id"]))

    def test_day_one_detail_creates_onboarding_task_with_next_day_deadline(self):
        response = self.client.get(f"/api/v1/onboarding/days/{self.day1.id}/")
        self.assertEqual(response.status_code, 200)

        task = Task.objects.get(assignee=self.user, onboarding_day=self.day1)
        self.assertEqual(task.title, "День 1: Ознакомление с регламентами компании")
        self.assertEqual(task.due_date, timezone.localdate() + timedelta(days=1))

    def test_day_two_detail_locked_until_day_one_completed(self):
        response = self.client.get(f"/api/v1/onboarding/days/{self.day2.id}/")
        self.assertEqual(response.status_code, 409)

    def test_day_three_detail_creates_subdivision_task_with_spec(self):
        OnboardingProgress.objects.create(
            user=self.user,
            day=self.day1,
            status=OnboardingProgress.Status.DONE,
            completed_at=timezone.now(),
        )
        OnboardingProgress.objects.create(
            user=self.user,
            day=self.day2,
            status=OnboardingProgress.Status.DONE,
            completed_at=timezone.now(),
        )

        response = self.client.get(f"/api/v1/onboarding/days/{self.day3.id}/")
        self.assertEqual(response.status_code, 200)

        task = Task.objects.get(assignee=self.user, onboarding_day=self.day3)
        self.assertEqual(task.title, "Day 3 Front")
        self.assertIn("Build UI", task.description)
        self.assertIn("ТЗ: https://example.com/front-day3", task.description)
