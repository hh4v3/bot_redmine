 (cd "$(git rev-parse --show-toplevel)" && git apply --3way <<'EOF' 
diff --git a/bot.py b/bot.py
new file mode 100644
index 0000000000000000000000000000000000000000..70a17c45a269f7f14b25185b9742a6b2abd6b1f1
--- /dev/null
+++ b/bot.py
@@ -0,0 +1,432 @@
+import json
+import os
+from copy import deepcopy
+from dataclasses import dataclass
+from datetime import datetime, timedelta, timezone
+from pathlib import Path
+from typing import Any
+
+try:
+    import requests
+except Exception:  # pragma: no cover
+    requests = None
+
+BASE_DIR = Path(__file__).resolve().parent
+TEMPLATES_FILE = BASE_DIR / "templates.json"
+ADMIN_SETTINGS_FILE = BASE_DIR / "admin_settings.json"
+ADMIN_REPORT_USERS_FILE = BASE_DIR / "admin_report_users.json"
+USERMAP_FILE = BASE_DIR / "user_redmine.json"
+
+
+STATE: dict[str, str] = {
+    "idle": "idle",
+    "admin_menu": "admin_menu",
+    "admin_templates": "admin_templates",
+    "admin_template_view": "admin_template_view",
+    "admin_template_add": "admin_template_add",
+    "admin_template_edit": "admin_template_edit",
+    "admin_stats": "admin_stats",
+    "admin_stats_users_pick": "admin_stats_users_pick",
+    "admin_stats_projects_pick": "admin_stats_projects_pick",
+    "admin_bot_stats": "admin_bot_stats",
+    "admin_projects": "admin_projects",
+    "admin_bindings": "admin_bindings",
+    "admin_diag": "admin_diag",
+    "admin_settings": "admin_settings",
+}
+
+
+# ---------- generic JSON helpers ----------
+
+def _load_json(path: Path, default: Any) -> Any:
+    if not path.exists():
+        return deepcopy(default)
+    with path.open("r", encoding="utf-8") as f:
+        try:
+            return json.load(f)
+        except json.JSONDecodeError:
+            return deepcopy(default)
+
+
+def _save_json(path: Path, payload: Any) -> None:
+    path.parent.mkdir(parents=True, exist_ok=True)
+    with path.open("w", encoding="utf-8") as f:
+        json.dump(payload, f, ensure_ascii=False, indent=2)
+
+
+# ---------- templates helpers ----------
+
+def tpl_list_all() -> list[dict[str, Any]]:
+    data = _load_json(TEMPLATES_FILE, {"templates": []})
+    if isinstance(data, list):
+        return data
+    return data.get("templates", [])
+
+
+def tpl_list_enabled() -> list[dict[str, Any]]:
+    return [tpl for tpl in tpl_list_all() if tpl.get("enabled") is not False]
+
+
+def tpl_list() -> list[dict[str, Any]]:
+    # Backward-compatible user-facing list.
+    return tpl_list_enabled()
+
+
+def tpl_save_all(data: list[dict[str, Any]]) -> None:
+    # Preserve currently used object schema.
+    old = _load_json(TEMPLATES_FILE, {"templates": []})
+    if isinstance(old, list):
+        _save_json(TEMPLATES_FILE, data)
+    else:
+        old["templates"] = data
+        _save_json(TEMPLATES_FILE, old)
+
+
+def tpl_update(tpl_id: str, patch_dict: dict[str, Any]) -> bool:
+    data = tpl_list_all()
+    for tpl in data:
+        if str(tpl.get("id")) == str(tpl_id):
+            tpl.update(patch_dict)
+            tpl_save_all(data)
+            return True
+    return False
+
+
+def tpl_delete(tpl_id: str) -> bool:
+    data = tpl_list_all()
+    new_data = [tpl for tpl in data if str(tpl.get("id")) != str(tpl_id)]
+    if len(new_data) == len(data):
+        return False
+    tpl_save_all(new_data)
+    return True
+
+
+# ---------- report users helpers ----------
+
+def report_users_load() -> dict[str, Any]:
+    return _load_json(ADMIN_REPORT_USERS_FILE, {"users": []})
+
+
+def report_users_save(data: dict[str, Any]) -> None:
+    _save_json(ADMIN_REPORT_USERS_FILE, data)
+
+
+def report_users_list() -> list[dict[str, Any]]:
+    return report_users_load().get("users", [])
+
+
+# ---------- admin settings / hidden projects ----------
+
+def admin_settings_load() -> dict[str, Any]:
+    return _load_json(
+        ADMIN_SETTINGS_FILE,
+        {
+            "hidden_projects": [],
+            "bot_stats_author_id": None,
+            "bot_stats_author_name": "",
+            "bot_stats_project_ids": [],
+        },
+    )
+
+
+def admin_settings_save(data: dict[str, Any]) -> None:
+    _save_json(ADMIN_SETTINGS_FILE, data)
+
+
+def hidden_projects_get() -> set[int]:
+    data = admin_settings_load()
+    return {int(x) for x in data.get("hidden_projects", [])}
+
+
+def hidden_projects_set(project_ids: set[int]) -> None:
+    data = admin_settings_load()
+    data["hidden_projects"] = sorted(int(x) for x in project_ids)
+    admin_settings_save(data)
+
+
+# ---------- bindings helpers ----------
+
+def usermap_load() -> dict[str, Any]:
+    return _load_json(USERMAP_FILE, {"map": {}})
+
+
+def usermap_save(data: dict[str, Any]) -> None:
+    _save_json(USERMAP_FILE, data)
+
+
+def get_user_redmine(tg_id: int | str) -> int | None:
+    data = usermap_load()
+    value = data.get("map", {}).get(str(tg_id))
+    return int(value) if value is not None else None
+
+
+def set_user_redmine(tg_id: int | str, redmine_uid: int) -> None:
+    data = usermap_load()
+    data.setdefault("map", {})[str(tg_id)] = int(redmine_uid)
+    usermap_save(data)
+
+
+def del_user_redmine(tg_id: int | str) -> bool:
+    data = usermap_load()
+    existed = str(tg_id) in data.get("map", {})
+    data.get("map", {}).pop(str(tg_id), None)
+    usermap_save(data)
+    return existed
+
+
+# ---------- keyboard builders ----------
+# Return generic structures that can be adapted by aiogram builders.
+
+def kb_admin_main() -> list[list[dict[str, str]]]:
+    return [
+        [{"text": "🧩 Шаблоны", "cb": "admin:templates"}],
+        [{"text": "📊 Статистика", "cb": "admin:stats"}],
+        [{"text": "📁 Проекты", "cb": "admin:projects"}],
+        [{"text": "🔑 Привязки API", "cb": "admin:bindings"}],
+        [{"text": "🩺 Диагностика", "cb": "admin:diag"}],
+        [{"text": "⚙ Настройки", "cb": "admin:settings"}],
+        [{"text": "⬅ Назад", "cb": "admin:back"}],
+    ]
+
+
+def kb_admin_templates_list(templates: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
+    rows = [[{"text": f"{t.get('title', '(без названия)')} ({t.get('id')})", "cb": f"admin:tpl:open:{t.get('id')}"}] for t in templates]
+    rows.append([{"text": "➕ Добавить", "cb": "admin:tpl:add"}])
+    rows.append([{"text": "⬅ Назад", "cb": "admin:menu"}])
+    return rows
+
+
+def kb_admin_template_card(tpl_id: str, enabled: bool = True) -> list[list[dict[str, str]]]:
+    toggle_text = "🚫 Выключить" if enabled else "✅ Включить"
+    return [
+        [{"text": "Открыть", "cb": f"admin:tpl:view:{tpl_id}"}],
+        [{"text": toggle_text, "cb": f"admin:tpl:toggle:{tpl_id}"}],
+        [{"text": "Изменить название", "cb": f"admin:tpl:edit:title:{tpl_id}"}],
+        [{"text": "Изменить project_id", "cb": f"admin:tpl:edit:project:{tpl_id}"}],
+        [{"text": "Изменить start", "cb": f"admin:tpl:edit:start:{tpl_id}"}],
+        [{"text": "Изменить subject_template", "cb": f"admin:tpl:edit:subject:{tpl_id}"}],
+        [{"text": "Изменить request_type", "cb": f"admin:tpl:edit:rtype:{tpl_id}"}],
+        [{"text": "Изменить ask.prompt", "cb": f"admin:tpl:edit:ask:{tpl_id}"}],
+        [{"text": "Изменить спец-флаги", "cb": f"admin:tpl:edit:flags:{tpl_id}"}],
+        [{"text": "🗑 Удалить", "cb": f"admin:tpl:del:{tpl_id}"}],
+        [{"text": "⬅ Назад", "cb": "admin:templates"}],
+    ]
+
+
+def kb_admin_yes_no() -> list[list[dict[str, str]]]:
+    return [[{"text": "✅ Да", "cb": "yes"}, {"text": "❌ Нет", "cb": "no"}]]
+
+
+def kb_admin_users_multiselect(users: list[dict[str, Any]], selected: set[int]) -> list[list[dict[str, str]]]:
+    rows: list[list[dict[str, str]]] = []
+    for u in users:
+        uid = int(u.get("redmine_user_id", 0))
+        mark = "✅" if uid in selected else "☐"
+        rows.append([{"text": f"{mark} {u.get('name', uid)}", "cb": f"admin:stats:usertoggle:{uid}"}])
+    rows.extend([
+        [{"text": "Готово", "cb": "admin:stats:users:done"}],
+        [{"text": "Сбросить", "cb": "admin:stats:users:reset"}],
+        [{"text": "⬅ Назад", "cb": "admin:stats"}],
+    ])
+    return rows
+
+
+def kb_admin_projects_multiselect(projects: list[dict[str, Any]], selected: set[int]) -> list[list[dict[str, str]]]:
+    rows: list[list[dict[str, str]]] = []
+    for p in projects:
+        pid = int(p.get("id", 0))
+        mark = "✅" if pid in selected else "☐"
+        rows.append([{"text": f"{mark} {p.get('name', pid)}", "cb": f"admin:stats:projecttoggle:{pid}"}])
+    rows.extend([
+        [{"text": "Готово", "cb": "admin:stats:projects:done"}],
+        [{"text": "Сбросить", "cb": "admin:stats:projects:reset"}],
+        [{"text": "⬅ Назад", "cb": "admin:stats:users"}],
+    ])
+    return rows
+
+
+def kb_admin_stats_menu() -> list[list[dict[str, str]]]:
+    return [
+        [{"text": "👤 По сотрудникам", "cb": "admin:stats:users"}],
+        [{"text": "🤖 Через бота", "cb": "admin:stats:bot"}],
+        [{"text": "⬅ Назад", "cb": "admin:menu"}],
+    ]
+
+
+def kb_admin_bindings_menu() -> list[list[dict[str, str]]]:
+    return [
+        [{"text": "🔎 Найти по TG ID", "cb": "admin:bind:find"}],
+        [{"text": "✏ Создать/Изменить", "cb": "admin:bind:set"}],
+        [{"text": "🗑 Удалить", "cb": "admin:bind:delete"}],
+        [{"text": "⬅ Назад", "cb": "admin:menu"}],
+    ]
+
+
+def kb_admin_projects_menu(projects: list[dict[str, Any]], hidden: set[int]) -> list[list[dict[str, str]]]:
+    rows: list[list[dict[str, str]]] = []
+    for p in projects:
+        pid = int(p.get("id", 0))
+        is_hidden = pid in hidden
+        marker = "🚫 Скрыт" if is_hidden else "✅ Видим"
+        rows.append([{"text": f"{marker} {p.get('name', pid)}", "cb": f"admin:project:toggle:{pid}"}])
+    rows.append([{"text": "⬅ Назад", "cb": "admin:menu"}])
+    return rows
+
+
+def kb_admin_settings_menu() -> list[list[dict[str, str]]]:
+    return [
+        [{"text": "👤 Автор статистики бота", "cb": "admin:settings:author"}],
+        [{"text": "📁 Проекты статистики бота", "cb": "admin:settings:projects"}],
+        [{"text": "⬅ Назад", "cb": "admin:menu"}],
+    ]
+
+
+# ---------- Redmine helpers ----------
+@dataclass
+class RedmineProfile:
+    url: str
+    api_key: str
+
+
+def _mask_secret(secret: str, keep: int = 3) -> str:
+    if not secret:
+        return "(empty)"
+    if len(secret) <= keep * 2:
+        return "*" * len(secret)
+    return f"{secret[:keep]}***{secret[-keep:]}"
+
+
+def _issues_count_from_api(url: str, api_key: str, params: dict[str, Any]) -> int:
+    if requests is None:
+        return 0
+    endpoint = f"{url.rstrip('/')}/issues.json"
+    headers = {"X-Redmine-API-Key": api_key}
+    base_params = {"limit": 1, **params}
+    resp = requests.get(endpoint, params=base_params, headers=headers, timeout=20)
+    resp.raise_for_status()
+    payload = resp.json()
+    return int(payload.get("total_count", 0))
+
+
+def issues_count_by_author_and_projects(
+    uid: int,
+    author_id: int,
+    project_ids: list[int],
+    date_from: str | None = None,
+    status_id: str | None = None,
+) -> int:
+    profile = get_user_profile(uid)
+    params: dict[str, Any] = {
+        "author_id": author_id,
+        "project_id": "|".join(str(x) for x in project_ids),
+    }
+    if date_from:
+        params["created_on"] = f">={date_from}"
+    if status_id:
+        params["status_id"] = status_id
+    return _issues_count_from_api(profile.url, profile.api_key, params)
+
+
+def issues_count_closed_by_author_projects(
+    uid: int,
+    author_id: int,
+    project_ids: list[int],
+    created_from: str | None = None,
+    closed_to: str | None = None,
+) -> int:
+    profile = get_user_profile(uid)
+    params: dict[str, Any] = {
+        "status_id": "closed",
+        "author_id": author_id,
+        "project_id": "|".join(str(x) for x in project_ids),
+    }
+    if created_from:
+        params["created_on"] = f">={created_from}"
+    if closed_to:
+        params["closed_on"] = f"<={closed_to}"
+    return _issues_count_from_api(profile.url, profile.api_key, params)
+
+
+def issues_count_grouped_by_project(
+    uid: int,
+    author_id: int,
+    project_ids: list[int],
+    created_from: str | None = None,
+    closed_to: str | None = None,
+) -> dict[int, int]:
+    out: dict[int, int] = {}
+    for pid in project_ids:
+        out[int(pid)] = issues_count_closed_by_author_projects(
+            uid,
+            author_id,
+            [pid],
+            created_from=created_from,
+            closed_to=closed_to,
+        )
+    return out
+
+
+# ---------- integration placeholders ----------
+def get_user_profile(uid: int) -> RedmineProfile:
+    # Placeholder integration point with existing auth profile logic.
+    url = os.getenv("REDMINE_URL", "")
+    api_key = os.getenv("REDMINE_API_KEY", "")
+    return RedmineProfile(url=url, api_key=api_key)
+
+
+def get_trackers(uid: int) -> list[dict[str, Any]]:
+    # Placeholder compatible shape for diagnostics.
+    return []
+
+
+def get_projects(uid: int) -> list[dict[str, Any]]:
+    return []
+
+
+def build_diag_report(uid: int) -> str:
+    profile = get_user_profile(uid)
+    trackers = get_trackers(uid)
+    projects = get_projects(uid)
+    tracker_names = {t.get("name", "") for t in trackers}
+    device = os.getenv("DEVICE_TRACKER_NAME", "")
+    request = os.getenv("REQUEST_TRACKER_NAME", "")
+    incident = os.getenv("INCIDENT_TRACKER_NAME", "")
+    proxy = os.getenv("TG_PROXY", "")
+
+    visible_projects_count = len([p for p in projects if int(p.get("id", -1)) not in hidden_projects_get()])
+
+    return "\n".join(
+        [
+            "🩺 Диагностика",
+            f"Redmine URL: {profile.url or '(empty)'}",
+            f"API key: {_mask_secret(profile.api_key)}",
+            f"DEVICE_TRACKER_NAME найден: {'да' if device in tracker_names else 'нет'}",
+            f"REQUEST_TRACKER_NAME найден: {'да' if request in tracker_names else 'нет'}",
+            f"INCIDENT_TRACKER_NAME найден: {'да' if incident in tracker_names else 'нет'}",
+            f"Видимых проектов: {visible_projects_count}",
+            f"Шаблонов (всего): {len(tpl_list_all())}",
+            f"TG_PROXY: {_mask_secret(proxy)}",
+            "Telegram: проверяется внешним health-check",
+            "Redmine: доступность проверяется API вызовами",
+        ]
+    )
+
+
+def format_users_stats(names_by_id: dict[int, str], project_names: list[str], stats: dict[int, tuple[int, int]]) -> str:
+    lines = ["Статистика по сотрудникам", "", "Проекты:"]
+    lines.extend(f"- {name}" for name in project_names)
+    lines.append("")
+    for uid, (c30, c90) in stats.items():
+        lines.append(names_by_id.get(uid, str(uid)))
+        lines.append(f"• за 30 дней: {c30}")
+        lines.append(f"• за 90 дней: {c90}")
+        lines.append("")
+    return "\n".join(lines).strip()
+
+
+def format_bot_stats(author_name: str, project_names: list[str], total: int, last30: int, by_project_30d: dict[str, int]) -> str:
+    lines = ["Заявки через бота", "", f"Автор: {author_name}", "Проекты:"]
+    lines.extend(f"- {p}" for p in project_names)
+    lines.extend(["", f"• За всё время: {total}", f"• За последние 30 дней: {last30}", "", "Разбивка за 30 дней:"])
+    lines.extend(f"- {name}: {count}" for name, count in by_project_30d.items())
+    return "\n".join(lines)
 
EOF
)
