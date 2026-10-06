import AppKit
import WebKit

// Only the shelf returned by our runner and token-shaped editor entry points
// may become application pages. File URLs never enter a WKWebView.
enum WorkbenchURL {
    static func isLoopback(_ url: URL) -> Bool {
        guard let parts = URLComponents(url: url, resolvingAgainstBaseURL: false),
              parts.scheme == "http", parts.host == "127.0.0.1",
              parts.user == nil, parts.password == nil, parts.query == nil,
              let port = parts.port, (1...65535).contains(port),
              parts.percentEncodedPath == parts.path else { return false }
        return true
    }

    static func isShelf(_ url: URL) -> Bool {
        isLoopback(url) && URLComponents(url: url, resolvingAgainstBaseURL: false)?.path == "/"
    }

    static func isEditor(_ url: URL) -> Bool {
        let path = URLComponents(url: url, resolvingAgainstBaseURL: false)?.path ?? ""
        return isLoopback(url) && path.range(of: "^/[A-Za-z0-9_-]{20,128}/$", options: .regularExpression) != nil
    }

    static func samePage(_ first: URL, _ second: URL) -> Bool {
        isLoopback(first) && isLoopback(second) && first.port == second.port
            && URLComponents(url: first, resolvingAgainstBaseURL: false)?.path == URLComponents(url: second, resolvingAgainstBaseURL: false)?.path
    }

    static func isExternalLink(_ url: URL) -> Bool {
        ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "")
            && url.host != "127.0.0.1" && url.host != "localhost" && url.host != "::1"
    }
}

struct WorkbenchLaunchOptions {
    let report: URL?
    let editorURL: URL?
    let switchEditorURL: URL?
    let shelfURL: URL?
    let secondEditorURL: URL?

    static func parse(_ arguments: [String]) throws -> WorkbenchLaunchOptions {
        func value(for flag: String) throws -> String? {
            let matches = arguments.indices.filter { arguments[$0] == flag }
            guard matches.count <= 1 else { throw invalid("自检参数不能重复。") }
            guard let index = matches.first else { return nil }
            guard index + 1 < arguments.count, !arguments[index + 1].hasPrefix("--"),
                  !arguments[index + 1].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                throw invalid("自检参数缺少值。")
            }
            return arguments[index + 1]
        }
        let reportPath = try value(for: "--self-test")
        let editorAddress = try value(for: "--self-test-editor-url")
        let switchAddress = try value(for: "--self-test-switch-editor-url")
        let shelfAddress = try value(for: "--self-test-shelf-url")
        let secondAddress = try value(for: "--self-test-second-editor-url")
        var editor: URL?
        if let editorAddress {
            guard reportPath != nil, let url = URL(string: editorAddress), WorkbenchURL.isEditor(url) else {
                throw invalid("作品自检需要报告路径及可核对的本机作品地址。")
            }
            editor = url
        }
        var switchEditor: URL?, shelf: URL?
        if let switchAddress {
            guard reportPath != nil, shelfAddress != nil, editorAddress == nil, let url = URL(string: switchAddress), WorkbenchURL.isEditor(url) else {
                throw invalid("切换自检需要报告路径及可核对的本机作品地址。")
            }
            switchEditor = url
        }
        if let shelfAddress {
            guard reportPath != nil, editorAddress == nil, let url = URL(string: shelfAddress), WorkbenchURL.isShelf(url) else {
                throw invalid("书架自检需要报告路径及可核对的本机书架地址。")
            }
            shelf = url
        }
        var second: URL?
        if let secondAddress {
            guard switchEditor != nil, let url = URL(string: secondAddress), WorkbenchURL.isEditor(url),
                  !WorkbenchURL.samePage(url, switchEditor!) else { throw invalid("第二作品自检需要不同的本机作品地址。") }
            second = url
        }
        return WorkbenchLaunchOptions(report: reportPath.map { URL(fileURLWithPath: $0).standardizedFileURL }, editorURL: editor,
                                      switchEditorURL: switchEditor, shelfURL: shelf, secondEditorURL: second)
    }

    private static func invalid(_ message: String) -> NSError {
        NSError(domain: "StoryWorkbenchArguments", code: 2, userInfo: [NSLocalizedDescriptionKey: message])
    }
}

enum WorkbenchAppearance {
    static func script(for url: URL, source: String) -> String? {
        guard WorkbenchURL.isEditor(url), let port = url.port,
              let editorPath = URLComponents(url: url, resolvingAgainstBaseURL: false)?.path,
              let encoded = try? JSONSerialization.data(withJSONObject: [editorPath]),
              let path = String(data: encoded, encoding: .utf8) else { return nil }
        return """
        (() => {
            if(window!==window.top || location.protocol!=='http:' || location.hostname!=='127.0.0.1' || Number(location.port)!==\(port) || location.pathname!==\(path)[0])return false;
            const appearance = \(source)
            return appearance?.applied === true;
        })()
        """
    }
}

final class WorkbenchPageCache<Page> {
    private var entries: [(url: URL, page: Page)] = []
    var pages: [Page] { entries.map(\.page) }
    var count: Int { entries.count }

    func resolve(_ url: URL, create: () -> Page) -> Page? {
        guard WorkbenchURL.isEditor(url) else { return nil }
        if let existing = entries.first(where: { WorkbenchURL.samePage($0.url, url) }) { return existing.page }
        let page = create()
        entries.append((url, page))
        return page
    }
}

@MainActor
final class BackendLauncher {
    private var process: Process?
    private var stdout = Data()
    private var stderr = Data()
    private var generation = UUID()
    private var completion: ((Result<URL, Error>, String?) -> Void)?
    private var timeout: Timer?

    func start(_ completion: @escaping (Result<URL, Error>, String?) -> Void) {
        stop()
        self.completion = completion
        stdout = Data()
        stderr = Data()
        let current = UUID()
        generation = current
        guard let resources = Bundle.main.resourceURL else {
            finish(.failure(failure("客户端缺少运行资源，请重新构建应用。")), note: nil)
            return
        }
        let python = resources.appendingPathComponent("runtime/bin/python3")
        let runner = resources.appendingPathComponent("backend_runner.py")
        guard FileManager.default.isExecutableFile(atPath: python.path),
              FileManager.default.fileExists(atPath: runner.path) else {
            finish(.failure(failure("客户端缺少本地运行环境，请重新构建完整应用。")), note: nil)
            return
        }
        let child = Process()
        let output = Pipe(), errors = Pipe()
        child.executableURL = python
        child.arguments = ["-s", "-B", "-X", "utf8", runner.path]
        child.currentDirectoryURL = resources
        child.standardOutput = output
        child.standardError = errors
        child.standardInput = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        for key in ["PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"] { environment.removeValue(forKey: key) }
        environment["PYTHONHOME"] = resources.appendingPathComponent("runtime").path
        environment["PYTHONNOUSERSITE"] = "1"
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        child.environment = environment
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                self.stdout.append(bytes)
                self.readStartupLine()
            }
        }
        errors.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let bytes = handle.availableData
            DispatchQueue.main.async {
                guard let self, self.generation == current else { return }
                // Keep a bounded diagnostic; never put a service's token URL in UI.
                if self.stderr.count < 65536 { self.stderr.append(bytes.prefix(65536 - self.stderr.count)) }
            }
        }
        child.terminationHandler = { [weak self] exited in
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            let finalOutput = output.fileHandleForReading.readDataToEndOfFile()
            let finalError = errors.fileHandleForReading.readDataToEndOfFile()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
                guard let self, self.generation == current else { return }
                self.stdout.append(finalOutput)
                if self.stderr.count < 65536 { self.stderr.append(finalError.prefix(65536 - self.stderr.count)) }
                self.readStartupLine(ended: true)
                if self.completion != nil {
                    self.finish(.failure(self.failure("书架启动未完成，请重试。")), note: nil)
                }
                // A successful runner exits normally after its handshake. The
                // detached shelf and editor services keep serving other windows.
                if self.process === exited { self.process = nil }
            }
        }
        process = child
        do {
            try child.run()
            timeout = Timer.scheduledTimer(withTimeInterval: 45, repeats: false) { [weak self] _ in
                DispatchQueue.main.async {
                    guard let self, self.generation == current, self.completion != nil else { return }
                    self.finish(.failure(self.failure("书架启动等待时间较长。请稍后重试；已打开的作品服务会保留。")), note: nil)
                    self.stop()
                }
            }
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            finish(.failure(failure("无法启动本地书架：" + error.localizedDescription)), note: nil)
        }
    }

    private func readStartupLine(ended: Bool = false) {
        guard completion != nil else { return }
        guard stdout.count <= 65536 else {
            finish(.failure(failure("本地书架返回了无法识别的启动信息。")), note: nil)
            stop()
            return
        }
        let line: Data
        if let newline = stdout.firstIndex(of: 10) {
            line = stdout.prefix(upTo: newline)
        } else if ended && !stdout.isEmpty {
            line = stdout
        } else { return }
        guard let packet = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
              let ok = packet["ok"] as? Bool else {
            finish(.failure(failure("本地书架返回了无法识别的启动信息。")), note: nil)
            return
        }
        guard ok else {
            finish(.failure(failure(packet["message"] as? String ?? "书架启动失败，请重试。")), note: nil)
            return
        }
        guard let address = packet["url"] as? String, let url = URL(string: address), WorkbenchURL.isShelf(url) else {
            finish(.failure(failure("书架地址无法核对，请重新启动本地书架。")), note: nil)
            return
        }
        let note = packet["outdated"] as? Bool == true
            ? "当前书架仍在使用较早代码。请先保存其他页面的编辑，再按照工作台说明重启服务。"
            : nil
        finish(.success(url), note: note)
    }

    private func failure(_ text: String) -> NSError {
        NSError(domain: "StoryWorkbench", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }

    private func finish(_ result: Result<URL, Error>, note: String?) {
        timeout?.invalidate()
        timeout = nil
        let callback = completion
        completion = nil
        callback?(result, note)
    }

    func stop() {
        generation = UUID()
        timeout?.invalidate()
        timeout = nil
        completion = nil
        if let child = process, child.isRunning { child.terminate() }
        process = nil
    }
}

@MainActor
// A page session owns its WebKit process and buffers; it never owns a second
// native window. Switching the host's content view keeps this session alive.
final class WorkbenchWindow: NSWindowController, NSToolbarDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    let identifier = UUID()
    let webView: WKWebView
    weak var app: WorkbenchApp?
    private(set) var entryURL: URL?
    private var titleObservation: NSKeyValueObservation?
    private var loadedPage = false
    private(set) var loadCount = 0
    private(set) var navigationCount = 0
    var isReady: Bool { isControlledPage }
    private(set) var appearanceApplied = false
    let pageContent = NSView()
    private let statusView = NSView()
    private let statusTitle = NSTextField(labelWithString: "正在打开写作书架")
    private let statusDetail = NSTextField(wrappingLabelWithString: "正在连接本机工作台，已有作品和编辑会继续保留。")
    private let retryButton = NSButton(title: "重试", target: nil, action: nil)
    private let saveButton = NSButton(title: "保存候选稿", target: nil, action: nil)
    private let addBookButton = NSButton(title: "添加作品…", target: nil, action: nil)
    private var downloads: [ObjectIdentifier: URL] = [:]
    private static let homeItem = NSToolbarItem.Identifier("workbench.home")
    private static let addBookItem = NSToolbarItem.Identifier("workbench.add-book")

    init(app: WorkbenchApp, window: NSWindow, entryURL: URL? = nil) {
        self.app = app
        self.entryURL = entryURL
        let settings = WKWebViewConfiguration()
        settings.websiteDataStore = .default()
        // No file-access preferences, native script bridge, or injected write API.
        webView = WKWebView(frame: .zero, configuration: settings)
        super.init(window: window)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = false
        buildContent()
        titleObservation = webView.observe(\.title, options: [.new]) { [weak self] view, _ in
            guard let title = view.title, !title.isEmpty else { return }
            DispatchQueue.main.async { [weak self] in
                guard let self, self.app?.activePage === self else { return }
                self.window?.title = title
            }
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }

    private func buildContent() {
        saveButton.target = self
        saveButton.action = #selector(saveCandidate)
        saveButton.isHidden = entryURL == nil || !WorkbenchURL.isEditor(entryURL!)
        addBookButton.target = self
        addBookButton.action = #selector(chooseBookDirectory)
        addBookButton.isHidden = entryURL != nil && !WorkbenchURL.isShelf(entryURL!)
        let root = pageContent
        for view in [webView, statusView] { view.translatesAutoresizingMaskIntoConstraints = false; root.addSubview(view) }
        NSLayoutConstraint.activate([
            webView.topAnchor.constraint(equalTo: root.topAnchor),
            webView.leadingAnchor.constraint(equalTo: root.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: root.trailingAnchor),
            webView.bottomAnchor.constraint(equalTo: root.bottomAnchor),
            statusView.leadingAnchor.constraint(equalTo: webView.leadingAnchor),
            statusView.trailingAnchor.constraint(equalTo: webView.trailingAnchor),
            statusView.topAnchor.constraint(equalTo: webView.topAnchor),
            statusView.bottomAnchor.constraint(equalTo: webView.bottomAnchor),
        ])
        statusView.wantsLayer = true
        statusView.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
        statusTitle.font = .systemFont(ofSize: 24, weight: .semibold)
        statusDetail.font = .systemFont(ofSize: 15)
        statusDetail.textColor = .secondaryLabelColor
        retryButton.target = self
        retryButton.action = #selector(retryStartup)
        let status = NSStackView(views: [statusTitle, statusDetail, retryButton])
        status.orientation = .vertical
        status.alignment = .leading
        status.spacing = 18
        status.translatesAutoresizingMaskIntoConstraints = false
        statusView.addSubview(status)
        NSLayoutConstraint.activate([
            status.centerXAnchor.constraint(equalTo: statusView.centerXAnchor),
            status.centerYAnchor.constraint(equalTo: statusView.centerYAnchor),
            status.widthAnchor.constraint(lessThanOrEqualToConstant: 560),
            status.leadingAnchor.constraint(greaterThanOrEqualTo: statusView.leadingAnchor, constant: 32),
            status.trailingAnchor.constraint(lessThanOrEqualTo: statusView.trailingAnchor, constant: -32),
        ])
        retryButton.isHidden = true
    }

    func present(in window: NSWindow) {
        window.contentView = pageContent
        window.title = webView.title?.isEmpty == false ? webView.title! : (entryURL.map(WorkbenchURL.isEditor) == true ? "作品 · 写作工作台" : "写作书架")
        configureToolbar(in: window)
        window.makeFirstResponder(webView)
    }

    private func configureToolbar(in window: NSWindow) {
        let toolbar = NSToolbar(identifier: "workbench.navigation")
        toolbar.delegate = self
        toolbar.displayMode = .iconOnly
        toolbar.allowsUserCustomization = false
        toolbar.autosavesConfiguration = false
        window.toolbarStyle = .unifiedCompact
        window.toolbar = toolbar
    }

    func toolbarAllowedItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        [Self.homeItem, Self.addBookItem]
    }

    func toolbarDefaultItemIdentifiers(_ toolbar: NSToolbar) -> [NSToolbarItem.Identifier] {
        entryURL == nil || WorkbenchURL.isShelf(entryURL!) ? [Self.homeItem, Self.addBookItem] : [Self.homeItem]
    }

    func toolbar(_ toolbar: NSToolbar, itemForItemIdentifier identifier: NSToolbarItem.Identifier, willBeInsertedIntoToolbar flag: Bool) -> NSToolbarItem? {
        let item = NSToolbarItem(itemIdentifier: identifier)
        item.target = self
        if identifier == Self.homeItem {
            item.label = "回书架"
            item.toolTip = "回书架（⌘1），当前作品编辑继续保留"
            item.image = NSImage(systemSymbolName: "books.vertical", accessibilityDescription: "回书架")
            item.action = #selector(goHome)
        } else if identifier == Self.addBookItem {
            item.label = "添加作品…"
            item.toolTip = "选择已有作品目录"
            item.image = NSImage(systemSymbolName: "folder.badge.plus", accessibilityDescription: "添加作品")
            item.action = #selector(chooseBookDirectory)
        } else { return nil }
        return item
    }

    var nativeToolbarIDs: [String] { window?.toolbar?.items.map { $0.itemIdentifier.rawValue } ?? [] }

    func showStatus(title: String, detail: String, retry: Bool) {
        statusTitle.stringValue = title
        statusDetail.stringValue = detail
        retryButton.isHidden = !retry
        statusView.isHidden = false
    }

    func load(_ url: URL) {
        entryURL = url
        loadedPage = false
        loadCount += 1
        appearanceApplied = false
        saveButton.isHidden = !WorkbenchURL.isEditor(url)
        addBookButton.isHidden = !WorkbenchURL.isShelf(url)
        if app?.activePage === self, let window { configureToolbar(in: window) }
        showStatus(title: WorkbenchURL.isShelf(url) ? "正在打开写作书架" : "正在打开作品", detail: "正在连接本机工作台…", retry: false)
        webView.load(URLRequest(url: url))
    }

    private var isControlledPage: Bool {
        guard let entryURL, let url = webView.url else { return false }
        return loadedPage && WorkbenchURL.samePage(url, entryURL)
    }

    func confirmLeaving(action: String, completion: @escaping (Bool) -> Void) {
        guard let entryURL, let url = webView.url, WorkbenchURL.samePage(url, entryURL) else { completion(true); return }
        // The source page keeps every opened buffer in docs, including inactive
        // documents. Automatic recovery is deliberately not treated as a save.
        let script = "typeof docs !== 'undefined' && [...docs.values()].some(d => d && (d.saving || (d.editable && d.value !== undefined && d.value !== d.text)))"
        webView.evaluateJavaScript(script) { [weak self] result, error in
            guard let self else { completion(false); return }
            if error == nil && (result as? Bool) == false { completion(true); return }
            self.app?.activate(self)
            let alert = NSAlert()
            alert.messageText = error == nil ? "还有未保存的编辑" : "暂时无法核对编辑状态"
            alert.informativeText = error == nil
                ? "这部作品包含未保存的文字。请返回保存候选稿或下载文字，再\(action)。自动恢复稿不等于已保存候选。"
                : "为保留当前文字，建议返回作品检查并保存后再\(action)。"
            alert.alertStyle = .warning
            alert.addButton(withTitle: "返回作品")
            alert.addButton(withTitle: "仍然\(action)")
            if let window = self.window {
                alert.beginSheetModal(for: window) { completion($0 == .alertSecondButtonReturn) }
            } else { completion(false) }
        }
    }

    @objc func goHome() { app?.showShelf() }

    @objc func refreshPage() {
        if !statusView.isHidden { retryStartup(); return }
        // Refresh the directory or shelf through its existing safe action. Page
        // reload is only a fallback, and needs an unsaved-buffer check.
        guard isControlledPage else { return }
        webView.evaluateJavaScript("(() => { const button=document.getElementById('refresh'); if(button){button.click();return true;}return false; })()") { [weak self] result, error in
            guard let self else { return }
            if error == nil && (result as? Bool) == true { return }
            self.reloadPage()
        }
    }

    @objc func reloadPage() {
        confirmLeaving(action: "重新载入") { [weak self] allowed in
            guard allowed, let self, let url = self.entryURL else { return }
            self.load(url)
        }
    }

    @objc func saveCandidate() {
        guard isControlledPage else { return }
        webView.evaluateJavaScript("(() => { const button=document.getElementById('save'); if(button&&!button.disabled)button.click(); })()", completionHandler: nil)
    }

    @objc func updateAppearance() {
        applyEditorAppearance { [weak self] applied in
            guard !applied, let self, let window = self.window else { return }
            let alert = NSAlert()
            alert.messageText = "界面外观暂未更新"
            alert.informativeText = "当前页面与编辑仍保留。请核对客户端更新后再试。"
            alert.beginSheetModal(for: window, completionHandler: nil)
        }
    }

    private func applyEditorAppearance(completion: ((Bool) -> Void)? = nil) {
        guard isControlledPage, let entryURL, let current = webView.url,
              WorkbenchURL.isEditor(entryURL), WorkbenchURL.isEditor(current),
              WorkbenchURL.samePage(current, entryURL),
              let resource = Bundle.main.resourceURL?.appendingPathComponent("editor-appearance.js"),
              let source = try? String(contentsOf: resource, encoding: .utf8),
              let script = WorkbenchAppearance.script(for: entryURL, source: source) else { completion?(false); return }
        webView.evaluateJavaScript(script) { [weak self] result, error in
            guard let self, self.isControlledPage, let current = self.webView.url,
                  let entry = self.entryURL, WorkbenchURL.samePage(current, entry),
                  WorkbenchURL.samePage(entry, entryURL) else { completion?(false); return }
            let applied = error == nil && (result as? Bool) == true
            self.appearanceApplied = applied
            completion?(applied)
        }
    }

    private func adaptBookHint() {
        guard isControlledPage, let entryURL, let port = entryURL.port,
              let bytes = try? JSONSerialization.data(withJSONObject: [entryURL.path]),
              let path = String(data: bytes, encoding: .utf8) else { return }
        let script = """
        (() => {
            if(window!==window.top || location.protocol!=='http:' || location.hostname!=='127.0.0.1' || Number(location.port)!==\(port) || location.pathname!==\(path)[0])return;
            const hint=document.getElementById('book-hint');
            if(!hint || hint.dataset.nativeHint==='true')return;
            hint.dataset.nativeHint='true';
            const sync=()=>{if(hint.children.length===0 && hint.textContent==='作品会在新标签页打开，当前编辑保留。')hint.textContent='作品在当前界面切换，已打开作品的编辑保留。';};
            new MutationObserver(sync).observe(hint,{childList:true,subtree:true,characterData:true});
            sync();
        })()
        """
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    @objc private func chooseBookDirectory() {
        guard isControlledPage, let entryURL, WorkbenchURL.isShelf(entryURL), let window else { return }
        let panel = NSOpenPanel()
        panel.title = "选择已有作品的目录"
        panel.prompt = "添加作品"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = false
        panel.allowsMultipleSelection = false
        panel.beginSheetModal(for: window) { [weak self] answer in
            guard answer == .OK, let url = panel.url, let self,
                  self.isControlledPage, let entry = self.entryURL, WorkbenchURL.isShelf(entry),
                  let port = entry.port,
                  let bytes = try? JSONSerialization.data(withJSONObject: [url.path]),
                  let encodedPath = String(data: bytes, encoding: .utf8) else { return }
            // The selected path is data, not executable text. Existing page/API
            // code still performs registration and book identity validation.
            let script = """
            (() => {
                if(window!==window.top || location.protocol!=='http:' || location.hostname!=='127.0.0.1' || location.pathname!=='/' || Number(location.port)!==\(port))return false;
                const input=document.getElementById('book-path'),button=document.getElementById('add');
                if(!input || !button || button.disabled)return false;
                const panel=document.getElementById('add-book-panel');
                if(panel)panel.open=true;
                input.value=\(encodedPath)[0];
                button.click();
                return true;
            })()
            """
            self.webView.evaluateJavaScript(script, completionHandler: nil)
        }
    }

    @objc private func retryStartup() {
        if let entryURL, WorkbenchURL.isEditor(entryURL) { reloadPage() }
        else { app?.startShelf() }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if navigationAction.shouldPerformDownload && isControlledPage && navigationAction.sourceFrame.isMainFrame && url.scheme == "blob" {
            decisionHandler(.download)
            return
        }
        if navigationAction.targetFrame == nil && isControlledPage && navigationAction.sourceFrame.isMainFrame && WorkbenchURL.isEditor(url) {
            app?.showEditor(url)
            decisionHandler(.cancel)
            return
        }
        if let entryURL, WorkbenchURL.samePage(url, entryURL) {
            // Same-document anchors preserve buffers and do not leave the page.
            if navigationAction.navigationType == .linkActivated && url.fragment == nil && isControlledPage {
                decisionHandler(.cancel)
                reloadPage()
            } else { decisionHandler(.allow) }
            return
        }
        if navigationAction.navigationType == .linkActivated && WorkbenchURL.isExternalLink(url) {
            NSWorkspace.shared.open(url)
        }
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        guard let url = navigationResponse.response.url,
              url.scheme == "blob" || (entryURL != nil && WorkbenchURL.samePage(url, entryURL!)) else {
            decisionHandler(.cancel)
            return
        }
        if !navigationResponse.canShowMIMEType { decisionHandler(.download) }
        else { decisionHandler(.allow) }
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        guard isControlledPage, navigationAction.sourceFrame.isMainFrame, let url = navigationAction.request.url, WorkbenchURL.isEditor(url) else { return nil }
        app?.showEditor(url)
        return nil
    }

    func webViewDidClose(_ webView: WKWebView) { if app?.activePage === self { app?.showShelf() } }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        navigationCount += 1
        loadedPage = true
        statusView.isHidden = true
        if let entryURL, WorkbenchURL.isEditor(entryURL) {
            applyEditorAppearance()
            adaptBookHint()
            return
        }
        guard let entryURL, let current = webView.url, let port = entryURL.port,
              WorkbenchURL.isShelf(current), WorkbenchURL.samePage(current, entryURL) else { return }
        // Adapt only the controlled main-frame shelf. Browser-served HTML and
        // every editor window retain their existing behavior and instructions.
        let script = """
        (() => {
            if(window!==window.top || location.protocol!=='http:' || location.hostname!=='127.0.0.1' || location.pathname!=='/' || Number(location.port)!==\(port))return;
            const paragraphs=document.querySelectorAll('body > p');
            const intro=document.getElementById('shelf-intro') || (paragraphs.length>1?paragraphs[0]:null);
            const footer=document.getElementById('shelf-footer') || (paragraphs.length>1?paragraphs[paragraphs.length-1]:null);
            if(intro)intro.textContent='从书架选择作品，在客户端中继续创作。';
            if(footer)footer.textContent='书架和作品在同一个窗口切换，已打开作品的编辑继续保留。退出客户端后，本机书架和作品服务仍会保留。';
        })()
        """
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failedNavigation(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failedNavigation(error) }

    private func failedNavigation(_ error: Error) {
        let detail = error as NSError
        if detail.domain == NSURLErrorDomain && detail.code == NSURLErrorCancelled { return }
        if detail.domain == WKError.errorDomain && detail.code == 102 { return }
        if isControlledPage && statusView.isHidden {
            app?.activate(self)
            let alert = NSAlert()
            alert.messageText = "页面未能重新载入"
            alert.informativeText = "当前页面和编辑会保留。请先保存文字，再重试。"
            if let window { alert.beginSheetModal(for: window, completionHandler: nil) }
            return
        }
        showStatus(title: "无法打开本地工作台", detail: "本机服务暂时无法连接。请重试；其他已打开作品的编辑会保留。", retry: true)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        loadedPage = false
        showStatus(title: "作品页面意外中断", detail: "请重新打开页面，核对候选稿与自动恢复稿。尚未写入文件的文字可能需要重新输入。", retry: true)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        app?.activate(self)
        let alert = NSAlert()
        alert.messageText = "本地工作台"
        alert.informativeText = message
        alert.addButton(withTitle: "好")
        if let window { alert.beginSheetModal(for: window) { _ in completionHandler() } }
        else { completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        app?.activate(self)
        let alert = NSAlert()
        alert.messageText = "本地工作台"
        alert.informativeText = message
        alert.addButton(withTitle: "继续")
        alert.addButton(withTitle: "取消")
        if let window { alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) } }
        else { completionHandler(false) }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        guard let window else { completionHandler(nil); return }
        app?.activate(self)
        let panel = NSSavePanel()
        panel.title = "下载当前文字"
        panel.prompt = "保存"
        panel.nameFieldStringValue = (suggestedFilename as NSString).lastPathComponent
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] answer in
            guard answer == .OK, let url = panel.url else { completionHandler(nil); return }
            // WebKit requires a nonexisting target. Let the save panel retain its
            // normal replacement confirmation, then use a fresh temporary file.
            let staging = url.deletingLastPathComponent().appendingPathComponent(".story-download-" + UUID().uuidString)
            self?.downloads[ObjectIdentifier(download)] = url
            self?.downloadStaging[ObjectIdentifier(download)] = staging
            completionHandler(staging)
        }
    }

    private var downloadStaging: [ObjectIdentifier: URL] = [:]

    func downloadDidFinish(_ download: WKDownload) {
        let key = ObjectIdentifier(download)
        guard let destination = downloads.removeValue(forKey: key), let staging = downloadStaging.removeValue(forKey: key) else { return }
        do {
            if FileManager.default.fileExists(atPath: destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt: staging)
            } else { try FileManager.default.moveItem(at: staging, to: destination) }
        } catch {
            showDownloadError("下载文件已保留在所选目录中的临时文件，请核对。" + error.localizedDescription)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        let key = ObjectIdentifier(download)
        downloads.removeValue(forKey: key)
        if let staging = downloadStaging.removeValue(forKey: key) { try? FileManager.default.removeItem(at: staging) }
        showDownloadError("下载未完成，当前文字仍保留在作品窗口中。" + error.localizedDescription)
    }

    private func showDownloadError(_ text: String) {
        app?.activate(self)
        let alert = NSAlert()
        alert.messageText = "请检查下载"
        alert.informativeText = text
        if let window { alert.beginSheetModal(for: window, completionHandler: nil) }
    }
}

@MainActor
final class WorkbenchApp: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let launcher = BackendLauncher()
    private let editorPages = WorkbenchPageCache<WorkbenchWindow>()
    private(set) var mainWindow: NSWindow?
    private(set) var activePage: WorkbenchWindow?
    private var shelf: WorkbenchWindow?
    private var shelfURL: URL?
    private var checkingQuit = false
    private var checkingClose = false
    private var allowClose = false
    private let selfTestReport: URL?
    private let selfTestEditorURL: URL?
    private let selfTestSwitchEditorURL: URL?
    private let selfTestShelfURL: URL?
    private let selfTestSecondEditorURL: URL?
    private var selfTestEditor: WorkbenchWindow?

    init(selfTestReport: URL? = nil, selfTestEditorURL: URL? = nil,
         selfTestSwitchEditorURL: URL? = nil, selfTestShelfURL: URL? = nil, selfTestSecondEditorURL: URL? = nil) {
        self.selfTestReport = selfTestReport
        self.selfTestEditorURL = selfTestEditorURL
        self.selfTestSwitchEditorURL = selfTestSwitchEditorURL
        self.selfTestShelfURL = selfTestShelfURL
        self.selfTestSecondEditorURL = selfTestSecondEditorURL
        super.init()
    }

    var cachedPageCount: Int { editorPages.count + (shelf == nil ? 0 : 1) }
    var cachedPages: [WorkbenchWindow] { (shelf.map { [$0] } ?? []) + editorPages.pages }

    private func hostWindow() -> NSWindow {
        if let mainWindow { return mainWindow }
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 840),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable],
                              backing: .buffered, defer: false)
        window.title = "写作工作台"
        window.minSize = NSSize(width: 760, height: 540)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        mainWindow = window
        return window
    }

    func activate(_ page: WorkbenchWindow) {
        let window = hostWindow()
        let switching = activePage !== page || window.contentView !== page.pageContent
        activePage = page
        if switching { page.present(in: window) }
        if selfTestReport == nil { window.makeKeyAndOrderFront(nil) }
        else { window.orderBack(nil) }
    }

    func showActiveWindow() {
        if let activePage { activate(activePage) }
        else { showShelf() }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        if let selfTestEditorURL {
            selfTestEditor = showEditor(selfTestEditorURL)
            collectSelfTest(attempt: 0)
            return
        }
        if let selfTestShelfURL { shelfURL = selfTestShelfURL }
        showShelf()
        if selfTestShelfURL == nil { startShelf() }
        else { collectSelfTest(attempt: 0) }
        if selfTestReport == nil { NSApp.activate(ignoringOtherApps: true) }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        guard selfTestReport == nil else { return false }
        showActiveWindow()
        return true
    }

    func showShelf() {
        if let shelf {
            activate(shelf)
            return
        }
        let controller = WorkbenchWindow(app: self, window: hostWindow(), entryURL: shelfURL)
        shelf = controller
        activate(controller)
        if let shelfURL { controller.load(shelfURL) }
    }

    func startShelf() {
        guard selfTestEditorURL == nil, selfTestShelfURL == nil else { return }
        showShelf()
        shelf?.showStatus(title: "正在打开写作书架", detail: "正在连接本机工作台，已有作品和编辑会继续保留。", retry: false)
        launcher.start { [weak self] result, note in
            guard let self else { return }
            switch result {
            case .success(let url):
                self.shelfURL = url
                self.shelf?.load(url)
                if self.selfTestReport != nil { self.collectSelfTest(attempt: 0) }
                else if let note, let window = self.shelf?.window {
                    let alert = NSAlert()
                    alert.messageText = "书架服务需要更新"
                    alert.informativeText = note
                    alert.beginSheetModal(for: window, completionHandler: nil)
                }
            case .failure(let error):
                self.shelf?.showStatus(title: "书架暂时无法打开", detail: error.localizedDescription, retry: true)
                if self.selfTestReport != nil { self.finishSelfTest(["ok": false, "error": error.localizedDescription]) }
            }
        }
    }

    @discardableResult
    func showEditor(_ url: URL) -> WorkbenchWindow? {
        guard WorkbenchURL.isEditor(url) else { return nil }
        let window = hostWindow()
        guard let page = editorPages.resolve(url, create: {
            let page = WorkbenchWindow(app: self, window: window, entryURL: url)
            page.load(url)
            return page
        }) else { return nil }
        activate(page)
        return page
    }

    private var currentWindow: WorkbenchWindow? { activePage }

    @objc private func home() { showShelf() }
    @objc private func refresh() { currentWindow?.refreshPage() }
    @objc private func reload() { currentWindow?.reloadPage() }
    @objc private func save() { currentWindow?.saveCandidate() }
    @objc private func appearance() { currentWindow?.updateAppearance() }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard !checkingQuit, !checkingClose else { return .terminateCancel }
        if selfTestReport != nil { launcher.stop(); return .terminateNow }
        checkingQuit = true
        let controllers = cachedPages
        DispatchQueue.main.async { [weak self] in
            self?.confirmAll(controllers, action: "退出客户端", index: 0) { allowed in
                self?.checkingQuit = false
                NSApp.reply(toApplicationShouldTerminate: allowed)
            }
        }
        return .terminateLater
    }

    private func confirmAll(_ controllers: [WorkbenchWindow], action: String, index: Int,
                            completion: @escaping (Bool) -> Void) {
        guard index < controllers.count else { completion(true); return }
        controllers[index].confirmLeaving(action: action) { [weak self] allowed in
            guard let self else { return }
            if allowed { self.confirmAll(controllers, action: action, index: index + 1, completion: completion) }
            else { completion(false) }
        }
    }

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        if allowClose { return true }
        guard !checkingClose, !checkingQuit else { return false }
        checkingClose = true
        confirmAll(cachedPages, action: "关闭窗口", index: 0) { [weak self] allowed in
            guard let self else { return }
            self.checkingClose = false
            if allowed { self.allowClose = true; self.mainWindow?.performClose(nil) }
        }
        return false
    }

    func windowWillClose(_ notification: Notification) {
        // Closing hides the one host. A Dock reopen reattaches its active page;
        // retaining every page also preserves buffers if the user returns.
        allowClose = false
    }

    func applicationWillTerminate(_ notification: Notification) {
        launcher.stop()
    }

    private func installMenu() {
        let main = NSMenu()
        let application = NSMenu(title: "写作工作台")
        application.addItem(withTitle: "关于写作工作台", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        application.addItem(.separator())
        application.addItem(withTitle: "隐藏写作工作台", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        application.addItem(withTitle: "退出写作工作台", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        addMenu(application, to: main)
        let file = NSMenu(title: "作品")
        for (title, action, key) in [("回书架", #selector(home), "1"), ("刷新列表", #selector(refresh), "r"), ("保存候选稿", #selector(save), "s")] {
            let item = file.addItem(withTitle: title, action: action, keyEquivalent: key)
            item.target = self
        }
        let appearanceItem = file.addItem(withTitle: "更新界面外观", action: #selector(appearance), keyEquivalent: "")
        appearanceItem.target = self
        let reloadItem = file.addItem(withTitle: "重新载入页面…", action: #selector(reload), keyEquivalent: "r")
        reloadItem.keyEquivalentModifierMask = [.command, .shift]
        reloadItem.target = self
        file.addItem(.separator())
        file.addItem(withTitle: "关闭窗口", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        addMenu(file, to: main)
        let edit = NSMenu(title: "编辑")
        for (title, selector, key) in [("撤销", "undo:", "z"), ("剪切", "cut:", "x"), ("复制", "copy:", "c"), ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(selector), keyEquivalent: key)
        }
        let redo = edit.insertItem(withTitle: "重做", action: Selector(("redo:")), keyEquivalent: "z", at: 1)
        redo.keyEquivalentModifierMask = [.command, .shift]
        addMenu(edit, to: main)
        let window = NSMenu(title: "窗口")
        window.addItem(withTitle: "最小化", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        window.addItem(withTitle: "显示所有窗口", action: #selector(NSApplication.arrangeInFront(_:)), keyEquivalent: "")
        addMenu(window, to: main)
        NSApp.windowsMenu = window
        NSApp.mainMenu = main
    }

    private func addMenu(_ menu: NSMenu, to main: NSMenu) {
        let item = NSMenuItem()
        item.submenu = menu
        main.addItem(item)
    }

    private struct SwitchProbe {
        let shelf: WorkbenchWindow
        let first: WorkbenchWindow
        let shelfState: String
        let firstState: String
        let firstLoads: Int
        let shelfLoads: Int
        let firstNavigations: Int
        let shelfNavigations: Int
    }

    // Only explicit developer URLs reach this branch. It clicks temporary
    // target=_blank links, but never editing/saving/reload controls or file APIs.
    private func clickEditorLink(from page: WorkbenchWindow, to url: URL, completion: @escaping (Bool) -> Void) {
        guard WorkbenchURL.isEditor(url), let data = try? JSONSerialization.data(withJSONObject: [url.absoluteString]),
              let encoded = String(data: data, encoding: .utf8) else { completion(false); return }
        page.webView.evaluateJavaScript("(() => {const a=document.createElement('a');a.href=\(encoded)[0];a.target='_blank';a.rel='noopener';document.body.append(a);a.click();a.remove();return true;})()") { result, error in
            completion(error == nil && (result as? Bool) == true)
        }
    }

    private func readSessionState(_ page: WorkbenchWindow, completion: @escaping (String?) -> Void) {
        let script = """
        (() => {
            if(document.readyState!=='complete')return null;
            if(window.__singleWindowFixtureProbe && !window.__singleWindowFixtureProbe.ready)return null;
            const title=document.getElementById('title'),progress=document.getElementById('book-progress');
            if(title?.textContent==='选择章节或材料' || progress?.textContent.includes('正在读取'))return null;
            const text=document.getElementById('text');
            return JSON.stringify({
                bookTitle:document.getElementById('book-title')?.textContent,
                documents:typeof docs==='undefined'?null:[...docs].map(([id,d])=>[id,{text:d.text,value:d.value,editable:d.editable,editing:d.editing,saving:!!d.saving}]),
                active:typeof active==='undefined'?null:active,
                textarea:text?{value:text.value,start:text.selectionStart,end:text.selectionEnd,scroll:text.scrollTop}:null,
                selection:window.getSelection()?.toString(),mainScroll:document.querySelector('main')?.scrollTop,
                bodyScroll:document.scrollingElement?.scrollTop,
                filters:['shelf-search','shelf-order','shelf-status','search','chapter-version'].map(id=>[id,document.getElementById(id)?.value])
            });
        })()
        """
        page.webView.evaluateJavaScript(script) { value, error in completion(error == nil ? value as? String : nil) }
    }

    private func waitForEditor(_ url: URL, attempt: Int = 0, completion: @escaping (WorkbenchWindow, String) -> Void) {
        guard attempt < 100 else { finishSelfTest(["ok": false, "error": "等待单窗口作品切换超时"]); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { [weak self] in
            guard let self else { return }
            guard let page = self.activePage, let entry = page.entryURL, WorkbenchURL.samePage(entry, url),
                  page.isReady, page.appearanceApplied else {
                self.waitForEditor(url, attempt: attempt + 1, completion: completion); return
            }
            self.readSessionState(page) { [weak self] state in
                guard let self else { return }
                if let state { completion(page, state) }
                else { self.waitForEditor(url, attempt: attempt + 1, completion: completion) }
            }
        }
    }

    private func beginSwitchSelfTest(_ shelf: WorkbenchWindow) {
        guard let url = selfTestSwitchEditorURL else { return }
        readSessionState(shelf) { [weak self] shelfState in
            guard let self, let shelfState else { self?.finishSelfTest(["ok": false, "error": "无法核对书架状态"]); return }
            self.clickEditorLink(from: shelf, to: url) { [weak self] clicked in
                guard let self, clicked else { self?.finishSelfTest(["ok": false, "error": "书架作品链接未能打开"]); return }
                self.waitForEditor(url) { [weak self] first, state in
                    guard let self else { return }
                    let probe = SwitchProbe(shelf: shelf, first: first, shelfState: shelfState, firstState: state,
                                            firstLoads: first.loadCount, shelfLoads: shelf.loadCount,
                                            firstNavigations: first.navigationCount, shelfNavigations: shelf.navigationCount)
                    self.showShelf()
                    if let secondURL = self.selfTestSecondEditorURL {
                        self.clickEditorLink(from: shelf, to: secondURL) { [weak self] clicked in
                            guard let self, clicked else { self?.finishSelfTest(["ok": false, "error": "第二作品链接未能打开"]); return }
                            self.waitForEditor(secondURL) { [weak self] second, state in
                                self?.returnToFirst(probe, second: second, secondState: state)
                            }
                        }
                    } else { self.returnToFirst(probe, second: nil, secondState: nil) }
                }
            }
        }
    }

    private func returnToFirst(_ probe: SwitchProbe, second: WorkbenchWindow?, secondState: String?) {
        let secondNavigations = second?.navigationCount
        guard let url = selfTestSwitchEditorURL, let source = activePage else { return }
        clickEditorLink(from: source, to: url) { [weak self] clicked in
            guard let self, clicked else { self?.finishSelfTest(["ok": false, "error": "重复打开作品链接失败"]); return }
            self.waitForEditor(url) { [weak self] repeated, _ in
                guard let self else { return }
                var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
                parts.fragment = "self-test-repeat"
                let hashPage = self.showEditor(parts.url!)
                self.showShelf()
                self.readSessionState(probe.shelf) { [weak self] shelfAfter in
                    guard let self else { return }
                    let final = self.showEditor(url)
                    self.readSessionState(probe.first) { [weak self] firstAfter in
                        guard let self else { return }
                        let finish: (Bool) -> Void = { [weak self] secondPreserved in
                            guard let self else { return }
                            // AppKit also owns invisible input-system windows. Keep
                            // their inventory, while checking every product window
                            // and any unexpected visible non-panel window separately.
                            let productWindows = NSApp.windows.filter { $0.delegate === self || $0.windowController is WorkbenchWindow }
                            let count = productWindows.count
                            let unexpectedVisibleWindows = NSApp.windows.filter { $0.isVisible && !($0 is NSPanel) && !productWindows.contains($0) }.count
                            let sharedWindow = self.cachedPages.allSatisfy { $0.window === self.mainWindow }
                            let expectedPageCount = second == nil ? 2 : 3
                            let reused = repeated === probe.first && hashPage === probe.first && final === probe.first
                            let preserved = firstAfter == probe.firstState && shelfAfter == probe.shelfState && secondPreserved
                            let loadsStable = probe.first.loadCount == probe.firstLoads && probe.shelf.loadCount == probe.shelfLoads
                            let navigationsStable = probe.first.navigationCount == probe.firstNavigations && probe.shelf.navigationCount == probe.shelfNavigations && second?.navigationCount == secondNavigations
                            let original = self.stateSummary(probe.firstState), restored = self.stateSummary(firstAfter)
                            self.captureSelfTest(probe.first, packet: ["ok": count == 1 && unexpectedVisibleWindows == 0 && sharedWindow && self.cachedPageCount == expectedPageCount && reused && preserved && loadsStable && navigationsStable,
                                "nativeWindowCount": count, "cachedPageCount": self.cachedPageCount,
                                "rawAppKitWindowCount": NSApp.windows.count, "unexpectedVisibleWindowCount": unexpectedVisibleWindows,
                                "windowInventory": NSApp.windows.map { ["class": String(describing: type(of: $0)), "visible": $0.isVisible, "isMainHost": $0 === self.mainWindow, "isPanel": $0 is NSPanel, "title": $0.title] as [String: Any] },
                                "popupNavigation": true, "sameEditorPage": reused, "sameEditorWebView": final?.webView === probe.first.webView,
                                "editorStatePreserved": firstAfter == probe.firstState, "shelfStatePreserved": shelfAfter == probe.shelfState,
                                "secondEditorStatePreserved": secondPreserved, "secondEditorChecked": second != nil,
                                "editorLoadCount": probe.first.loadCount, "shelfLoadCount": probe.shelf.loadCount,
                                "secondEditorLoadCount": second?.loadCount ?? 0,
                                "editorNavigationCount": probe.first.navigationCount,
                                "shelfNavigationCount": probe.shelf.navigationCount,
                                "secondEditorNavigationCount": second?.navigationCount ?? 0,
                                "navigationsUnchanged": navigationsStable,
                                "pageIDs": ["shelf": probe.shelf.identifier.uuidString, "first": probe.first.identifier.uuidString, "second": second?.identifier.uuidString ?? ""],
                                "dirtyBufferCount": original["dirtyBufferCount"] ?? 0, "pendingSaveCount": original["pendingSaveCount"] ?? 0,
                                "selectionBefore": original["selection"] ?? [], "selectionAfter": restored["selection"] ?? [],
                                "textareaValuePreserved": original["value"] as? String == restored["value"] as? String,
                                "activeBookTitle": restored["bookTitle"] ?? "",
                                "loadsUnchanged": loadsStable, "activePageIsFirstEditor": self.activePage === probe.first,
                                "allPagesShareMainWindow": sharedWindow])
                        }
                        if let second { self.readSessionState(second) { finish($0 == secondState) } }
                        else { finish(true) }
                    }
                }
            }
        }
    }

    private func stateSummary(_ state: String?) -> [String: Any] {
        guard let state, let data = state.data(using: .utf8),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        let records = (value["documents"] as? [[Any]] ?? []).compactMap { $0.count == 2 ? $0[1] as? [String: Any] : nil }
        let text = value["textarea"] as? [String: Any] ?? [:]
        return ["dirtyBufferCount": records.filter { ($0["editable"] as? Bool) == true && $0["value"] as? String != nil && $0["value"] as? String != $0["text"] as? String }.count,
                "pendingSaveCount": records.filter { ($0["saving"] as? Bool) == true }.count,
                "selection": [text["start"] as? Int ?? 0, text["end"] as? Int ?? 0],
                "value": text["value"] as? String ?? "", "bookTitle": value["bookTitle"] as? String ?? ""]
    }

    // Developer-only acceptance. Explicit URLs bypass the launcher completely.
    private func collectSelfTest(attempt: Int) {
        guard selfTestReport != nil, let controller = selfTestEditor ?? shelf else { return }
        let editor = selfTestEditorURL != nil
        if attempt >= (editor ? 20 : 90) {
            finishSelfTest(["ok": false, "error": editor ? "等待作品页面与外观布局超时" : "等待书架页面超时"])
            return
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self, weak controller] in
            guard let self, let controller else { return }
            if editor && !controller.appearanceApplied {
                self.collectSelfTest(attempt: attempt + 1)
                return
            }
            let script = editor ? """
            (() => {
                const header=document.querySelector('body > header'),info=document.getElementById('book-info');
                const title=document.getElementById('book-title'),progress=document.getElementById('book-progress');
                const documentTitle=document.getElementById('title');
                if(!header || !info || !title || !progress || !documentTitle || documentTitle.textContent==='选择章节或材料' || progress.textContent.includes('正在读取') || header.getBoundingClientRect().height<=0)return null;
                return {
                    title:document.title,
                    bookTitle:title.textContent,
                    headerHeight:header.getBoundingClientRect().height,
                    metadataPopup:!!info.querySelector('.menu-panel'),
                    bookinfo:{exists:true,open:info.open,summary:info.querySelector('summary')?.textContent,title:document.getElementById('book-info-title')?.textContent},
                    bodyViewWidth:document.body.getBoundingClientRect().width,
                    viewportWidth:window.innerWidth,
                    horizontalOverflow:document.documentElement.scrollWidth>window.innerWidth,
                    documentTitle:documentTitle.textContent,
                    message:document.getElementById('message')?.textContent
                };
            })()
            """ : """
            (() => {
                const summary=document.getElementById('filter-summary');
                if(!summary || !summary.textContent.trim())return null;
                return {
                    title:document.title,
                    intro:document.getElementById('shelf-intro')?.textContent,
                    footer:document.getElementById('shelf-footer')?.textContent,
                    layout:{width:window.innerWidth,columns:getComputedStyle(document.getElementById('books')).gridTemplateColumns,search:!!document.getElementById('shelf-search')},
                    summary:summary.textContent,
                    types:Array.from(document.querySelectorAll('.type-filters button')).map(n=>({text:n.textContent,selected:n.getAttribute('aria-pressed')})),
                    sort:{value:document.getElementById('shelf-order').value,options:Array.from(document.querySelectorAll('#shelf-order option')).map(n=>n.textContent)},
                    writingStatusFilter:{value:document.getElementById('shelf-status')?.value,options:Array.from(document.querySelectorAll('#shelf-status option')).map(n=>n.textContent)},
                    cards:Array.from(document.querySelectorAll('.book')).map(n=>({title:n.querySelector('strong').textContent,updated:n.querySelector('.book-updated')?.textContent,progress:n.querySelector('.book-progress')?.textContent,status:n.querySelector('.book-status')?.textContent,statusEditable:!!n.querySelector('.book-status-editor'),tags:Array.from(n.querySelectorAll('.genre-tag')).map(t=>t.textContent)})),
                    message:document.getElementById('message').textContent
                };
            })()
            """
            controller.webView.evaluateJavaScript(script) { [weak self, weak controller] result, error in
                guard let self, let controller else { return }
                guard var packet = result as? [String: Any] else {
                    self.collectSelfTest(attempt: attempt + 1)
                    return
                }
                if self.selfTestSwitchEditorURL != nil { self.beginSwitchSelfTest(controller); return }
                packet["ok"] = true
                packet["nativeToolbarIDs"] = controller.nativeToolbarIDs
                packet["appearanceApplied"] = controller.appearanceApplied
                self.captureSelfTest(controller, packet: packet)
            }
        }
    }

    private func captureSelfTest(_ controller: WorkbenchWindow, packet initial: [String: Any]) {
        guard let report = selfTestReport else { return }
        var packet = initial
        let snapshot = report.deletingPathExtension().appendingPathExtension("png")
        let configuration = WKSnapshotConfiguration()
        configuration.rect = controller.webView.bounds
        controller.webView.takeSnapshot(with: configuration) { [weak self] image, error in
            guard let self else { return }
            if let image, let tiff = image.tiffRepresentation,
               let bitmap = NSBitmapImageRep(data: tiff), let png = bitmap.representation(using: .png, properties: [:]) {
                do {
                    try FileManager.default.createDirectory(at: snapshot.deletingLastPathComponent(), withIntermediateDirectories: true)
                    try png.write(to: snapshot, options: .atomic)
                    packet["snapshot"] = snapshot.path
                } catch { packet["ok"] = false; packet["snapshot_error"] = error.localizedDescription }
            } else { packet["ok"] = false; packet["snapshot_error"] = error?.localizedDescription ?? "WebKit未返回截图" }
            self.finishSelfTest(packet)
        }
    }

    private func finishSelfTest(_ packet: [String: Any]) {
        guard let selfTestReport else { return }
        do {
            try FileManager.default.createDirectory(at: selfTestReport.deletingLastPathComponent(), withIntermediateDirectories: true)
            let bytes = try JSONSerialization.data(withJSONObject: packet, options: [.prettyPrinted, .sortedKeys])
            try bytes.write(to: selfTestReport, options: .atomic)
        } catch { fputs("Self-test report could not be saved: \(error.localizedDescription)\n", stderr) }
        NSApp.terminate(nil)
    }
}

@main
struct StoryWorkbenchMain {
    @MainActor static func main() {
        let options: WorkbenchLaunchOptions
        do { options = try WorkbenchLaunchOptions.parse(CommandLine.arguments) }
        catch {
            fputs(error.localizedDescription + "\n", stderr)
            exit(2)
        }
        let application = NSApplication.shared
        application.setActivationPolicy(options.report == nil ? .regular : .accessory)
        var clientLease: ClientInstanceLease?
        switch ClientInstance.claim(selfTest: options.report != nil) {
        case .primary(let lease): clientLease = lease
        case .bypassed: break
        case .existing(let running):
            ClientInstance.activate(running) { activated in
                if !activated { fputs("已有客户端仍保留，暂时无法唤回。\n", stderr) }
                exit(activated ? 0 : 2)
            }
            RunLoop.main.run()
            exit(2)
        case .occupied:
            guard let identity = Bundle.main.bundleIdentifier else { exit(2) }
            ClientInstance.activateExisting(bundleIdentifier: identity) { activated in
                if !activated { fputs("已有客户端正在运行或启动；未创建第二个窗口。\n", stderr) }
                exit(activated ? 0 : 2)
            }
            RunLoop.main.run()
            exit(2)
        case .failure(let message):
            fputs(message + "\n", stderr)
            exit(2)
        }
        let delegate = WorkbenchApp(selfTestReport: options.report, selfTestEditorURL: options.editorURL,
                                    selfTestSwitchEditorURL: options.switchEditorURL, selfTestShelfURL: options.shelfURL,
                                    selfTestSecondEditorURL: options.secondEditorURL)
        application.delegate = delegate
        withExtendedLifetime((clientLease, delegate)) { application.run() }
    }
}
