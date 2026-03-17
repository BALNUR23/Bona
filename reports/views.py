from django.shortcuts import get_object_or_404

from rest_framework import status as drf_status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.views import APIView
from django.db.models import Q
from django.utils import timezone

from accounts.access_policy import AccessPolicy
from accounts.models import Role, User
from common.models import Notification
from common.notification_codes import NotificationCode, NotificationEntity
from .models import EmployeeDailyReport, OnboardingReport, OnboardingReportLog, ReportNotification
from .serializers import (
    EmployeeDailyReportSerializer,
    AdminOnboardingReportSerializer,
    OnboardingReportCreateSerializer,
    OnboardingReportLogSerializer,
    ReportNotificationSerializer,
)

from accounts.permissions import HasPermission
from onboarding_core.models import OnboardingDay, OnboardingProgress
from onboarding_core.utils import is_deadline_passed
from .audit import ReportsAuditService


SYSTEM_REJECT_COMMENT = "Report is empty. Fill required fields before submit."


class SubmitOnboardingReportView(APIView):
    permission_classes = [IsAuthenticated]

    def _mark_rejected(self, report):
        report.status = OnboardingReport.Status.REJECTED
        report.reviewer_comment = SYSTEM_REJECT_COMMENT
        report.save(update_fields=["status", "reviewer_comment", "updated_at"])
        OnboardingReportLog.objects.create(
            report=report,
            action=OnboardingReportLog.Action.REJECTED,
            author=None,
        )

    def post(self, request):
        if not AccessPolicy.is_intern(request.user):
            return Response(
                {"detail": "Only intern can submit onboarding report."},
                status=drf_status.HTTP_403_FORBIDDEN,
            )
        serializer = OnboardingReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        day = get_object_or_404(
            OnboardingDay,
            id=serializer.validated_data["day_id"],
            is_active=True,
        )

        if is_deadline_passed(day):
            ReportsAuditService.log_report_deadline_blocked(request, day)
            return Response(
                {"detail": "Report deadline has passed"},
                status=drf_status.HTTP_403_FORBIDDEN,
            )

        did = serializer.validated_data.get("did", "").strip()
        will_do = serializer.validated_data.get("will_do", "").strip()
        problems = serializer.validated_data.get("problems", "").strip()
        report_title = serializer.validated_data.get("report_title", "").strip()
        report_description = serializer.validated_data.get("report_description", "").strip()
        github_url = serializer.validated_data.get("github_url", "").strip()
        attachment = serializer.validated_data.get("attachment")

        if day.day_number == 2:
            did = did or report_title
            will_do = will_do or report_description

        existing_report = OnboardingReport.objects.filter(
            user=request.user,
            day=day,
        ).first()

        if existing_report and not existing_report.can_be_modified():
            ReportsAuditService.log_report_edit_conflict(request, existing_report)
            return Response(
                {"detail": "Report cannot be modified"},
                status=drf_status.HTTP_409_CONFLICT,
            )

        if existing_report:
            existing_report.did = did
            existing_report.will_do = will_do
            existing_report.problems = problems
            existing_report.report_title = report_title
            existing_report.report_description = report_description
            existing_report.github_url = github_url
            if attachment is not None:
                existing_report.attachment = attachment
            existing_report.save(
                update_fields=[
                    "did",
                    "will_do",
                    "problems",
                    "report_title",
                    "report_description",
                    "github_url",
                    "attachment",
                    "updated_at",
                ]
            )

            if existing_report.can_be_sent():
                existing_report.send()
                ReportsAuditService.log_report_submitted(request, existing_report)
                if day.day_number == 2:
                    OnboardingProgress.objects.update_or_create(
                        user=request.user,
                        day=day,
                        defaults={
                            "status": OnboardingProgress.Status.DONE,
                            "completed_at": timezone.now(),
                        },
                    )
            else:
                self._mark_rejected(existing_report)
                ReportsAuditService.log_report_rejected_empty(request, existing_report)

            return Response(
                {
                    "id": existing_report.id,
                    "status": existing_report.status,
                }
            )

        report = OnboardingReport.objects.create(
            user=request.user,
            day=day,
            did=did,
            will_do=will_do,
            problems=problems,
            report_title=report_title,
            report_description=report_description,
            github_url=github_url,
            attachment=attachment,
        )

        OnboardingReportLog.objects.create(
            report=report,
            action=OnboardingReportLog.Action.CREATED,
            author=request.user,
        )

        if report.can_be_sent():
            report.send()
            ReportsAuditService.log_report_submitted(request, report)
            if day.day_number == 2:
                OnboardingProgress.objects.update_or_create(
                    user=request.user,
                    day=day,
                    defaults={
                        "status": OnboardingProgress.Status.DONE,
                        "completed_at": timezone.now(),
                    },
                )
        else:
            self._mark_rejected(report)
            ReportsAuditService.log_report_rejected_empty(request, report)

        return Response(
            {
                "id": report.id,
                "status": report.status,
            },
            status=drf_status.HTTP_201_CREATED,
        )


class AdminOnboardingReportViewSet(ModelViewSet):
    queryset = OnboardingReport.objects.all()
    serializer_class = AdminOnboardingReportSerializer
    permission_classes = [IsAuthenticated, HasPermission]
    required_permission = "reports_review"
    http_method_names = ["get", "patch"]

    def get_queryset(self):
        qs = OnboardingReport.objects.select_related("user", "day").all()
        if AccessPolicy.is_admin(self.request.user) and not AccessPolicy.is_super_admin(self.request.user):
            if self.request.user.department_id:
                qs = qs.filter(user__department_id=self.request.user.department_id)
        elif AccessPolicy.is_main_admin(self.request.user):
            pass
        return qs

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        old_status = instance.status

        new_status = request.data.get("status")
        comment = request.data.get("reviewer_comment")

        instance.set_status(
            new_status=new_status,
            reviewer=request.user,
            comment=comment,
        )
        ReportsAuditService.log_review_status_changed(
            request,
            report=instance,
            from_status=old_status,
            to_status=instance.status,
            has_comment=bool((comment or "").strip()),
        )

        serializer = self.get_serializer(instance)
        return Response(serializer.data)


class OnboardingReportLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = OnboardingReportLogSerializer
    permission_classes = [IsAuthenticated, HasPermission]
    required_permission = "logs_read"

    def get_queryset(self):
        return OnboardingReportLog.objects.select_related(
            "report", "author"
        )


class ReportNotificationViewSet(viewsets.ModelViewSet):
    serializer_class = ReportNotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ReportNotification.objects.filter(
            report__user=self.request.user
        )


class EmployeeDailyReportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not (
            AccessPolicy.is_employee(request.user)
            or AccessPolicy.is_teamlead(request.user)
            or AccessPolicy.is_admin(request.user)
            or AccessPolicy.is_main_admin(request.user)
            or AccessPolicy.is_super_admin(request.user)
        ):
            return Response({"detail": "Access denied."}, status=drf_status.HTTP_403_FORBIDDEN)

        report_date = request.query_params.get("date")
        qs = EmployeeDailyReport.objects.select_related("user")
        if AccessPolicy.is_super_admin(request.user):
            pass
        elif AccessPolicy.is_main_admin(request.user):
            pass
        elif AccessPolicy.is_admin(request.user):
            if request.user.department_id:
                qs = qs.filter(user__department_id=request.user.department_id)
            else:
                qs = qs.none()
        elif AccessPolicy.is_teamlead(request.user):
            team_filter = Q(user__manager_id=request.user.id) | Q(user=request.user)
            if request.user.subdivision_id:
                team_filter = team_filter | Q(user__subdivision_id=request.user.subdivision_id)
            qs = qs.filter(team_filter)
        else:
            qs = qs.filter(user=request.user)
        if report_date:
            qs = qs.filter(report_date=report_date)

        serializer = EmployeeDailyReportSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        if not (AccessPolicy.is_employee(request.user) or AccessPolicy.is_teamlead(request.user)):
            return Response({"detail": "Only employee/teamlead can submit report."}, status=drf_status.HTTP_403_FORBIDDEN)

        report_date = request.data.get("report_date") or timezone.localdate()
        started_tasks = (request.data.get("started_tasks") or "").strip()
        taken_tasks = (request.data.get("taken_tasks") or "").strip()
        completed_tasks = (request.data.get("completed_tasks") or "").strip()
        blockers = (request.data.get("blockers") or "").strip()
        blocker_category = (request.data.get("blocker_category") or "").strip()
        summary = (request.data.get("summary") or "").strip()
        if not summary:
            summary = (
                f"Начал: {started_tasks or '-'}\n"
                f"Отправил на обработку: {taken_tasks or '-'}\n"
                f"Завершил: {completed_tasks or '-'}\n"
                f"Проблемы/блокеры: {blockers or '-'}"
            )

        report, _ = EmployeeDailyReport.objects.update_or_create(
            user=request.user,
            report_date=report_date,
            defaults={
                "summary": summary,
                "started_tasks": started_tasks,
                "taken_tasks": taken_tasks,
                "completed_tasks": completed_tasks,
                "blockers": blockers,
                "blocker_category": blocker_category,
            },
        )

        recipient_ids = set()
        if request.user.manager_id:
            recipient_ids.add(request.user.manager_id)
        else:
            teamlead_qs = User.objects.filter(role__name=Role.Name.TEAMLEAD, is_active=True)
            if request.user.subdivision_id:
                teamlead_qs = teamlead_qs.filter(subdivision_id=request.user.subdivision_id)
            elif request.user.department_id:
                teamlead_qs = teamlead_qs.filter(department_id=request.user.department_id)
            recipient_ids.update(teamlead_qs.values_list("id", flat=True))
        admin_qs = User.objects.filter(
            role__name__in=AccessPolicy.admin_recipient_role_names(),
            is_active=True,
        )
        if request.user.department_id:
            # Keep department heads/administrators scoped to their department,
            # while global admin roles receive notifications from all departments.
            global_admin_roles = {
                Role.Name.ADMIN,
                Role.Name.SUPER_ADMIN,
                *AccessPolicy.LEGACY_SYSTEM_ADMIN_NAMES,
            }
            admin_qs = admin_qs.filter(
                Q(department_id=request.user.department_id) | Q(role__name__in=global_admin_roles)
            )
        recipient_ids.update(admin_qs.values_list("id", flat=True))
        recipient_ids.discard(request.user.id)

        Notification.objects.bulk_create(
            [
                Notification(
                    user_id=recipient_id,
                    title="Ежедневный отчет сотрудника",
                    message=f"{request.user.username} отправил ежедневный отчет за {report.report_date}.",
                    type=Notification.Type.INFO,
                    code=NotificationCode.REPORT_DAILY_SUBMITTED,
                    severity=Notification.Severity.INFO,
                    entity_type=NotificationEntity.EMPLOYEE_DAILY_REPORT,
                    entity_id=str(report.id),
                    action_url=f"/admin/reports/daily?date={report.report_date}",
                )
                for recipient_id in recipient_ids
            ]
        )
        return Response(EmployeeDailyReportSerializer(report).data, status=drf_status.HTTP_201_CREATED)


