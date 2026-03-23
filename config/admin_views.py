from datetime import date as dt_date
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import authenticate, login as auth_login
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.access_policy import AccessPolicy
from accounts.models import Department, LoginHistory, Position, Role, User
from apps.attendance.models import AttendanceMark, AttendanceSession, OfficeNetwork
from apps.audit import AuditEvents, log_event
from content.models import Feedback, Instruction, News
from onboarding_core.models import OnboardingDay, OnboardingMaterial
from regulations.models import Regulation
from reports.models import OnboardingReport
from work_schedule.models import UserWorkSchedule, WeeklyWorkPlan, WorkSchedule

STATUS_META = {
    "DRAFT": {"label": "Черновик", "color": "#64748b"},
    "SENT": {"label": "Отправлен", "color": "#2563eb"},
    "ACCEPTED": {"label": "Принят", "color": "#16a34a"},
    "REVISION": {"label": "На доработку", "color": "#d97706"},
    "REJECTED": {"label": "Отклонен", "color": "#dc2626"},
}

ATTENDANCE_STATUS_RU = {
    "present": "Присутствует",
    "remote": "Удаленно",
    "vacation": "Отпуск",
    "sick": "Больничный",
    "absent": "Отсутствует",
    "business_trip": "Командировка",
}


def _get_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _landing_for(user):
    role_name = user.role.name if getattr(user, "role", None) else ""
    if role_name in {Role.Name.ADMINISTRATOR, Role.Name.ADMIN, Role.Name.SUPER_ADMIN}:
        return "admin_panel"
    if role_name == Role.Name.INTERN:
        return "intern_portal"
    if role_name == Role.Name.TEAMLEAD:
        return "teamlead_portal"
    return "employee_portal"


def unified_admin_login(request):
    if request.method == "GET":
        if request.user.is_authenticated:
            return redirect("/admin/login/portal/")
        return render(request, "admin/unified_login.html")

    username = (request.POST.get("username") or "").strip()
    password = request.POST.get("password") or ""

    if not username or not password:
        return render(
            request,
            "admin/unified_login.html",
            {"error": "Введите логин и пароль."},
            status=400,
        )

    existing_user = User.objects.filter(username=username).first()
    if not existing_user:
        existing_user = User.objects.filter(email__iexact=username).first()

    auth_username = existing_user.username if existing_user else username
    user = authenticate(request, username=auth_username, password=password)

    if existing_user and existing_user.lockout_until and existing_user.lockout_until > timezone.now():
        log_event(
            action=AuditEvents.LOGIN_BLOCKED_LOCKOUT,
            actor=existing_user,
            object_type="user",
            object_id=str(existing_user.id),
            level="warning",
            category="auth",
            ip_address=_get_ip(request),
        )
        return render(
            request,
            "admin/unified_login.html",
            {"error": "Слишком много неудачных попыток. Попробуйте позже."},
            status=403,
        )

    if not user:
        if existing_user:
            existing_user.failed_login_attempts += 1
            if existing_user.failed_login_attempts >= 5:
                existing_user.lockout_until = timezone.now() + timedelta(minutes=15)
            existing_user.save(update_fields=["failed_login_attempts", "lockout_until"])

        LoginHistory.objects.create(
            user=existing_user,
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            success=False,
        )
        log_event(
            action=AuditEvents.LOGIN_FAILED,
            actor=existing_user,
            object_type="user",
            object_id=str(existing_user.id) if existing_user else "",
            level="warning",
            category="auth",
            ip_address=_get_ip(request),
            metadata={"username": username},
        )
        return render(
            request,
            "admin/unified_login.html",
            {"error": "Неверный логин или пароль."},
            status=400,
        )

    if user.is_blocked:
        log_event(
            action=AuditEvents.LOGIN_BLOCKED_MANUAL,
            actor=user,
            object_type="user",
            object_id=str(user.id),
            level="warning",
            category="auth",
            ip_address=_get_ip(request),
        )
        return render(
            request,
            "admin/unified_login.html",
            {"error": "Пользователь заблокирован."},
            status=403,
        )

    user.failed_login_attempts = 0
    user.lockout_until = None
    user.save(update_fields=["failed_login_attempts", "lockout_until"])
    auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    LoginHistory.objects.create(
        user=user,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        success=True,
    )
    log_event(
        action=AuditEvents.LOGIN_SUCCESS,
        actor=user,
        object_type="user",
        object_id=str(user.id),
        level="info",
        category="auth",
        ip_address=_get_ip(request),
    )

    refresh = RefreshToken.for_user(user)
    return render(
        request,
        "admin/unified_employee_redirect.html",
        {
            "access_token": str(refresh.access_token),
            "landing": _landing_for(user),
            "role": user.role.name if user.role else "",
            "target_url": "/admin/login/portal/",
        },
    )


@staff_member_required
def onboarding_dashboard(request):
    reports = list(
        OnboardingReport.objects.select_related("user", "day").order_by("-created_at")[:20]
    )
    for report in reports:
        report.status_meta = STATUS_META.get(report.status, {"label": report.status, "color": "#64748b"})

    days = (
        OnboardingDay.objects.annotate(materials_count=Count("materials"))
        .order_by("position", "day_number")[:30]
    )

    materials = (
        OnboardingMaterial.objects.select_related("day")
        .order_by("day__position", "position")[:30]
    )

    context = {
        "title": "Онбординг / Отчёты",
        "reports": reports,
        "days": days,
        "materials": materials,
        "stats": {
            "total": OnboardingReport.objects.count(),
            "sent": OnboardingReport.objects.filter(status="SENT").count(),
            "draft": OnboardingReport.objects.filter(status="DRAFT").count(),
            "accepted": OnboardingReport.objects.filter(status="ACCEPTED").count(),
        },
    }
    return render(request, "admin/onboarding_dashboard.html", context)


@staff_member_required
def content_dashboard(request):
    context = {
        "title": "Управление контентом",
        "cards": [
            {
                "emoji": "📰",
                "title": "Новости компании",
                "desc": "Публикация новостей и объявлений для сотрудников.",
                "meta": f"{News.objects.count()} опубликовано",
                "url": "/admin/panel/content/news/",
                "action": "Перейти",
            },
            {
                "emoji": "📘",
                "title": "Инструкция по платформе",
                "desc": "Текст, ссылки и файлы с инструкциями.",
                "meta": f"{Instruction.objects.filter(is_active=True).count()} активна",
                "url": "/admin/panel/content/instruction/",
                "action": "Редактировать",
            },
            {
                "emoji": "💬",
                "title": "Обратная связь",
                "desc": "Жалобы, предложения и отзывы сотрудников.",
                "meta": f"{Feedback.objects.filter(status='new').count()} новых",
                "url": "/admin/panel/content/feedback/",
                "action": "Перейти",
            },
            {
                "emoji": "🏢",
                "title": "Компания: структура",
                "desc": "Иерархия отделов и распределение сотрудников по командам.",
                "meta": f"{Department.objects.filter(is_active=True).count()} активных отделов",
                "url": "/admin/panel/company/structure/",
                "action": "Открыть",
            },
            {
                "emoji": "📋",
                "title": "Компания: список",
                "desc": "Список сотрудников компании с фильтрами и быстрым поиском.",
                "meta": f"{User.objects.filter(is_active=True).exclude(role__name=Role.Name.SUPER_ADMIN).count()} сотрудников",
                "url": "/admin/panel/company/list/",
                "action": "Открыть",
            },
            {
                "emoji": "📑",
                "title": "Регламенты",
                "desc": "База документов и внутренних правил.",
                "meta": f"{Regulation.objects.count()} документов",
                "url": "/admin/panel/regulations/regulation/",
                "action": "Перейти",
            },
        ],
    }
    return render(request, "admin/content_dashboard.html", context)


@staff_member_required
def company_structure_page(request):
    departments = list(
        Department.objects.filter(is_active=True)
        .select_related("parent")
        .order_by("name")
    )
    users = list(
        User.objects.filter(is_active=True)
        .exclude(role__name=Role.Name.SUPER_ADMIN)
        .select_related("department", "position", "role")
        .order_by("department__name", "last_name", "first_name", "username")
    )

    members_by_department = {}
    for user in users:
        members_by_department.setdefault(user.department_id, []).append(user)

    children_by_parent = {}
    department_ids = {d.id for d in departments}
    for dep in departments:
        parent_id = dep.parent_id if dep.parent_id in department_ids else None
        children_by_parent.setdefault(parent_id, []).append(dep)
    for dep_list in children_by_parent.values():
        dep_list.sort(key=lambda d: d.name.lower())

    rows = []

    def visit(dep, level):
        rows.append(
            {
                "department": dep,
                "level": level,
                "members": members_by_department.get(dep.id, []),
            }
        )
        for child in children_by_parent.get(dep.id, []):
            visit(child, level + 1)

    for root in children_by_parent.get(None, []):
        visit(root, 0)

    context = {
        "title": "Компания: структура",
        "rows": rows,
        "orphan_members": members_by_department.get(None, []),
    }
    return render(request, "admin/company_structure.html", context)


@staff_member_required
def company_list_page(request):
    q = (request.GET.get("q") or "").strip()
    department_id = (request.GET.get("department_id") or "").strip()
    position_id = (request.GET.get("position_id") or "").strip()

    users_qs = (
        User.objects.filter(is_active=True)
        .exclude(role__name=Role.Name.SUPER_ADMIN)
        .select_related("department", "position", "role")
        .order_by("last_name", "first_name", "username")
    )

    if department_id.isdigit():
        users_qs = users_qs.filter(department_id=int(department_id))
    if position_id.isdigit():
        users_qs = users_qs.filter(position_id=int(position_id))
    if q:
        users_qs = users_qs.filter(
            Q(username__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
        )

    context = {
        "title": "Компания: список сотрудников",
        "users": list(users_qs),
        "departments": Department.objects.filter(is_active=True).order_by("name"),
        "positions": Position.objects.filter(is_active=True).order_by("name"),
        "filters": {
            "q": q,
            "department_id": department_id,
            "position_id": position_id,
        },
    }
    return render(request, "admin/company_list.html", context)


@staff_member_required
def attendance_checkin_page(request):
    role_name = getattr(getattr(request.user, "role", None), "name", "")
    can_checkin = role_name in {Role.Name.ADMIN, Role.Name.EMPLOYEE, Role.Name.INTERN}
    can_view_company_attendance = role_name in {Role.Name.SUPER_ADMIN, Role.Name.ADMINISTRATOR}

    today = timezone.localdate()
    date_param = (request.GET.get("date") or "").strip()
    month_param = (request.GET.get("month") or "").strip()
    try:
        selected_date = dt_date.fromisoformat(date_param) if date_param else today
    except ValueError:
        selected_date = today
    try:
        if month_param:
            y, m = month_param.split("-", 1)
            selected_month_year = int(y)
            selected_month_num = int(m)
        else:
            selected_month_year = today.year
            selected_month_num = today.month
    except (TypeError, ValueError):
        selected_month_year = today.year
        selected_month_num = today.month

    today_mark = (
        AttendanceMark.objects.filter(user=request.user, date=today)
        .select_related("created_by")
        .first()
    )
    if today_mark:
        today_mark.status_ru = ATTENDANCE_STATUS_RU.get(today_mark.status, today_mark.get_status_display())
    recent_marks = list(
        AttendanceMark.objects.filter(user=request.user)
        .order_by("-date")[:7]
    )
    for mark in recent_marks:
        mark.status_ru = ATTENDANCE_STATUS_RU.get(mark.status, mark.get_status_display())
    recent_sessions = list(
        AttendanceSession.objects.filter(user=request.user)
        .select_related("attendance_mark")
        .order_by("-checked_at")[:10]
    )
    day_marks = []
    month_counts = []
    if can_view_company_attendance:
        day_marks = list(
            AttendanceMark.objects.filter(date=selected_date)
            .exclude(user__role__name=Role.Name.SUPER_ADMIN)
            .select_related("user", "user__department", "user__role")
            .order_by("user__department__name", "user__username")
        )
        for mark in day_marks:
            mark.status_ru = ATTENDANCE_STATUS_RU.get(mark.status, mark.get_status_display())

        month_marks = list(
            AttendanceMark.objects.filter(
                date__year=selected_month_year,
                date__month=selected_month_num,
            )
            .exclude(user__role__name=Role.Name.SUPER_ADMIN)
            .select_related("user", "user__department", "user__role")
            .order_by("date", "user__department__name", "user__username")
        )
        month_counts = list(
            AttendanceMark.objects.filter(
                date__year=selected_month_year,
                date__month=selected_month_num,
            )
            .exclude(user__role__name=Role.Name.SUPER_ADMIN)
            .values("date")
            .annotate(total=Count("id"))
            .order_by("date")
        )
        month_day_details_map = {}
        for mark in month_marks:
            month_day_details_map.setdefault(mark.date, []).append(
                {
                    "username": mark.user.username,
                    "department": mark.user.department.name if mark.user.department_id else "-",
                    "role": mark.user.role.name if mark.user.role_id else "-",
                    "status_ru": ATTENDANCE_STATUS_RU.get(mark.status, mark.get_status_display()),
                }
            )
        month_day_details = [
            {"date": day, "items": items}
            for day, items in month_day_details_map.items()
        ]
    else:
        month_day_details = []

    context = {
        "title": "Отметка и посещаемость",
        "checkin_api_url": "/api/v1/attendance/check-in/",
        "today": today,
        "can_checkin": can_checkin,
        "can_view_company_attendance": can_view_company_attendance,
        "selected_date": selected_date,
        "selected_month": f"{selected_month_year:04d}-{selected_month_num:02d}",
        "today_mark": today_mark,
        "recent_marks": recent_marks,
        "recent_sessions": recent_sessions,
        "day_marks": day_marks,
        "month_counts": month_counts,
        "month_day_details": month_day_details,
    }
    return render(request, "admin/attendance_checkin.html", context)


@staff_member_required
def work_schedule_board_page(request):
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_dates = [week_start + timedelta(days=i) for i in range(7)]

    fallback_schedule = (
        WorkSchedule.objects.filter(is_active=True, is_default=True).first()
        or WorkSchedule.objects.filter(is_active=True).order_by("id").first()
    )
    employees = User.objects.filter(role__name=Role.Name.EMPLOYEE).select_related("role")

    existing_assignments = {
        item.user_id: item
        for item in UserWorkSchedule.objects.select_related("user", "schedule").filter(user__in=employees)
    }
    if fallback_schedule:
        missing = [u for u in employees if u.id not in existing_assignments]
        if missing:
            UserWorkSchedule.objects.bulk_create(
                [
                    UserWorkSchedule(user=user, schedule=fallback_schedule, approved=False)
                    for user in missing
                ],
                ignore_conflicts=True,
            )
            existing_assignments = {
                item.user_id: item
                for item in UserWorkSchedule.objects.select_related("user", "schedule").filter(user__in=employees)
            }

    day_buckets = [[] for _ in range(7)]
    for user in employees:
        assignment = existing_assignments.get(user.id)
        if not assignment:
            continue

        weekly_plan = (
            WeeklyWorkPlan.objects.filter(user=user, week_start=week_start)
            .order_by("-updated_at")
            .first()
        )
        day_map = {}
        if weekly_plan:
            for item in (weekly_plan.days or []):
                if isinstance(item, dict) and item.get("date"):
                    day_map[str(item.get("date"))] = item

        user_label = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username
        details_url = reverse("admin:work_schedule_userworkschedule_change", args=[assignment.id])

        for idx, day in enumerate(week_dates):
            item = day_map.get(day.isoformat())
            if item:
                mode = item.get("mode")
                if mode == "day_off":
                    continue
                start_time = item.get("start_time") or "-"
                end_time = item.get("end_time") or "-"
                mode_label = "online" if mode == "online" else "office"
            else:
                if day.weekday() not in (assignment.schedule.work_days or []):
                    continue
                start_time = assignment.schedule.start_time.strftime("%H:%M")
                end_time = assignment.schedule.end_time.strftime("%H:%M")
                mode_label = "office"

            day_buckets[idx].append(
                {
                    "user_display": user_label,
                    "time_range": f"{start_time} - {end_time}",
                    "mode": mode_label,
                    "details_url": details_url,
                }
            )

    context = {
        "title": "Графики работы (неделя)",
        "week_start": week_start,
        "day_headers": list(
            zip(
                ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"),
                week_dates,
            )
        ),
        "day_buckets": day_buckets,
    }
    return render(request, "admin/work_schedule_board.html", context)




@staff_member_required
def office_networks_page(request):
    user = request.user
    if not (user.is_superuser or AccessPolicy.is_super_admin(user)):
        messages.error(request, "Доступ только для superadmin.")
        return redirect("admin:index")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "create":
            name = (request.POST.get("name") or "").strip()
            cidr = (request.POST.get("cidr") or "").strip()
            is_active = request.POST.get("is_active") == "on"

            if not name or not cidr:
                messages.error(request, "Укажите название и CIDR.")
            else:
                try:
                    obj = OfficeNetwork(name=name, cidr=cidr, is_active=is_active)
                    obj.full_clean()
                    obj.save()
                    messages.success(request, "Сеть добавлена.")
                except ValidationError as exc:
                    messages.error(request, f"Ошибка валидации: {exc}")

        elif action == "toggle":
            network_id = request.POST.get("network_id")
            obj = OfficeNetwork.objects.filter(id=network_id).first()
            if not obj:
                messages.error(request, "Сеть не найдена.")
            else:
                obj.is_active = not obj.is_active
                obj.save(update_fields=["is_active", "updated_at"])
                messages.success(request, "Статус сети обновлён.")

        elif action == "delete":
            network_id = request.POST.get("network_id")
            obj = OfficeNetwork.objects.filter(id=network_id).first()
            if not obj:
                messages.error(request, "Сеть не найдена.")
            else:
                obj.delete()
                messages.success(request, "Сеть удалена.")

        else:
            messages.error(request, "Неизвестное действие.")

        return redirect("admin-office-networks-page")

    context = {
        "title": "Офисные сети (Whitelist)",
        "networks": OfficeNetwork.objects.all().order_by("name", "id"),
    }
    return render(request, "admin/office_networks_page.html", context)


class AdminProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "telegram", "photo"]
        labels = {
            "first_name": "Имя",
            "last_name": "Фамилия",
            "email": "Email",
            "phone": "Телефон",
            "telegram": "Telegram",
            "photo": "Фото",
        }
        help_texts = {
            "photo": "JPG/PNG/WEBP, до 2MB.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if name == "photo":
                continue
            field.widget.attrs.update(
                {
                    "style": "width:100%; border:1px solid #d1d5db; border-radius:10px; padding:10px 12px;",
                }
            )


@staff_member_required
def profile_page(request):
    user = request.user

    if request.method == "POST":
        form = AdminProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Профиль сохранён.")
            return redirect("admin-profile-page")
        messages.error(request, "Проверьте поля формы.")
    else:
        form = AdminProfileForm(instance=user)

    context = {
        "title": "Профиль пользователя",
        "form": form,
        "profile_user": user,
        "role_name": getattr(getattr(user, "role", None), "name", "—"),
        "department_name": getattr(getattr(user, "department", None), "name", "—"),
        "position_name": getattr(getattr(user, "position", None), "name", "—"),
        "departments": Department.objects.filter(is_active=True).order_by("name"),
        "positions": Position.objects.filter(is_active=True).order_by("name"),
    }
    return render(request, "admin/profile_page.html", context)
