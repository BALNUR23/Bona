from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Department, Role, User
from work_schedule.models import WeeklyWorkPlan

from .models import Board, Column, Task
from .views import MANDATORY_WEEKLY_PLAN_TASK_TITLE


class TasksApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.employee_role, _ = Role.objects.get_or_create(
            name=Role.Name.EMPLOYEE,
            defaults={"level": Role.Level.EMPLOYEE},
        )
        self.teamlead_role, _ = Role.objects.get_or_create(
            name=Role.Name.TEAMLEAD,
            defaults={"level": Role.Level.TEAMLEAD},
        )
        self.admin_role, _ = Role.objects.get_or_create(
            name=Role.Name.ADMIN,
            defaults={"level": Role.Level.ADMIN},
        )
        self.department = Department.objects.create(name="Backend")

        self.lead = User.objects.create_user(
            username="lead_task",
            password="StrongPass123!",
            role=self.teamlead_role,
            department=self.department,
        )
        self.subordinate = User.objects.create_user(
            username="sub_task",
            password="StrongPass123!",
            role=self.employee_role,
            manager=self.lead,
            department=self.department,
        )
        self.outsider = User.objects.create_user(
            username="out_task",
            password="StrongPass123!",
            role=self.employee_role,
        )
        self.admin = User.objects.create_user(
            username="admin_task",
            password="StrongPass123!",
            role=self.admin_role,
            department=self.department,
        )

    @patch("apps.tasks.views.TasksAuditService.log_task_created")
    def test_teamlead_can_create_task_for_subordinate(self, log_task_created):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {
                "title": "Task 1",
                "description": "Desc",
                "assignee_id": self.subordinate.id,
                "priority": "medium",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(Task.objects.filter(assignee=self.subordinate, reporter=self.lead).exists())
        log_task_created.assert_called_once()

    def test_teamlead_cannot_create_task_for_outside_user(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {"title": "Task 2", "assignee_id": self.outsider.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @patch("apps.tasks.views.TasksAuditService.log_task_created")
    def test_employee_can_create_task_for_self_without_assignee_id(self, log_task_created):
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {
                "title": "Self task",
                "description": "Created by employee",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        task = Task.objects.filter(assignee=self.subordinate, reporter=self.subordinate, title="Self task").first()
        self.assertIsNotNone(task)
        log_task_created.assert_called_once()

    @patch("apps.tasks.views.TasksAuditService.log_task_created")
    def test_employee_can_create_task_for_self_with_assignee_id(self, log_task_created):
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {
                "title": "Self task explicit",
                "assignee_id": self.subordinate.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        task = Task.objects.filter(
            assignee=self.subordinate,
            reporter=self.subordinate,
            title="Self task explicit",
        ).first()
        self.assertIsNotNone(task)
        log_task_created.assert_called_once()

    def test_employee_cannot_create_task_for_another_user(self):
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {"title": "Not allowed", "assignee_id": self.outsider.id},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_my_endpoint_returns_only_assigned_tasks(self):
        task1 = Task.objects.create(
            board=self._create_default_board(self.subordinate),
            column=self._get_new_column(self.subordinate),
            title="Mine",
            assignee=self.subordinate,
            reporter=self.lead,
        )
        Task.objects.create(
            board=self._create_default_board(self.outsider),
            column=self._get_new_column(self.outsider),
            title="Other",
            assignee=self.outsider,
            reporter=self.lead,
        )
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.get("/api/v1/tasks/my/")
        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(task1.id, returned_ids)

    def test_team_endpoint_for_lead_returns_subordinates_tasks(self):
        task = Task.objects.create(
            board=self._create_default_board(self.subordinate),
            column=self._get_new_column(self.subordinate),
            title="Team task",
            assignee=self.subordinate,
            reporter=self.lead,
        )
        Task.objects.create(
            board=self._create_default_board(self.outsider),
            column=self._get_new_column(self.outsider),
            title="Out task",
            assignee=self.outsider,
            reporter=self.lead,
        )
        self.client.force_authenticate(user=self.lead)
        response = self.client.get("/api/v1/tasks/team/")
        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(task.id, returned_ids)

    @patch("apps.tasks.views.TasksAuditService.log_task_moved")
    def test_move_task_logs_audit(self, log_task_moved):
        board = self._create_default_board(self.subordinate)
        col1 = self._get_new_column(self.subordinate)
        col2 = board.columns.filter(order=2).first()
        self.assertIsNotNone(col2)
        task = Task.objects.create(
            board=board,
            column=col1,
            title="Move me",
            assignee=self.subordinate,
            reporter=self.lead,
        )
        self.client.force_authenticate(user=self.lead)
        response = self.client.patch(f"/api/v1/tasks/{task.id}/move/", {"column_id": col2.id}, format="json")
        self.assertEqual(response.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.column_id, col2.id)
        log_task_moved.assert_called_once()

    def _create_default_board(self, user):
        from .views import get_user_default_board

        return get_user_default_board(user)

    def _get_new_column(self, user):
        board = self._create_default_board(user)
        return board.columns.order_by("order").first()

    def _create_columns_for_board(self, board):
        for order, name in enumerate(("To Do", "In Progress", "Review", "Done", "Blocked"), start=1):
            Column.objects.create(board=board, name=name, order=order)

    def _next_monday(self):
        today = timezone.localdate()
        days_ahead = (7 - today.weekday()) % 7
        return today + timedelta(days=days_ahead or 7)

    def _empty_week_days(self, monday):
        return [
            {
                "date": (monday + timedelta(days=i)).isoformat(),
                "mode": "day_off",
            }
            for i in range(7)
        ]

    def test_team_endpoint_creates_weekly_plan_task_if_missing(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get("/api/v1/tasks/team/")
        self.assertEqual(response.status_code, 200)
        task = Task.objects.filter(
            assignee=self.subordinate,
            title=MANDATORY_WEEKLY_PLAN_TASK_TITLE,
            due_date=self._next_monday(),
        ).first()
        self.assertIsNotNone(task)

    def test_team_endpoint_does_not_create_weekly_plan_task_if_plan_exists(self):
        next_monday = self._next_monday()
        WeeklyWorkPlan.objects.create(
            user=self.subordinate,
            week_start=next_monday,
            days=self._empty_week_days(next_monday),
            office_hours=0,
            online_hours=0,
            online_reason="n/a",
        )
        self.client.force_authenticate(user=self.lead)
        response = self.client.get("/api/v1/tasks/team/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Task.objects.filter(
                assignee=self.subordinate,
                title=MANDATORY_WEEKLY_PLAN_TASK_TITLE,
                due_date=next_monday,
            ).exists()
        )

    def test_assignees_endpoint_for_teamlead_returns_only_subordinates(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.get("/api/v1/tasks/assignees/")
        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(self.subordinate.id, returned_ids)
        self.assertIn(self.lead.id, returned_ids)
        self.assertNotIn(self.outsider.id, returned_ids)

    def test_assignees_endpoint_for_employee_returns_self_only(self):
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.get("/api/v1/tasks/assignees/")
        self.assertEqual(response.status_code, 200)
        returned_ids = [item["id"] for item in response.data]
        self.assertEqual(returned_ids, [self.subordinate.id])

    def test_teamlead_can_create_project_with_own_team_members(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            "/api/v1/tasks/projects/",
            {
                "name": "CRM rollout",
                "description": "New customer flows",
                "member_ids": [self.subordinate.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        board = Board.objects.get(name="CRM rollout")
        self.assertFalse(board.is_personal)
        self.assertEqual(board.created_by, self.lead)
        self.assertEqual(board.responsible_user, self.lead)
        self.assertEqual(board.status, Board.Status.ACTIVE)
        self.assertEqual(set(board.members.values_list("id", flat=True)), {self.lead.id, self.subordinate.id})
        self.assertEqual(board.columns.count(), 5)

    def test_teamlead_cannot_create_project_with_outside_member(self):
        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            "/api/v1/tasks/projects/",
            {
                "name": "Forbidden project",
                "member_ids": [self.outsider.id],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Board.objects.filter(name="Forbidden project").exists())

    def test_project_response_contains_extended_fields(self):
        board = Board.objects.create(
            name="Project structure",
            description="Board with extra fields",
            is_personal=False,
            created_by=self.lead,
            responsible_user=self.subordinate,
            status=Board.Status.PLANNING,
            end_date=timezone.localdate() + timedelta(days=7),
            department=self.department,
        )
        board.members.set([self.lead, self.subordinate])

        self.client.force_authenticate(user=self.lead)
        response = self.client.get("/api/v1/tasks/projects/")
        self.assertEqual(response.status_code, 200)
        project = next(item for item in response.data if item["id"] == board.id)
        self.assertEqual(project["status"], Board.Status.PLANNING)
        self.assertEqual(project["responsible_user"], self.subordinate.id)
        self.assertIn("created_at", project)
        self.assertEqual(project["end_date"], board.end_date.isoformat())

    @patch("apps.tasks.views.TasksAuditService.log_task_created")
    def test_teamlead_can_create_task_inside_project_board(self, log_task_created):
        board = Board.objects.create(
            name="Mobile app",
            is_personal=False,
            created_by=self.lead,
            department=self.department,
        )
        board.members.set([self.lead, self.subordinate])
        self._create_columns_for_board(board)

        self.client.force_authenticate(user=self.lead)
        response = self.client.post(
            "/api/v1/tasks/create/",
            {
                "board_id": board.id,
                "title": "Implement API",
                "assignee_id": self.subordinate.id,
                "priority": "high",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        task = Task.objects.get(title="Implement API")
        self.assertEqual(task.board_id, board.id)
        self.assertEqual(task.assignee, self.subordinate)
        log_task_created.assert_called_once()

    def test_project_tasks_endpoint_returns_only_selected_project_tasks(self):
        board = Board.objects.create(
            name="Internal tools",
            is_personal=False,
            created_by=self.lead,
            department=self.department,
        )
        board.members.set([self.lead, self.subordinate])
        self._create_columns_for_board(board)

        other_board = Board.objects.create(
            name="Other board",
            is_personal=False,
            created_by=self.lead,
            department=self.department,
        )
        other_board.members.set([self.lead, self.subordinate])
        self._create_columns_for_board(other_board)

        Task.objects.create(
            board=board,
            column=board.columns.order_by("order").first(),
            title="Project task",
            assignee=self.subordinate,
            reporter=self.lead,
        )
        Task.objects.create(
            board=other_board,
            column=other_board.columns.order_by("order").first(),
            title="Other task",
            assignee=self.subordinate,
            reporter=self.lead,
        )

        self.client.force_authenticate(user=self.subordinate)
        response = self.client.get(f"/api/v1/tasks/projects/{board.id}/tasks/")
        self.assertEqual(response.status_code, 200)
        titles = {item["title"] for item in response.data}
        self.assertEqual(titles, {"Project task"})

    def test_project_report_endpoint_returns_summary_and_overdue_tasks(self):
        board = Board.objects.create(
            name="Project report",
            is_personal=False,
            created_by=self.lead,
            department=self.department,
        )
        board.members.set([self.lead, self.subordinate])
        self._create_columns_for_board(board)

        todo_column = board.columns.get(order=1)
        in_progress_column = board.columns.get(order=2)
        done_column = board.columns.get(order=4)

        Task.objects.create(
            board=board,
            column=todo_column,
            title="Overdue task",
            assignee=self.subordinate,
            reporter=self.lead,
            status=Task.Status.TO_DO,
            due_date=timezone.localdate() - timedelta(days=1),
        )
        Task.objects.create(
            board=board,
            column=in_progress_column,
            title="In progress task",
            assignee=self.subordinate,
            reporter=self.lead,
            status=Task.Status.IN_PROGRESS,
        )
        Task.objects.create(
            board=board,
            column=done_column,
            title="Completed task",
            assignee=self.subordinate,
            reporter=self.lead,
            status=Task.Status.DONE,
        )

        self.client.force_authenticate(user=self.subordinate)
        response = self.client.get(f"/api/v1/tasks/projects/{board.id}/report/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_task_count"], 3)
        self.assertEqual(response.data["completed_task_count"], 1)
        self.assertEqual(response.data["progress_percentage"], 33)
        self.assertEqual(response.data["status_counts"]["to_do"], 1)
        self.assertEqual(response.data["status_counts"]["in_progress"], 1)
        self.assertEqual(response.data["status_counts"]["done"], 1)
        self.assertEqual(response.data["overdue_task_count"], 1)
        self.assertEqual(len(response.data["overdue_tasks"]), 1)
        self.assertEqual(response.data["overdue_tasks"][0]["title"], "Overdue task")

    def test_filter_tasks_by_priority_and_status(self):
        board = self._create_default_board(self.subordinate)
        todo_column = board.columns.get(order=1)
        done_column = board.columns.get(order=4)
        Task.objects.create(
            board=board,
            column=todo_column,
            title="Critical task",
            assignee=self.subordinate,
            reporter=self.lead,
            priority=Task.Priority.CRITICAL,
            status=Task.Status.TO_DO,
        )
        Task.objects.create(
            board=board,
            column=done_column,
            title="Done task",
            assignee=self.subordinate,
            reporter=self.lead,
            priority=Task.Priority.LOW,
            status=Task.Status.DONE,
        )
        self.client.force_authenticate(user=self.subordinate)
        response = self.client.get("/api/v1/tasks/my/?priority=critical&status=to_do")
        self.assertEqual(response.status_code, 200)
        titles = [item["title"] for item in response.data]
        self.assertEqual(titles, ["Critical task"])

    def test_create_and_complete_subtasks(self):
        board = self._create_default_board(self.subordinate)
        task = Task.objects.create(
            board=board,
            column=board.columns.get(order=1),
            title="Parent task",
            assignee=self.subordinate,
            reporter=self.lead,
            status=Task.Status.TO_DO,
        )
        self.client.force_authenticate(user=self.lead)
        create_response = self.client.post(
            f"/api/v1/tasks/{task.id}/subtasks/",
            {"title": "Write tests"},
            format="json",
        )
        self.assertEqual(create_response.status_code, 201)
        subtask_id = create_response.data["id"]

        update_response = self.client.patch(
            f"/api/v1/tasks/{task.id}/subtasks/{subtask_id}/",
            {"is_completed": True},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)

        detail_response = self.client.get(f"/api/v1/tasks/{task.id}/")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["subtasks_total"], 1)
        self.assertEqual(detail_response.data["subtasks_completed"], 1)
        self.assertTrue(detail_response.data["can_complete_parent"])
