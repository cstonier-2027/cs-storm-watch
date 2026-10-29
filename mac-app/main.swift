// CS's Storm Watch for macOS: a native window around the live map site.
// GitHub refreshes the site every 30 minutes; the app reloads when meta.json's build time changes.
import Cocoa
import WebKit

let site = URL(string: "https://cs-storm-watch.csstormwatch.workers.dev/")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var lastBuilt: String?
    var loadFailed = false
    var timer: Timer?

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

        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()  // always fetch fresh data
        web = WKWebView(frame: window.contentView!.bounds, configuration: config)
        web.autoresizingMask = [.width, .height]
        web.navigationDelegate = self
        web.setValue(false, forKey: "drawsBackground")
        window.contentView!.addSubview(web)
        window.makeKeyAndOrderFront(nil)

        web.load(URLRequest(url: site))
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.checkForNewData() }
    }

    // Reload when the site has a newer build (meta.json "built"), and after a failed load (e.g. offline at wake).
    func checkForNewData() {
        var req = URLRequest(url: site.appendingPathComponent("data/meta.json"))
        req.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: req) { [weak self] data, _, _ in
            guard let data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let built = obj["built"] as? String else { return }
            DispatchQueue.main.async {
                guard let self else { return }
                if self.lastBuilt == nil { self.lastBuilt = built; if self.loadFailed { self.reloadMap() }; return }
                if built != self.lastBuilt || self.loadFailed { self.lastBuilt = built; self.reloadMap() }
            }
        }.resume()
    }

    func webView(_ webView: WKWebView, didFinish nav: WKNavigation!) { loadFailed = false }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation nav: WKNavigation!, withError error: Error) {
        loadFailed = true
        webView.loadHTMLString("""
          <body style="background:#0a1119;color:#e6edf3;font:15px -apple-system;display:grid;place-items:center;height:100vh;margin:0">
          <div style="text-align:center"><h2>Can't reach the map</h2><p>Check your internet connection. It will retry every minute.</p></div></body>
          """, baseURL: nil)
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

    @objc func reloadMap() { web.load(URLRequest(url: site, cachePolicy: .reloadIgnoringLocalCacheData)) }

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
