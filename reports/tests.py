from datetime import date, datetime
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch
from rest_framework.test import APIClient

from accounts.models import Permission, Role, User
from onboarding_core.models import OnboardingDay, OnboardingProgress
from .models import EmployeeDailyReport, OnboardingReport


class OnboardingReportTests(TestCase):
    def setUp(self):
        self.role, _ = Role.objects.get_or_create(
            name=Role.Name.INTERN,
            defaults={"level": Role.Level.INTERN},
        )
        self.user = User.objects.create_user(
            username="test",
            password="StrongPass123!",
            role=self.role,
        )
        self.day = OnboardingDay.objects.create(
            day_number=1,
            title="Test Day",
            is_active=True,
        )

    def test_cannot_review_non_sent_report(self):
        report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="a",
            will_do="b",
        )

        with self.assertRaises(ValueError):
            report.set_status(OnboardingReport.Status.ACCEPTED)

    def test_revision_requires_comment(self):
        report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="a",
            will_do="b",
            status=OnboardingReport.Status.SENT,
        )

        with self.assertRaises(ValueError):
            report.set_status(OnboardingReport.Status.REVISION)

    def test_accept_report(self):
        report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="a",
            will_do="b",
            status=OnboardingReport.Status.SENT,
        )

        report.set_status(OnboardingReport.Status.ACCEPTED)
        self.assertEqual(report.status, OnboardingReport.Status.ACCEPTED)

    def test_send_report(self):
        report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="a",
            will_do="b",
        )

        report.send()
        self.assertEqual(report.status, OnboardingReport.Status.SENT)

    def test_rejected_can_be_modified(self):
        report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="",
            will_do="",
            status=OnboardingReport.Status.REJECTED,
        )
        self.assertTrue(report.can_be_modified())


class SubmitReportApiTests(TestCase):
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
        self.day = OnboardingDay.objects.create(day_number=1, title="Day", is_active=True)
        self.client.force_authenticate(user=self.user)

    def test_empty_report_auto_rejected(self):
        response = self.client.post(
            "/api/v1/reports/submit/",
            {"day_id": str(self.day.id), "did": "", "will_do": "", "problems": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], OnboardingReport.Status.REJECTED)

    def test_non_empty_report_sent(self):
        response = self.client.post(
            "/api/v1/reports/submit/",
            {"day_id": str(self.day.id), "did": "Did", "will_do": "Will", "problems": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], OnboardingReport.Status.SENT)

    def test_day2_report_requires_title_description_and_github(self):
        day2 = OnboardingDay.objects.create(day_number=2, title="Day 2", is_active=True)
        response = self.client.post(
            "/api/v1/reports/submit/",
            {
                "day_id": str(day2.id),
                "did": "",
                "will_do": "",
                "report_title": "",
                "report_description": "",
                "github_url": "",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], OnboardingReport.Status.REJECTED)

    def test_day2_report_marks_progress_done(self):
        day2 = OnboardingDay.objects.create(day_number=2, title="Day 2", is_active=True)
        response = self.client.post(
            "/api/v1/reports/submit/",
            {
                "day_id": str(day2.id),
                "report_title": "Parser",
                "report_description": "Implemented parser by spec",
                "github_url": "https://github.com/example/repo",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], OnboardingReport.Status.SENT)
        progress = OnboardingProgress.objects.get(user=self.user, day=day2)
        self.assertEqual(progress.status, OnboardingProgress.Status.DONE)

    @patch("reports.views.ReportsAuditService.log_report_rejected_empty")
    def test_empty_report_logs_rejected_event_once(self, log_rejected):
        response = self.client.post(
            "/api/v1/reports/submit/",
            {"day_id": str(self.day.id), "did": "", "will_do": "", "problems": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        log_rejected.assert_called_once()

    @patch("reports.views.ReportsAuditService.log_report_submitted")
    def test_sent_report_logs_submitted_event_once(self, log_submitted):
        response = self.client.post(
            "/api/v1/reports/submit/",
            {"day_id": str(self.day.id), "did": "Done", "will_do": "Next", "problems": ""},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        log_submitted.assert_called_once()


class AdminReportReviewAuditTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.permission, _ = Permission.objects.get_or_create(
            codename="reports_review",
            defaults={"module": "reports"},
        )
        self.role, _ = Role.objects.get_or_create(
            name=Role.Name.ADMIN,
            defaults={"level": Role.Level.ADMIN},
        )
        self.role.permissions.add(self.permission)

        self.user = User.objects.create_user(
            username="reviewer",
            password="StrongPass123!",
            role=self.role,
        )
        self.client.force_authenticate(user=self.user)

        self.day = OnboardingDay.objects.create(day_number=2, title="Review Day", is_active=True)
        self.report = OnboardingReport.objects.create(
            user=self.user,
            day=self.day,
            did="Did",
            will_do="Will",
            status=OnboardingReport.Status.SENT,
        )

    @patch("reports.views.ReportsAuditService.log_review_status_changed")
    def test_review_status_change_logs_event_once(self, log_review_changed):
        response = self.client.patch(
            f"/api/v1/reports/admin/onboarding/reports/{self.report.id}/",
            {"status": OnboardingReport.Status.ACCEPTED},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        log_review_changed.assert_called_once()


class EmployeeDailyReportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.employee_role, _ = Role.objects.get_or_create(
            name=Role.Name.EMPLOYEE,
            defaults={"level": Role.Level.EMPLOYEE},
        )
        self.employee = User.objects.create_user(
            username="employee_reports",
            password="StrongPass123!",
            role=self.employee_role,
        )
        self.client.force_authenticate(self.employee)

    def test_employee_can_create_daily_report(self):
        response = self.client.post(
            "/api/v1/reports/employee/daily/",
            {"report_date": "2026-02-20", "summary": "Done tasks"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["summary"], "Done tasks")

    @patch("reports.models.timezone.now")
    def test_daily_report_marks_late_after_10(self, mocked_now):
        mocked_now.return_value = timezone.make_aware(
            datetime(2026, 2, 20, 10, 1, 0),
            timezone.get_current_timezone(),
        )
        report = EmployeeDailyReport.objects.create(
            user=self.employee,
            report_date=date(2026, 2, 20),
            summary="Late report",
        )
        self.assertTrue(report.is_late)
