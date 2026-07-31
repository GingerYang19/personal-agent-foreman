// AgentForeman 桌面窗口程序（AppKit + WKWebView，无第三方依赖）
// 职责: 启动/确保后端服务(经同目录 launch-server.sh) -> 原生窗口内加载监工台界面。
// 由 build_app.sh 用系统 swiftc 编译为 universal 二进制，产物提交在 AgentForeman.app/Contents/MacOS/AgentForeman。
import Cocoa
import WebKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var webView: WKWebView!
    let port = ProcessInfo.processInfo.environment["FOREMAN_PORT"] ?? "9527"

    func applicationDidFinishLaunching(_ notification: Notification) {
        // 1) 经引导脚本确保服务就绪（脚本内部处理仓库/独立双模式与错误弹窗）
        let exeDir = URL(fileURLWithPath: Bundle.main.executablePath!).deletingLastPathComponent()
        let script = exeDir.appendingPathComponent("launch-server.sh").path
        let boot = Process()
        boot.executableURL = URL(fileURLWithPath: "/bin/bash")
        boot.arguments = [script]
        boot.environment = ProcessInfo.processInfo.environment
        do {
            try boot.run()
            boot.waitUntilExit()
        } catch {
            fatalAlert("无法运行启动脚本: \(error.localizedDescription)")
            NSApp.terminate(nil); return
        }
        if boot.terminationStatus != 0 {
            // 脚本内部已用 osascript 弹出具体原因
            NSApp.terminate(nil); return
        }

        // 2) 原生窗口 + 内嵌 WebView
        let rect = NSRect(x: 0, y: 0, width: 1280, height: 840)
        window = NSWindow(contentRect: rect,
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Agent 监工台"
        window.minSize = NSSize(width: 900, height: 600)
        window.setFrameAutosaveName("AgentForemanMain")
        window.center()

        webView = WKWebView(frame: rect)
        webView.autoresizingMask = [.width, .height]
        window.contentView = webView
        webView.load(URLRequest(url: URL(string: "http://127.0.0.1:\(port)/")!))

        buildMenu()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // 关窗即退出（后端服务独立存活，继续采集）
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationSupportsSecureRestorableState(_ app: NSApplication) -> Bool { true }

    private func buildMenu() {
        let mainMenu = NSMenu()

        let appItem = NSMenuItem(); mainMenu.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "关于 Agent 监工台",
                        action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "隐藏", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "退出 Agent 监工台",
                        action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        // 编辑菜单：保证发话输入框的 Cmd+C/V/X/A 可用
        let editItem = NSMenuItem(); mainMenu.addItem(editItem)
        let editMenu = NSMenu(title: "编辑")
        editMenu.addItem(withTitle: "撤销", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "剪切", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "拷贝", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "粘贴", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "全选", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu

        let viewItem = NSMenuItem(); mainMenu.addItem(viewItem)
        let viewMenu = NSMenu(title: "显示")
        viewMenu.addItem(withTitle: "刷新", action: #selector(WKWebView.reload(_:)), keyEquivalent: "r")
        viewItem.submenu = viewMenu

        NSApp.mainMenu = mainMenu
    }

    private func fatalAlert(_ text: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "Agent 监工台"
        alert.informativeText = text
        alert.runModal()
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
