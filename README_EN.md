# Personal Agent Foreman

> A local dashboard for unified monitoring and management of multiple desktop AI Agents' working status.

[中文文档](README.md)

A lightweight local web service that collects real-time session data from multiple desktop AI Agents — **Codex**, **QoderWork**, **Mulerun**, **Qoder**, and **QwenWork** — and presents working status, session statistics, Skill usage analytics, and daily journals through a clean, minimal dashboard UI. Supports one-click message injection to any Agent.

## Features

- **Multi-Agent Status Monitoring** — Real-time polling of 5 Agent data sources with tri-state detection (Working / Waiting for Reply / Idle), refreshed every 2 seconds
- **Two Forms** — Native desktop app (AppKit + WKWebView) or web version, sharing the same service and data
- **Session Classification & Timeline** — Task cards grouped by Agent, with a unified daily timeline of all activity
- **Real-time Data Collection** — Supports SQLite, JSONL, and directory scanning data source formats with incremental caching to avoid redundant reads
- **Message Injection** — Send messages directly to Agents from the browser (Codex via CLI / others via clipboard + deep link + keystroke injection)
- **Skill Analytics** — Cumulative scanning of all historical skill invocations, with bar chart rankings + daily trend chart + search filtering
- **Work Overview** — Cumulative sessions, work duration, active days, and today's metrics, with per-Agent comparison charts + daily activity trends
- **Daily Journal** — Record collaboration insights, persisted by day, with support for editing historical entries
- **Deep Link Navigation** — One-click jump to the corresponding Agent's session window (supports codex:// / mulerun:// / qoder-work:// protocols)
- **Alias System** — Give each Agent a friendly nickname
- **launchd Keep-Alive** — Registered as a macOS background service with auto-start on boot and auto-restart on crash

## Screenshots

### Work Overview

Cumulative session/duration metric cards + per-Agent session bar chart + daily activity trend area chart + detailed metrics table.

![Work Overview](screenshots/overview.png)

### Daily Foreman Dashboard

Real-time tri-state stat cards (Waiting/Working/Idle) + Agent team task cards + daily timeline, with click-to-filter and message injection.

![Daily Foreman Dashboard](screenshots/dashboard.png)

### Skill Analytics

Cumulative skill invocation ranking bar chart + daily usage trend chart + search-linked detail table.

![Skill Analytics](screenshots/skills.png)

### Daily Journal

Record daily collaboration insights, with save/edit/history browsing support.

![Daily Journal](screenshots/journal.png)

## System Requirements

- **OS**: macOS (depends on launchd, pbcopy, osascript, open commands)
- **Python**: 3.9+ (standard library only, no third-party dependencies required)
- **Browser**: Any modern browser (Chrome / Safari / Firefox)
- **Accessibility Permission**: Message injection requires authorizing SendHelper.app in System Settings → Privacy & Security → Accessibility

## Installation & Usage

Two forms are available — **desktop app** and **web version** — sharing the same backend service and data. Pick either, or use both.

### Desktop app (recommended)

Download `AgentForeman.dmg` from [Releases](https://github.com/GingerYang19/personal-agent-foreman/releases/latest) → mount it → drag **AgentForeman** into Applications → double-click.

Runs in a native window and starts the backend automatically; closing the window leaves the backend collecting data. For the web version, choose “View → Open in Browser” (⌘⇧B).

> If the first launch warns about an unverified developer: right-click the app → Open.
> Runtime data lives in `~/Library/Application Support/AgentForeman` and survives app upgrades; message injection requires granting Accessibility permission to the `SendHelper.app` inside that directory.
> You can also clone the repo and run `./build_app.sh` to build it yourself (output in `dist/`).

### Web version

```bash
git clone https://github.com/GingerYang19/personal-agent-foreman.git
cd personal-agent-foreman
python3 server.py
```

Open **http://localhost:9527** — identical features to the desktop app. No installer needed.

> To stop the service: `pkill -f 'python3 .*server.py'`; the port can be overridden with `FOREMAN_PORT`.

### Auto-start on boot (optional)

For the web version you can create a launchd plist for auto-start on boot + crash recovery (the desktop app needs no setup — just double-click):

```bash
# Copy project to runtime directory
mkdir -p ~/.personal-hub
cp -r ./* ~/.personal-hub/

# Create launchd configuration
cat > ~/Library/LaunchAgents/com.personal-hub.monitor.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.personal-hub.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/.personal-hub/server.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/.personal-hub/server.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/.personal-hub/server.err</string>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/.personal-hub</string>
</dict>
</plist>
EOF

# Load the service
launchctl load ~/Library/LaunchAgents/com.personal-hub.monitor.plist
```

> Replace `YOUR_USERNAME` with your actual macOS username.

### Grant Accessibility Permission (required for message injection)

1. Open System Settings → Privacy & Security → Accessibility
2. Click `+` and add `SendHelper.app` from the project directory
3. Ensure the toggle is enabled

## Configuration

### Agent Data Sources

The service automatically scans the following paths on startup — no manual configuration needed:

| Agent | Data Source Path | Format |
|-------|-----------------|--------|
| QoderWork | `~/Library/Application Support/QoderWork/data/agents.db` | SQLite |
| Qoder | `~/.qoder/projects/*/transcript/*.jsonl` | JSONL |
| Mulerun | `~/Library/Application Support/mulerun-desktop/mulerun.db` | SQLite |
| Codex | `~/.codex/state_5.sqlite` | SQLite |
| QwenWork | `~/.qwenworkcn/workspace/*` | Directory |

### Message Injection Channels

| Agent | Method | Description |
|-------|--------|-------------|
| Codex | CLI (true send) | Invokes `codex exec resume <id> "<msg>"` in background |
| QoderWork | UI injection | Deep link `qoder-work://chats/<id>` + keystroke paste |
| Mulerun | UI injection | Deep link `mulerun://session/<id>` + keystroke paste |
| Qoder | UI injection | `open -a Qoder <project-dir>` + Cmd+L focus + paste |
| QwenWork | UI injection | Launch app + keystroke paste |

### Tunable Parameters

Configurable at the top of `server.py`:

```python
PORT = 9527              # Server port
POLL_INTERVAL = 5        # Polling interval (seconds)
WORKING_THRESHOLD = 60   # Write within 60s = Working
WAITING_WINDOW = 900     # Active within 15min & last msg is assistant = Waiting for Reply
```

## Technical Architecture

```
┌─────────────────────────────────────────────────┐
│              Browser (Frontend)                  │
│   index.html + style.css + app.js (Vanilla JS)  │
│   Zero deps · Hand-drawn SVG charts · 2s poll   │
└──────────────────────┬──────────────────────────┘
                       │ HTTP (localhost:9527)
┌──────────────────────▼──────────────────────────┐
│            Python Backend (server.py)            │
│   http.server.ThreadingHTTPServer               │
│   Stdlib only · Multi-threaded poll · Inc cache │
├─────────────────────────────────────────────────┤
│  Data Source Adapters                           │
│  SQLite (QoderWork/Mulerun/Codex)              │
│  JSONL  (Qoder/Claude)                         │
│  Dir scan (QwenWork)                           │
├─────────────────────────────────────────────────┤
│  Message Injection Channels                     │
│  Codex CLI · Deep links · SendHelper keystrokes │
└─────────────────────────────────────────────────┘
```

- **Backend**: Python 3 standard library (`http.server` + `sqlite3` + `threading`), zero third-party dependencies
- **Desktop shell**: Swift + AppKit + WKWebView (compiled to a universal binary with the system `swiftc`, no third-party frameworks)
- **Frontend**: Vanilla HTML/CSS/JavaScript, no framework, no build step, hand-drawn SVG trend charts + CSS bar charts
- **Data Collection**: Background thread polls every 5s, file-level mtime incremental caching, Skill/Overview full scans throttled to 300s
- **Message Injection**: Codex uses CLI true-send; other Agents use `clipboard → deep link navigation → SendHelper.app keystroke injection`

## Project Structure

```
personal-agent-foreman/
├── server.py              # Backend service (data collection + API + static files)
├── AgentForeman.app/      # Desktop app (native window + embedded WebView, includes boot script)
├── AgentForemanApp.swift  # Desktop window program source (AppKit + WKWebView)
├── build_app.sh           # Compiles the window program + builds the standalone app + DMG
├── web/
│   ├── index.html         # Page structure (4 tabs)
│   ├── style.css          # Light minimal theme styles
│   └── app.js             # Frontend logic (polling/rendering/charts/messaging)
├── SendHelper.app/        # macOS keystroke injection helper (compiled AppleScript)
├── send_helper.applescript # SendHelper source code
├── screenshots/           # README screenshots
├── .gitignore
└── README.md
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/state` | Get all Agent status, tasks, skills, and overview data |
| GET | `/api/journal` | Get daily journal entries |
| POST | `/api/send` | Send message to an Agent task `{agent, task_id, message}` |
| POST | `/api/open` | Navigate to an Agent session `{agent, task_id}` |
| POST | `/api/journal` | Save today's journal `{text}` |
| POST | `/api/alias` | Set Agent alias `{agent, alias}` |

## Contributing

Issues and Pull Requests are welcome!

1. Fork this repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Commit your changes: `git commit -m 'feat: add your feature'`
4. Push to the branch: `git push origin feature/your-feature`
5. Open a Pull Request

### Development Guidelines

- Backend must remain zero-dependency (Python standard library only) — no third-party packages
- Frontend must remain Vanilla JS — no frameworks or build tools
- To add a new Agent adapter, add a `poll_xxx()` function in `server.py` and register it in `poll_all()`
- Charts should use hand-drawn SVG or CSS — no chart libraries

## Contributors

- [GingerYang19](https://github.com/GingerYang19) — Project Author

## License

[MIT License](LICENSE)
