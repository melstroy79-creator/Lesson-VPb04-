"""Система управления задачами (Todo) — финальная реализация архитектуры.

Слои:
    1. Данные   — Task, Project, User
    2. Сценарии — TaskManager
    3. Хранение — TaskRepository (интерфейс) + JsonTaskRepository / InMemoryRepository
    4. Выборка  — TaskFilter: ByStatus, ByTag, ByDueDate

Запуск демонстрации: python main.py
Требования: Python 3.10+, только стандартная библиотека.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


# ---------------------------------------------------------------------------
# Перечисления и исключения
# ---------------------------------------------------------------------------


class Priority(str, Enum):
    """Важность задачи."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"

    @classmethod
    def coerce(cls, value: Priority | str) -> Priority:
        """Приводит строку или готовое значение к Priority."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as error:
            raise ValueError(f"Недопустимый приоритет: {value!r}") from error


class TaskStatus(str, Enum):
    """Режим выборки задач в Project.list_tasks()."""

    ALL = "all"
    ACTIVE = "active"
    DONE = "done"

    @classmethod
    def coerce(cls, value: TaskStatus | str | None) -> TaskStatus:
        """Приводит строку, None или значение перечисления к TaskStatus."""
        if value is None:
            return cls.ALL
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as error:
            raise ValueError(f"Недопустимый статус выборки: {value!r}") from error


class NotFoundError(Exception):
    """Сущность с указанным идентификатором не найдена."""


# ---------------------------------------------------------------------------
# 1. Данные: Task
# ---------------------------------------------------------------------------


class Task:
    """Задача — атомарная единица работы. О проекте ничего не знает."""

    def __init__(
        self,
        title: str,
        description: str = "",
        *,
        priority: Priority | str = Priority.NORMAL,
        due_date: datetime | None = None,
        tags: Iterable[str] | None = None,
    ) -> None:
        self._id = uuid4().hex
        self._title = self._validate_title(title)
        self._description = description
        self._is_done = False
        self._priority = Priority.coerce(priority)
        self._due_date = due_date
        self._created_at = datetime.now()
        self._completed_at: datetime | None = None
        self._tags: list[str] = []
        for tag in tags or ():
            self.add_tag(tag)

    # --- чтение состояния ------------------------------------------------

    @property
    def id(self) -> str:
        """Уникальный идентификатор (не меняется никогда)."""
        return self._id

    @property
    def title(self) -> str:
        """Название задачи."""
        return self._title

    @property
    def description(self) -> str:
        """Описание задачи."""
        return self._description

    @property
    def is_done(self) -> bool:
        """Выполнена ли задача."""
        return self._is_done

    @property
    def priority(self) -> Priority:
        """Приоритет задачи."""
        return self._priority

    @property
    def due_date(self) -> datetime | None:
        """Срок выполнения или None."""
        return self._due_date

    @property
    def created_at(self) -> datetime:
        """Момент создания."""
        return self._created_at

    @property
    def completed_at(self) -> datetime | None:
        """Момент выполнения или None."""
        return self._completed_at

    @property
    def tags(self) -> list[str]:
        """Копия меток: менять можно только через add_tag()/remove_tag()."""
        return list(self._tags)

    # --- изменение состояния --------------------------------------------

    def mark_as_done(self) -> None:
        """Выполняет задачу и фиксирует время завершения (идемпотентно)."""
        if self._is_done:
            return
        self._is_done = True
        self._completed_at = datetime.now()

    def reopen(self) -> None:
        """Возвращает задачу в работу и снимает время завершения."""
        self._is_done = False
        self._completed_at = None

    def rename(self, new_title: str) -> None:
        """Меняет название с проверкой на непустоту."""
        self._title = self._validate_title(new_title)

    def change_description(self, new_text: str) -> None:
        """Заменяет описание."""
        self._description = new_text

    def change_due_date(self, date_or_none: datetime | None) -> None:
        """Ставит новый срок или снимает его."""
        self._due_date = date_or_none

    def change_priority(self, level: Priority | str) -> None:
        """Меняет важность задачи."""
        self._priority = Priority.coerce(level)

    def add_tag(self, tag: str) -> None:
        """Добавляет метку, если её ещё нет."""
        normalized = tag.strip()
        if not normalized:
            raise ValueError("Метка не может быть пустой")
        if normalized not in self._tags:
            self._tags.append(normalized)

    def remove_tag(self, tag: str) -> None:
        """Удаляет метку; отсутствующая метка ошибкой не считается."""
        normalized = tag.strip()
        if normalized in self._tags:
            self._tags.remove(normalized)

    # --- вычисляемые признаки -------------------------------------------

    def is_overdue(self, now: datetime | None = None) -> bool:
        """Просрочена ли: не выполнена и срок раньше now."""
        if self._is_done or self._due_date is None:
            return False
        current = now if now is not None else datetime.now()
        if self._due_date.tzinfo is not None and current.tzinfo is None:
            current = current.astimezone(self._due_date.tzinfo)
        elif self._due_date.tzinfo is None and current.tzinfo is not None:
            current = current.replace(tzinfo=None)
        return self._due_date < current

    # --- сериализация ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Отдаёт состояние словарём (даты — ISO 8601)."""
        return {
            "id": self._id,
            "title": self._title,
            "description": self._description,
            "is_done": self._is_done,
            "priority": self._priority.value,
            "due_date": self._due_date.isoformat() if self._due_date else None,
            "created_at": self._created_at.isoformat(),
            "completed_at": self._completed_at.isoformat() if self._completed_at else None,
            "tags": list(self._tags),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Task:
        """Восстанавливает задачу из словаря, сохраняя id и даты."""
        task = cls(
            title=data["title"],
            description=data.get("description", ""),
            priority=data.get("priority", Priority.NORMAL.value),
            due_date=cls._parse_datetime(data.get("due_date")),
            tags=data.get("tags") or (),
        )
        task._id = str(data["id"])
        task._created_at = cls._parse_datetime(data.get("created_at")) or datetime.now()
        task._is_done = bool(data.get("is_done", False))
        completed_at = cls._parse_datetime(data.get("completed_at"))
        task._completed_at = (
            completed_at or task._created_at if task._is_done else None
        )
        return task

    # --- внутреннее --------------------------------------------------------

    @staticmethod
    def _validate_title(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Название задачи не может быть пустым")
        return value.strip()

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    def __repr__(self) -> str:
        state = "done" if self._is_done else "active"
        return f"<Task {self._id[:8]} {self._title!r} {state}>"


# ---------------------------------------------------------------------------
# 2. Данные: Project
# ---------------------------------------------------------------------------


class Project:
    """Проект — контейнер задач, принадлежащий пользователю."""

    def __init__(
        self,
        name: str,
        description: str = "",
        owner: User | None = None,
    ) -> None:
        self._id = uuid4().hex
        self._name = self._validate_name(name)
        self._description = description
        self._owner = owner
        self._tasks: list[Task] = []
        self._created_at = datetime.now()

    @property
    def id(self) -> str:
        """Уникальный идентификатор проекта."""
        return self._id

    @property
    def name(self) -> str:
        """Название проекта."""
        return self._name

    @property
    def description(self) -> str:
        """Описание проекта."""
        return self._description

    @property
    def owner(self) -> User | None:
        """Владелец проекта или None."""
        return self._owner

    @property
    def tasks(self) -> tuple[Task, ...]:
        """Снимок задач: обойти add_task() снаружи нельзя."""
        return tuple(self._tasks)

    @property
    def created_at(self) -> datetime:
        """Момент создания проекта."""
        return self._created_at

    def add_task(self, task: Task) -> Task:
        """Добавляет задачу и возвращает её."""
        if self.get_task(task.id) is not None:
            raise ValueError(f"Задача с id={task.id} уже есть в проекте {self._name!r}")
        self._tasks.append(task)
        return task

    def remove_task(self, task_id: str) -> Task | None:
        """Удаляет задачу по id и возвращает её (нужно для переноса)."""
        for index, task in enumerate(self._tasks):
            if task.id == task_id:
                return self._tasks.pop(index)
        return None

    def get_task(self, task_id: str) -> Task | None:
        """Находит задачу по id или возвращает None."""
        return next((task for task in self._tasks if task.id == task_id), None)

    def list_tasks(self, status: TaskStatus | str | None = None) -> list[Task]:
        """Возвращает задачи: все, только active или только done."""
        mode = TaskStatus.coerce(status)
        if mode is TaskStatus.ALL:
            return list(self._tasks)
        expected_done = mode is TaskStatus.DONE
        return [task for task in self._tasks if task.is_done is expected_done]

    def rename(self, new_name: str) -> None:
        """Меняет название проекта."""
        self._name = self._validate_name(new_name)

    def change_description(self, text: str) -> None:
        """Заменяет описание проекта."""
        self._description = text

    def is_empty(self) -> bool:
        """Есть ли в проекте задачи."""
        return not self._tasks

    def progress(self) -> float:
        """Доля выполненных задач от 0.0 до 1.0; на пустом проекте — 0.0."""
        if not self._tasks:
            return 0.0
        return sum(1 for task in self._tasks if task.is_done) / len(self._tasks)

    def _to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "name": self._name,
            "description": self._description,
            "owner_id": self._owner.id if self._owner else None,
            "created_at": self._created_at.isoformat(),
            "tasks": [task.to_dict() for task in self._tasks],
        }

    @classmethod
    def _from_dict(cls, data: dict[str, Any], owner: User) -> Project:
        project = cls(
            name=data["name"],
            description=data.get("description", ""),
            owner=owner,
        )
        project._id = str(data["id"])
        project._created_at = (
            datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now()
        )
        project._tasks = [Task.from_dict(item) for item in data.get("tasks", [])]
        return project

    @staticmethod
    def _validate_name(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Название проекта не может быть пустым")
        return value.strip()

    def __repr__(self) -> str:
        return f"<Project {self._name!r} tasks={len(self._tasks)}>"


# ---------------------------------------------------------------------------
# 3. Данные: User
# ---------------------------------------------------------------------------


class User:
    """Пользователь — владелец проектов и точка входа в систему."""

    def __init__(self, name: str, email: str) -> None:
        self._id = uuid4().hex
        self._name = self._validate_name(name)
        self._email = self._validate_email(email)
        self._projects: list[Project] = []

    @property
    def id(self) -> str:
        """Уникальный идентификатор пользователя."""
        return self._id

    @property
    def name(self) -> str:
        """Имя пользователя."""
        return self._name

    @property
    def email(self) -> str:
        """Email (в нижнем регистре), он же логин."""
        return self._email

    @property
    def projects(self) -> tuple[Project, ...]:
        """Снимок проектов пользователя."""
        return tuple(self._projects)

    def create_project(self, name: str, description: str = "") -> Project:
        """Создаёт проект и сразу становится его владельцем."""
        project = Project(name, description, owner=self)
        self._projects.append(project)
        return project

    def remove_project(self, project_id: str) -> Project | None:
        """Удаляет проект вместе с задачами (каскад) и возвращает его."""
        for index, project in enumerate(self._projects):
            if project.id == project_id:
                return self._projects.pop(index)
        return None

    def get_project(self, project_id: str) -> Project | None:
        """Находит проект по id или возвращает None."""
        return next((p for p in self._projects if p.id == project_id), None)

    def list_projects(self) -> list[Project]:
        """Список проектов пользователя."""
        return list(self._projects)

    def all_tasks(self) -> list[Task]:
        """Плоский список задач по всем проектам."""
        return [task for project in self._projects for task in project.list_tasks()]

    def _to_dict(self) -> dict[str, Any]:
        return {
            "id": self._id,
            "name": self._name,
            "email": self._email,
            "projects": [project._to_dict() for project in self._projects],
        }

    @classmethod
    def _from_dict(cls, data: dict[str, Any]) -> User:
        user = cls(name=data["name"], email=data["email"])
        user._id = str(data["id"])
        user._projects = [
            Project._from_dict(item, user) for item in data.get("projects", [])
        ]
        return user

    @staticmethod
    def _validate_name(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Имя пользователя не может быть пустым")
        return value.strip()

    @staticmethod
    def _validate_email(value: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"Некорректный email: {value!r}")
        email = value.strip().lower()
        local, separator, domain = email.partition("@")
        if (
            not separator
            or email.count("@") != 1
            or not local
            or "." not in domain
            or domain.startswith(".")
            or domain.endswith(".")
            or " " in email
        ):
            raise ValueError(f"Некорректный email: {value!r}")
        return email

    def __repr__(self) -> str:
        return f"<User {self._email} projects={len(self._projects)}>"


# ---------------------------------------------------------------------------
# 4. Хранение
# ---------------------------------------------------------------------------


class TaskRepository(ABC):
    """Интерфейс хранилища: домен не знает, файл это или БД."""

    @abstractmethod
    def save(self, data: dict[str, Any]) -> None:
        """Сохраняет состояние целиком."""

    @abstractmethod
    def load(self) -> dict[str, Any]:
        """Загружает состояние или возвращает пустой словарь."""


class JsonTaskRepository(TaskRepository):
    """Хранилище в одном JSON-файле с атомарной записью."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """Путь к файлу состояния."""
        return self._path

    def save(self, data: dict[str, Any]) -> None:
        """Пишет во временный файл и подменяет им основной."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f".{self._path.name}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self._path)
        finally:
            temp_path.unlink(missing_ok=True)

    def load(self) -> dict[str, Any]:
        """Читает состояние; на отсутствующем или битом файле отдаёт пустое."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


class InMemoryRepository(TaskRepository):
    """Хранилище в памяти — демонстрация взаимозаменяемости интерфейса."""

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def save(self, data: dict[str, Any]) -> None:
        """Запоминает копию состояния."""
        self._state = json.loads(json.dumps(data, ensure_ascii=False))

    def load(self) -> dict[str, Any]:
        """Возвращает копию сохранённого состояния."""
        return json.loads(json.dumps(self._state, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 5. Фильтры (стратегии)
# ---------------------------------------------------------------------------


class TaskFilter(ABC):
    """Базовый фильтр; комбинируются последовательным применением."""

    @abstractmethod
    def apply(self, tasks: Iterable[Task]) -> list[Task]:
        """Оставляет задачи, подходящие под условие."""


class ByStatus(TaskFilter):
    """Фильтр по статусу."""

    def __init__(self, status: TaskStatus | str) -> None:
        self._status = TaskStatus.coerce(status)

    def apply(self, tasks: Iterable[Task]) -> list[Task]:
        """Оставляет задачи с выбранным статусом."""
        if self._status is TaskStatus.ALL:
            return list(tasks)
        expected_done = self._status is TaskStatus.DONE
        return [task for task in tasks if task.is_done is expected_done]


class ByTag(TaskFilter):
    """Фильтр по наличию метки."""

    def __init__(self, tag: str) -> None:
        normalized = tag.strip()
        if not normalized:
            raise ValueError("Метка для фильтра не может быть пустой")
        self._tag = normalized

    def apply(self, tasks: Iterable[Task]) -> list[Task]:
        """Оставляет задачи с заданной меткой."""
        return [task for task in tasks if self._tag in task.tags]


class ByDueDate(TaskFilter):
    """Фильтр по сроку в интервале [due_after, due_before], границы включительно."""

    def __init__(
        self,
        due_after: datetime | None = None,
        due_before: datetime | None = None,
    ) -> None:
        if due_after is not None and due_before is not None and due_after > due_before:
            raise ValueError("Начало интервала не может быть позже конца")
        self._due_after = due_after
        self._due_before = due_before

    def apply(self, tasks: Iterable[Task]) -> list[Task]:
        """Оставляет задачи со сроком внутри границ; задачи без срока отбрасывает."""
        result: list[Task] = []
        for task in tasks:
            if task.due_date is None:
                continue
            if self._due_after is not None and task.due_date < self._due_after:
                continue
            if self._due_before is not None and task.due_date > self._due_before:
                continue
            result.append(task)
        return result


# ---------------------------------------------------------------------------
# 6. Координация: TaskManager
# ---------------------------------------------------------------------------


class TaskManager:
    """Сценарии использования: связывает объекты и хранилище."""

    def __init__(self, repository: TaskRepository) -> None:
        self._repository = repository
        self._users: dict[str, User] = {}

    @property
    def users(self) -> tuple[User, ...]:
        """Снимок зарегистрированных пользователей."""
        return tuple(self._users.values())

    @property
    def repository(self) -> TaskRepository:
        """Хранилище, через которое сохраняется состояние."""
        return self._repository

    # --- пользователи ----------------------------------------------------

    def register_user(self, name: str, email: str) -> User:
        """Регистрирует пользователя с уникальным email."""
        user = User(name, email)
        if any(existing.email == user.email for existing in self._users.values()):
            raise ValueError(f"Пользователь с email {user.email!r} уже зарегистрирован")
        self._users[user.id] = user
        return user

    # --- задачи ------------------------------------------------------------

    def create_task(
        self,
        user_id: str,
        project_id: str,
        title: str,
        *,
        description: str = "",
        priority: Priority | str = Priority.NORMAL,
        due_date: datetime | None = None,
        tags: Iterable[str] | None = None,
    ) -> Task:
        """Создаёт задачу в проекте пользователя."""
        project = self._get_project(user_id, project_id)
        task = Task(
            title,
            description,
            priority=priority,
            due_date=due_date,
            tags=tags,
        )
        project.add_task(task)
        return task

    def complete_task(self, user_id: str, project_id: str, task_id: str) -> Task:
        """Отмечает задачу выполненной."""
        task = self._get_task(user_id, project_id, task_id)
        task.mark_as_done()
        return task

    def move_task(
        self,
        user_id: str,
        from_project_id: str,
        to_project_id: str,
        task_id: str,
    ) -> Task:
        """Переносит задачу между проектами, сохраняя id и даты."""
        source = self._get_project(user_id, from_project_id)
        target = self._get_project(user_id, to_project_id)
        task = self._get_project_task(source, task_id)
        if source.id == target.id:
            return task
        # Сначала добавляем; если целевой проект не примет задачу,
        # исходный останется нетронутым.
        target.add_task(task)
        source.remove_task(task.id)
        return task

    def delete_task(self, user_id: str, project_id: str, task_id: str) -> Task:
        """Удаляет задачу и возвращает удалённый объект."""
        project = self._get_project(user_id, project_id)
        task = self._get_project_task(project, task_id)
        project.remove_task(task.id)
        return task

    def delete_project(self, user_id: str, project_id: str) -> Project:
        """Удаляет проект вместе с задачами (каскад)."""
        user = self._get_user(user_id)
        project = user.remove_project(project_id)
        if project is None:
            raise NotFoundError(f"Проект с id={project_id!r} не найден")
        return project

    # --- отчёты ------------------------------------------------------------

    def find_overdue(self, user_id: str) -> list[Task]:
        """Просроченные задачи пользователя, отсортированные по сроку."""
        user = self._get_user(user_id)
        now = datetime.now()
        overdue = [task for task in user.all_tasks() if task.is_overdue(now)]
        return sorted(overdue, key=lambda task: task.due_date or now)

    def search(self, user_id: str, text: str) -> list[Task]:
        """Ищет по названию и описанию без учёта регистра."""
        user = self._get_user(user_id)
        needle = text.strip().lower()
        if not needle:
            return []
        return [
            task
            for task in user.all_tasks()
            if needle in task.title.lower() or needle in task.description.lower()
        ]

    # --- хранение ----------------------------------------------------------

    def save(self) -> None:
        """Сохраняет состояние через репозиторий."""
        self._repository.save(
            {
                "version": 1,
                "users": [user._to_dict() for user in self._users.values()],
            }
        )

    def load(self) -> None:
        """Заменяет текущее состояние данными из репозитория."""
        state = self._repository.load()
        if not isinstance(state, dict):
            return
        items = state.get("users", [])
        if not isinstance(items, list):
            return
        self._users = {}
        for item in items:
            user = User._from_dict(item)
            self._users[user.id] = user

    # --- поиск объектов ----------------------------------------------------

    def _get_user(self, user_id: str) -> User:
        user = self._users.get(user_id)
        if user is None:
            raise NotFoundError(f"Пользователь с id={user_id!r} не найден")
        return user

    def _get_project(self, user_id: str, project_id: str) -> Project:
        project = self._get_user(user_id).get_project(project_id)
        if project is None:
            raise NotFoundError(f"Проект с id={project_id!r} не найден")
        return project

    def _get_task(self, user_id: str, project_id: str, task_id: str) -> Task:
        project = self._get_project(user_id, project_id)
        return self._get_project_task(project, task_id)

    @staticmethod
    def _get_project_task(project: Project, task_id: str) -> Task:
        task = project.get_task(task_id)
        if task is None:
            raise NotFoundError(f"Задача с id={task_id!r} не найдена")
        return task


# ---------------------------------------------------------------------------
# Демонстрация работы системы
# ---------------------------------------------------------------------------


def main() -> None:
    """Проходит по всем сценариям архитектуры и печатает отчёт."""
    # Русский текст в перенаправленном выводе (файл, pipe) иначе уедет в cp1251/cp866.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    storage_path = Path(tempfile.gettempdir()) / "todo_demo_state.json"
    manager = TaskManager(JsonTaskRepository(storage_path))

    print("=" * 64)
    print("1. Пользователь и проекты")
    print("=" * 64)
    anna = manager.register_user("Анна Петрова", "anna@example.com")
    work = anna.create_project("Работа", "Рабочие задачи")
    study = anna.create_project("Учёба")
    print(f"Пользователь: {anna.name} <{anna.email}>")
    print(f"Проекты: {[project.name for project in anna.list_projects()]}")

    print("\n" + "=" * 64)
    print("2. Создание задач")
    print("=" * 64)
    now = datetime.now()
    report = manager.create_task(
        anna.id,
        work.id,
        "Подготовить квартальный отчёт",
        description="Свести данные по всем направлениям",
        priority=Priority.HIGH,
        due_date=now - timedelta(days=2),
        tags=["работа", "срочно"],
    )
    letters = manager.create_task(
        anna.id,
        work.id,
        "Ответить на письма",
        description="Разобрать входящую почту",
        due_date=now - timedelta(days=1),
        tags=["работа"],
    )
    meetup = manager.create_task(
        anna.id,
        work.id,
        "Спланировать встречу",
        description="Согласовать время с командой",
        due_date=now + timedelta(days=3),
        tags=["планы"],
    )
    print(f"Создано задач: {len(work.list_tasks())}")

    print("\n" + "=" * 64)
    print("3. Методы Task: выполнить, переименовать, сменить приоритет")
    print("=" * 64)
    manager.complete_task(anna.id, work.id, letters.id)
    print(f"Выполнена «{letters.title}», completed_at={letters.completed_at:%d.%m.%Y %H:%M}")
    letters.reopen()
    letters.mark_as_done()
    report.rename("Подготовить годовой отчёт")
    report.change_priority(Priority.LOW)
    meetup.change_due_date(now + timedelta(days=5))
    report.add_tag("срочно")  # дубль не появится
    report.add_tag("важное")
    print(f"«{report.title}»: приоритет={report.priority.value}, теги={report.tags}")

    print("\n" + "=" * 64)
    print("4. Задачи по проектам и прогресс")
    print("=" * 64)
    for project in anna.list_projects():
        owner = project.owner.name if project.owner else "—"
        print(f"\nПроект «{project.name}» (владелец: {owner})")
        for task in project.list_tasks():
            mark = "x" if task.is_done else " "
            due = task.due_date.strftime("%d.%m.%Y") if task.due_date else "без срока"
            print(f"  [{mark}] {task.title} | {task.priority.value} | до {due}")
        print(f"  Прогресс: {project.progress():.0%}")

    print("\n" + "=" * 64)
    print("5. Фильтры")
    print("=" * 64)
    active = ByStatus(TaskStatus.ACTIVE).apply(anna.all_tasks())
    urgent = ByTag("срочно").apply(anna.all_tasks())
    week = ByDueDate(now, now + timedelta(days=7)).apply(anna.all_tasks())
    print(f"Активных: {len(active)}")
    print(f"С меткой «срочно»: {[task.title for task in urgent]}")
    print(f"Со сроком на неделе: {[task.title for task in week]}")

    print("\n" + "=" * 64)
    print("6. Просроченные задачи и поиск")
    print("=" * 64)
    for task in manager.find_overdue(anna.id):
        print(f"  просрочена: {task.title} (срок {task.due_date:%d.%m.%Y})")
    print(f"Поиск «отчёт»: {[task.title for task in manager.search(anna.id, 'отчёт')]}")

    print("\n" + "=" * 64)
    print("7. Перенос задачи между проектами")
    print("=" * 64)
    moved = manager.move_task(anna.id, work.id, study.id, meetup.id)
    print(f"«{moved.title}» перенесена; id сохранён: {moved.id[:8]}…")
    print(f"Работа: {len(work.list_tasks())} задач, Учёба: {len(study.list_tasks())}")

    print("\n" + "=" * 64)
    print("8. Сохранение и загрузка (JSON)")
    print("=" * 64)
    manager.save()
    print(f"Состояние: {storage_path}")

    restored = TaskManager(JsonTaskRepository(storage_path))
    restored.load()
    restored_user = restored.users[0]
    same_ids = {task.id for task in restored_user.all_tasks()} == {
        task.id for task in anna.all_tasks()
    }
    print(f"Пользователей: {len(restored.users)}, проектов: {len(restored_user.list_projects())}")
    print(f"Задач: {len(restored_user.all_tasks())}, id совпадают: {'да' if same_ids else 'нет'}")
    print(f"Просроченных после загрузки: {len(restored.find_overdue(restored_user.id))}")

    print("\n" + "=" * 64)
    print("9. Подмена хранилища на in-memory (без правок домена)")
    print("=" * 64)
    memory_manager = TaskManager(InMemoryRepository())
    memory_manager.load()
    memory_user = memory_manager.register_user("Иван", "ivan@example.com")
    inbox = memory_user.create_project("Входящие")
    memory_manager.create_task(memory_user.id, inbox.id, "Проверить работу системы")
    memory_manager.save()
    print(f"В памяти сохранено пользователей: {len(memory_manager.users)}")
    print(f"Задач после перезагрузки: {len(memory_manager.users[0].all_tasks())}")

    print("\n" + "=" * 64)
    print("10. Удаление и служебные методы")
    print("=" * 64)
    report.change_description("Свести данные за год и согласовать с финдиректором")
    report.remove_tag("важное")
    print(f"«{report.title}»: описание={report.description!r}")
    print(f"Теги после remove_tag: {report.tags}")

    removed = manager.delete_task(anna.id, work.id, report.id)
    print(f"Удалена задача «{removed.title}», в «{work.name}» осталось: {len(work.list_tasks())}")

    manager.delete_project(anna.id, study.id)
    print(f"Удалён проект «{study.name}» (каскадом с задачами)")
    print(f"Проектов у пользователя: {[project.name for project in anna.list_projects()]}")
    print(f"«{work.name}» пуст: {'да' if work.is_empty() else 'нет'}")
    print(f"Всего задач у пользователя: {len(anna.all_tasks())}")

    try:
        manager.complete_task(anna.id, study.id, "any")
    except NotFoundError as error:
        print(f"Ожидаемая ошибка: {error}")

    print("\nГотово.")


if __name__ == "__main__":
    main()
