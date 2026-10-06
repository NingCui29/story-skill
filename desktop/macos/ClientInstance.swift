import AppKit
import Darwin

// Keep this lease strongly referenced until the normal client exits. The lock
// inode stays in place; closing the descriptor releases ownership after crashes.
final class ClientInstanceLease {
    let fileURL: URL
    private var descriptor: Int32

    fileprivate init(descriptor: Int32, fileURL: URL) {
        self.descriptor = descriptor
        self.fileURL = fileURL
    }

    func release() {
        guard descriptor >= 0 else { return }
        _ = flock(descriptor, LOCK_UN)
        _ = Darwin.close(descriptor)
        descriptor = -1
    }

    deinit { release() }
}

enum ClientInstanceLock {
    enum Claim {
        case acquired(ClientInstanceLease)
        case occupied
    }

    static func validBundleIdentifier(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$", options: .regularExpression) != nil
    }

    static func acquire(bundleIdentifier: String) throws -> Claim {
        guard validBundleIdentifier(bundleIdentifier),
              let account = getpwuid(getuid()), let homePath = account.pointee.pw_dir else {
            throw invalid("客户端身份或当前用户目录无法核对。")
        }
        var location = URL(fileURLWithPath: String(cString: homePath), isDirectory: true)
        var directory = try openDirectory(location)
        defer { _ = Darwin.close(directory) }
        for name in ["Library", "Application Support", bundleIdentifier] {
            let next = try childDirectory(parent: directory, name: name)
            _ = Darwin.close(directory)
            directory = next
            location.appendPathComponent(name, isDirectory: true)
        }
        return try acquire(in: directory, location: location)
    }

    // Isolated harnesses supply an existing private temporary parent directory.
    // Production claims always use the account's shared application-support path.
    static func acquire(at location: URL) throws -> Claim {
        guard location.isFileURL, !location.lastPathComponent.isEmpty,
              ![".", ".."].contains(location.lastPathComponent) else { throw invalid("锁目录无效。") }
        let parent = try openDirectory(location.deletingLastPathComponent())
        defer { _ = Darwin.close(parent) }
        let directory = try childDirectory(parent: parent, name: location.lastPathComponent)
        defer { _ = Darwin.close(directory) }
        return try acquire(in: directory, location: location)
    }

    private static func openDirectory(_ location: URL) throws -> Int32 {
        let descriptor = Darwin.open(location.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw systemError() }
        do {
            try checkDirectory(descriptor)
            return descriptor
        } catch { _ = Darwin.close(descriptor); throw error }
    }

    private static func childDirectory(parent: Int32, name: String) throws -> Int32 {
        if Darwin.mkdirat(parent, name, mode_t(0o700)) != 0 && errno != EEXIST { throw systemError() }
        let descriptor = Darwin.openat(parent, name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw systemError() }
        do {
            let opened = try checkDirectory(descriptor)
            var bound = stat()
            guard Darwin.fstatat(parent, name, &bound, AT_SYMLINK_NOFOLLOW) == 0,
                  sameFile(opened, bound) else { throw invalid("锁目录在核对时变化。") }
            return descriptor
        } catch { _ = Darwin.close(descriptor); throw error }
    }

    @discardableResult private static func checkDirectory(_ descriptor: Int32) throws -> stat {
        var info = stat()
        guard Darwin.fstat(descriptor, &info) == 0 else { throw systemError() }
        guard (info.st_mode & S_IFMT) == S_IFDIR, info.st_uid == getuid(),
              (info.st_mode & mode_t(0o022)) == 0 else { throw invalid("锁目录的归属或权限无法核对。") }
        return info
    }

    private static func acquire(in directory: Int32, location: URL) throws -> Claim {
        let descriptor = Darwin.openat(directory, "client.lock", O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, mode_t(0o600))
        guard descriptor >= 0 else { throw systemError() }
        var retained = false
        defer { if !retained { _ = Darwin.close(descriptor) } }
        var info = stat()
        guard Darwin.fstat(descriptor, &info) == 0 else { throw systemError() }
        guard (info.st_mode & S_IFMT) == S_IFREG, info.st_uid == getuid(), info.st_nlink == 1,
              (info.st_mode & mode_t(0o022)) == 0 else { throw invalid("客户端锁的归属或文件类型无法核对。") }
        try verifyBound(directory: directory, location: location, file: info)
        if flock(descriptor, LOCK_EX | LOCK_NB) != 0 {
            let code = errno
            guard code == EWOULDBLOCK || code == EAGAIN else { throw systemError(code) }
            try verifyBound(directory: directory, location: location, file: info)
            return .occupied
        }
        do { try verifyBound(directory: directory, location: location, file: info) }
        catch { _ = flock(descriptor, LOCK_UN); throw error }
        retained = true
        return .acquired(ClientInstanceLease(descriptor: descriptor, fileURL: location.appendingPathComponent("client.lock")))
    }

    private static func verifyBound(directory: Int32, location: URL, file: stat) throws {
        let opened = try checkDirectory(directory)
        var currentDirectory = stat(), currentFile = stat()
        guard Darwin.lstat(location.path, &currentDirectory) == 0, sameFile(opened, currentDirectory),
              Darwin.fstatat(directory, "client.lock", &currentFile, AT_SYMLINK_NOFOLLOW) == 0,
              sameFile(file, currentFile), (currentFile.st_mode & S_IFMT) == S_IFREG,
              currentFile.st_uid == getuid(), currentFile.st_nlink == 1,
              (currentFile.st_mode & mode_t(0o022)) == 0 else { throw invalid("客户端锁或目录在核对时变化。") }
    }

    private static func sameFile(_ first: stat, _ second: stat) -> Bool {
        first.st_dev == second.st_dev && first.st_ino == second.st_ino
    }

    private static func invalid(_ message: String) -> NSError {
        NSError(domain: "StoryWorkbenchClientInstance", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    private static func systemError(_ code: Int32 = errno) -> NSError {
        NSError(domain: NSPOSIXErrorDomain, code: Int(code), userInfo: nil)
    }
}

@MainActor
enum ClientInstance {
    enum Claim {
        case primary(ClientInstanceLease)
        case existing(NSRunningApplication)
        case occupied
        case bypassed
        case failure(String)
    }

    // Call after selecting the activation policy, before NSApplication.run().
    // Fully launched regular apps include older clients that do not hold a lease;
    // unfinished new contenders defer to flock instead of activating each other.
    static func claim(bundleIdentifier: String? = Bundle.main.bundleIdentifier, selfTest: Bool = false) -> Claim {
        if selfTest { return .bypassed }
        guard let bundleIdentifier, ClientInstanceLock.validBundleIdentifier(bundleIdentifier) else {
            return .failure("客户端身份无法核对，请使用完整应用。")
        }
        if let existing = existingApplication(bundleIdentifier: bundleIdentifier) { return .existing(existing) }
        do {
            switch try ClientInstanceLock.acquire(bundleIdentifier: bundleIdentifier) {
            case .occupied: return .occupied
            case .acquired(let lease):
                if let existing = existingApplication(bundleIdentifier: bundleIdentifier) {
                    lease.release()
                    return .existing(existing)
                }
                return .primary(lease)
            }
        } catch { return .failure("客户端单实例锁无法核对：" + error.localizedDescription) }
    }

    static func existingApplication(bundleIdentifier: String) -> NSRunningApplication? {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
            .filter { $0.processIdentifier != getpid() && !$0.isTerminated && $0.isFinishedLaunching && $0.activationPolicy == .regular }
            .sorted {
                let first = $0.launchDate ?? .distantPast, second = $1.launchDate ?? .distantPast
                return first == second ? $0.processIdentifier < $1.processIdentifier : first < second
            }.first
    }

    // A lock winner may still be starting. Wait briefly to activate that client;
    // failure never permits a second window or kills a process.
    static func activateExisting(bundleIdentifier: String, attempts: Int = 30, completion: @escaping @MainActor (Bool) -> Void) {
        if let existing = existingApplication(bundleIdentifier: bundleIdentifier) {
            activate(existing, completion: completion)
            return
        }
        guard attempts > 0 else { completion(false); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            activateExisting(bundleIdentifier: bundleIdentifier, attempts: attempts - 1, completion: completion)
        }
    }

    static func reopenConfiguration() -> NSWorkspace.OpenConfiguration {
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.createsNewApplicationInstance = false
        configuration.allowsRunningApplicationSubstitution = true
        configuration.activates = true
        configuration.promptsUserIfNeeded = false
        configuration.addsToRecentItems = false
        return configuration
    }

    // Launch Services sends reopen to the running app, which can recreate its
    // closed main window. Activating a process alone does not provide that event.
    static func activate(_ existing: NSRunningApplication, completion: @escaping @MainActor (Bool) -> Void) {
        guard existing.processIdentifier != getpid(), !existing.isTerminated,
              existing.isFinishedLaunching, existing.activationPolicy == .regular,
              let bundleURL = existing.bundleURL, let bundleIdentifier = existing.bundleIdentifier else {
            completion(false)
            return
        }
        NSWorkspace.shared.openApplication(at: bundleURL, configuration: reopenConfiguration()) { application, error in
            let reopened = error == nil && application?.bundleIdentifier == bundleIdentifier && application?.processIdentifier != getpid()
            DispatchQueue.main.async { completion(reopened) }
        }
    }
}
