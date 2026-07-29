#!/usr/bin/env python3
"""
个人中枢 v2 · Agent 监工台
参考 agent-foreman：状态三分组（等回话/开工/摸鱼）、浏览器内发话、点击跳转到 Agent。

数据源:
  QoderWork  ~/Library/Application Support/QoderWork/data/agents.db   (SQLite)
  Qoder      ~/.qoder/projects/*/transcript/*.jsonl                    (JSONL)
  Mulerun    ~/Library/Application Support/mulerun-desktop/mulerun.db  (SQLite)
  Codex      ~/.codex/state_5.sqlite                                   (SQLite)
  QwenWork   ~/.qwenworkcn/workspace/*                                 (目录)

发话通道:
  Codex   -> Desktop 内置 CLI: codex exec resume <id> "<msg>"（后台真发话）
  其余    -> 剪贴板 + 深链跳到会话 + SendHelper.app 注入 Cmd+V 粘贴并回车
             （launchd 后台服务直调 osascript 会被 TCC 判为 Platform Binary 拒绝授权，
               故经 open -W 启动独立助手 App，只需给 SendHelper 勾选辅助功能权限）

跳转深链:
  Codex     codex://threads/<id>
  Mulerun   mulerun://session/<id>
  QoderWork qoder-work://chats/<id>   (对应应用内部路由 /chats/:chatId)
  Qoder     open -a Qoder <项目目录>

启动: python3 server.py    访问: http://localhost:9527
"""

import http.server
import json
import os
import glob
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

TZ = timezone(timedelta(hours=8))
PORT = 9527
POLL_INTERVAL = 5          # 秒，后台轮询间隔
WORKING_THRESHOLD = 60     # 60 秒内有写入 = 开工
WAITING_WINDOW = 900       # 15 分钟内活跃且最后一条是 assistant = 等回话

HUB_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(HUB_DIR, "web")
ALIAS_FILE = os.path.join(HUB_DIR, "aliases.json")
SEND_LOG = os.path.join(HUB_DIR, "send.log")

CODEX_CLI = "/Applications/Codex.app/Contents/Resources/codex"
SEND_HELPER = os.path.join(HUB_DIR, "SendHelper.app")
SEND_TASK_FILE = os.path.join(HUB_DIR, "send_task.txt")
SEND_RESULT_FILE = os.path.join(HUB_DIR, "send_result.txt")

# UI 注入发话配置: proc = 用于 activate 的应用名(tell application), pre_key = 粘贴前聚焦聊天输入框的快捷键, delay = 等待会话窗口就绪秒数
# 注: SendHelper 按键直发给前台应用, 不再 tell process(Qoder 等 Electron 应用的进程名与应用名不一致)
UI_SEND = {
    "QoderWork": {"proc": "QoderWork",       "pre_key": None, "delay": 2.0},
    "Mulerun":   {"proc": "MuleRun Alibaba", "pre_key": None, "delay": 2.0},
    "QwenWork":  {"proc": "QwenWorkCN",      "pre_key": None, "delay": 2.0},
    "Qoder":     {"proc": "Qoder",           "pre_key": "l",  "delay": 2.5},  # Cmd+L 聚焦聊天
}

# 全局状态缓存
_state = {"agents": [], "updated_at": None, "today_stats": {}}
_tasks_index = {}   # {agent: {task_id: task_dict}}  用于发话/跳转时查找
_lock = threading.Lock()


def now_ts():
    return datetime.now(TZ)


def get_today_range():
    now = now_ts()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def load_aliases():
    try:
        with open(ALIAS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_alias(agent, alias):
    aliases = load_aliases()
    if alias:
        aliases[agent] = alias
    else:
        aliases.pop(agent, None)
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, ensure_ascii=False, indent=2)


def send_log(msg):
    try:
        with open(SEND_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{now_ts().isoformat()}] {msg}\n")
    except OSError:
        pass


def task_status(last_ts, last_role):
    """三态判定: working(开工) / waiting(等回话) / idle(摸鱼)"""
    if last_ts is None:
        return "idle"
    age = time.time() - last_ts
    if age < WORKING_THRESHOLD:
        return "working"
    if age < WAITING_WINDOW and last_role == "assistant":
        return "waiting"
    return "idle"


def tail_last_message(path, max_bytes=128 * 1024):
    """读取 jsonl 尾部，返回最后一条 user/assistant 消息的 (角色, 文本摘要)"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return None, ""
    for line in reversed(data.strip().split("\n")):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        role, content = None, None
        t = obj.get("type")
        if t in ("user", "assistant"):
            role = t
            content = (obj.get("message") or {}).get("content")
        else:
            payload = obj.get("payload")
            if isinstance(payload, dict) and payload.get("role") in ("user", "assistant"):
                role = payload["role"]
                content = payload.get("content")
        if not role:
            continue
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = " ".join(
                c.get("text", "") for c in content
                if isinstance(c, dict) and c.get("type") in (None, "text", "input_text", "output_text"))
        text = " ".join(text.split())
        if text:  # 跳过纯工具调用消息，找有文本的一条
            return role, text[:160]
    return None, ""


def tail_last_role(path, max_bytes=128 * 1024):
    """读取 jsonl 尾部，返回最后一条 user/assistant 消息的角色"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "ignore")
    except OSError:
        return None
    for line in reversed(data.strip().split("\n")):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = obj.get("type")
        if t in ("user", "assistant"):
            return t
        payload = obj.get("payload")
        if isinstance(payload, dict):
            role = payload.get("role")
            if role in ("user", "assistant"):
                return role
    return None


def make_task(tid, title, project, last_dt, created_dt, last_role=None, cwd="", last_msg="", extra=None):
    st = task_status(last_dt.timestamp() if last_dt else None, last_role)
    # 开始时间 = 会话创建时间；非今日创建的带上日期，避免与当日时间混淆
    started = ""
    if created_dt:
        fmt = "%H:%M" if created_dt.date() == now_ts().date() else "%m-%d %H:%M"
        started = created_dt.strftime(fmt)
    task = {
        "id": tid,
        "title": (title or "(无标题)")[:100],
        "project": project or "",
        "cwd": cwd or "",
        "status": st,
        "started": started,
        "last_active": last_dt.strftime("%H:%M:%S") if last_dt else "",
        "last_active_ts": last_dt.timestamp() if last_dt else 0,
        "duration_min": int((last_dt - created_dt).total_seconds() / 60) if (last_dt and created_dt) else 0,
        "last_msg": last_msg or "",
        "last_role": last_role or "",
    }
    if extra:
        task.update(extra)
    return task


def agent_result(name, tasks, send_mode, error=None):
    tasks.sort(key=lambda t: t["last_active_ts"], reverse=True)
    working = [t for t in tasks if t["status"] == "working"]
    waiting = [t for t in tasks if t["status"] == "waiting"]
    if error:
        status = "error"
    elif working:
        status = "working"
    elif waiting:
        status = "waiting"
    elif tasks:
        status = "idle"
    else:
        status = "offline"
    return {
        "name": name,
        "status": status,
        "send_mode": send_mode,  # cli / clipboard
        "error": error,
        "working_count": len(working),
        "waiting_count": len(waiting),
        "today_count": len(tasks),
        "recent_tasks": tasks[:6],
    }


# ─── Pollers ─────────────────────────────────────────────────────────────────

def poll_qoderwork():
    db_path = os.path.expanduser("~/Library/Application Support/QoderWork/data/agents.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        start, _ = get_today_range()
        cur.execute("""
            SELECT c.id, c.name, c.created_at, c.updated_at, c.worktree_path,
                   p.name AS project_name,
                   (SELECT m.role FROM messages m WHERE m.chat_id = c.id
                    ORDER BY m.sequence DESC LIMIT 1) AS last_role,
                   (SELECT m.searchable_text FROM messages m WHERE m.chat_id = c.id
                    AND m.searchable_text IS NOT NULL AND m.searchable_text != ''
                    ORDER BY m.sequence DESC LIMIT 1) AS last_text
            FROM chats c LEFT JOIN projects p ON c.project_id = p.id
            WHERE c.updated_at >= ? AND c.deleted_at IS NULL
            ORDER BY c.updated_at DESC LIMIT 10
        """, (int(start.timestamp()),))
        tasks = []
        for row in cur.fetchall():
            tasks.append(make_task(
                row["id"], row["name"],
                row["project_name"] or os.path.basename(row["worktree_path"] or ""),
                datetime.fromtimestamp(row["updated_at"], tz=TZ),
                datetime.fromtimestamp(row["created_at"], tz=TZ),
                last_role=row["last_role"],
                cwd=row["worktree_path"] or "",
                last_msg=" ".join((row["last_text"] or "").split())[:160],
            ))
        conn.close()
        return agent_result("QoderWork", tasks, "ui")
    except Exception as e:
        return agent_result("QoderWork", [], "ui", error=str(e))


def poll_qoder():
    qoder_dir = os.path.expanduser("~/.qoder/projects")
    if not os.path.isdir(qoder_dir):
        return None
    try:
        start, _ = get_today_range()
        tasks = []
        for jsonl_path in glob.glob(os.path.join(qoder_dir, "*/transcript/*.jsonl")):
            mtime = os.path.getmtime(jsonl_path)
            mod_dt = datetime.fromtimestamp(mtime, tz=TZ)
            if mod_dt < start:
                continue
            title, cwd, first_ts = "", "", None
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "session_meta" and not cwd:
                        cwd = obj.get("cwd", "")
                    if obj.get("type") == "user" and not title:
                        content = obj.get("message", {}).get("content", "")
                        if isinstance(content, list):
                            content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                        title = str(content).strip()[:100]
                    ts = obj.get("timestamp")
                    if ts and first_ts is None:
                        first_ts = ts
            created_dt = None
            if first_ts:
                try:
                    created_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00")).astimezone(TZ)
                except (ValueError, TypeError):
                    pass
            if not cwd:
                # 从项目目录 slug 还原路径: -Users-yakexi-Documents-x -> /Users/yakexi/Documents/x
                slug = os.path.basename(os.path.dirname(os.path.dirname(jsonl_path)))
                cwd = slug.replace("-", "/") if slug.startswith("-") else ""
            role, text = tail_last_message(jsonl_path)
            tasks.append(make_task(
                os.path.basename(jsonl_path),
                title or os.path.basename(jsonl_path),
                os.path.basename(cwd) if cwd else "",
                mod_dt, created_dt or mod_dt,
                last_role=role,
                cwd=cwd,
                last_msg=text,
            ))
        return agent_result("Qoder", tasks, "ui")
    except Exception as e:
        return agent_result("Qoder", [], "ui", error=str(e))


def poll_mulerun():
    db_path = os.path.expanduser("~/Library/Application Support/mulerun-desktop/mulerun.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        start, _ = get_today_range()
        cur.execute("""
            SELECT c.id, c.title, c.acp_session_id, c.created_at, c.last_turn_at,
                   c.model, p.name AS project_name
            FROM chats c LEFT JOIN projects p ON c.project_id = p.id
            WHERE c.last_turn_at >= ? AND c.status != 'deleted'
            ORDER BY c.last_turn_at DESC LIMIT 10
        """, (int(start.timestamp() * 1000),))
        tasks = []
        for row in cur.fetchall():
            # 最后角色/动静: acp_session_id 对应 ~/.claude/projects/*/<id>.jsonl
            last_role, last_msg = None, ""
            if row["acp_session_id"]:
                matches = glob.glob(os.path.expanduser(
                    f"~/.claude/projects/*/{row['acp_session_id']}.jsonl"))
                if matches:
                    last_role, last_msg = tail_last_message(matches[0])
            tasks.append(make_task(
                row["id"], row["title"], row["project_name"] or "",
                datetime.fromtimestamp(row["last_turn_at"] / 1000, tz=TZ),
                datetime.fromtimestamp(row["created_at"] / 1000, tz=TZ),
                last_role=last_role,
                last_msg=last_msg,
                extra={"model": row["model"] or ""},
            ))
        conn.close()
        return agent_result("Mulerun", tasks, "ui")
    except Exception as e:
        return agent_result("Mulerun", [], "ui", error=str(e))


def poll_codex():
    db_path = os.path.expanduser("~/.codex/state_5.sqlite")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        start, _ = get_today_range()
        cur.execute("""
            SELECT id, title, cwd, model, first_user_message, rollout_path,
                   created_at_ms, updated_at_ms, git_branch
            FROM threads
            WHERE updated_at_ms >= ? AND archived = 0
            ORDER BY updated_at_ms DESC LIMIT 10
        """, (int(start.timestamp() * 1000),))
        tasks = []
        for row in cur.fetchall():
            last_role, last_msg = None, ""
            rollout = os.path.expanduser(row["rollout_path"] or "")
            if rollout and os.path.exists(rollout):
                last_role, last_msg = tail_last_message(rollout)
            tasks.append(make_task(
                row["id"],
                row["title"] or row["first_user_message"],
                os.path.basename(row["cwd"] or ""),
                datetime.fromtimestamp(row["updated_at_ms"] / 1000, tz=TZ),
                datetime.fromtimestamp(row["created_at_ms"] / 1000, tz=TZ),
                last_role=last_role,
                cwd=row["cwd"] or "",
                last_msg=last_msg,
                extra={"model": row["model"] or ""},
            ))
        conn.close()
        return agent_result("Codex", tasks, "cli")
    except Exception as e:
        # 数据库读取失败（如 WAL 锁）静默降级为离线，不在页面提示异常
        send_log(f"poll codex: {e}")
        return agent_result("Codex", [], "cli")


def poll_qwenwork():
    ws_dir = os.path.expanduser("~/.qwenworkcn/workspace")
    if not os.path.isdir(ws_dir):
        return None
    try:
        start, _ = get_today_range()
        tasks = []
        for entry in os.scandir(ws_dir):
            if not entry.is_dir():
                continue
            mod_dt = datetime.fromtimestamp(os.path.getmtime(entry.path), tz=TZ)
            if mod_dt < start:
                continue
            tasks.append(make_task(
                entry.name, entry.name, "",
                mod_dt,
                datetime.fromtimestamp(os.path.getctime(entry.path), tz=TZ),
                cwd=entry.path,
                last_msg="工作区文件有更新",
            ))
        return agent_result("QwenWork", tasks, "ui")
    except Exception as e:
        return agent_result("QwenWork", [], "ui", error=str(e))


# ─── Skill 使用统计 ──────────────────────────────────────────────────────────────

_skills_cache = {"data": [], "scanned_at": 0}
SKILL_SCAN_INTERVAL = 300  # 秒，全量历史扫描成本高，降频缓存


def _skill_hit(agg, skill, agent, project, ts):
    key = skill.strip()
    if not key:
        return
    ent = agg.setdefault(key, {"skill": key, "count": 0, "agents": set(), "projects": set(),
                               "last_ts": 0, "daily": {}})
    ent["count"] += 1
    ent["agents"].add(agent)
    if project:
        ent["projects"].add(project)
    ent["last_ts"] = max(ent["last_ts"], ts)
    day = datetime.fromtimestamp(ts, tz=TZ).strftime("%Y-%m-%d")
    ent["daily"][day] = ent["daily"].get(day, 0) + 1


def _scan_jsonl_skills(agg, path, agent, project):
    """扫描 Claude-Code 系 jsonl（Qoder transcript / claude projects）里的 Skill 调用"""
    mtime = os.path.getmtime(path)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if '"Skill"' not in line and "<command-name>" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                content = (obj.get("message") or {}).get("content")
                ts = mtime
                raw_ts = obj.get("timestamp")
                if raw_ts:
                    try:
                        ts = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp()
                    except (ValueError, TypeError):
                        pass
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Skill":
                            inp = b.get("input") or {}
                            _skill_hit(agg, str(inp.get("skill") or inp.get("command") or ""), agent, project, ts)
                elif isinstance(content, str) and "<command-name>" in content:
                    # 斜杠命令形式: <command-name>/xxx</command-name>
                    seg = content.split("<command-name>")[1].split("</command-name>")[0]
                    _skill_hit(agg, seg.strip().lstrip("/"), agent, project, ts)
    except OSError:
        pass


def scan_skills_all():
    """累计统计各 Agent 全部历史会话的 Skill 使用: 名称/频次/项目/来源/每日分布"""
    agg = {}

    # Qoder transcripts
    for path in glob.glob(os.path.expanduser("~/.qoder/projects/*/transcript/*.jsonl")):
        slug = os.path.basename(os.path.dirname(os.path.dirname(path)))
        project = slug.rsplit("-", 1)[-1] if slug.startswith("-") else slug
        _scan_jsonl_skills(agg, path, "Qoder", project)

    # Claude Code / Mulerun
    for path in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")):
        slug = os.path.basename(os.path.dirname(path))
        is_mule = "-mulerun-workspaces" in slug
        project = "" if is_mule else (slug.rsplit("-", 1)[-1] if slug.startswith("-") else slug)
        _scan_jsonl_skills(agg, path, "Mulerun" if is_mule else "Claude", project)

    # QoderWork agents.db: parts 里 type == tool-Skill
    db_path = os.path.expanduser("~/Library/Application Support/QoderWork/data/agents.db")
    if os.path.exists(db_path):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            rows = conn.execute("""
                SELECT m.parts, m.created_at, c.name, p.name
                FROM messages m
                JOIN chats c ON m.chat_id = c.id
                LEFT JOIN projects p ON c.project_id = p.id
                WHERE m.parts LIKE '%tool-Skill%'
            """).fetchall()
            conn.close()
            for parts_s, created_at, chat_name, proj_name in rows:
                try:
                    parts = json.loads(parts_s)
                except (json.JSONDecodeError, ValueError):
                    continue
                for p in parts if isinstance(parts, list) else []:
                    if isinstance(p, dict) and p.get("type") == "tool-Skill":
                        inp = p.get("input") or {}
                        _skill_hit(agg, str(inp.get("skill") or ""), "QoderWork",
                                   proj_name or (chat_name or "")[:20], created_at or time.time())
        except Exception as e:
            send_log(f"skill scan qoderwork: {e}")

    result = []
    for ent in agg.values():
        result.append({
            "skill": ent["skill"],
            "count": ent["count"],
            "agents": sorted(ent["agents"]),
            "projects": sorted(ent["projects"]),
            "last_used": datetime.fromtimestamp(ent["last_ts"], tz=TZ).strftime("%m-%d %H:%M") if ent["last_ts"] else "",
            "daily": ent["daily"],
        })
    result.sort(key=lambda x: -x["count"])
    return result


# ─── 工作总览（累计历史聚合） ────────────────────────────────────────────────

_overview_cache = {"data": {}, "scanned_at": 0}
OVERVIEW_SCAN_INTERVAL = 300  # 秒
SESSION_DUR_CAP = 480         # 单会话时长上限(min)，避免长期挂着的会话扭曲统计


def _scan_jsonl_processing_time(path, today_start_ts):
    """扫描 Qoder JSONL，按对话轮次计算 Agent 实际处理时间。
    处理时间 = 每轮 user 消息 → 该轮最后一条 assistant 消息的时间间隔之和。
    返回 (today_first_ts, total_processing_min, today_processing_min)。
    """
    today_first = None
    total_sec = 0.0
    today_sec = 0.0
    turn_start = None   # 当前轮 user 消息时间
    turn_end = None     # 当前轮最后 assistant 时间
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = rec.get("timestamp")
                    if not ts:
                        continue
                    epoch = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                role = rec.get("type") or rec.get("role") or ""
                if today_first is None and epoch >= today_start_ts:
                    today_first = epoch
                if role == "user":
                    # 结算上一轮
                    if turn_start is not None and turn_end is not None and turn_end > turn_start:
                        d = min(turn_end - turn_start, SESSION_DUR_CAP * 60)
                        total_sec += d
                        if turn_start >= today_start_ts:
                            today_sec += d
                    turn_start = epoch
                    turn_end = None
                elif role == "assistant":
                    turn_end = epoch
    except OSError:
        pass
    # 结算最后一轮
    if turn_start is not None and turn_end is not None and turn_end > turn_start:
        d = min(turn_end - turn_start, SESSION_DUR_CAP * 60)
        total_sec += d
        if turn_start >= today_start_ts:
            today_sec += d
    return today_first, total_sec / 60, today_sec / 60


def _mulerun_turn_processing_time(conn, chat_id, today_start_ts):
    """从 Mulerun turn_dispatches 计算精确的 Agent 处理时长。
    处理时间 = 每轮 request_written_at → turn_finished_at 的间隔之和。
    返回 (duration_min, today_first_ts)。
    """
    total_ms = 0.0
    today_first = None
    try:
        for written, finished in conn.execute(
                "SELECT request_written_at, turn_finished_at FROM turn_dispatches "
                "WHERE chat_id = ? AND status = 'turn_finished' AND turn_finished_at IS NOT NULL",
                (chat_id,)):
            if not written or not finished or finished <= written:
                continue
            d = min(finished - written, SESSION_DUR_CAP * 60 * 1000)  # 单轮上限
            total_ms += d
            epoch = written / 1000
            if today_first is None and epoch >= today_start_ts:
                today_first = epoch
    except Exception:
        return None, None
    if total_ms == 0:
        return None, None
    return total_ms / 60 / 1000, today_first


def _ov_add(ov, agent, created_ts, updated_ts, today_start_override=None, dur_override=None, today_dur_override=None):
    """聚合一条会话。
    today_start_override: 今天实际开始工作的时间戳（用于跨天会话精确计算）
    dur_override: 精确计算的累计时长(min)，替代简单的 created→mtime 触顶逻辑
    today_dur_override: 精确计算的今日处理时长(min)，优先级最高
    """
    if not updated_ts:
        return
    if not created_ts or created_ts > updated_ts:
        created_ts = updated_ts
    dur = dur_override if dur_override is not None else min((updated_ts - created_ts) / 60, SESSION_DUR_CAP)
    ent = ov.setdefault(agent, {"name": agent, "sessions": 0, "duration_min": 0,
                                "daily": {}, "today_count": 0, "today_min": 0})
    ent["sessions"] += 1
    ent["duration_min"] += dur
    day = datetime.fromtimestamp(updated_ts, tz=TZ).strftime("%Y-%m-%d")
    ent["daily"][day] = ent["daily"].get(day, 0) + 1
    start, _ = get_today_range()
    if updated_ts >= start.timestamp():
        ent["today_count"] += 1
        if today_dur_override is not None:
            # 精确的今日处理时长（如 Qoder 按轮次计算）
            today_dur = today_dur_override
        elif created_ts >= start.timestamp():
            # 会话今天创建：全部时长计入今日
            today_dur = dur
        elif today_start_override and today_start_override >= start.timestamp():
            # 跨天会话，有精确的今日首次活动时间
            today_dur = min((updated_ts - today_start_override) / 60, SESSION_DUR_CAP)
        else:
            # 跨天会话无精确数据：保守估计 30min（仅被触碰而非持续工作）
            today_dur = 30
        ent["today_min"] += max(today_dur, 0)


def _db_chat_day_spans(conn, chat_id, ts_divisor=1, today_start_ts=0):
    """从消息表按天计算会话活跃窗口。返回 (duration_min, today_first_ts)。
    ts_divisor: 时间戳除数（秒=1，毫秒=1000）
    """
    day_spans = {}
    today_first = None
    try:
        for (raw,) in conn.execute(
                "SELECT created_at FROM messages WHERE chat_id = ? AND created_at IS NOT NULL ORDER BY created_at",
                (chat_id,)):
            epoch = raw / ts_divisor
            if today_start_ts and epoch >= today_start_ts and today_first is None:
                today_first = epoch
            day = datetime.fromtimestamp(epoch, tz=TZ).strftime("%Y-%m-%d")
            if day in day_spans:
                day_spans[day][1] = epoch
            else:
                day_spans[day] = [epoch, epoch]
    except Exception:
        return None, None
    if not day_spans:
        return None, None
    total = 0.0
    for first, last in day_spans.values():
        span = (last - first) / 60
        total += max(span, 1) if span < 1 else min(span, SESSION_DUR_CAP)
    return total, today_first


def scan_overview():
    """累计聚合各 Agent 全部历史会话: 会话数/工作时长/每日分布/今日指标"""
    ov = {}
    today_start_ts = get_today_range()[0].timestamp()

    # QoderWork（消息级时间戳精确计算每日活跃窗口）
    db = os.path.expanduser("~/Library/Application Support/QoderWork/data/agents.db")
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for cid, c, u in conn.execute("SELECT id, created_at, updated_at FROM chats WHERE deleted_at IS NULL"):
                dur, today_first = _db_chat_day_spans(conn, cid, ts_divisor=1, today_start_ts=today_start_ts)
                _ov_add(ov, "QoderWork", c, u,
                        today_start_override=today_first, dur_override=dur)
            conn.close()
        except Exception as e:
            send_log(f"overview qoderwork: {e}")

    # Qoder transcripts（按轮次计算 Agent 实际处理时间）
    for path in glob.glob(os.path.expanduser("~/.qoder/projects/*/transcript/*.jsonl")):
        try:
            created = None
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(4096)
            for line in head.split("\n"):
                try:
                    ts = json.loads(line).get("timestamp")
                    if ts:
                        created = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
                        break
                except (json.JSONDecodeError, ValueError):
                    continue
            created = created or os.path.getctime(path)
            mtime = os.path.getmtime(path)
            # 按轮次计算精确处理时间 + 今日处理时间
            today_first, proc_dur, today_proc = _scan_jsonl_processing_time(path, today_start_ts)
            _ov_add(ov, "Qoder", created, mtime,
                    today_start_override=today_first, dur_override=proc_dur,
                    today_dur_override=today_proc)
        except OSError:
            pass

    # Mulerun（turn_dispatches 精确轮次处理时长）
    db = os.path.expanduser("~/Library/Application Support/mulerun-desktop/mulerun.db")
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for cid, c, u in conn.execute("SELECT id, created_at, last_turn_at FROM chats WHERE status != 'deleted'"):
                dur, today_first = _mulerun_turn_processing_time(conn, cid, today_start_ts)
                _ov_add(ov, "Mulerun", (c or 0) / 1000, (u or 0) / 1000,
                        today_start_override=today_first, dur_override=dur)
            conn.close()
        except Exception as e:
            send_log(f"overview mulerun: {e}")

    # Codex
    db = os.path.expanduser("~/.codex/state_5.sqlite")
    if os.path.exists(db):
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            for c, u in conn.execute("SELECT created_at_ms, updated_at_ms FROM threads WHERE archived = 0"):
                _ov_add(ov, "Codex", (c or 0) / 1000, (u or 0) / 1000)
            conn.close()
        except Exception as e:
            send_log(f"overview codex: {e}")

    # QwenWork
    ws = os.path.expanduser("~/.qwenworkcn/workspace")
    if os.path.isdir(ws):
        for entry in os.scandir(ws):
            if entry.is_dir():
                try:
                    _ov_add(ov, "QwenWork", os.path.getctime(entry.path), os.path.getmtime(entry.path))
                except OSError:
                    pass

    result = []
    for ent in ov.values():
        result.append({
            "name": ent["name"],
            "sessions": ent["sessions"],
            "duration_min": int(ent["duration_min"]),
            "active_days": len(ent["daily"]),
            "today_count": ent["today_count"],
            "today_min": int(ent["today_min"]),
            "daily": ent["daily"],
        })
    result.sort(key=lambda x: -x["sessions"])
    return {"agents": result}


# ─── 每日总结（持久化） ──────────────────────────────────────────────────

JOURNAL_FILE = os.path.join(HUB_DIR, "journal.json")
_journal_lock = threading.Lock()


def load_journal():
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def journal_entries():
    data = load_journal()
    entries = [{"date": d, "text": v.get("text", ""), "updated_at": v.get("updated_at", "")}
               for d, v in data.items()]
    entries.sort(key=lambda x: x["date"], reverse=True)
    return entries


def save_journal_today(text):
    today = now_ts().strftime("%Y-%m-%d")
    with _journal_lock:
        data = load_journal()
        text = (text or "").strip()
        if text:
            data[today] = {"text": text[:20000], "updated_at": now_ts().strftime("%H:%M")}
        else:
            data.pop(today, None)  # 保存空内容 = 删除今日总结
        with open(JOURNAL_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return today


# ─── Token 用量统计 ────────────────────────────────────────────────────────
# 已移除：仅 Claude/Mulerun 记录 token，QoderWork/Qoder/QwenWork/Codex 数据源无记录，
# 覆盖不全容易误导，应用户要求下线。


def poll_all():
    agents = []
    index = {}
    # Codex 暂不常用，排在班组末位
    for poller in [poll_qoderwork, poll_qoder, poll_mulerun, poll_qwenwork, poll_codex]:
        result = poller()
        if result:
            agents.append(result)
            index[result["name"]] = {t["id"]: t for t in result["recent_tasks"]}

    aliases = load_aliases()
    for a in agents:
        a["alias"] = aliases.get(a["name"], "")

    # Skill 统计降频扫描（累计全量）
    if time.time() - _skills_cache["scanned_at"] > SKILL_SCAN_INTERVAL:
        try:
            _skills_cache["data"] = scan_skills_all()
        except Exception as e:
            send_log(f"skill scan: {e}")
        _skills_cache["scanned_at"] = time.time()

    # 工作总览降频扫描（累计全量）
    if time.time() - _overview_cache["scanned_at"] > OVERVIEW_SCAN_INTERVAL:
        try:
            _overview_cache["data"] = scan_overview()
        except Exception as e:
            send_log(f"overview scan: {e}")
        _overview_cache["scanned_at"] = time.time()

    with _lock:
        _state["agents"] = agents
        _state["updated_at"] = now_ts().isoformat()
        _state["skills"] = _skills_cache["data"]
        _state["overview"] = _overview_cache["data"]
        _state["today_stats"] = {
            "total_tasks": sum(a["today_count"] for a in agents),
            "working_agents": sum(1 for a in agents if a["status"] == "working"),
            "waiting_agents": sum(1 for a in agents if a["status"] == "waiting"),
            "idle_agents": sum(1 for a in agents if a["status"] in ("idle", "offline")),
        }
        _tasks_index.clear()
        _tasks_index.update(index)


def poll_loop():
    while True:
        try:
            poll_all()
        except Exception as e:
            print(f"[POLL ERROR] {e}")
        time.sleep(POLL_INTERVAL)


# ─── 发话 / 跳转 ─────────────────────────────────────────────────────────────

def find_task(agent, task_id):
    with _lock:
        return _tasks_index.get(agent, {}).get(task_id)


def copy_to_clipboard(text):
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def helper_activate(app_name):
    """经 SendHelper 将应用置前。launchd 后台进程直接 open/osascript 无法抢焦点，
    独立 App 经 open -W 启动后具备激活其他应用的能力（与发话注入同一机制）。"""
    try:
        with open(SEND_TASK_FILE, "w", encoding="utf-8") as f:
            f.write(f"{app_name}\n\nactivate")
        # -g: SendHelper 自身不抢焦点，否则它退出时 macOS 会把焦点回退给之前的应用，
        # 覆盖掉刚激活的目标应用
        subprocess.run(["open", "-W", "-g", "-a", SEND_HELPER], capture_output=True, timeout=15)
    except Exception as e:
        send_log(f"activate {app_name}: {e}")


def inject_message(agent, task, message):
    """真发话: 消息进剪贴板 -> 深链跳到会话 -> SendHelper.app 粘贴并回车"""
    cfg = UI_SEND[agent]
    copy_to_clipboard(message)
    open_url_or_app(agent, task)
    time.sleep(cfg["delay"])
    with open(SEND_TASK_FILE, "w", encoding="utf-8") as f:
        f.write(f"{cfg['proc']}\n{cfg['pre_key'] or ''}")
    try:
        os.remove(SEND_RESULT_FILE)
    except OSError:
        pass
    r = subprocess.run(["open", "-W", "-a", SEND_HELPER], capture_output=True, text=True, timeout=30)
    result = ""
    try:
        with open(SEND_RESULT_FILE, "r", encoding="utf-8") as f:
            result = f.read().strip()
    except OSError:
        pass
    if result != "ok":
        err = result or (r.stderr or "").strip() or "SendHelper 无响应"
        send_log(f"inject {agent} FAIL: {err[-200:]}")
        if "1002" in err or "not allowed" in err or "不允许" in err:
            raise RuntimeError("需授权: 系统设置→隐私与安全性→辅助功能，勾选 SendHelper 后重试（消息已在剪贴板，可手动粘贴）")
        raise RuntimeError(f"注入失败: {err[-120:]}（消息已在剪贴板，可手动粘贴）")
    send_log(f"inject {agent} {str(task.get('id'))[:8]}: {message[:80]}")


def open_url_or_app(agent, task):
    """跳转到对应 Agent 的会话/项目，返回描述文本。
    深链只负责应用内导航；除 QoderWork(handler 自带 bringToFront) 外，
    其余应用需额外经 SendHelper 激活置前。"""
    if agent == "Codex":
        subprocess.run(["open", f"codex://threads/{task['id']}"], check=True)
        helper_activate("Codex")
        return "已打开 Codex 会话"
    if agent == "Mulerun":
        subprocess.run(["open", f"mulerun://session/{task['id']}"], check=True)
        helper_activate("MuleRun Alibaba")
        return "已打开 Mulerun 会话"
    if agent == "Qoder":
        cwd = task.get("cwd")
        if cwd and os.path.isdir(cwd):
            subprocess.run(["open", "-a", "Qoder", cwd], check=True)
            helper_activate("Qoder")
            return f"已在 Qoder 打开 {os.path.basename(cwd)}"
        subprocess.run(["open", "-a", "Qoder"], check=True)
        helper_activate("Qoder")
        return "已唤起 Qoder"
    if agent == "QoderWork":
        # 深链直达会话：app 的 handleDeepLink 白名单仅支持 notification-click?chatId= 导航到会话
        # （chats/<id> 路由不存在，只会激活应用不导航）；handler 内部 bringToFront，无需额外激活
        subprocess.run(["open", f"qoder-work://notification-click?chatId={task['id']}"], check=True)
        return "已打开 QoderWork 会话"
    if agent == "QwenWork":
        subprocess.run(["open", "-a", "QwenWorkCN"], check=True)
        helper_activate("QwenWorkCN")
        return "已唤起 QwenWork"
    raise ValueError(f"未知 agent: {agent}")


def do_send(agent, task_id, message):
    """发话分发。返回 dict: {ok, mode, detail}"""
    task = find_task(agent, task_id)
    if not task:
        return {"ok": False, "detail": "任务不存在或已过期，请刷新"}

    message = (message or "").strip()

    if agent == "Codex":
        prompt = message or "继续"
        cmd = [CODEX_CLI, "exec", "resume", "--skip-git-repo-check", task_id, prompt]
        cwd = task.get("cwd") or None
        if cwd and not os.path.isdir(cwd):
            cwd = None

        def run():
            try:
                r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)
                send_log(f"codex resume {task_id[:8]} rc={r.returncode} err={r.stderr[-300:] if r.stderr else ''}")
            except Exception as e:
                send_log(f"codex resume {task_id[:8]} EXC={e}")

        threading.Thread(target=run, daemon=True).start()
        send_log(f"codex dispatch {task_id[:8]}: {prompt[:80]}")
        return {"ok": True, "mode": "cli", "detail": "已派发给 Codex，回复稍后出现在时间线"}

    # UI 注入发话: 跳到会话窗口后自动粘贴发送；留空 = 催它继续
    if agent in UI_SEND:
        try:
            inject_message(agent, task, message or "继续")
            return {"ok": True, "mode": "ui", "detail": f"已打开 {agent} 会话并发送消息"}
        except Exception as e:
            return {"ok": False, "mode": "ui", "detail": str(e)}

    # 兜底: 剪贴板 + 唤起
    try:
        if message:
            copy_to_clipboard(message)
        detail = open_url_or_app(agent, task)
        if message:
            detail = f"消息已复制到剪贴板，{detail}，粘贴即可发送"
        return {"ok": True, "mode": "clipboard", "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def do_open(agent, task_id):
    task = find_task(agent, task_id)
    if not task:
        return {"ok": False, "detail": "任务不存在或已过期，请刷新"}
    try:
        return {"ok": True, "detail": open_url_or_app(agent, task)}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


# ─── HTTP 服务 ───────────────────────────────────────────────────────────────

MIME = {".html": "text/html", ".js": "application/javascript", ".css": "text/css",
        ".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(http.server.BaseHTTPRequestHandler):

    def _json(self, obj, code=200):
        payload = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            return {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/state":
            with _lock:
                snapshot = json.loads(json.dumps(_state, ensure_ascii=False))
            self._json(snapshot)
            return
        if path == "/api/journal":
            self._json({"today": now_ts().strftime("%Y-%m-%d"), "entries": journal_entries()})
            return
        # 静态文件
        if path == "/":
            path = "/index.html"
        file_path = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if file_path.startswith(WEB_DIR) and os.path.isfile(file_path):
            ext = os.path.splitext(file_path)[1]
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", f"{MIME.get(ext, 'application/octet-stream')}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        if path == "/api/send":
            self._json(do_send(body.get("agent", ""), body.get("task_id", ""), body.get("message", "")))
        elif path == "/api/open":
            self._json(do_open(body.get("agent", ""), body.get("task_id", "")))
        elif path == "/api/journal":
            today = save_journal_today(body.get("text", ""))
            self._json({"ok": True, "date": today, "entries": journal_entries()})
        elif path == "/api/alias":
            agent = body.get("agent", "")
            if agent:
                save_alias(agent, (body.get("alias") or "").strip()[:20])
                poll_all()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "detail": "缺少 agent"}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format, *args):
        pass


def main():
    print("个人中枢 v2 · Agent 监工台")
    print(f"http://localhost:{PORT}")
    print(f"轮询 {POLL_INTERVAL}s · 开工阈值 {WORKING_THRESHOLD}s · 等回话窗口 {WAITING_WINDOW // 60}min")
    poll_all()
    with _lock:
        stats = _state["today_stats"]
    print(f"[INIT] 今日 {stats.get('total_tasks', 0)} 个任务")
    threading.Thread(target=poll_loop, daemon=True).start()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[SERVER] 监听中，Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] 服务已停止")
        server.shutdown()


if __name__ == "__main__":
    main()
