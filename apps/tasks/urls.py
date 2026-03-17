from django.urls import path

from .views import (
    ProjectDetailAPIView,
    ProjectListCreateAPIView,
    ProjectTasksAPIView,
    TaskAssigneesAPIView,
    TaskCommentDetailAPIView,
    TaskCommentsAPIView,
    TaskCreateAPIView,
    TaskDetailAPIView,
    TaskHistoryAPIView,
    TaskMoveAPIView,
    TaskMyAPIView,
    TaskSubTaskDetailAPIView,
    TaskSubTasksAPIView,
    TaskTeamAPIView,
)


urlpatterns = [
    path("my/", TaskMyAPIView.as_view(), name="tasks-my"),
    path("team/", TaskTeamAPIView.as_view(), name="tasks-team"),
    path("projects/", ProjectListCreateAPIView.as_view(), name="projects-list-create"),
    path("projects/<int:pk>/", ProjectDetailAPIView.as_view(), name="projects-detail"),
    path("projects/<int:pk>/tasks/", ProjectTasksAPIView.as_view(), name="projects-tasks"),
    path("assignees/", TaskAssigneesAPIView.as_view(), name="tasks-assignees"),
    path("create/", TaskCreateAPIView.as_view(), name="tasks-create"),
    path("<int:pk>/", TaskDetailAPIView.as_view(), name="tasks-detail"),
    path("<int:pk>/comments/", TaskCommentsAPIView.as_view(), name="tasks-comments"),
    path("<int:pk>/comments/<int:comment_id>/", TaskCommentDetailAPIView.as_view(), name="tasks-comment-detail"),
    path("<int:pk>/history/", TaskHistoryAPIView.as_view(), name="tasks-history"),
    path("<int:pk>/subtasks/", TaskSubTasksAPIView.as_view(), name="tasks-subtasks"),
    path("<int:pk>/subtasks/<int:subtask_id>/", TaskSubTaskDetailAPIView.as_view(), name="tasks-subtask-detail"),
    path("<int:pk>/move/", TaskMoveAPIView.as_view(), name="tasks-move"),
]
