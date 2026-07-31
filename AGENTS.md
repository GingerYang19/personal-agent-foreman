# AGENTS.md — Agent 作业须知

本文件面向在本仓库工作的 coding agent,以安全边界为主。用户视角的功能与部署说明见 [README.md](README.md)。

## ⚠️ 发话链路副作用清单(调试前必读)

`POST /api/send`(`server.py: do_send`)不是普通接口,每次调用都会在**本机桌面产生真实副作用**:

| 分支 | 触发条件 | 真实副作用 |
| --- | --- | --- |
| Codex CLI 真发话 | `agent == "Codex"` | 后台线程执行 `codex exec resume --skip-git-repo-check <task_id> "<msg>"`(CLI 路径 `/Applications/Codex.app/Contents/Resources/codex`),在任务原 cwd 中**真实驱动 Codex 继续工作**,单次超时 **1800 秒(30 分钟)**;消息留空会自动发送"继续" |
| UI 注入发话 | agent ∈ QoderWork / Mulerun / QwenWork / Qoder(`inject_message`) | ① `pbcopy` **覆盖用户当前剪贴板**(不备份、不恢复);② 深链/`open` 将目标应用**抢焦点置前**;③ 等待 2.0–2.5s 后由 `SendHelper.app` 向**当时的前台应用**注入 Cmd+V 粘贴 + 回车按键(`open -W` 等待,30s 超时)——若期间用户切换了窗口,按键会打进错误的应用 |
| 剪贴板兜底 | 其余 agent | 覆盖剪贴板 + 深链唤起应用(同样抢焦点) |

`POST /api/open`(`do_open` → `open_url_or_app`)同样会打开深链并经 `helper_activate` 抢焦点置前,只是不注入按键。

**调试前须知**:

- 不要在真实使用中的桌面上随手 curl `/api/send` 做验证——剪贴板会被覆盖,按键会注入前台窗口,Codex 分支会真实消耗一次 Agent 会话。
- 纯逻辑验证请走测试层:`python3 -m unittest discover tests`(覆盖 `task_status` 三态判定、`agent_result` 聚合、时长统计等纯函数,无副作用)。
- 注入失败是可恢复的:`inject_message` 失败时消息仍在剪贴板,接口返回的 detail 会提示"可手动粘贴"。

## 诊断入口

每次 `/api/send` 请求会生成一个 8 位关联 id(sid),同时出现在三处证据中,可用它串联单次失败链路:

| 位置 | 内容 |
| --- | --- |
| HTTP 响应 `detail` 字段 | 面向用户的失败摘要,末尾附 `[sid xxxxxxxx]` |
| `send.log`(仓库根,被 .gitignore) | 主诊断汇:注入失败尾部错误、codex resume 的 rc/stderr、各 poller 异常;ISO 时间戳,发话相关行均带 `sid=`。若 send.log 不可写,`send_log()` 会向 stderr 打印 `[SEND_LOG DEGRADED]` 降级提示(launchd 下见 `server.log`) |
| `send_result.txt`(仓库根) | SendHelper 单次执行结果,成功为 `ok`,其余为 AppleScript 错误文本;每次注入前被删除,读取后服务端会补记 `sid=` 行 |

其他:`send_task.txt` 是传给 SendHelper 的任务文件(应用名 + 前置快捷键);轮询循环异常走 `print` 到 stdout,launchd 部署下进入 `server.log`;HTTP 访问日志被 `log_message` 关闭,不要指望从访问日志排查。

## 环境前置

- 仅 macOS。`SendHelper.app` 需在「系统设置 → 隐私与安全性 → 辅助功能」中勾选授权;未授权时注入报错含 `1002` / `not allowed`,接口会返回引导提示。launchd 后台进程直调 osascript 会被 TCC 判为 Platform Binary 拒绝,这正是 SendHelper 独立 App 存在的原因(详见 `server.py` 顶部注释)。
- 数据源硬依赖本机 5 个桌面 App 的私有路径(QoderWork / Qoder / Mulerun / Codex / QwenWork,见 `server.py` 顶部注释),不具备这些 App 的环境轮询结果为空,无 mock 手段。
- 服务只绑 `127.0.0.1:9527`;端口被占用会直接崩溃(`Errno 48`),重启前先确认旧进程已退出。

## Qoder MCP 使用契约（均为用户级配置，非项目依赖）

当前项目视图可见 4 个 MCP server，处置如下（修改/移除属用户全局配置，需用户单独授权）：

| Server | 处置 | 何时用 / 典型输入输出 |
| --- | --- | --- |
| browser-use | 保留 | 验证本项目 Web UI（http://localhost:9527）时用：导航/点击/截图/读控制台；输入 URL 与页面操作，输出快照/截图/网络日志。注意不要经页面发话按钮触发真实注入（见副作用清单） |
| schedule | 保留 | 需要定时/周期任务（如定期复评、定时提醒）时用；输入任务描述与频率，输出计划任务登记 |
| genui | 保留 | 仅供 Skill 弹出交互 Widget（show_widget）使用，与本仓库运行时无关；日常开发不主动调用 |
| blender | 与本项目无关 | 3D 建模/资产生成（含任意代码执行），与监工台任务族无交集；在本项目任务中不得使用；已列入待用户授权的移除建议清单 |

## 项目约定(指针)

遵守 [README.md「开发建议」](README.md#开发建议):后端零依赖(仅 Python 标准库)、前端 Vanilla JS 无框架无构建、图表用 SVG/CSS 不引图表库;新增 Agent 适配 = 在 `server.py` 添加 `poll_xxx()` 并注册进 `poll_all()`。测试层同样遵守零依赖(标准库 `unittest`,不引入 pytest)。
