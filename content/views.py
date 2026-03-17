from rest_framework.generics import ListAPIView, RetrieveAPIView, CreateAPIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.access_policy import AccessPolicy
from accounts.models import Role, User

from .models import (
    News, WelcomeBlock, Feedback, Employee,
    Instruction, LanguageSetting, NewsSliderSettings, Course, CourseEnrollment
)
from onboarding_core.models import OnboardingDay, OnboardingProgress

from .serializers import (
    NewsListSerializer,
    NewsDetailSerializer,
    WelcomeBlockSerializer,
    EmployeeSerializer,
    InstructionSerializer,
    LanguageSettingSerializer,
    FeedbackSerializer,
    FeedbackAdminListSerializer,
    FeedbackCreateSerializer,
    FeedbackResponseSerializer,
    FeedbackStatusUpdateSerializer,
    CourseSerializer,
    CourseEnrollmentSerializer,
    CourseAssignSerializer,
    CourseAcceptSerializer,
    CourseProgressUpdateSerializer,
    CourseSelfEnrollSerializer,
)
from .audit import ContentAuditService


def has_courses_menu_access(user) -> tuple[bool, str]:
    if not user or not user.is_authenticated:
        return False, "Unauthorized"

    if not AccessPolicy.is_intern(user):
        return True, ""

    active_days = OnboardingDay.objects.filter(is_active=True)
    total_days = active_days.count()
    if total_days == 0:
        return True, ""

    completed_days = OnboardingProgress.objects.filter(
        user=user,
        day__in=active_days,
        status=OnboardingProgress.Status.DONE,
    ).count()
    if completed_days == total_days:
        return True, ""
    return False, "Intern must complete regulation onboarding before accessing courses."


# ---------------- NEWS ----------------

class NewsListAPIView(ListAPIView):
    serializer_class = NewsListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        language = self.request.query_params.get("language", "ru")
        return News.objects.filter(
            is_active=True,
            language=language
        ).order_by("position", "-published_at")[:10]


class NewsDetailAPIView(RetrieveAPIView):
    queryset = News.objects.filter(is_active=True)
    serializer_class = NewsDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"


# ---------------- FEEDBACK ----------------

class FeedbackAdminView(ModelViewSet):
    queryset = Feedback.objects.all().order_by("-created_at")
    serializer_class = FeedbackSerializer
    permission_classes = [IsAuthenticated]

    def _ensure_super_admin(self):
        user = self.request.user
        if not (user and user.is_authenticated and AccessPolicy.is_super_admin(user)):
            self.permission_denied(self.request, message="Only super admin can manage feedback dashboard.")

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self._ensure_super_admin()

    def get_serializer_class(self):
        if self.action in {"list", "retrieve"}:
            return FeedbackAdminListSerializer
        if self.action == "set_status":
            return FeedbackStatusUpdateSerializer
        return FeedbackSerializer

    def get_queryset(self):
        qs = Feedback.objects.all().order_by("-created_at")
        status_filter = self.request.query_params.get("status")
        type_filter = self.request.query_params.get("type")
        search = self.request.query_params.get("search")
        is_read = self.request.query_params.get("is_read")

        if status_filter:
            qs = qs.filter(status=status_filter)
        if type_filter:
            qs = qs.filter(type=type_filter)
        if is_read in {"true", "false"}:
            qs = qs.filter(is_read=(is_read == "true"))
        if search:
            qs = qs.filter(
                Q(text__icontains=search)
                | Q(full_name__icontains=search)
                | Q(contact__icontains=search)
            )
        return qs

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        qs = Feedback.objects.all()
        payload = {
            "total": qs.count(),
            "new": qs.filter(status="new").count(),
            "in_progress": qs.filter(status="in_progress").count(),
            "closed": qs.filter(status="closed").count(),
            "unread": qs.filter(is_read=False).count(),
        }
        return Response(payload)

    @action(detail=False, methods=["get"], url_path="meta")
    def meta(self, request):
        return Response(
            {
                "status_choices": [
                    {"value": value, "label": label}
                    for value, label in Feedback.STATUS_CHOICES
                ],
                "type_choices": [
                    {"value": value, "label": label}
                    for value, label in Feedback.TYPE_CHOICES
                ],
            }
        )

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        feedback = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_status = feedback.status
        feedback.status = serializer.validated_data["status"]
        feedback.is_read = True
        feedback.save(update_fields=["status", "is_read"])

        changed_fields = ["status", "is_read"]
        ContentAuditService.log_feedback_updated_admin(
            request,
            feedback,
            changed_fields=changed_fields,
        )
        if old_status != feedback.status:
            ContentAuditService.log_feedback_status_changed_admin(
                request,
                feedback,
                from_status=old_status,
                to_status=feedback.status,
            )

        return Response(FeedbackAdminListSerializer(feedback).data)

    def perform_update(self, serializer):
        instance = serializer.instance
        old_status = instance.status
        feedback = serializer.save()
        changed_fields = sorted(serializer.validated_data.keys())
        ContentAuditService.log_feedback_updated_admin(
            self.request,
            feedback,
            changed_fields=changed_fields,
        )
        if old_status != feedback.status:
            ContentAuditService.log_feedback_status_changed_admin(
                self.request,
                feedback,
                from_status=old_status,
                to_status=feedback.status,
            )


class FeedbackCreateView(CreateAPIView):
    queryset = Feedback.objects.all()
    serializer_class = FeedbackCreateSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        feedback = serializer.save()
        ContentAuditService.log_feedback_created(self.request, feedback)


# ---------------- EMPLOYEES ----------------

class EmployeeListAPIView(ListAPIView):
    serializer_class = EmployeeSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        show_management = self.request.query_params.get("management")
        qs = Employee.objects.filter(is_active=True)

        if show_management == "true":
            qs = qs.filter(is_management=True)

        return qs


# ---------------- INSTRUCTION ----------------

class InstructionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lang = request.query_params.get("lang", "ru")
        instruction = Instruction.objects.filter(language=lang, is_active=True).first()

        if not instruction:
            return Response(
                {"detail": "Инструкция не найдена"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(InstructionSerializer(instruction).data)


# ---------------- LANGUAGES ----------------

class EnabledLanguagesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        languages = LanguageSetting.objects.filter(is_enabled=True)
        return Response(LanguageSettingSerializer(languages, many=True).data)


# ---------------- SLIDER SETTINGS ----------------

class NewsSliderSettingsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        settings_obj = NewsSliderSettings.objects.first()

        if not settings_obj:
            return Response({
                "autoplay": True,
                "autoplay_delay": 5000
            })

        return Response({
            "autoplay": settings_obj.autoplay,
            "autoplay_delay": settings_obj.autoplay_delay
        })


# ---------------- WELCOME BLOCK ----------------

class WelcomeBlockAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        language = request.query_params.get("language", "ru")
        block = WelcomeBlock.objects.filter(
            language=language,
            is_active=True
        ).first()

        if not block:
            return Response(
                {"detail": "Welcome block not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response(WelcomeBlockSerializer(block).data)


# ---------------- COURSES ----------------

class CoursesMenuAccessAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        has_access, reason = has_courses_menu_access(request.user)
        response = {"has_access": has_access}
        if reason:
            response["reason"] = reason
        return Response(response)


class AdminCourseViewSet(ModelViewSet):
    queryset = Course.objects.select_related("department").all()
    serializer_class = CourseSerializer
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # drf-spectacular introspection should not trigger runtime permission denial.
        if getattr(self, "swagger_fake_view", False):
            return super().get_permissions()
        if not (AccessPolicy.is_admin(self.request.user) or AccessPolicy.is_super_admin(self.request.user)):
            self.permission_denied(self.request, message="Only admin or super admin can manage courses.")
        return super().get_permissions()


class AdminCourseAssignAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if not (AccessPolicy.is_admin(request.user) or AccessPolicy.is_super_admin(request.user)):
            return Response(
                {"detail": "Only admin or super admin can assign courses."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = CourseAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = serializer.validated_data["course"]
        assign_to_all = serializer.validated_data.get("assign_to_all", False)
        user_ids = serializer.validated_data.get("user_ids", [])

        if assign_to_all:
            users = User.objects.exclude(role__name=Role.Name.INTERN).select_related("role")
        else:
            users = User.objects.filter(id__in=user_ids).exclude(role__name=Role.Name.INTERN).select_related("role")

        created_count = 0
        with transaction.atomic():
            for target_user in users:
                _, created = CourseEnrollment.objects.get_or_create(
                    course=course,
                    user=target_user,
                    defaults={
                        "source": CourseEnrollment.Source.ADMIN,
                        "status": CourseEnrollment.Status.ASSIGNED,
                        "assigned_by": request.user,
                    },
                )
                if created:
                    created_count += 1

        return Response(
            {
                "course_id": str(course.id),
                "assigned_count": created_count,
            },
            status=status.HTTP_200_OK,
        )


class AvailableCoursesListAPIView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseSerializer

    def get_queryset(self):
        user = self.request.user
        has_access, reason = has_courses_menu_access(user)
        if not has_access:
            self.permission_denied(self.request, message=reason)

        base_qs = Course.objects.filter(is_active=True).select_related("department")
        if AccessPolicy.is_employee(user):
            public_qs = base_qs.filter(visibility=Course.Visibility.PUBLIC)
            if not user.department_id:
                return public_qs
            return public_qs | base_qs.filter(
                visibility=Course.Visibility.DEPARTMENT,
                department=user.department,
            )
        return base_qs


class MyCoursesListAPIView(ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = CourseEnrollmentSerializer

    def get_queryset(self):
        user = self.request.user
        has_access, reason = has_courses_menu_access(user)
        if not has_access:
            self.permission_denied(self.request, message=reason)
        return (
            CourseEnrollment.objects
            .select_related("course", "course__department")
            .filter(user=user, course__is_active=True)
        )


class CourseSelfEnrollAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        has_access, reason = has_courses_menu_access(request.user)
        if not has_access:
            return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = CourseSelfEnrollSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course_id = serializer.validated_data["course_id"]

        try:
            course = Course.objects.select_related("department").get(id=course_id, is_active=True)
        except Course.DoesNotExist:
            return Response({"detail": "Course not found."}, status=status.HTTP_404_NOT_FOUND)

        if course.visibility == Course.Visibility.DEPARTMENT:
            if not request.user.department_id or course.department_id != request.user.department_id:
                return Response(
                    {"detail": "Department course is not available for this user."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        enrollment, created = CourseEnrollment.objects.get_or_create(
            course=course,
            user=request.user,
            defaults={
                "source": CourseEnrollment.Source.SELF,
                "status": CourseEnrollment.Status.IN_PROGRESS,
                "started_at": timezone.now(),
            },
        )
        if not created:
            return Response(
                {"detail": "Course already selected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(CourseEnrollmentSerializer(enrollment).data, status=status.HTTP_201_CREATED)


class AcceptAssignedCourseAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        has_access, reason = has_courses_menu_access(request.user)
        if not has_access:
            return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = CourseAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrollment_id = serializer.validated_data["enrollment_id"]

        enrollment = CourseEnrollment.objects.filter(
            id=enrollment_id,
            user=request.user,
            source=CourseEnrollment.Source.ADMIN,
        ).first()
        if not enrollment:
            return Response({"detail": "Assigned course not found."}, status=status.HTTP_404_NOT_FOUND)

        if enrollment.status != CourseEnrollment.Status.ASSIGNED:
            return Response({"detail": "Course is already accepted or started."}, status=status.HTTP_400_BAD_REQUEST)

        enrollment.status = CourseEnrollment.Status.ACCEPTED
        enrollment.accepted_at = timezone.now()
        enrollment.save(update_fields=["status", "accepted_at", "updated_at"])
        return Response(CourseEnrollmentSerializer(enrollment).data)


class StartCourseAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        has_access, reason = has_courses_menu_access(request.user)
        if not has_access:
            return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = CourseAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrollment_id = serializer.validated_data["enrollment_id"]

        enrollment = CourseEnrollment.objects.filter(
            id=enrollment_id,
            user=request.user,
        ).first()
        if not enrollment:
            return Response({"detail": "Course enrollment not found."}, status=status.HTTP_404_NOT_FOUND)

        if enrollment.status == CourseEnrollment.Status.ASSIGNED:
            return Response(
                {"detail": "Accept assigned course first."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if enrollment.status == CourseEnrollment.Status.COMPLETED:
            return Response(
                {"detail": "Course is already completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enrollment.status = CourseEnrollment.Status.IN_PROGRESS
        if not enrollment.started_at:
            enrollment.started_at = timezone.now()
        enrollment.save(update_fields=["status", "started_at", "updated_at"])
        return Response(CourseEnrollmentSerializer(enrollment).data)


class UpdateCourseProgressAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        has_access, reason = has_courses_menu_access(request.user)
        if not has_access:
            return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = CourseProgressUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        enrollment = CourseEnrollment.objects.filter(
            id=serializer.validated_data["enrollment_id"],
            user=request.user,
        ).first()
        if not enrollment:
            return Response({"detail": "Course enrollment not found."}, status=status.HTTP_404_NOT_FOUND)

        progress_percent = serializer.validated_data["progress_percent"]
        enrollment.progress_percent = progress_percent

        if progress_percent >= 100:
            enrollment.status = CourseEnrollment.Status.COMPLETED
            enrollment.completed_at = timezone.now()
        elif enrollment.status in {CourseEnrollment.Status.ACCEPTED, CourseEnrollment.Status.IN_PROGRESS}:
            enrollment.status = CourseEnrollment.Status.IN_PROGRESS

        if enrollment.status == CourseEnrollment.Status.IN_PROGRESS and not enrollment.started_at:
            enrollment.started_at = timezone.now()

        enrollment.save(
            update_fields=[
                "progress_percent",
                "status",
                "completed_at",
                "started_at",
                "updated_at",
            ]
        )
        return Response(CourseEnrollmentSerializer(enrollment).data)
