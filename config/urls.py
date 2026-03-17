from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .admin_views import (
    attendance_checkin_page,
    content_dashboard,
    onboarding_dashboard,
    work_schedule_board_page,
)
from .health import health_check
from .spa_views import spa_asset, spa_index, spa_portal, spa_vite_icon

admin.site.site_header = "HRM Администрирование"
admin.site.site_title = "Админ-панель HRM"
admin.site.index_title = "Управление системой"

urlpatterns = [
    path("health/", health_check, name="health"),
    path("ckeditor5/", include("django_ckeditor_5.urls")),

    # Unified web login: use frontend login page.
    path("login/", spa_index, name="spa-login"),
    path("admin/login/portal/", spa_portal, name="admin-login-portal"),
    path("admin/login/", RedirectView.as_view(url="/login/", permanent=False), name="admin-login"),
    path("admin/", RedirectView.as_view(url="/login/", permanent=False), name="admin-root"),

    # Keep Django admin available on a separate technical path.
    path("admin/panel/onboarding/", onboarding_dashboard, name="admin-onboarding-dashboard"),
    path("admin/panel/content/", content_dashboard, name="admin-content-dashboard"),
    path("admin/panel/attendance/check-in/", attendance_checkin_page, name="admin-attendance-checkin-page"),
    path("admin/panel/work-schedule-board/", work_schedule_board_page, name="admin-work-schedule-board"),
    path("admin/panel/login/", RedirectView.as_view(url="/login/", permanent=False), name="admin-panel-login-redirect"),
    path("admin/panel/", admin.site.urls),
    re_path(r"^admin/(?!login(?:/|$)|panel(?:/|$)).*$", spa_portal, name="spa-admin-catch-all"),

    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),

    path("api/v1/auth/login/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    path("api/v1/accounts/", include("accounts.urls")),
    path("api/v1/onboarding/", include("onboarding_core.urls")),
    path("api/v1/reports/", include("reports.urls")),
    path("api/v1/security/", include("security.urls")),
    path("api/v1/regulations/", include("regulations.urls")),
    path("api/v1/common/", include("common.urls")),
    path("api/v1/content/", include("content.urls")),
    path("api/v1/attendance/", include("apps.attendance.urls")),
    path("api/v1/tasks/", include("apps.tasks.urls")),
    path("api/v1/payroll/", include("apps.payroll.urls")),
    path("api/v1/kb/", include("apps.kb.urls")),
    path("api/v1/metrics/", include("apps.metrics.urls")),
    path("api/v1/bpm/", include("apps.bpm.urls")),
    # Frontend compatibility endpoints: exposed on both /api/ and /api/v1/
    # because the frontend uses /api/auth/*, /api/v1/auth/* paths interchangeably.
    path("api/v1/", include("config.frontend_compat_urls")),
    path("api/", include("config.frontend_compat_urls")),
    path("api/", include("work_schedule.urls")),

    path("assets/<path:asset_path>", spa_asset, name="spa-asset"),
    path("vite.svg", spa_vite_icon, name="spa-vite-icon"),

    # Remove direct web entrypoints outside /admin
    path("", RedirectView.as_view(url="/admin/", permanent=False), name="spa-index"),
    re_path(
        r"^(?!admin/|api/|ckeditor5/|media/|static/|assets/|vite\.svg$).*$",
        RedirectView.as_view(url="/admin/", permanent=False),
        name="spa-catch-all",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
