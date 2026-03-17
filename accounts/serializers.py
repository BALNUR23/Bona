from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Department, DepartmentSubdivision, Position, Role, User


class UserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="role.name", read_only=True)
    role_level = serializers.IntegerField(source="role.level", read_only=True)
    department = serializers.CharField(source="department.name", read_only=True)
    subdivision = serializers.CharField(source="subdivision.name", read_only=True)
    position = serializers.CharField(source="position.name", read_only=True)
    manager = serializers.PrimaryKeyRelatedField(read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "first_name",
            "last_name",
            "full_name",
            "role",
            "role_level",
            "department",
            "subdivision",
            "position",
            "manager",
            "custom_position",
            "telegram",
            "phone",
            "photo",
        )

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    username_or_email = serializers.CharField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.UUIDField()
    new_password = serializers.CharField(write_only=True)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "Current password is incorrect."})
        if attrs["new_password"] != attrs["new_password_confirm"]:
            raise serializers.ValidationError({"new_password_confirm": "Passwords do not match."})
        try:
            validate_password(attrs["new_password"], user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"new_password": list(exc.messages)})
        return attrs


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ("id", "name", "level")


class DepartmentSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        required=False,
        allow_null=True,
    )
    users_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Department
        fields = ("id", "name", "parent", "is_active", "users_count")

    def validate_parent(self, value):
        instance = getattr(self, "instance", None)
        if not instance or value is None:
            return value
        if value.id == instance.id:
            raise serializers.ValidationError("Отдел не может быть родителем сам себе.")
        cursor = value
        while cursor is not None:
            if cursor.id == instance.id:
                raise serializers.ValidationError("Нельзя создать циклическую иерархию отделов.")
            cursor = cursor.parent
        return value


class PositionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Position
        fields = ("id", "name", "is_active")


class StructureUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    role = serializers.CharField(source="role.name", read_only=True)
    subdivision = serializers.CharField(source="subdivision.name", read_only=True)
    position = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = (
            "id",
            "full_name",
            "role",
            "position",
            "subdivision",
            "username",
            "telegram",
            "phone",
        )

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username

    def get_position(self, obj):
        if obj.position_id:
            return obj.position.name
        return obj.custom_position or ""


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "photo",
            "department",
            "subdivision",
            "position",
            "custom_position",
            "telegram",
            "phone",
        )

    def validate(self, attrs):
        position = attrs.get("position")
        custom_position = attrs.get("custom_position")
        department = attrs.get("department")
        subdivision = attrs.get("subdivision")

        if self.instance:
            position = position if "position" in attrs else self.instance.position
            custom_position = (
                custom_position if "custom_position" in attrs else self.instance.custom_position
            )
            department = department if "department" in attrs else self.instance.department
            subdivision = subdivision if "subdivision" in attrs else self.instance.subdivision

        if position and custom_position:
            raise serializers.ValidationError(
                "Укажите либо должность из списка, либо другую должность, но не оба поля."
            )

        if not position and not custom_position:
            raise serializers.ValidationError(
                "Необходимо указать должность из списка или другую должность."
            )

        if subdivision and department and subdivision.department_id != department.id:
            raise serializers.ValidationError("Подотдел должен принадлежать выбранному отделу.")

        return attrs


class DepartmentSubdivisionSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    users_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = DepartmentSubdivision
        fields = (
            "id",
            "department",
            "department_name",
            "name",
            "day_two_task_title",
            "day_two_task_description",
            "day_two_spec_url",
            "day_three_task_title",
            "day_three_task_description",
            "day_three_spec_url",
            "is_active",
            "users_count",
        )


class InternSubdivisionChoiceSerializer(serializers.Serializer):
    subdivision_id = serializers.IntegerField()

    def validate_subdivision_id(self, value):
        subdivision = DepartmentSubdivision.objects.select_related("department").filter(
            id=value,
            is_active=True,
        ).first()
        if not subdivision:
            raise serializers.ValidationError("Подотдел не найден или неактивен.")
        return value
