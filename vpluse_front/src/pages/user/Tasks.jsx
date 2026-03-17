import { useEffect, useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';
import MainLayout from '../../layouts/MainLayout';
import { useAuth } from '../../context/AuthContext';
import { tasksAPI } from '../../api/content';

const PRIORITY_LABELS = {
  critical: 'Критический',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
};

const PRIORITY_ORDER = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const STATUS_LABELS = {
  to_do: 'To Do',
  in_progress: 'В работе',
  review: 'Review',
  done: 'Done',
  blocked: 'Blocked',
};

const DEFAULT_COLUMN_ORDERS = [1, 2, 3, 4, 5];
const DEFAULT_COLUMN_NAMES = {
  1: 'To Do',
  2: 'In Progress',
  3: 'Review',
  4: 'Done',
  5: 'Blocked',
};

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function normalizeTask(raw) {
  return {
    id: raw.id,
    title: raw.title || '',
    description: raw.description || '',
    assigneeId: raw.assignee,
    assigneeName: raw.assignee_display || raw.assignee_username || 'Без исполнителя',
    reporterName: raw.reporter_username || '',
    dueDate: raw.due_date || null,
    priority: raw.priority || 'medium',
    priorityLabel: raw.priority_label || PRIORITY_LABELS[raw.priority] || raw.priority,
    status: raw.status || 'to_do',
    isOverdue: Boolean(raw.is_overdue),
    columnId: raw.column,
    columnOrder: Number(raw.column_order || 0),
    columnName: raw.column_name || '',
    boardId: raw.board,
    boardName: raw.board_name || '',
    boardColumns: Array.isArray(raw.board_columns) ? raw.board_columns : [],
    subtasksTotal: Number(raw.subtasks_total || 0),
    subtasksCompleted: Number(raw.subtasks_completed || 0),
    canCompleteParent: Boolean(raw.can_complete_parent),
    updatedAt: raw.updated_at,
  };
}

function normalizeSubtask(raw) {
  return {
    id: raw.id,
    title: raw.title || '',
    isCompleted: Boolean(raw.is_completed),
    createdBy: raw.created_by_username || '',
  };
}

function normalizeProject(raw) {
  return {
    id: raw.id,
    name: raw.name || '',
    description: raw.description || '',
    taskCount: Number(raw.task_count || 0),
    members: Array.isArray(raw.members) ? raw.members : [],
  };
}

function normalizeReport(raw) {
  return {
    id: raw.id,
    username: raw.user_full_name || raw.username || '-',
    started: raw.started_tasks || '',
    taken: raw.taken_tasks || '',
    completed: raw.completed_tasks || '',
    blockers: raw.blockers || '',
  };
}

export default function Tasks() {
  const { user } = useAuth();
  const role = user?.role;
  const isManager = ['department_head', 'admin', 'superadmin', 'projectmanager'].includes(role);
  const canSwitchTaskSections = !['employee', 'intern'].includes(role);
  const canSeeOwnTasksSection = canSwitchTaskSections && role !== 'superadmin';
  const canSeeTeamTasksSection = canSwitchTaskSections;
  const canSubmitDaily = ['employee', 'projectmanager'].includes(role);
  const canViewDaily = ['department_head', 'admin', 'superadmin', 'projectmanager'].includes(role);

  const [taskSection, setTaskSection] = useState(role === 'superadmin' ? 'team' : 'my');
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [projects, setProjects] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [reports, setReports] = useState([]);
  const [assigneeOptions, setAssigneeOptions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [movingTaskId, setMovingTaskId] = useState(null);
  const [reportDate, setReportDate] = useState(todayISO());
  const [showTaskModal, setShowTaskModal] = useState(false);
  const [showProjectModal, setShowProjectModal] = useState(false);
  const [activeTask, setActiveTask] = useState(null);
  const [taskComments, setTaskComments] = useState([]);
  const [taskHistory, setTaskHistory] = useState([]);
  const [taskSubtasks, setTaskSubtasks] = useState([]);
  const [commentText, setCommentText] = useState('');
  const [subtaskTitle, setSubtaskTitle] = useState('');

  const [filters, setFilters] = useState({
    assigneeId: '',
    priority: '',
    status: '',
    overdueOnly: false,
  });

  const [taskForm, setTaskForm] = useState({
    board_id: '',
    title: '',
    description: '',
    priority: 'medium',
    due_date: '',
    assignee_id: '',
  });

  const [projectForm, setProjectForm] = useState({
    name: '',
    description: '',
    member_ids: [],
  });

  const [dailyForm, setDailyForm] = useState({
    started_tasks: '',
    taken_tasks: '',
    completed_tasks: '',
    blockers: '',
  });

  const loadProjects = async () => {
    if (!isManager) {
      setProjects([]);
      return;
    }
    try {
      const response = await tasksAPI.projects();
      const rows = Array.isArray(response.data) ? response.data : [];
      setProjects(rows.map(normalizeProject));
    } catch {
      setProjects([]);
    }
  };

  const loadAssignees = async () => {
    try {
      const response = await tasksAPI.assignees();
      const list = Array.isArray(response.data) ? response.data : [];
      setAssigneeOptions(
        list.map((item) => ({
          id: item.id,
          name: item.full_name || item.username || `ID ${item.id}`,
        }))
      );
    } catch {
      setAssigneeOptions([]);
    }
  };

  const loadTasksAndReports = async () => {
    setLoading(true);
    setError('');
    try {
      const taskRequest = selectedProjectId
        ? tasksAPI.projectTasks(selectedProjectId)
        : taskSection === 'team'
          ? tasksAPI.team()
          : tasksAPI.my();

      const [taskRes, reportsRes] = await Promise.all([
        taskRequest.catch(() => ({ data: [] })),
        canViewDaily && taskSection === 'team'
          ? tasksAPI.dailyReports({ date: reportDate }).catch(() => ({ data: [] }))
          : Promise.resolve({ data: [] }),
      ]);

      const taskRows = Array.isArray(taskRes.data) ? taskRes.data : [];
      setTasks(taskRows.map(normalizeTask));

      const reportRows = Array.isArray(reportsRes.data) ? reportsRes.data : [];
      setReports(reportRows.map(normalizeReport));
    } catch {
      setError('Не удалось загрузить задачи.');
    } finally {
      setLoading(false);
    }
  };

  const loadTaskMeta = async (taskId) => {
    try {
      const [commentsRes, historyRes, subtasksRes] = await Promise.all([
        tasksAPI.comments(taskId).catch(() => ({ data: [] })),
        tasksAPI.history(taskId).catch(() => ({ data: [] })),
        tasksAPI.subtasks(taskId).catch(() => ({ data: [] })),
      ]);
      setTaskComments(Array.isArray(commentsRes.data) ? commentsRes.data : []);
      setTaskHistory(Array.isArray(historyRes.data) ? historyRes.data : []);
      setTaskSubtasks(Array.isArray(subtasksRes.data) ? subtasksRes.data.map(normalizeSubtask) : []);
    } catch {
      setTaskComments([]);
      setTaskHistory([]);
      setTaskSubtasks([]);
    }
  };

  useEffect(() => {
    loadProjects();
    loadAssignees();
  }, []);

  useEffect(() => {
    loadTasksAndReports();
  }, [taskSection, selectedProjectId, reportDate]);

  useEffect(() => {
    if (!activeTask) return;
    const current = tasks.find((item) => item.id === activeTask.id);
    if (current) setActiveTask(current);
  }, [tasks, activeTask]);

  useEffect(() => {
    if (!activeTask) return;
    loadTaskMeta(activeTask.id);
  }, [activeTask?.id]);

  useEffect(() => {
    if (!showTaskModal) return;
    setTaskForm((prev) => ({
      ...prev,
      board_id: prev.board_id || selectedProjectId || '',
      assignee_id: prev.assignee_id || user?.id || '',
    }));
  }, [showTaskModal, selectedProjectId, user?.id]);

  const filteredTasks = useMemo(() => {
    return tasks.filter((task) => {
      if (filters.assigneeId && String(task.assigneeId || '') !== String(filters.assigneeId)) return false;
      if (filters.priority && task.priority !== filters.priority) return false;
      if (filters.status && task.status !== filters.status) return false;
      if (filters.overdueOnly && !task.isOverdue) return false;
      return true;
    });
  }, [tasks, filters]);

  const columns = useMemo(() => {
    const map = new Map();
    DEFAULT_COLUMN_ORDERS.forEach((order) => {
      map.set(order, { order, id: null, name: DEFAULT_COLUMN_NAMES[order], items: [] });
    });

    filteredTasks.forEach((task) => {
      const order = task.columnOrder || 1;
      if (!map.has(order)) {
        map.set(order, {
          order,
          id: task.columnId,
          name: task.columnName || `Колонка ${order}`,
          items: [],
        });
      }
      const column = map.get(order);
      if (!column.id) column.id = task.columnId;
      if (task.columnName) column.name = task.columnName;
      column.items.push(task);
    });

    return Array.from(map.values())
      .sort((a, b) => a.order - b.order)
      .map((column) => ({
        ...column,
        items: [...column.items].sort((a, b) => {
          const priorityDiff = (PRIORITY_ORDER[a.priority] ?? 99) - (PRIORITY_ORDER[b.priority] ?? 99);
          if (priorityDiff !== 0) return priorityDiff;
          return new Date(b.updatedAt || 0) - new Date(a.updatedAt || 0);
        }),
      }));
  }, [filteredTasks]);

  const resetTaskForm = () => {
    setTaskForm({
      board_id: selectedProjectId || '',
      title: '',
      description: '',
      priority: 'medium',
      due_date: '',
      assignee_id: user?.id || '',
    });
  };

  const createTask = async () => {
    const title = taskForm.title.trim();
    if (!title) {
      setError('Название задачи не может быть пустым.');
      return;
    }

    try {
      await tasksAPI.create({
        board_id: taskForm.board_id || null,
        title,
        description: taskForm.description.trim(),
        assignee_id: taskForm.assignee_id ? Number(taskForm.assignee_id) : null,
        due_date: taskForm.due_date || null,
        priority: taskForm.priority,
      });
      setShowTaskModal(false);
      resetTaskForm();
      await loadTasksAndReports();
      await loadProjects();
    } catch (e) {
      setError(e.response?.data?.detail || 'Не удалось создать задачу.');
    }
  };

  const createProject = async () => {
    const name = projectForm.name.trim();
    if (!name) {
      setError('Название проекта обязательно.');
      return;
    }

    try {
      await tasksAPI.createProject({
        name,
        description: projectForm.description.trim(),
        member_ids: projectForm.member_ids.map(Number),
      });
      setShowProjectModal(false);
      setProjectForm({ name: '', description: '', member_ids: [] });
      await loadProjects();
    } catch (e) {
      setError(e.response?.data?.detail || 'Не удалось создать проект.');
    }
  };

  const moveTask = async (task, targetOrder) => {
    if (!task || targetOrder === task.columnOrder) return;
    const target = task.boardColumns.find((item) => Number(item.order) === Number(targetOrder));
    if (!target?.id) return;
    try {
      setMovingTaskId(task.id);
      await tasksAPI.move(task.id, target.id);
      await loadTasksAndReports();
    } catch (e) {
      setError(e.response?.data?.detail || 'Не удалось изменить статус задачи.');
    } finally {
      setMovingTaskId(null);
    }
  };

  const submitDailyReport = async () => {
    if (!canSubmitDaily) return;
    try {
      await tasksAPI.submitDailyReport({
        report_date: reportDate,
        ...dailyForm,
      });
      setDailyForm({ started_tasks: '', taken_tasks: '', completed_tasks: '', blockers: '' });
      await loadTasksAndReports();
    } catch {
      setError('Не удалось отправить ежедневный отчет.');
    }
  };

  const submitComment = async () => {
    if (!activeTask || !commentText.trim()) return;
    try {
      await tasksAPI.addComment(activeTask.id, { text: commentText.trim() });
      setCommentText('');
      await loadTaskMeta(activeTask.id);
    } catch {
      setError('Не удалось добавить комментарий.');
    }
  };

  const submitSubtask = async () => {
    if (!activeTask || !subtaskTitle.trim()) return;
    try {
      await tasksAPI.addSubtask(activeTask.id, { title: subtaskTitle.trim() });
      setSubtaskTitle('');
      await Promise.all([loadTaskMeta(activeTask.id), loadTasksAndReports()]);
    } catch {
      setError('Не удалось добавить подзадачу.');
    }
  };

  const toggleSubtask = async (subtask) => {
    if (!activeTask) return;
    try {
      await tasksAPI.updateSubtask(activeTask.id, subtask.id, { is_completed: !subtask.isCompleted });
      await Promise.all([loadTaskMeta(activeTask.id), loadTasksAndReports()]);
    } catch {
      setError('Не удалось обновить подзадачу.');
    }
  };

  const deleteSubtask = async (subtaskId) => {
    if (!activeTask) return;
    try {
      await tasksAPI.deleteSubtask(activeTask.id, subtaskId);
      await Promise.all([loadTaskMeta(activeTask.id), loadTasksAndReports()]);
    } catch {
      setError('Не удалось удалить подзадачу.');
    }
  };

  return (
    <MainLayout title="Задачи">
      <div className="page-header">
        <div>
          <div className="page-title">Task Management</div>
          <div className="page-subtitle">Проекты, канбан, фильтры, дедлайны и история изменений</div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {isManager && (
            <button className="btn btn-secondary" onClick={() => setShowProjectModal(true)}>
              <Plus size={15} /> Новый проект
            </button>
          )}
          <button className="btn btn-primary" onClick={() => setShowTaskModal(true)}>
            <Plus size={15} /> Новая задача
          </button>
        </div>
      </div>

      {canSwitchTaskSections && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
          {canSeeOwnTasksSection && (
            <button
              type="button"
              className={`btn btn-sm ${taskSection === 'my' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setTaskSection('my')}
            >
              Мои задачи
            </button>
          )}
          {canSeeTeamTasksSection && (
            <button
              type="button"
              className={`btn btn-sm ${taskSection === 'team' ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setTaskSection('team')}
            >
              Задачи команды
            </button>
          )}
        </div>
      )}

      {error ? (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="card-body" style={{ color: 'var(--danger)' }}>{error}</div>
        </div>
      ) : null}

      {isManager && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-body" style={{ display: 'grid', gap: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div>
                <div style={{ fontWeight: 700 }}>Проекты</div>
                <div style={{ fontSize: 12, color: 'var(--gray-500)' }}>
                  Тимлид создаёт проект и добавляет в него участников команды
                </div>
              </div>
              <select
                className="form-select"
                style={{ minWidth: 260 }}
                value={selectedProjectId}
                onChange={(e) => setSelectedProjectId(e.target.value)}
              >
                <option value="">Все задачи без фильтра проекта</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.name} ({project.taskCount})
                  </option>
                ))}
              </select>
            </div>

            {projects.length > 0 && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
                {projects.map((project) => (
                  <button
                    key={project.id}
                    type="button"
                    className="card"
                    style={{
                      textAlign: 'left',
                      border: String(selectedProjectId) === String(project.id) ? '1px solid var(--primary)' : '1px solid var(--border)',
                    }}
                    onClick={() => setSelectedProjectId(String(project.id) === String(selectedProjectId) ? '' : String(project.id))}
                  >
                    <div className="card-body">
                      <div style={{ fontWeight: 700, marginBottom: 4 }}>{project.name}</div>
                      <div style={{ fontSize: 12, color: 'var(--gray-500)', marginBottom: 8 }}>
                        {project.description || 'Без описания'}
                      </div>
                      <div style={{ fontSize: 12 }}>Участники: {project.members.map((member) => member.full_name || member.username).join(', ') || '—'}</div>
                      <div style={{ fontSize: 12, marginTop: 4 }}>Задачи: {project.taskCount}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-body">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 12 }}>
            <div className="form-group">
              <label className="form-label">Исполнитель</label>
              <select
                className="form-select"
                value={filters.assigneeId}
                onChange={(e) => setFilters((prev) => ({ ...prev, assigneeId: e.target.value }))}
              >
                <option value="">Все</option>
                {assigneeOptions.map((option) => (
                  <option key={option.id} value={option.id}>{option.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Приоритет</label>
              <select
                className="form-select"
                value={filters.priority}
                onChange={(e) => setFilters((prev) => ({ ...prev, priority: e.target.value }))}
              >
                <option value="">Все</option>
                <option value="critical">Критический</option>
                <option value="high">Высокий</option>
                <option value="medium">Средний</option>
                <option value="low">Низкий</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Статус</label>
              <select
                className="form-select"
                value={filters.status}
                onChange={(e) => setFilters((prev) => ({ ...prev, status: e.target.value }))}
              >
                <option value="">Все</option>
                <option value="to_do">To Do</option>
                <option value="in_progress">In Progress</option>
                <option value="review">Review</option>
                <option value="done">Done</option>
                <option value="blocked">Blocked</option>
              </select>
            </div>
            <label className="form-group" style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 28 }}>
              <input
                type="checkbox"
                checked={filters.overdueOnly}
                onChange={(e) => setFilters((prev) => ({ ...prev, overdueOnly: e.target.checked }))}
              />
              Только просроченные
            </label>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="card"><div className="card-body">Загрузка...</div></div>
      ) : (
        <>
          <div className="kanban-board">
            {columns.map((column) => (
              <div key={column.order} className="kanban-col">
                <div className="kanban-col-header">
                  <span className="kanban-col-title">{column.name}</span>
                  <span className="badge badge-blue">{column.items.length}</span>
                </div>
                {column.items.map((task) => (
                  <button
                    key={task.id}
                    type="button"
                    className="kanban-card"
                    style={{ textAlign: 'left', border: activeTask?.id === task.id ? '1px solid var(--primary)' : '1px solid transparent' }}
                    onClick={() => setActiveTask(task)}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                      <div className="kanban-card-title">{task.title}</div>
                      {task.isOverdue ? <span className="badge badge-red">Overdue</span> : null}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--gray-500)', marginBottom: 8 }}>{task.description || 'Без описания'}</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                      <span className="badge badge-blue">{task.priorityLabel}</span>
                      <span className="badge badge-gray">{STATUS_LABELS[task.status] || task.status}</span>
                      {task.boardName ? <span className="badge badge-gray">{task.boardName}</span> : null}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--gray-600)' }}>Исполнитель: {task.assigneeName}</div>
                    <div style={{ fontSize: 12, color: 'var(--gray-600)' }}>Постановщик: {task.reporterName || '-'}</div>
                    <div style={{ fontSize: 12, color: 'var(--gray-600)' }}>Срок: {task.dueDate || '—'}</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                      {DEFAULT_COLUMN_ORDERS.map((order) => (
                        <span key={`${task.id}-${order}`}>
                          <button
                            className="btn btn-secondary btn-sm"
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              moveTask(task, order);
                            }}
                            disabled={movingTaskId === task.id || order === task.columnOrder || task.status === 'done'}
                          >
                            {DEFAULT_COLUMN_NAMES[order]}
                          </button>
                        </span>
                      ))}
                    </div>
                  </button>
                ))}
              </div>
            ))}
          </div>

          {activeTask ? (
            <div className="grid-2" style={{ marginTop: 16, gap: 16 }}>
              <div className="card">
                <div className="card-body" style={{ display: 'grid', gap: 10 }}>
                  <div style={{ fontWeight: 700 }}>Карточка задачи</div>
                  <div><strong>{activeTask.title}</strong></div>
                  <div style={{ color: 'var(--gray-600)' }}>{activeTask.description || 'Без описания'}</div>
                  <div>Проект: {activeTask.boardName || 'Личная доска'}</div>
                  <div>Исполнитель: {activeTask.assigneeName}</div>
                  <div>Приоритет: {activeTask.priorityLabel}</div>
                  <div>Статус: {STATUS_LABELS[activeTask.status] || activeTask.status}</div>
                  <div>Срок: {activeTask.dueDate || '—'}</div>
                  <div>Подзадачи: {activeTask.subtasksCompleted}/{activeTask.subtasksTotal}</div>
                  {activeTask.canCompleteParent ? (
                    <div style={{ color: 'var(--success)', fontWeight: 700 }}>
                      Все подзадачи выполнены. Можно завершить основную задачу.
                    </div>
                  ) : null}
                  {activeTask.isOverdue ? <div style={{ color: '#b91c1c', fontWeight: 700 }}>Задача просрочена</div> : null}
                </div>
              </div>

              <div className="card">
                <div className="card-body" style={{ display: 'grid', gap: 12 }}>
                  <div style={{ fontWeight: 700 }}>Подзадачи</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {taskSubtasks.map((subtask) => (
                      <div key={subtask.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 10, borderRadius: 10, background: 'var(--gray-50)' }}>
                        <input
                          type="checkbox"
                          checked={subtask.isCompleted}
                          onChange={() => toggleSubtask(subtask)}
                        />
                        <div style={{ flex: 1, textDecoration: subtask.isCompleted ? 'line-through' : 'none' }}>
                          {subtask.title}
                        </div>
                        <button className="btn btn-secondary btn-sm" onClick={() => deleteSubtask(subtask.id)}>Удалить</button>
                      </div>
                    ))}
                    {taskSubtasks.length === 0 ? <div style={{ color: 'var(--gray-500)' }}>Подзадач пока нет.</div> : null}
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input
                      className="form-input"
                      placeholder="Новая подзадача"
                      value={subtaskTitle}
                      onChange={(e) => setSubtaskTitle(e.target.value)}
                    />
                    <button className="btn btn-primary btn-sm" onClick={submitSubtask}>Добавить</button>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-body" style={{ display: 'grid', gap: 12 }}>
                  <div style={{ fontWeight: 700 }}>Комментарии</div>
                  <div style={{ display: 'grid', gap: 8 }}>
                    {taskComments.map((comment) => (
                      <div key={comment.id} style={{ padding: 10, borderRadius: 10, background: 'var(--gray-50)' }}>
                        <div style={{ fontSize: 12, color: 'var(--gray-500)', marginBottom: 4 }}>
                          {comment.author_username} • {String(comment.created_at || '').slice(0, 16).replace('T', ' ')}
                        </div>
                        <div>{comment.text}</div>
                      </div>
                    ))}
                    {taskComments.length === 0 ? <div style={{ color: 'var(--gray-500)' }}>Комментариев пока нет.</div> : null}
                  </div>
                  <textarea
                    className="form-textarea"
                    placeholder="Оставить комментарий"
                    value={commentText}
                    onChange={(e) => setCommentText(e.target.value)}
                  />
                  <div>
                    <button className="btn btn-primary btn-sm" onClick={submitComment}>Добавить комментарий</button>
                  </div>
                </div>
              </div>

              <div className="card" style={{ gridColumn: '1 / -1' }}>
                <div className="card-body">
                  <div style={{ fontWeight: 700, marginBottom: 10 }}>История изменений</div>
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th>Время</th>
                          <th>Пользователь</th>
                          <th>Действие</th>
                          <th>Уровень</th>
                        </tr>
                      </thead>
                      <tbody>
                        {taskHistory.map((row) => (
                          <tr key={row.id}>
                            <td>{String(row.created_at || '').slice(0, 16).replace('T', ' ')}</td>
                            <td>{row.actor_username || '-'}</td>
                            <td>{row.action}</td>
                            <td>{row.level}</td>
                          </tr>
                        ))}
                        {taskHistory.length === 0 ? (
                          <tr><td colSpan={4}>История пока пустая.</td></tr>
                        ) : null}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          ) : null}

          {canSubmitDaily && (!canSwitchTaskSections || taskSection === 'my') && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-body" style={{ display: 'grid', gap: 10 }}>
                <div style={{ fontWeight: 700 }}>Ежедневный отчет</div>
                <div className="grid-2">
                  <div className="form-group">
                    <label className="form-label">Дата</label>
                    <input className="form-input" type="date" value={reportDate} onChange={(e) => setReportDate(e.target.value)} />
                  </div>
                </div>
                <textarea className="form-textarea" placeholder="Что начал" value={dailyForm.started_tasks} onChange={(e) => setDailyForm((p) => ({ ...p, started_tasks: e.target.value }))} />
                <textarea className="form-textarea" placeholder="Что взял в работу" value={dailyForm.taken_tasks} onChange={(e) => setDailyForm((p) => ({ ...p, taken_tasks: e.target.value }))} />
                <textarea className="form-textarea" placeholder="Что завершил" value={dailyForm.completed_tasks} onChange={(e) => setDailyForm((p) => ({ ...p, completed_tasks: e.target.value }))} />
                <textarea className="form-textarea" placeholder="Проблемы / блокеры" value={dailyForm.blockers} onChange={(e) => setDailyForm((p) => ({ ...p, blockers: e.target.value }))} />
                <div>
                  <button className="btn btn-primary" onClick={submitDailyReport}>Отправить отчет</button>
                </div>
              </div>
            </div>
          )}

          {canViewDaily && canSwitchTaskSections && taskSection === 'team' && (
            <div className="card" style={{ marginTop: 16 }}>
              <div className="card-body">
                <div style={{ fontWeight: 700, marginBottom: 10 }}>Отчеты сотрудников за {reportDate}</div>
                <div className="table-wrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Сотрудник</th>
                        <th>Начал</th>
                        <th>Взял в работу</th>
                        <th>Завершил</th>
                        <th>Блокеры</th>
                      </tr>
                    </thead>
                    <tbody>
                      {reports.map((report) => (
                        <tr key={report.id}>
                          <td>{report.username}</td>
                          <td>{report.started || '-'}</td>
                          <td>{report.taken || '-'}</td>
                          <td>{report.completed || '-'}</td>
                          <td>{report.blockers || '-'}</td>
                        </tr>
                      ))}
                      {reports.length === 0 ? (
                        <tr><td colSpan={5}>Отчетов за этот день пока нет.</td></tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {showTaskModal ? (
        <div className="modal-overlay" onClick={() => setShowTaskModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div className="modal-header">
              <div className="modal-title">Новая задача</div>
              <button className="btn-icon" onClick={() => setShowTaskModal(false)}><X size={18} /></button>
            </div>
            <div className="modal-body">
              {isManager ? (
                <div className="form-group" style={{ marginBottom: 12 }}>
                  <label className="form-label">Проект</label>
                  <select
                    className="form-select"
                    value={taskForm.board_id}
                    onChange={(e) => setTaskForm((prev) => ({ ...prev, board_id: e.target.value }))}
                  >
                    <option value="">Личная доска / без проекта</option>
                    {projects.map((project) => (
                      <option key={project.id} value={project.id}>{project.name}</option>
                    ))}
                  </select>
                </div>
              ) : null}
              <div className="form-group" style={{ marginBottom: 12 }}>
                <label className="form-label">Название</label>
                <input className="form-input" value={taskForm.title} onChange={(e) => setTaskForm((prev) => ({ ...prev, title: e.target.value }))} />
              </div>
              <div className="form-group" style={{ marginBottom: 12 }}>
                <label className="form-label">Описание</label>
                <textarea className="form-textarea" style={{ minHeight: 80 }} value={taskForm.description} onChange={(e) => setTaskForm((prev) => ({ ...prev, description: e.target.value }))} />
              </div>
              <div className="grid-2" style={{ marginBottom: 12 }}>
                <div className="form-group">
                  <label className="form-label">Исполнитель</label>
                  <select className="form-select" value={taskForm.assignee_id} onChange={(e) => setTaskForm((prev) => ({ ...prev, assignee_id: e.target.value }))}>
                    {assigneeOptions.map((option) => (
                      <option key={option.id} value={option.id}>{option.name}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Приоритет</label>
                  <select className="form-select" value={taskForm.priority} onChange={(e) => setTaskForm((prev) => ({ ...prev, priority: e.target.value }))}>
                    <option value="critical">Критический</option>
                    <option value="high">Высокий</option>
                    <option value="medium">Средний</option>
                    <option value="low">Низкий</option>
                  </select>
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Срок</label>
                <input className="form-input" type="date" value={taskForm.due_date} onChange={(e) => setTaskForm((prev) => ({ ...prev, due_date: e.target.value }))} />
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowTaskModal(false)}>Отмена</button>
              <button className="btn btn-primary" onClick={createTask}>Создать</button>
            </div>
          </div>
        </div>
      ) : null}

      {showProjectModal ? (
        <div className="modal-overlay" onClick={() => setShowProjectModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 560 }}>
            <div className="modal-header">
              <div className="modal-title">Новый проект</div>
              <button className="btn-icon" onClick={() => setShowProjectModal(false)}><X size={18} /></button>
            </div>
            <div className="modal-body">
              <div className="form-group" style={{ marginBottom: 12 }}>
                <label className="form-label">Название проекта</label>
                <input className="form-input" value={projectForm.name} onChange={(e) => setProjectForm((prev) => ({ ...prev, name: e.target.value }))} />
              </div>
              <div className="form-group" style={{ marginBottom: 12 }}>
                <label className="form-label">Описание</label>
                <textarea className="form-textarea" value={projectForm.description} onChange={(e) => setProjectForm((prev) => ({ ...prev, description: e.target.value }))} />
              </div>
              <div className="form-group">
                <label className="form-label">Участники проекта</label>
                <select
                  multiple
                  className="form-select"
                  style={{ minHeight: 180 }}
                  value={projectForm.member_ids.map(String)}
                  onChange={(e) => {
                    const values = Array.from(e.target.selectedOptions).map((option) => Number(option.value));
                    setProjectForm((prev) => ({ ...prev, member_ids: values }));
                  }}
                >
                  {assigneeOptions.map((option) => (
                    <option key={option.id} value={option.id}>{option.name}</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="modal-footer">
              <button className="btn btn-secondary" onClick={() => setShowProjectModal(false)}>Отмена</button>
              <button className="btn btn-primary" onClick={createProject}>Создать проект</button>
            </div>
          </div>
        </div>
      ) : null}
    </MainLayout>
  );
}
