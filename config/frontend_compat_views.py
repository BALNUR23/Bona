from __future__ import annotations

from django.contrib.auth import authenticate
from django.db import IntegrityError
from django.db.models import Q
from django.utils import timezone
from django.utils import translation
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.access_policy import AccessPolicy
from accounts.models import (
    AuditLog,
    Department,
    DepartmentSubdivision,
    PasswordResetToken,
    Position,
    PromotionRequest,
    Role,
    User,
    UserSession,
)
from accounts.serializers import PasswordChangeSerializer, UserProfileUpdateSerializer
from content.models import Feedback, Instruction, News
from content.serializers import (
    InstructionSerializer,
    NewsDetailSerializer,
    NewsListSerializer,
)
from onboarding_core.models import OnboardingDay, OnboardingProgress
from onboarding_core.views import OnboardingOverviewView
from regulations.models import Regulation, RegulationAcknowledgement
from regulations.serializers import RegulationAdminSerializer, RegulationSerializer
from reports.models import OnboardingReport
from work_schedule.models import ProductionCalendar
from work_schedule.views import MyScheduleAPIView, WorkScheduleListAPIView
from django.contrib.sessions.models import Session
from common.i18n import role_label


def _role_to_front(role: Role | None) -> str:
    if not role:
        return ""
    mapping = {
        Role.Name.SUPER_ADMIN: "superadmin",
        Role.Name.ADMIN: "admin",
        Role.Name.DEPARTMENT_HEAD: "department_head",
        Role.Name.TEAMLEAD: "projectmanager",
        Role.Name.EMPLOYEE: "employee",
        Role.Name.INTERN: "intern",
    }
    return mapping.get(role.name, role.name.lower())


def _user_to_front_payload(user: User) -> dict:
    full_name = f"{user.first_name} {user.last_name}".strip() or user.username
    role_front = _role_to_front(user.role)
    current_lang = translation.get_language() or "ru"
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": full_name,
        "role": role_front,
        "role_label": role_label(role_front, current_lang),
        "department": user.department_id,
        "department_name": user.department.name if user.department_id else "",
        "subdivision": user.subdivision_id,
        "subdivision_name": user.subdivision.name if user.subdivision_id else "",
        "position": user.position_id,
        "position_name": user.position.name if user.position_id else (user.custom_position or ""),
        "manager": user.manager_id,
        "manager_name": (
            f"{user.manager.first_name} {user.manager.last_name}".strip() or user.manager.username
            if user.manager_id
            else ""
        ),
        "phone": user.phone or "",
        "telegram": user.telegram or "",
        "photo": user.photo.url if getattr(user, "photo", None) else "",
        "hire_date": user.date_joined.date().isoformat() if user.date_joined else None,
        "is_active": user.is_active,
    }


def _ensure_admin_like(user: User):
    if not AccessPolicy.is_admin_like(user):
        raise PermissionDenied("Only department head/admin/super admin can perform this action.")


def _ensure_content_manager(user: User):
    if not (AccessPolicy.is_main_admin(user) or AccessPolicy.is_super_admin(user)):
        raise PermissionDenied("Only admin/super admin can manage content.")


def _is_privileged_target(target: User) -> bool:
    if not getattr(target, "role_id", None):
        return False
    return target.role.name in {Role.Name.DEPARTMENT_HEAD, Role.Name.ADMIN, Role.Name.SUPER_ADMIN}


def _resolve_role(value: str | None) -> Role | None:
    if not value:
        return None
    normalized = str(value).strip().upper()
    aliases = {
        "PROJECTMANAGER": Role.Name.TEAMLEAD,
        "PROJECT_MANAGER": Role.Name.TEAMLEAD,
        "TEAMLEAD": Role.Name.TEAMLEAD,
        "TEAM_LEAD": Role.Name.TEAMLEAD,
        "DEPARTMENTHEAD": Role.Name.DEPARTMENT_HEAD,
        "DEPARTMENT_HEAD": Role.Name.DEPARTMENT_HEAD,
        "ADMIN": Role.Name.ADMIN,
        "SUPERADMIN": Role.Name.SUPER_ADMIN,
        "SUPER_ADMIN": Role.Name.SUPER_ADMIN,
        "EMPLOYEE": Role.Name.EMPLOYEE,
        "INTERN": Role.Name.INTERN,
    }
    role_name = aliases.get(normalized)
    if not role_name:
        return None
    return Role.objects.filter(name=role_name).first()


class FrontendLoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class FrontendLoginResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    landing = serializers.CharField()
    user = serializers.DictField()


class FrontendLogoutRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)


class FrontendMessageSerializer(serializers.Serializer):
    detail = serializers.CharField()


class FrontendSetRoleRequestSerializer(serializers.Serializer):
    role = serializers.CharField()


class FrontendLoginAPIView(APIView):
    @extend_schema(
        tags=["Frontend API"],
        request=FrontendLoginRequestSerializer,
        responses={200: FrontendLoginResponseSerializer},
    )
    def post(self, request):
        username = (request.data.get("username") or "").strip()
        password = request.data.get("password") or ""
        if not username or not password:
            return Response({"error": "Username and password are required"}, status=400)

        user = User.objects.filter(Q(username=username) | Q(email__iexact=username)).first()
        auth_username = user.username if user else username
        authed_user = authenticate(username=auth_username, password=password)
        if not authed_user:
            return Response({"error": "Invalid credentials"}, status=400)
        if authed_user.is_blocked:
            return Response({"error": "User blocked"}, status=403)

        refresh = RefreshToken.for_user(authed_user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "landing": "admin_panel" if AccessPolicy.is_admin_like(authed_user) else "employee_portal",
                "user": _user_to_front_payload(authed_user),
            }
        )


class FrontendLogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Frontend API"],
        request=FrontendLogoutRequestSerializer,
        responses={200: FrontendMessageSerializer},
    )
    def post(self, request):
        refresh = request.data.get("refresh")
        if refresh:
            try:
                token = RefreshToken(refresh)
                token.blacklist()
            except Exception:
                pass
        return Response({"detail": "Logged out."}, status=200)


class FrontendMeAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["Frontend API"], responses={200: serializers.DictField()})
    def get(self, request):
        return Response(_user_to_front_payload(request.user))

    @extend_schema(tags=["Frontend API"], request=serializers.DictField(), responses={200: serializers.DictField()})
    def patch(self, request):
        serializer = UserProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        request.user.refresh_from_db()
        return Response(_user_to_front_payload(request.user))


class FrontendMePasswordAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        return Response({"detail": "Password changed successfully."}, status=200)


class FrontendUsersSerializer(serializers.ModelSerializer):
    role = serializers.CharField(required=False, allow_blank=True)
    password = serializers.CharField(required=False, allow_blank=False, write_only=True)

    class Meta:
        model = User
        fields = (
            "username",
            "email",
            "first_name",
            "last_name",
            "department",
            "subdivision",
            "position",
            "manager",
            "custom_position",
            "telegram",
            "phone",
            "is_active",
            "role",
            "password",
        )

    def validate_role(self, value):
        if not value:
            return value
        role = _resolve_role(value)
        if not role:
            raise serializers.ValidationError("Unknown role.")
        return role.name


class FrontendUsersCollectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_like(request.user)
        qs = User.objects.select_related("role", "department", "position", "manager").order_by("id")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            qs = qs.filter(
                Q(role__name=Role.Name.INTERN)
                | Q(role__name=Role.Name.EMPLOYEE, department_id=request.user.department_id)
                | Q(role__name=Role.Name.TEAMLEAD, department_id=request.user.department_id)
            )
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(
                Q(username__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
            )
        return Response([_user_to_front_payload(item) for item in qs])

    def post(self, request):
        _ensure_admin_like(request.user)
        serializer = FrontendUsersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)
        password = validated.pop("password", None)
        role_name = validated.pop("role", None)

        user = User(**validated)
        user.role = _resolve_role(role_name) or Role.objects.filter(name=Role.Name.INTERN).first()
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            # Department head can create employees/teamleads only in own department.
            # Interns must be created without department.
            if user.role and user.role.name in {Role.Name.EMPLOYEE, Role.Name.TEAMLEAD}:
                if not request.user.department_id:
                    return Response({"detail": "Department head must belong to a department."}, status=400)
                user.department_id = request.user.department_id
            elif user.role and user.role.name == Role.Name.INTERN:
                user.department = None

        if user.role and user.role.name == Role.Name.TEAMLEAD and user.manager_id:
            return Response({"detail": "Teamlead cannot have a manager."}, status=400)
        if user.manager_id:
            manager = User.objects.select_related("role").filter(id=user.manager_id).first()
            if not manager or manager.role.name != Role.Name.TEAMLEAD:
                return Response({"detail": "Manager must be a teamlead."}, status=400)
            if (
                AccessPolicy.is_admin(request.user)
                and request.user.department_id
                and manager.department_id != request.user.department_id
            ):
                return Response({"detail": "Department head can assign only teamleads from own department."}, status=403)
        if user.subdivision_id:
            subdivision = DepartmentSubdivision.objects.filter(id=user.subdivision_id, is_active=True).first()
            if not subdivision:
                return Response({"detail": "Subdivision not found."}, status=400)
            if user.department_id and subdivision.department_id != user.department_id:
                return Response({"detail": "Subdivision must belong to selected department."}, status=400)
            if not user.department_id:
                user.department_id = subdivision.department_id
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if user.role and user.role.name in {Role.Name.DEPARTMENT_HEAD, Role.Name.ADMIN, Role.Name.SUPER_ADMIN}:
                return Response({"detail": "Department head cannot create privileged users."}, status=403)
        if not user.role:
            return Response({"detail": "Default role INTERN not found. Run role seed first."}, status=400)
        if password:
            user.set_password(password)
        else:
            user.set_password("ChangeMe123!")
        user.save()
        return Response(_user_to_front_payload(user), status=status.HTTP_201_CREATED)


class FrontendUsersDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_user(self, user_id: int) -> User:
        user = User.objects.select_related("role", "department", "position", "manager").filter(id=user_id).first()
        if not user:
            raise NotFound("User not found.")
        return user

    def patch(self, request, user_id: int):
        _ensure_admin_like(request.user)
        target = self._get_user(user_id)
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if _is_privileged_target(target):
                return Response({"detail": "Department head cannot edit privileged users."}, status=403)
        serializer = FrontendUsersSerializer(target, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        validated = dict(serializer.validated_data)
        role_name = validated.pop("role", None)
        password = validated.pop("password", None)
        next_role = _resolve_role(role_name) if role_name else target.role
        if role_name and AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if next_role and next_role.name in {Role.Name.DEPARTMENT_HEAD, Role.Name.ADMIN, Role.Name.SUPER_ADMIN}:
                return Response({"detail": "Department head cannot assign privileged role."}, status=403)

        if next_role and next_role.name == Role.Name.TEAMLEAD:
            if "manager" in validated and validated.get("manager"):
                return Response({"detail": "Teamlead cannot have a manager."}, status=400)
            validated["manager"] = None
        if "manager" in validated:
            manager = validated.get("manager")
            if manager:
                manager = User.objects.select_related("role").filter(id=manager.id).first()
                if not manager or manager.role.name != Role.Name.TEAMLEAD:
                    return Response({"detail": "Manager must be a teamlead."}, status=400)
                if (
                    AccessPolicy.is_admin(request.user)
                    and request.user.department_id
                    and manager.department_id != request.user.department_id
                ):
                    return Response({"detail": "Department head can assign only teamleads from own department."}, status=403)
        next_department = validated.get("department", target.department)
        next_subdivision = validated.get("subdivision", target.subdivision)
        if next_subdivision:
            subdivision = DepartmentSubdivision.objects.filter(id=next_subdivision.id, is_active=True).first()
            if not subdivision:
                return Response({"detail": "Subdivision not found."}, status=400)
            if next_department and subdivision.department_id != next_department.id:
                return Response({"detail": "Subdivision must belong to selected department."}, status=400)
            if not next_department:
                validated["department"] = subdivision.department
        for field, value in validated.items():
            setattr(target, field, value)
        if role_name:
            target.role = next_role
            if next_role and next_role.name == Role.Name.TEAMLEAD:
                target.manager = None
        if password:
            target.set_password(password)
        target.save()
        return Response(_user_to_front_payload(target))

    def delete(self, request, user_id: int):
        _ensure_admin_like(request.user)
        target = self._get_user(user_id)
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if _is_privileged_target(target):
                return Response({"detail": "Department head cannot delete privileged users."}, status=403)
        target.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FrontendUsersToggleStatusAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, user_id: int):
        _ensure_admin_like(request.user)
        target = User.objects.filter(id=user_id).first()
        if not target:
            raise NotFound("User not found.")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if _is_privileged_target(target):
                return Response({"detail": "Department head cannot change status of privileged users."}, status=403)
        target.is_active = not target.is_active
        target.is_blocked = not target.is_active
        target.save(update_fields=["is_active", "is_blocked"])
        target.refresh_from_db()
        return Response(_user_to_front_payload(target))


class FrontendUsersSetRoleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Frontend API"],
        request=FrontendSetRoleRequestSerializer,
        responses={200: serializers.DictField()},
    )
    def post(self, request, user_id: int):
        _ensure_admin_like(request.user)
        target = User.objects.filter(id=user_id).first()
        if not target:
            raise NotFound("User not found.")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if _is_privileged_target(target):
                return Response({"detail": "Department head cannot change role of privileged users."}, status=403)
        role = _resolve_role(request.data.get("role"))
        if not role:
            return Response({"detail": "Invalid role."}, status=400)
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if role.name in {Role.Name.DEPARTMENT_HEAD, Role.Name.ADMIN, Role.Name.SUPER_ADMIN}:
                return Response({"detail": "Department head cannot assign privileged role."}, status=403)
        target.role = role
        if role.name == Role.Name.TEAMLEAD:
            target.manager = None
            target.save(update_fields=["role", "manager"])
        else:
            target.save(update_fields=["role"])
        target.refresh_from_db()
        return Response(_user_to_front_payload(target))


class FrontendDepartmentsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = Department.objects.select_related("parent").order_by("name")
        return Response(
            [
                {
                    "id": item.id,
                    "name": item.name,
                    "parent": item.parent_id,
                    "is_active": item.is_active,
                }
                for item in items
            ]
        )

    def post(self, request):
        _ensure_admin_like(request.user)
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"name": ["This field is required."]}, status=400)
        parent_id = request.data.get("parent")
        item = Department.objects.create(
            name=name,
            parent_id=parent_id if parent_id else None,
            is_active=bool(request.data.get("is_active", True)),
        )
        return Response({"id": item.id, "name": item.name, "parent": item.parent_id, "is_active": item.is_active}, status=201)


class FrontendDepartmentsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, department_id: int):
        _ensure_admin_like(request.user)
        item = Department.objects.filter(id=department_id).first()
        if not item:
            raise NotFound("Department not found.")

        if "name" in request.data:
            name = (request.data.get("name") or "").strip()
            if not name:
                return Response({"name": ["This field may not be blank."]}, status=400)
            item.name = name

        if "parent" in request.data:
            parent_id = request.data.get("parent")
            if parent_id in ("", None):
                item.parent = None
            else:
                parent = Department.objects.filter(id=parent_id).first()
                if not parent:
                    return Response({"parent": ["Parent department not found."]}, status=404)
                if parent.id == item.id:
                    return Response({"parent": ["Department cannot be parent of itself."]}, status=400)
                item.parent = parent

        if "is_active" in request.data:
            item.is_active = bool(request.data.get("is_active"))

        try:
            item.full_clean()
            item.save()
        except IntegrityError:
            return Response({"name": ["Department with this name already exists."]}, status=400)

        return Response({"id": item.id, "name": item.name, "parent": item.parent_id, "is_active": item.is_active})

    def delete(self, request, department_id: int):
        _ensure_admin_like(request.user)
        item = Department.objects.filter(id=department_id).first()
        if not item:
            raise NotFound("Department not found.")
        if User.objects.filter(department=item).exists():
            return Response({"detail": "Department has users and cannot be deleted."}, status=400)
        item.delete()
        return Response(status=204)


class FrontendDepartmentsTransferUsersAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, department_id: int):
        _ensure_admin_like(request.user)
        source = Department.objects.filter(id=department_id).first()
        if not source:
            raise NotFound("Department not found.")

        target_department_id = request.data.get("target_department_id")
        if not target_department_id:
            return Response({"target_department_id": ["This field is required."]}, status=400)

        target = Department.objects.filter(id=target_department_id, is_active=True).first()
        if not target:
            return Response({"target_department_id": ["Target department not found."]}, status=404)
        if target.id == source.id:
            return Response({"target_department_id": ["Target department must be different."]}, status=400)

        users_qs = User.objects.filter(department_id=source.id)
        moved_count = users_qs.count()
        users_qs.update(department_id=target.id, subdivision=None)

        delete_source = bool(request.data.get("delete_source", False))
        deleted = False
        if delete_source and not User.objects.filter(department_id=source.id).exists():
            source.delete()
            deleted = True

        return Response(
            {
                "moved_count": moved_count,
                "source_department_id": source.id,
                "target_department_id": target.id,
                "deleted_source": deleted,
            }
        )


class FrontendPositionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = Position.objects.order_by("name")
        return Response([{"id": item.id, "name": item.name, "is_active": item.is_active} for item in items])

    def post(self, request):
        _ensure_admin_like(request.user)
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"name": ["This field is required."]}, status=400)
        item = Position.objects.create(name=name, is_active=bool(request.data.get("is_active", True)))
        return Response({"id": item.id, "name": item.name, "is_active": item.is_active}, status=201)


class FrontendPositionsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, position_id: int):
        _ensure_admin_like(request.user)
        item = Position.objects.filter(id=position_id).first()
        if not item:
            raise NotFound("Position not found.")
        item.delete()
        return Response(status=204)


class FrontendSubdivisionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        items = DepartmentSubdivision.objects.select_related("department").order_by("department__name", "name")
        department_id = request.query_params.get("department_id")
        if department_id:
            items = items.filter(department_id=department_id)
        is_active = request.query_params.get("is_active")
        if is_active is not None:
            items = items.filter(is_active=str(is_active).lower() in {"1", "true", "yes"})
        return Response(
            [
                {
                    "id": item.id,
                    "name": item.name,
                    "department_id": item.department_id,
                    "department_name": item.department.name,
                    "is_active": item.is_active,
                }
                for item in items
            ]
        )

    def post(self, request):
        _ensure_admin_like(request.user)
        name = (request.data.get("name") or "").strip()
        if not name:
            return Response({"name": ["This field is required."]}, status=400)
        department_id = request.data.get("department_id")
        if not department_id:
            return Response({"department_id": ["This field is required."]}, status=400)

        department = Department.objects.filter(id=department_id, is_active=True).first()
        if not department:
            return Response({"department_id": ["Department not found."]}, status=404)

        item = DepartmentSubdivision.objects.create(
            department=department,
            name=name,
            is_active=bool(request.data.get("is_active", True)),
        )
        return Response(
            {
                "id": item.id,
                "name": item.name,
                "department_id": item.department_id,
                "department_name": item.department.name,
                "is_active": item.is_active,
            },
            status=201,
        )


class FrontendSubdivisionsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, subdivision_id: int):
        _ensure_admin_like(request.user)
        item = DepartmentSubdivision.objects.select_related("department").filter(id=subdivision_id).first()
        if not item:
            raise NotFound("Subdivision not found.")

        if "name" in request.data:
            name = (request.data.get("name") or "").strip()
            if not name:
                return Response({"name": ["This field may not be blank."]}, status=400)
            item.name = name

        if "department_id" in request.data:
            department_id = request.data.get("department_id")
            department = Department.objects.filter(id=department_id, is_active=True).first()
            if not department:
                return Response({"department_id": ["Department not found."]}, status=404)
            item.department = department

        if "is_active" in request.data:
            item.is_active = bool(request.data.get("is_active"))

        try:
            item.full_clean()
            item.save()
        except IntegrityError:
            return Response({"name": ["Subdivision with this name already exists in this department."]}, status=400)

        return Response(
            {
                "id": item.id,
                "name": item.name,
                "department_id": item.department_id,
                "department_name": item.department.name,
                "is_active": item.is_active,
            }
        )

    def delete(self, request, subdivision_id: int):
        _ensure_admin_like(request.user)
        item = DepartmentSubdivision.objects.filter(id=subdivision_id).first()
        if not item:
            raise NotFound("Subdivision not found.")
        if User.objects.filter(subdivision=item).exists():
            return Response({"detail": "Subdivision has users and cannot be deleted."}, status=400)
        item.delete()
        return Response(status=204)


class FrontendPromotionRequestsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_like(request.user)
        qs = PromotionRequest.objects.select_related("user", "requested_role", "reviewed_by").order_by("-created_at")
        status_value = (request.query_params.get("status") or "").strip().lower()
        if status_value in {PromotionRequest.Status.PENDING, PromotionRequest.Status.APPROVED, PromotionRequest.Status.REJECTED}:
            qs = qs.filter(status=status_value)
        return Response(
            [
                {
                    "id": item.id,
                    "user_id": item.user_id,
                    "username": item.user.username,
                    "requested_role": _role_to_front(item.requested_role),
                    "reason": item.reason,
                    "status": item.status,
                    "review_comment": item.review_comment,
                    "reviewed_by": item.reviewed_by.username if item.reviewed_by_id else None,
                    "reviewed_at": item.reviewed_at,
                    "created_at": item.created_at,
                }
                for item in qs
            ]
        )

    def post(self, request):
        role = _resolve_role(request.data.get("role") or request.data.get("requested_role"))
        if not role:
            return Response({"detail": "Invalid requested role."}, status=400)
        target_user = request.user
        requested_user_id = request.data.get("user_id")
        if requested_user_id:
            _ensure_admin_like(request.user)
            candidate = User.objects.filter(id=requested_user_id).first()
            if not candidate:
                return Response({"detail": "Target user not found."}, status=404)
            target_user = candidate
        reason = (request.data.get("reason") or "").strip()
        item = PromotionRequest.objects.create(
            user=target_user,
            requested_role=role,
            reason=reason,
        )
        return Response(
            {
                "id": item.id,
                "user_id": item.user_id,
                "requested_role": _role_to_front(item.requested_role),
                "reason": item.reason,
                "status": item.status,
                "created_at": item.created_at,
            },
            status=201,
        )


class FrontendPromotionRequestsActionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, request_id: int, action: str):
        _ensure_admin_like(request.user)
        item = PromotionRequest.objects.select_related("user", "requested_role").filter(id=request_id).first()
        if not item:
            raise NotFound("Promotion request not found.")
        if item.status != PromotionRequest.Status.PENDING:
            return Response({"detail": "Request already processed."}, status=400)

        action_value = (action or "").strip().lower()
        comment = (request.data.get("comment") or request.data.get("reason") or "").strip()
        if action_value == "approve":
            item.status = PromotionRequest.Status.APPROVED
            target_user = item.user
            target_user.role = item.requested_role
            update_fields = ["role"]

            # Keep intern-selected subdivision and align department to it on promotion.
            if (
                target_user.subdivision_id
                and item.requested_role.name in {Role.Name.EMPLOYEE, Role.Name.TEAMLEAD}
            ):
                subdivision = DepartmentSubdivision.objects.select_related("department").filter(
                    id=target_user.subdivision_id,
                    is_active=True,
                ).first()
                if subdivision:
                    if target_user.department_id != subdivision.department_id:
                        target_user.department_id = subdivision.department_id
                        update_fields.append("department")
                else:
                    # If subdivision became inactive/missing, clear it to keep data consistent.
                    target_user.subdivision = None
                    update_fields.append("subdivision")

            target_user.save(update_fields=update_fields)
        elif action_value == "reject":
            item.status = PromotionRequest.Status.REJECTED
        else:
            return Response({"detail": "Unknown action."}, status=400)

        item.review_comment = comment
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.save(update_fields=["status", "review_comment", "reviewed_by", "reviewed_at", "updated_at"])
        return Response(
            {
                "id": item.id,
                "status": item.status,
                "review_comment": item.review_comment,
                "reviewed_at": item.reviewed_at,
            }
        )


class FrontendNewsCollectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        language = request.query_params.get("language", "ru")
        qs = News.objects.filter(is_active=True, language=language).order_by("position", "-published_at")[:10]
        return Response(NewsListSerializer(qs, many=True).data)

    def post(self, request):
        _ensure_content_manager(request.user)
        serializer = NewsDetailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        news = News.objects.create(
            language=request.data.get("language", "ru"),
            title=serializer.validated_data["title"],
            full_text=serializer.validated_data["full_text"],
            published_at=serializer.validated_data["published_at"],
            image=serializer.validated_data.get("image"),
            is_active=bool(request.data.get("is_active", True)),
            position=int(request.data.get("position", 0)),
            created_by=request.user,
        )
        return Response(NewsDetailSerializer(news).data, status=201)


class FrontendNewsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, news_id):
        item = News.objects.filter(id=news_id, is_active=True).first()
        if not item:
            raise NotFound("News not found.")
        return Response(NewsDetailSerializer(item).data)

    def patch(self, request, news_id):
        _ensure_content_manager(request.user)
        item = News.objects.filter(id=news_id).first()
        if not item:
            raise NotFound("News not found.")
        serializer = NewsDetailSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(NewsDetailSerializer(item).data)

    def delete(self, request, news_id):
        _ensure_content_manager(request.user)
        item = News.objects.filter(id=news_id).first()
        if not item:
            raise NotFound("News not found.")
        item.delete()
        return Response(status=204)


class FrontendAuditListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        _ensure_admin_like(request.user)
        qs = AuditLog.objects.select_related("user").order_by("-created_at")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if request.user.department_id:
                qs = qs.filter(user__department_id=request.user.department_id)
            else:
                qs = qs.none()
        level = request.query_params.get("level")
        if level:
            qs = qs.filter(level=level)
        payload = [
            {
                "id": item.id,
                "actor_id": item.user_id,
                "actor_username": item.user.username if item.user_id else "",
                "action": item.action,
                "level": item.level,
                "category": item.category,
                "metadata": item.metadata if hasattr(item, "metadata") else None,
                "created_at": item.created_at,
            }
            for item in qs[:200]
        ]
        return Response(payload)


class FrontendRegulationsCollectionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Regulation.objects.filter(is_active=True).order_by("position", "-created_at")
        regulation_type = request.query_params.get("type")
        if regulation_type in {Regulation.RegulationType.LINK, Regulation.RegulationType.FILE}:
            qs = qs.filter(type=regulation_type)
        if AccessPolicy.is_main_admin(request.user) or AccessPolicy.is_super_admin(request.user):
            return Response(RegulationAdminSerializer(qs, many=True).data)
        ack_map = {
            ack.regulation_id: ack
            for ack in RegulationAcknowledgement.objects.filter(user=request.user, regulation__in=qs)
        }
        serializer = RegulationSerializer(qs, many=True, context={"request": request, "ack_map": ack_map})
        return Response(serializer.data)

    def post(self, request):
        _ensure_content_manager(request.user)
        serializer = RegulationAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        return Response(RegulationAdminSerializer(instance).data, status=201)


class FrontendRegulationsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, regulation_id):
        item = Regulation.objects.filter(id=regulation_id, is_active=True).first()
        if not item:
            raise NotFound("Regulation not found.")
        if AccessPolicy.is_main_admin(request.user) or AccessPolicy.is_super_admin(request.user):
            return Response(RegulationAdminSerializer(item).data)
        ack = RegulationAcknowledgement.objects.filter(user=request.user, regulation=item).first()
        serializer = RegulationSerializer(item, context={"request": request, "ack_map": {item.id: ack} if ack else {}})
        return Response(serializer.data)

    def patch(self, request, regulation_id):
        _ensure_content_manager(request.user)
        item = Regulation.objects.filter(id=regulation_id).first()
        if not item:
            raise NotFound("Regulation not found.")
        serializer = RegulationAdminSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, regulation_id):
        _ensure_content_manager(request.user)
        item = Regulation.objects.filter(id=regulation_id).first()
        if not item:
            raise NotFound("Regulation not found.")
        item.delete()
        return Response(status=204)


class FrontendInstructionsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lang = request.query_params.get("lang")
        qs = Instruction.objects.filter(is_active=True)
        if lang:
            qs = qs.filter(language=lang)
        return Response(InstructionSerializer(qs.order_by("-updated_at"), many=True).data)

    def post(self, request):
        _ensure_content_manager(request.user)
        language = (request.data.get("language") or "ru").strip().lower()
        instruction_type = (request.data.get("type") or "text").strip().lower()
        content = (request.data.get("content") or "").strip()
        if language not in {"ru", "en", "kg"}:
            return Response({"language": ["Unsupported language."]}, status=400)
        if instruction_type not in {"text", "link", "file"}:
            return Response({"type": ["Unsupported instruction type."]}, status=400)
        if not content and instruction_type != "file":
            return Response({"content": ["This field is required."]}, status=400)

        payload = {
            "language": language,
            "type": instruction_type,
            "is_active": bool(request.data.get("is_active", True)),
        }
        if instruction_type == "text":
            payload["text"] = content
            payload["external_url"] = ""
        elif instruction_type == "link":
            payload["text"] = ""
            payload["external_url"] = content
        else:
            payload["text"] = ""
            payload["external_url"] = ""

        item = Instruction.objects.create(**payload)
        return Response(InstructionSerializer(item).data, status=201)


class FrontendInstructionsDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, instruction_id):
        _ensure_content_manager(request.user)
        item = Instruction.objects.filter(id=instruction_id).first()
        if not item:
            raise NotFound("Instruction not found.")

        if "language" in request.data:
            language = (request.data.get("language") or "").strip().lower()
            if language not in {"ru", "en", "kg"}:
                return Response({"language": ["Unsupported language."]}, status=400)
            item.language = language

        if "type" in request.data:
            instruction_type = (request.data.get("type") or "").strip().lower()
            if instruction_type not in {"text", "link", "file"}:
                return Response({"type": ["Unsupported instruction type."]}, status=400)
            item.type = instruction_type

        if "is_active" in request.data:
            item.is_active = bool(request.data.get("is_active"))

        if "content" in request.data:
            content = (request.data.get("content") or "").strip()
            if item.type == "text":
                item.text = content
                item.external_url = ""
            elif item.type == "link":
                item.text = ""
                item.external_url = content
            else:
                item.text = ""
                item.external_url = ""

        item.save()
        return Response(InstructionSerializer(item).data)

    def delete(self, request, instruction_id):
        _ensure_content_manager(request.user)
        item = Instruction.objects.filter(id=instruction_id).first()
        if not item:
            raise NotFound("Instruction not found.")
        item.delete()
        return Response(status=204)


class FrontendOnboardingMyAPIView(OnboardingOverviewView):
    permission_classes = [IsAuthenticated]


class FrontendOnboardingReportsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = OnboardingReport.objects.select_related("day", "user").order_by("-updated_at", "day__day_number")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if request.user.department_id:
                qs = qs.filter(user__department_id=request.user.department_id)
        elif not AccessPolicy.is_admin_like(request.user):
            qs = qs.filter(user=request.user)
        return Response(
            [
                {
                    "id": str(item.id),
                    "user_id": item.user_id,
                    "username": item.user.username,
                    "full_name": f"{item.user.first_name} {item.user.last_name}".strip() or item.user.username,
                    "day_id": str(item.day_id),
                    "day_number": item.day.day_number,
                    "did": item.did,
                    "will_do": item.will_do,
                    "problems": item.problems,
                    "report_title": item.report_title,
                    "report_description": item.report_description,
                    "github_url": item.github_url,
                    "attachment": item.attachment.url if item.attachment else "",
                    "status": item.status,
                    "reviewer_comment": item.reviewer_comment,
                    "updated_at": item.updated_at,
                }
                for item in qs
            ]
        )

    def post(self, request):
        if not AccessPolicy.is_intern(request.user):
            return Response({"detail": "Only intern can submit onboarding report."}, status=403)
        day_id = request.data.get("day_id")
        if not day_id:
            return Response({"day_id": ["This field is required."]}, status=400)
        did = (request.data.get("did") or "").strip()
        will_do = (request.data.get("will_do") or "").strip()
        problems = (request.data.get("problems") or "").strip()
        report_title = (request.data.get("report_title") or "").strip()
        report_description = (request.data.get("report_description") or "").strip()
        github_url = (request.data.get("github_url") or "").strip()
        attachment = request.FILES.get("attachment")
        day = OnboardingDay.objects.filter(id=day_id, is_active=True).first()
        if not day:
            return Response({"detail": "Day not found."}, status=404)
        if day.day_number == 2:
            if github_url and "github.com" not in github_url.lower():
                return Response({"github_url": ["Use GitHub URL."]}, status=400)
            did = did or report_title
            will_do = will_do or report_description
        report, _ = OnboardingReport.objects.update_or_create(
            user=request.user,
            day_id=day_id,
            defaults={
                "did": did,
                "will_do": will_do,
                "problems": problems,
                "report_title": report_title,
                "report_description": report_description,
                "github_url": github_url,
                "attachment": attachment,
            },
        )
        if report.can_be_sent():
            report.status = OnboardingReport.Status.SENT
            report.save(update_fields=["status", "updated_at"])
            if day.day_number == 2:
                OnboardingProgress.objects.update_or_create(
                    user=request.user,
                    day=day,
                    defaults={
                        "status": OnboardingProgress.Status.DONE,
                        "completed_at": timezone.now(),
                    },
                )
        return Response({"id": str(report.id), "status": report.status}, status=201)


class FrontendOnboardingReportDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, report_id):
        report = OnboardingReport.objects.filter(id=report_id, user=request.user).first()
        if not report:
            raise NotFound("Report not found.")
        if not report.can_be_modified():
            return Response({"detail": "Report cannot be modified"}, status=409)
        for field in ("did", "will_do", "problems", "report_title", "report_description", "github_url"):
            if field in request.data:
                setattr(report, field, request.data.get(field) or "")
        attachment = request.FILES.get("attachment")
        if attachment is not None:
            report.attachment = attachment
        if report.github_url and "github.com" not in report.github_url.lower():
            return Response({"github_url": ["Use GitHub URL."]}, status=400)
        report.save(
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
        if report.can_be_sent():
            report.status = OnboardingReport.Status.SENT
            report.save(update_fields=["status", "updated_at"])
            if report.day.day_number == 2:
                OnboardingProgress.objects.update_or_create(
                    user=request.user,
                    day=report.day,
                    defaults={
                        "status": OnboardingProgress.Status.DONE,
                        "completed_at": timezone.now(),
                    },
                )
        return Response({"id": str(report.id), "status": report.status})


class FrontendOnboardingReportReviewAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, report_id):
        _ensure_admin_like(request.user)
        report = OnboardingReport.objects.filter(id=report_id).first()
        if not report:
            raise NotFound("Report not found.")
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if request.user.department_id and report.user.department_id != request.user.department_id:
                raise PermissionDenied("Access denied for this department.")
        status_value = request.data.get("status")
        comment = request.data.get("reviewer_comment") or request.data.get("comment")
        report.set_status(status_value, reviewer=request.user, comment=comment)
        return Response({"id": str(report.id), "status": report.status, "reviewer_comment": report.reviewer_comment})


class FrontendSchedulesWorkSchedulesAPIView(WorkScheduleListAPIView):
    permission_classes = [IsAuthenticated]


class FrontendSchedulesMineAPIView(MyScheduleAPIView):
    permission_classes = [IsAuthenticated]


class FrontendSchedulesHolidaysAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        year = request.query_params.get("year")
        qs = ProductionCalendar.objects.filter(is_holiday=True).order_by("date")
        if year:
            qs = qs.filter(date__year=year)
        return Response(
            [{"date": item.date, "name": item.holiday_name or "", "is_holiday": True} for item in qs]
        )


class FrontendSecurityUnlockUsersAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _ensure_admin_like(request.user)
        user_ids = request.data.get("user_ids") or []
        reset_all = bool(request.data.get("all"))

        qs = User.objects.all()
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            if request.user.department_id:
                qs = qs.filter(department_id=request.user.department_id)
            else:
                qs = qs.none()

        if not reset_all:
            if not isinstance(user_ids, list) or not user_ids:
                return Response({"detail": "Provide user_ids or set all=true."}, status=400)
            qs = qs.filter(id__in=user_ids)

        updated = qs.update(failed_login_attempts=0, lockout_until=None, is_blocked=False)
        return Response({"updated_users": int(updated)})


class FrontendSecurityForceLogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        _ensure_admin_like(request.user)
        if AccessPolicy.is_admin(request.user) and not AccessPolicy.is_super_admin(request.user):
            return Response({"detail": "Only admin/superadmin can force logout all users."}, status=403)

        deleted_sessions, _ = Session.objects.all().delete()
        deleted_custom_sessions, _ = UserSession.objects.all().delete()
        deleted_tokens, _ = PasswordResetToken.objects.filter(is_used=False).delete()
        return Response(
            {
                "deleted_sessions": int(deleted_sessions),
                "deleted_custom_sessions": int(deleted_custom_sessions),
                "deleted_unused_reset_tokens": int(deleted_tokens),
            }
        )


class FrontendFeedbackTicketsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _to_bool(value, default=True):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def get(self, request):
        _ensure_admin_like(request.user)
        qs = Feedback.objects.all().order_by("-created_at")
        return Response(
            [
                {
                    "id": item.id,
                    "type": item.type,
                    "text": item.text,
                    "status": item.status,
                    "is_anonymous": item.is_anonymous,
                    "full_name": item.full_name,
                    "contact": item.contact,
                    "is_read": item.is_read,
                    "created_at": item.created_at,
                }
                for item in qs
            ]
        )

    def post(self, request):
        text = (request.data.get("text") or "").strip()
        feedback_type = request.data.get("type") or "review"
        if not text:
            return Response({"text": ["This field is required."]}, status=400)
        item = Feedback.objects.create(
            type=feedback_type,
            text=text,
            is_anonymous=self._to_bool(request.data.get("is_anonymous"), default=True),
            full_name=request.data.get("full_name"),
            contact=request.data.get("contact"),
        )
        return Response({"id": item.id, "status": item.status}, status=201)


class FrontendFeedbackReplyAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, ticket_id):
        _ensure_admin_like(request.user)
        item = Feedback.objects.filter(id=ticket_id).first()
        if not item:
            raise NotFound("Ticket not found.")
        # Legacy frontend expects reply endpoint. We mark ticket as in_progress/closed.
        new_status = request.data.get("status") or "in_progress"
        if new_status not in {"new", "in_progress", "closed"}:
            new_status = "in_progress"
        item.status = new_status
        item.is_read = True
        item.save(update_fields=["status", "is_read"])
        return Response({"id": item.id, "status": item.status})



