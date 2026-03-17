from datetime import timedelta

from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status as drf_status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.views import APIView

from accounts.permissions import HasPermission
from accounts.access_policy import AccessPolicy
from accounts.models import Role, User
from apps.audit import AuditEvents, log_event
from apps.tasks.models import Board, Column, Task
from regulations.models import (
    Regulation,
    RegulationAcknowledgement,
    RegulationFeedback,
    RegulationReadProgress,
    RegulationKnowledgeCheck,
)
from reports.models import OnboardingReport

from .models import OnboardingDay, OnboardingMaterial, OnboardingProgress
from .audit import OnboardingAuditService
from .services import ensure_day_task_for_intern
from .serializers import (
    AdminOnboardingDaySerializer,
    AdminOnboardingMaterialSerializer,
    AdminOnboardingProgressSerializer,
    OnboardingDayDetailSerializer,
    OnboardingDayListSerializer,
)


def _get_user_default_board(user) -> Board:
    board, _ = Board.objects.get_or_create(
        created_by=user,
        is_personal=True,
        defaults={"name": f"{user.username} board"},
    )
    Column.objects.get_or_create(
        board=board,
        order=1,
        defaults={"name": "New"},
    )
    return board


def _get_day_one_regulations_queryset(day):
    if day.day_number == 1:
        return Regulation.objects.filter(is_active=True).order_by("position", "-created_at")
    return day.regulations.filter(is_active=True).order_by("position", "-created_at")


def _regulation_requires_quiz(regulation: Regulation) -> bool:
    return bool(regulation.requires_quiz)


def _get_first_incomplete_previous_day(user, day):
    previous_days = (
        OnboardingDay.objects.filter(is_active=True, day_number__lt=day.day_number)
        .order_by("day_number")
    )
    if not previous_days.exists():
        return None

    completed_day_ids = set(
        OnboardingProgress.objects.filter(
            user=user,
            day_id__in=previous_days.values_list("id", flat=True),
            status=OnboardingProgress.Status.DONE,
        ).values_list("day_id", flat=True)
    )
    for prev_day in previous_days:
        if prev_day.id not in completed_day_ids:
            return prev_day
    return None


def _ensure_day_one_onboarding_task_for_intern(*, user, day):
    if not (getattr(user, "role_id", None) and user.role.name == Role.Name.INTERN):
        return
    if day.day_number != 1:
        return

    existing = Task.objects.filter(assignee=user, onboarding_day=day).first()
    if existing is not None:
        return

    board = _get_user_default_board(user)
    column = board.columns.order_by("order", "id").first()
    if column is None:
        column = Column.objects.create(board=board, name="New", order=1)

    today = timezone.localdate()
    due_date = today + timedelta(days=1)
    regulation_titles = list(_get_day_one_regulations_queryset(day).values_list("title", flat=True))
    regulations_block = (
        "\n".join(f"- {title}" for title in regulation_titles)
        if regulation_titles
        else "- Регламенты будут добавлены администратором."
    )
    description = (
        "Цель этого дня: ознакомление с регламентами компании.\n"
        "Дедлайн: один день (до следующего дня с момента открытия задачи).\n\n"
        "Задача: прочитать все регламенты.\n\n"
        "Регламенты:\n"
        f"{regulations_block}\n\n"
        "После ознакомления отправьте отчет (стендап) во вкладке отчета."
    )

    Task.objects.create(
        board=board,
        column=column,
        title="День 1: Ознакомление с регламентами компании",
        description=description,
        assignee=user,
        reporter=user.manager if user.manager_id else user,
        onboarding_day=day,
        due_date=due_date,
        priority=Task.Priority.HIGH,
    )

class OnboardingDayListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OnboardingDayListSerializer

    def get_queryset(self):
        return (
            OnboardingDay.objects
            .filter(is_active=True)
            .order_by("position", "day_number")
        )


class OnboardingDayDetailView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = OnboardingDayDetailSerializer
    lookup_field = "id"

    def get_queryset(self):
        return (
            OnboardingDay.objects
            .filter(is_active=True)
            .prefetch_related("materials", "regulations")
        )

    def get_serializer_context(self):
        return {"request": self.request}

    def retrieve(self, request, *args, **kwargs):
        day = self.get_object()
        missing_prev = _get_first_incomplete_previous_day(request.user, day)
        if missing_prev is not None:
            return Response(
                {
                    "detail": (
                        f"День {day.day_number} пока недоступен. "
                        f"Сначала завершите день {missing_prev.day_number}."
                    )
                },
                status=drf_status.HTTP_409_CONFLICT,
            )
        _ensure_day_one_onboarding_task_for_intern(user=request.user, day=day)
        ensure_day_task_for_intern(user=request.user, day=day)
        serializer = self.get_serializer(day)
        return Response(serializer.data)


class CompleteOnboardingDayView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Marks onboarding day as completed for current user.",
        responses={
            200: OpenApiResponse(description="Day marked as completed"),
            401: OpenApiResponse(description="Unauthorized"),
            404: OpenApiResponse(description="Day not found"),
        },
    )
    def post(self, request, id):
        day = get_object_or_404(OnboardingDay, id=id, is_active=True)
        missing_prev = _get_first_incomplete_previous_day(request.user, day)
        if missing_prev is not None:
            return Response(
                {
                    "detail": (
                        f"Нельзя завершить день {day.day_number}, "
                        f"пока не завершен день {missing_prev.day_number}."
                    )
                },
                status=drf_status.HTTP_409_CONFLICT,
            )

        if (
            getattr(request.user, "role_id", None)
            and request.user.role.name == Role.Name.INTERN
            and day.day_number in {1, 2}
        ):
            if day.day_number == 1:
                mandatory = Regulation.objects.filter(
                    is_active=True,
                    is_mandatory_on_day_one=True,
                )
                if mandatory.exists():
                    acknowledged_ids = set(
                        RegulationAcknowledgement.objects.filter(
                            user=request.user,
                            regulation__in=mandatory,
                        ).values_list("regulation_id", flat=True)
                    )
                    missing = mandatory.exclude(id__in=acknowledged_ids)
                    if missing.exists():
                        missing_docs = list(missing.values("id", "title"))
                        log_event(
                            action=AuditEvents.ONBOARDING_DAY1_BLOCKED_MISSING_REGULATIONS,
                            actor=request.user,
                            object_type="onboarding_day",
                            object_id=str(day.id),
                            category="content",
                            level="warning",
                            ip_address=request.META.get("REMOTE_ADDR"),
                            metadata={
                                "missing_count": len(missing_docs),
                                "missing_docs": [
                                    {"id": str(item["id"]), "title": item["title"]}
                                    for item in missing_docs
                                ],
                            },
                        )
                        return Response(
                            {
                                "detail": "Нельзя завершить 1-й день: подтвердите ознакомление с обязательными регламентами.",
                                "missing_regulations": [
                                    {"id": str(item["id"]), "title": item["title"]}
                                    for item in missing_docs
                                ],
                            },
                            status=drf_status.HTTP_409_CONFLICT,
                        )

                day_regs = list(_get_day_one_regulations_queryset(day))
                if day_regs:
                    reg_ids = [reg.id for reg in day_regs]
                    read_ids = set(
                        RegulationReadProgress.objects.filter(
                            user=request.user,
                            regulation_id__in=reg_ids,
                            is_read=True,
                        ).values_list("regulation_id", flat=True)
                    )
                    feedback_ids = set(
                        RegulationFeedback.objects.filter(
                            user=request.user,
                            regulation_id__in=reg_ids,
                        ).values_list("regulation_id", flat=True)
                    )
                    quiz_ids = set(
                        RegulationKnowledgeCheck.objects.filter(
                            user=request.user,
                            regulation_id__in=reg_ids,
                            is_passed=True,
                        ).values_list("regulation_id", flat=True)
                    )

                    missing_steps = []
                    for reg in day_regs:
                        steps = []
                        if reg.id not in read_ids:
                            steps.append("read")
                        if reg.id not in feedback_ids:
                            steps.append("feedback")
                        if _regulation_requires_quiz(reg) and reg.id not in quiz_ids:
                            steps.append("quiz")
                        if steps:
                            missing_steps.append(
                                {
                                    "id": str(reg.id),
                                    "title": reg.title,
                                    "missing": steps,
                                }
                            )

                    if missing_steps:
                        return Response(
                            {
                                "detail": "Нельзя завершить 1-й день: для каждого регламента нужны шаги 'прочитал', 'фидбек' и 'тест'.",
                                "missing_steps": missing_steps,
                            },
                            status=drf_status.HTTP_409_CONFLICT,
                        )
            elif day.day_number == 2:
                if not request.user.subdivision_id:
                    return Response(
                        {
                            "detail": "Нельзя завершить 2-й день: сначала выберите роль/подотдел."
                        },
                        status=drf_status.HTTP_409_CONFLICT,
                    )
                report = OnboardingReport.objects.filter(user=request.user, day=day).first()
                if not report or report.status not in {
                    OnboardingReport.Status.SENT,
                    OnboardingReport.Status.ACCEPTED,
                }:
                    return Response(
                        {
                            "detail": "Нельзя завершить 2-й день: отправьте отчет с описанием и ссылкой на GitHub."
                        },
                        status=drf_status.HTTP_409_CONFLICT,
                    )

        progress, created = OnboardingProgress.objects.get_or_create(
            user=request.user,
            day=day,
        )

        if not created and progress.status == OnboardingProgress.Status.DONE:
            OnboardingAuditService.log_day_completed(
                request,
                day,
                progress.completed_at,
                idempotent=True,
            )
            return Response(
                {
                    "day_id": str(day.id),
                    "status": progress.status,
                    "completed_at": progress.completed_at,
                },
                status=drf_status.HTTP_200_OK,
            )

        progress.status = OnboardingProgress.Status.DONE
        progress.completed_at = timezone.now()
        progress.save(update_fields=["status", "completed_at", "updated_at"])
        OnboardingAuditService.log_day_completed(
            request,
            day,
            progress.completed_at,
            idempotent=False,
        )

        return Response(
            {
                "day_id": str(day.id),
                "status": progress.status,
                "completed_at": progress.completed_at,
            },
            status=drf_status.HTTP_200_OK,
        )


class OnboardingOverviewView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        description="Onboarding summary for current user.",
        responses={
            200: OpenApiResponse(description="Onboarding summary"),
            401: OpenApiResponse(description="Unauthorized"),
        },
    )
    def get(self, request):
        user = request.user

        days = (
            OnboardingDay.objects
            .filter(is_active=True)
            .order_by("position", "day_number")
        )

        progress_qs = OnboardingProgress.objects.filter(user=user)
        progress_map = {p.day_id: p for p in progress_qs}

        completed_days = 0
        result_days = []
        current_day = None
        previous_incomplete_found = False

        for day in days:
            progress = progress_map.get(day.id)

            if progress and progress.status == OnboardingProgress.Status.DONE:
                completed_days += 1
                result_days.append({
                    "day_id": str(day.id),
                    "day_number": day.day_number,
                    "status": "DONE",
                })
                continue

            if not previous_incomplete_found:
                previous_incomplete_found = True
                if current_day is None:
                    current_day = day
                result_days.append({
                    "day_id": str(day.id),
                    "day_number": day.day_number,
                    "status": "IN_PROGRESS",
                })
                continue

            result_days.append({
                "day_id": str(day.id),
                "day_number": day.day_number,
                "status": "LOCKED",
            })

        if current_day is None and days.exists():
            # All completed.
            current_day = days.order_by("-day_number").first()

        total_days = days.count()
        progress_percent = int((completed_days / total_days) * 100) if total_days else 0
        OnboardingAuditService.log_overview_viewed(
            request,
            total_days=total_days,
            completed_days=completed_days,
            progress_percent=progress_percent,
        )

        return Response({
            "total_days": total_days,
            "completed_days": completed_days,
            "progress_percent": progress_percent,
            "current_day": (
                {
                    "id": str(current_day.id),
                    "day_number": current_day.day_number,
                    "title": current_day.title,
                } if current_day else None
            ),
            "days": result_days,
        })


class AdminOnboardingDayViewSet(ModelViewSet):
    queryset = (
        OnboardingDay.objects
        .all()
        .order_by("position", "day_number")
    )
    serializer_class = AdminOnboardingDaySerializer
    permission_classes = [IsAuthenticated, HasPermission]
    required_permission = "onboarding_manage"

    filterset_fields = ["is_active"]
    ordering_fields = ["position", "day_number"]

    def perform_create(self, serializer):
        day = serializer.save()
        OnboardingAuditService.log_day_created(self.request, day)

    def perform_update(self, serializer):
        day = serializer.save()
        changed_fields = sorted(serializer.validated_data.keys())
        OnboardingAuditService.log_day_updated(self.request, day, changed_fields)

    def perform_destroy(self, instance):
        day = instance
        OnboardingAuditService.log_day_deleted(self.request, day)
        super().perform_destroy(instance)


class AdminOnboardingMaterialViewSet(ModelViewSet):
    queryset = (
        OnboardingMaterial.objects
        .all()
        .order_by("position")
    )
    serializer_class = AdminOnboardingMaterialSerializer
    permission_classes = [IsAuthenticated, HasPermission]
    required_permission = "onboarding_manage"

    def perform_create(self, serializer):
        material = serializer.save()
        OnboardingAuditService.log_material_created(self.request, material)

    def perform_update(self, serializer):
        material = serializer.save()
        changed_fields = sorted(serializer.validated_data.keys())
        OnboardingAuditService.log_material_updated(self.request, material, changed_fields)

    def perform_destroy(self, instance):
        material = instance
        OnboardingAuditService.log_material_deleted(self.request, material)
        super().perform_destroy(instance)


class AdminOnboardingProgressViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, HasPermission]
    required_permission = "reports_review"
    serializer_class = AdminOnboardingProgressSerializer
    http_method_names = ["get"]

    def list(self, request, *args, **kwargs):
        filters = {
            "user_id": request.query_params.get("user_id"),
            "status": request.query_params.get("status"),
            "day_number": request.query_params.get("day_number"),
        }
        OnboardingAuditService.log_progress_viewed_admin(request, filters)
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        qs = (
            OnboardingProgress.objects
            .select_related("user", "day")
            .order_by("user_id", "day__day_number")
        )

        user_id = self.request.query_params.get("user_id")
        status = self.request.query_params.get("status")
        day_number = self.request.query_params.get("day_number")

        if user_id:
            qs = qs.filter(user_id=user_id)

        if status:
            qs = qs.filter(status=status)

        if day_number:
            qs = qs.filter(day__day_number=day_number)

        return qs


class InternOnboardingProgressDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _can_view(self, actor, target):
        if actor.id == target.id:
            return True
        if AccessPolicy.is_super_admin(actor) or AccessPolicy.is_main_admin(actor):
            return True
        if AccessPolicy.is_admin(actor):
            return bool(actor.department_id and actor.department_id == target.department_id)
        if AccessPolicy.is_teamlead(actor):
            return target.manager_id == actor.id
        return False

    def get(self, request, user_id):
        target = get_object_or_404(
            User.objects.select_related("role", "department", "subdivision", "manager"),
            id=user_id,
            is_active=True,
        )
        if not self._can_view(request.user, target):
            return Response({"detail": "Access denied."}, status=drf_status.HTTP_403_FORBIDDEN)

        days = list(OnboardingDay.objects.filter(is_active=True).order_by("position", "day_number"))
        progress_qs = OnboardingProgress.objects.filter(user=target, day__in=days).select_related("day")
        progress_map = {item.day_id: item for item in progress_qs}

        report_qs = OnboardingReport.objects.filter(user=target, day__in=days).select_related("day")
        reports = [
            {
                "id": str(item.id),
                "day_id": str(item.day_id),
                "day_number": item.day.day_number if item.day_id else None,
                "status": item.status,
                "updated_at": item.updated_at,
            }
            for item in report_qs
        ]

        day_progress = []
        current_day = None
        completed_days = 0
        for day in days:
            progress = progress_map.get(day.id)
            status = progress.status if progress else OnboardingProgress.Status.NOT_STARTED
            if status == OnboardingProgress.Status.DONE:
                completed_days += 1
            if current_day is None and status != OnboardingProgress.Status.DONE:
                current_day = day
            day_progress.append(
                {
                    "day_id": str(day.id),
                    "day_number": day.day_number,
                    "status": status,
                    "completed_at": progress.completed_at if progress else None,
                }
            )
        if current_day is None and days:
            current_day = days[-1]

        tasks = (
            Task.objects.filter(assignee=target)
            .select_related("column", "onboarding_day")
            .order_by("-updated_at")
        )
        task_rows = [
            {
                "id": item.id,
                "title": item.title,
                "column": item.column.name if item.column_id else "",
                "priority": item.priority,
                "onboarding_day_id": str(item.onboarding_day_id) if item.onboarding_day_id else None,
                "onboarding_day_number": item.onboarding_day.day_number if item.onboarding_day_id else None,
                "updated_at": item.updated_at,
            }
            for item in tasks
        ]

        regulation_rows = []
        is_intern = bool(target.role_id and target.role.name == Role.Name.INTERN)
        if is_intern:
            regulations = list(Regulation.objects.filter(is_active=True).order_by("position", "-created_at"))
            reg_ids = [item.id for item in regulations]
            read_map = {
                item.regulation_id: item
                for item in RegulationReadProgress.objects.filter(user=target, regulation_id__in=reg_ids)
            }
            feedback_map = {
                item.regulation_id: item
                for item in RegulationFeedback.objects.filter(user=target, regulation_id__in=reg_ids).order_by("-created_at")
            }
            quiz_map = {
                item.regulation_id: item
                for item in RegulationKnowledgeCheck.objects.filter(user=target, regulation_id__in=reg_ids)
            }
            day_relations = {}
            for day in days:
                for reg_id in day.regulations.values_list("id", flat=True):
                    current = day_relations.get(reg_id)
                    if current is None or day.day_number < current:
                        day_relations[reg_id] = day.day_number

            for reg in regulations:
                read_item = read_map.get(reg.id)
                feedback_item = feedback_map.get(reg.id)
                quiz_item = quiz_map.get(reg.id)
                requires_quiz = bool(reg.requires_quiz)
                fallback_quiz_total = max(
                    len(reg.quiz_questions or []),
                    1 if ((reg.quiz_question or "").strip() or (reg.quiz_expected_answer or "").strip()) else 0,
                )
                raw_quiz_total = quiz_item.total_questions if quiz_item else 0
                raw_quiz_incorrect = quiz_item.incorrect_answers if quiz_item else 0
                raw_quiz_score = quiz_item.score if quiz_item else 0
                quiz_total = raw_quiz_total if raw_quiz_total > 0 else (fallback_quiz_total if requires_quiz else 0)
                if raw_quiz_score > 0:
                    quiz_score = min(raw_quiz_score, quiz_total) if quiz_total > 0 else raw_quiz_score
                elif quiz_item and quiz_item.is_passed and quiz_total > 0:
                    quiz_score = max(quiz_total - max(raw_quiz_incorrect, 0), 0)
                else:
                    quiz_score = 0
                if not read_item or not read_item.is_read:
                    step = "read"
                elif not feedback_item:
                    step = "feedback"
                elif requires_quiz and not (quiz_item and quiz_item.is_passed):
                    step = "quiz"
                else:
                    step = "done"

                regulation_rows.append(
                    {
                        "id": str(reg.id),
                        "title": reg.title,
                        "day_number": day_relations.get(reg.id, 1),
                        "position": reg.position,
                        "step": step,
                        "is_read": bool(read_item and read_item.is_read),
                        "read_at": read_item.read_at if read_item else None,
                        "feedback": feedback_item.text if feedback_item else "",
                        "feedback_at": feedback_item.created_at if feedback_item else None,
                        "quiz_required": requires_quiz,
                        "quiz_passed": bool(quiz_item and quiz_item.is_passed) if requires_quiz else True,
                        "quiz_score": quiz_score,
                        "quiz_total": quiz_total,
                        "quiz_incorrect": raw_quiz_incorrect,
                        "quiz_submitted_at": quiz_item.submitted_at if quiz_item else None,
                    }
                )

        return Response(
            {
                "user": {
                    "id": target.id,
                    "username": target.username,
                    "full_name": f"{target.first_name} {target.last_name}".strip() or target.username,
                    "role": target.role.name if target.role_id else "",
                    "department": target.department.name if target.department_id else "",
                    "subdivision": target.subdivision.name if target.subdivision_id else "",
                    "manager": (
                        f"{target.manager.first_name} {target.manager.last_name}".strip() or target.manager.username
                        if target.manager_id
                        else ""
                    ),
                },
                "overview": {
                    "completed_days": completed_days,
                    "total_days": len(days),
                    "current_day_number": current_day.day_number if current_day else None,
                },
                "day_progress": day_progress,
                "regulations": regulation_rows,
                "tasks": task_rows,
                "reports": reports,
            }
        )



