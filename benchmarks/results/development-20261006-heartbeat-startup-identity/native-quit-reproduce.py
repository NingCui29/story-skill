from pathlib import Path
import tempfile, subprocess
source=Path('<repo>/desktop/macos/StoryWorkbench.swift').read_text()
start=source.index('    private func confirmQuit(')
end=source.index('\n    func applicationWillTerminate', start)
method=source[start:end].replace('private func confirmQuit', 'func confirmQuit')
head='''import Foundation
final class ApplicationStub {
    var replies: [Bool] = []
    func reply(toApplicationShouldTerminate allowed: Bool) { replies.append(allowed) }
}
let NSApp = ApplicationStub()
final class WorkbenchWindow {
    var dirty: Bool
    var checked = 0
    var completion: ((Bool) -> Void)?
    init(dirty: Bool) { self.dirty = dirty }
    func confirmLeaving(action: String, completion: @escaping (Bool) -> Void) {
        checked += 1
        if dirty { self.completion = completion } else { completion(true) }
    }
}
final class QuitHarness {
    var checkingQuit = true
'''
tail='''
}
let first = WorkbenchWindow(dirty: false)
let second = WorkbenchWindow(dirty: true)
let app = QuitHarness()
app.confirmQuit([first, second], index: 0)
precondition(first.checked == 1 && second.checked == 1 && NSApp.replies.isEmpty)
// Model a state change in the already checked document while the second callback is pending.
first.dirty = true
second.completion?(true)
precondition(NSApp.replies == [true])
precondition(first.dirty && first.checked == 1)
print("quit=allowed; first_document_dirty=true; first_document_checks=1")
'''
with tempfile.TemporaryDirectory(prefix='story-native-audit-') as raw:
    f=Path(raw)/'quit.swift'; f.write_text(head+method+tail)
    p=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(f),'-o',str(Path(raw)/'quit')],capture_output=True,text=True,timeout=60)
    print('compile:',p.returncode,p.stderr)
    if p.returncode == 0:
        q=subprocess.run([str(Path(raw)/'quit')],capture_output=True,text=True,timeout=10)
        print(q.stdout,end=''); print(q.stderr,end=''); print('result:',q.returncode)
