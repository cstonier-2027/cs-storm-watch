// CS's Storm Watch for macOS: a native window around the map in the Storm Watch folder.
// Files are served through a custom URL scheme so the page can fetch its data files,
// and the page reloads itself when the 30-minute refresh writes new data.
import Cocoa
import WebKit
import UniformTypeIdentifiers

let scheme = "stormwatch"

func siteRoot() -> URL? {
    let fm = FileManager.default
    let beside = Bundle.main.bundleURL.deletingLastPathComponent().appendingPathComponent("wxmap")
    let home = fm.homeDirectoryForCurrentUser.appendingPathComponent("Storm Watch/wxmap")
    return [beside, home].first { fm.fileExists(atPath: $0.appendingPathComponent("index.html").path) }
}

final class FolderSchemeHandler: NSObject, WKURLSchemeHandler {
    let root: URL
    init(root: URL) { self.root = root }

    func webView(_ webView: WKWebView, start task: WKURLSchemeTask) {
        guard let url = task.request.url else { return }
        var path = url.path.isEmpty || url.path == "/" ? "/index.html" : url.path
        path = path.removingPercentEncoding ?? path
        let file = root.appendingPathComponent(String(path.dropFirst())).standardizedFileURL
        guard file.path.hasPrefix(root.standardizedFileURL.path), let data = try? Data(contentsOf: file) else {
            task.didReceive(HTTPURLResponse(url: url, statusCode: 404, httpVersion: "HTTP/1.1", headerFields: nil)!)
            task.didFinish()
            return
        }
        let mime = UTType(filenameExtension: file.pathExtension)?.preferredMIMEType ?? "application/octet-stream"
        let headers = ["Content-Type": mime, "Content-Length": "\(data.count)", "Cache-Control": "no-store"]
        task.didReceive(HTTPURLResponse(url: url, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: headers)!)
        task.didReceive(data)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: WKURLSchemeTask) {}
}

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var lastBuilt: Date?
    var timer: Timer?
    var root: URL?

    func applicationDidFinishLaunching(_ note: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        buildMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                          backing: .buffered, defer: false)
        window.title = "CS's Storm Watch"
        window.titlebarAppearsTransparent = true
        window.backgroundColor = NSColor(red: 0.04, green: 0.07, blue: 0.10, alpha: 1)
        window.minSize = NSSize(width: 900, height: 600)
        window.setFrameAutosaveName("StormwatchMain")
        if !window.setFrameUsingName("StormwatchMain") { window.center() }

        root = siteRoot()
        let config = WKWebViewConfiguration()
        if let root { config.setURLSchemeHandler(FolderSchemeHandler(root: root), forURLScheme: scheme) }
        web = WKWebView(frame: window.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(web)
        window.makeKeyAndOrderFront(nil)

        if root == nil {
            web.loadHTMLString("""
              <body style="background:#0a1119;color:#e6edf3;font:15px -apple-system;display:grid;place-items:center;height:100vh;margin:0">
              <div style="max-width:420px;text-align:center"><h2>Map files not found</h2>
              <p>CS's Storm Watch looks for <b>Storm Watch/wxmap</b> in your home folder, or a <b>wxmap</b> folder next to this app.</p></div></body>
              """, baseURL: nil)
            return
        }
        lastBuilt = builtDate()
        web.load(URLRequest(url: URL(string: "\(scheme)://app/index.html")!))
        // Reload when the background refresh publishes new data.
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.checkForNewData() }
    }

    func builtDate() -> Date? {
        guard let root else { return nil }
        let meta = root.appendingPathComponent("data/meta.json")
        return (try? FileManager.default.attributesOfItem(atPath: meta.path))?[.modificationDate] as? Date
    }

    func checkForNewData() {
        guard let d = builtDate(), d != lastBuilt else { return }
        // Give the build a moment to finish writing images after meta.json lands.
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self] in
            self?.lastBuilt = d
            self?.web.reload()
        }
    }

    // Links to other sites (SPC, NWS) open in the default browser.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, url.scheme == "https", action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    @objc func reloadMap() { web.reload() }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About CS's Storm Watch", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide CS's Storm Watch", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit CS's Storm Watch", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload Map", action: #selector(reloadMap), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        viewItem.submenu = viewMenu

        let winItem = NSMenuItem(); main.addItem(winItem)
        let winMenu = NSMenu(title: "Window")
        winMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        winMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        winItem.submenu = winMenu
        NSApp.mainMenu = main
        NSApp.windowsMenu = winMenu
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
