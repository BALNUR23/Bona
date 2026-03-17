# Onboarding Backend

Django REST backend для HRM/onboarding-платформы (роли, онбординг, регламенты, контент, отчеты, графики, attendance, задачи, payroll, BPM, KB и метрики).

## Что внутри
- `Python + Django 4.2`
- `Django REST Framework`
- `JWT` через `djangorestframework-simplejwt`
- `PostgreSQL`
- `drf-spectacular` (OpenAPI/Swagger)
- `django-unfold` (админка, если пакет установлен)

## Структура backend
- `config/` - настройки, роутинг, healthcheck, SPA/compat endpoints
- `accounts/` - пользователи, роли, орг-структура, авторизация, reset пароля
- `onboarding_core/` - дни онбординга, материалы, прогресс
- `regulations/` - регламенты, подтверждение/квизы, intern flow
- `reports/` - ежедневные и onboarding-отчеты
- `content/` - новости, welcome, инструкции, feedback, курсы
- `work_schedule/` - графики, календари, weekly plans
- `apps/attendance/` - attendance-отметки, check-in по гео/IP, рабочий календарь
- `apps/tasks/` - командные/личные задачи
- `apps/payroll/` - зарплатные периоды и профили
- `apps/kb/` - база знаний
- `apps/metrics/` - персональные/командные метрики
- `apps/bpm/` - BPM-процессы и шаги
- `apps/audit/`, `security/`, `common/` - аудит, системные логи, уведомления

## Быстрый старт (локально)
1. Перейти в backend:
```bash
cd onboarding-backend
```

2. Создать и активировать venv:
```bash
python -m venv .venv
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate
```

3. Установить зависимости:
```bash
pip install -r requirements.txt
```

4. Подготовить `.env` в корне `onboarding-backend/`.

5. Применить миграции:
```bash
python manage.py migrate
```

6. Инициализировать роли/права (обязательно для RBAC):
```bash
python manage.py init_rbac
```

7. (Опционально) создать суперпользователя:
```bash
python manage.py createsuperuser
```

8. Запустить сервер:
```bash
python manage.py runserver
```

## Пример `.env`
```env
SECRET_KEY=change-me
DEBUG=true
ALLOWED_HOSTS=127.0.0.1,localhost

DB_NAME=onboarding
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=127.0.0.1
DB_PORT=5432
# либо одной строкой:
# DATABASE_URL=postgres://user:password@host:5432/dbname

CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
CSRF_TRUSTED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

AUDIT_PRIMARY_BACKEND=accounts
AUDIT_LEGACY_BACKEND=security
AUDIT_WRITE_MODE=primary_only

OFFICE_GEOFENCE_LATITUDE=42.8746
OFFICE_GEOFENCE_LONGITUDE=74.5698
OFFICE_GEOFENCE_RADIUS_M=150
OFFICE_IP_NETWORKS=127.0.0.1/32,192.168.1.0/24,192.168.10.0/24,10.0.0.0/16
```

## Основные URL
- Health: `GET /health/`
- Swagger UI: `GET /api/docs/`
- OpenAPI schema: `GET /api/schema/`
- JWT login: `POST /api/v1/auth/login/`
- JWT refresh: `POST /api/v1/auth/refresh/`
- Админ-вход: `/admin/login/`
- Django admin (технический): `/admin/panel/`

## API-модули (корневые префиксы)
- `/api/v1/accounts/` - профиль, орг-структура, reference-справочники, reset пароля
- `/api/v1/onboarding/` - дни/прогресс онбординга
- `/api/v1/regulations/` - регламенты, интерн-поток, подтверждения
- `/api/v1/reports/` - отчеты и review
- `/api/v1/content/` - новости, инструкции, feedback, курсы
- `/api/v1/common/` - уведомления
- `/api/v1/security/` - системные логи
- `/api/v1/attendance/` - attendance/check-in/календарь
- `/api/v1/tasks/` - задачи
- `/api/v1/payroll/` - payroll
- `/api/v1/kb/` - база знаний
- `/api/v1/metrics/` - метрики
- `/api/v1/bpm/` - BPM
- `/api/v1/work-schedules/...` и legacy schedule endpoints идут через `work_schedule.urls` под `/api/`
- Доп. compat endpoints для фронта - через `/api/` (`config/frontend_compat_urls.py`)

## Полезные management-команды
- `python manage.py init_rbac` - создать/обновить системные роли и права
- `python manage.py normalize_roles --dry-run` - проверить лишние роли без записи
- `python manage.py normalize_roles` - нормализовать роли и удалить лишние
- `python manage.py seed_demo_employee` - демо-пользователи + тестовые данные
- `python manage.py prepare_e2e` - очистить и нормализовать состояние БД для e2e
- `python manage.py generate_work_calendar_month --year 2026 --month 3` - сгенерировать рабочий календарь
- `python manage.py check_weekly_plan_deadlines` - проверка дедлайна weekly plan (понедельник 12:00)
- `python manage.py check_audit_writes` - проверка на прямые записи в аудит

## Планировщик weekly-plan дедлайна
Команду ниже рекомендуется запускать каждый понедельник в `12:01` (локальное серверное время):
```bash
python manage.py check_weekly_plan_deadlines
```

Пример cron (Linux):
```cron
1 12 * * 1 cd /path/to/onboarding-backend && /path/to/python manage.py check_weekly_plan_deadlines
```

Пример Windows Task Scheduler:
- Trigger: Weekly, Monday, `12:01`
- Program/script: путь к `python.exe`
- Arguments: `manage.py check_weekly_plan_deadlines`
- Start in: `C:\path\to\onboarding-backend`

## Тесты
```bash
pytest
```

Точечные примеры:
```bash
pytest work_schedule/tests/test_weekly_work_plan_api.py
pytest apps/payroll/tests/test_api.py
```

## Продакшен заметки
- WSGI entrypoint: `config.wsgi:application`
- `Procfile`: `web: gunicorn config.wsgi:application`
- Для статики используется `whitenoise` (если установлен)
- В production рекомендуется `DEBUG=false`, корректный `ALLOWED_HOSTS`, HTTPS и валидные `CSRF_TRUSTED_ORIGINS`

## Deploy в Google Cloud Run
В репозитории есть готовые файлы для Cloud Run:
- `Dockerfile`
- `.dockerignore`
- `start-cloudrun.sh`
- `.env.cloudrun.example`

Что нужно перед деплоем:
1. Подготовить PostgreSQL с внешним доступом или Cloud SQL и получить `DATABASE_URL`.
2. Подготовить `SECRET_KEY`.
3. Определить frontend-домен и прописать его в `CORS_ALLOWED_ORIGINS` и `CSRF_TRUSTED_ORIGINS`.

Пример деплоя через `gcloud`:
```bash
gcloud config set project project-88d05a80-3e89-49ce-87c

gcloud run deploy onboarding-backend \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars DEBUG=false \
  --set-env-vars SECRET_KEY=replace-me \
  --set-env-vars ALLOWED_HOSTS=* \
  --set-env-vars DATABASE_URL=postgres://USER:PASSWORD@HOST:5432/DBNAME \
  --set-env-vars CORS_ALLOWED_ORIGINS=https://your-frontend-domain.com \
  --set-env-vars CSRF_TRUSTED_ORIGINS=https://your-frontend-domain.com
```

Cloud Run при старте контейнера автоматически выполнит:
```bash
python manage.py collectstatic --noinput
python manage.py migrate --noinput
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT
```

После первого деплоя при необходимости отдельно выполните инициализацию ролей:
```bash
python manage.py init_rbac
```
Эту команду удобнее запускать один раз через Cloud Run Job или локально против production-базы.

## Роли в системе
Базовые роли:
- `SUPER_ADMIN`
- `ADMIN`
- `DEPARTMENT_HEAD`
- `TEAMLEAD`
- `EMPLOYEE`
- `INTERN`

Роли и права инициализируются командой `init_rbac`.
