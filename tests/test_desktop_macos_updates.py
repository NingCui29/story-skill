"""Run the native Mac update policy against isolated release/network fixtures."""
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'desktop/macos/StoryWorkbench.swift'


class DesktopMacUpdatesTests(unittest.TestCase):
    def test_release_selection_errors_and_download_url_policy(self):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        source = SOURCE.read_text()
        policy = source[source.index('struct WorkbenchClientVersion:'):source.index('\n@MainActor')]
        checks = r'''
func bytes(_ assets: [[String: Any]], _ changes: [String: Any] = [:]) -> Data {
    var release: [String: Any] = ["draft":false,"prerelease":false,"tag_name":"v0.6.18",
        "html_url":"https://github.com/NingCui29/story-skill/releases/tag/v0.6.18","assets":assets]
    for (key,value) in changes { release[key] = value }
    return try! JSONSerialization.data(withJSONObject:release)
}
func asset(_ version:String="0.1.10", _ architecture:String="x86_64", _ ext:String="zip", _ changes:[String:Any]=[:]) -> [String:Any] {
    let name="story-workbench-\(version)-macos-\(architecture).\(ext)"
    var record:[String:Any] = ["name":name,"state":"uploaded","size":14,"digest":"sha256:"+String(repeating:"a",count:64),
        "browser_download_url":"https://github.com/NingCui29/story-skill/releases/download/v0.6.18/"+name]
    for (key,value) in changes { record[key]=value }
    return record
}
func check(_ data:Data? = nil, _ status:Int=200, _ error:Error?=nil, _ version:String?="0.1.6",
           _ architecture:String="x86_64", _ url:URL=WorkbenchUpdate.endpoint) -> WorkbenchUpdate.Result {
    WorkbenchUpdate.evaluate(data:data,response:HTTPURLResponse(url:url,statusCode:status,httpVersion:nil,headerFields:nil),
        error:error,currentVersion:version,architecture:architecture)
}
func unavailable(_ result:WorkbenchUpdate.Result,_ reason:String?=nil) {
    guard case .unavailable(let message) = result else { fatalError("Unverified result claimed success") }
    precondition(!message.isEmpty)
    if let reason { precondition(message.contains(reason),message) }
}
let older=WorkbenchClientVersion("0.1.9")!, newer=WorkbenchClientVersion("0.1.10")!
precondition(older < newer)
precondition(WorkbenchClientVersion("0.1.6") == WorkbenchClientVersion("0.1.6.0"))
precondition(WorkbenchClientVersion("0.10")! > WorkbenchClientVersion("0.9.99")!)
for invalid in ["", "v0.1.7", "0.1.7-beta", "0.-1.7", "0.1.7\n", "0.1.7+1", "0.1.7.0.0", "0.1.9999999999999999999999999"] {
    precondition(WorkbenchClientVersion(invalid) == nil, invalid)
}
let request=WorkbenchUpdate.request()
precondition(request.url == WorkbenchUpdate.endpoint && request.timeoutInterval == 15)
precondition(request.value(forHTTPHeaderField:"Accept") == "application/vnd.github+json")
precondition(request.value(forHTTPHeaderField:"X-GitHub-Api-Version") == "2022-11-28")
precondition(request.value(forHTTPHeaderField:"Authorization") == nil)
// Choose the client installer version, independently of runtime tag v0.6.18.
switch check(bytes([asset("0.1.9"),asset(),asset("0.2.0","arm64"),asset("0.1.8","universal2","dmg")])) {
case .available(let package):
    precondition(package.version == "0.1.10" && package.releaseURL.absoluteString == "https://github.com/NingCui29/story-skill/releases/tag/v0.6.18")
default: fatalError("Expected the newest compatible client version")
}
for version in ["0.1.6", "0.1.6.0"] {
    switch check(bytes([asset("0.1.6")]),200,nil,version) {
    case .current(let actual):precondition(actual == version)
    default:fatalError("Expected no newer client")
    }
}
switch check(bytes([asset("0.1.6")]),200,nil,"0.1.7") {
case .ahead(let current,let latest):precondition(current == "0.1.7" && latest == "0.1.6")
default:fatalError("A newer candidate must not claim to be the latest stable release")
}
switch check(bytes([asset("0.1.7","universal2","dmg")]),200,nil,"0.1.6","arm64") {
case .available(let package):precondition(package.version == "0.1.7")
default:fatalError("Universal Mac installer should be compatible")
}
unavailable(check(bytes([asset("0.1.10","arm64")])),"Mac 安装包")
unavailable(check(bytes([])),"Mac 安装包")
unavailable(check(bytes([asset("0.1.10","x86_64","zip",["state":"open"])])),"Mac 安装包")
for suffix in ["zip.sha256","tar.gz","exe"] { unavailable(check(bytes([asset("0.9.9","x86_64",suffix)])),"Mac 安装包") }
for status in [403,429] { unavailable(check(bytes([asset()]),status),"限制") }
unavailable(check(nil,404),"正式发布")
for status in [301,304,500,503] { unavailable(check(bytes([asset()]),status),"HTTP") }
unavailable(check(nil,200,NSError(domain:NSURLErrorDomain,code:NSURLErrorTimedOut)),"超时")
unavailable(check(nil,200,NSError(domain:NSURLErrorDomain,code:NSURLErrorNotConnectedToInternet)),"网络")
for data in [Data(),Data("not-json".utf8),Data("{}".utf8),Data(repeating:32,count:2_000_001)] { unavailable(check(data)) }
for version in [nil,"","story-skill 0.6.18"] { unavailable(check(bytes([asset()]),200,nil,version),"当前客户端版本") }
for change:[String:Any] in [["draft":true],["prerelease":true],["tag_name":"v0.6.19"],["html_url":"https://github.com/other/story-skill/releases/tag/v0.6.18"]] {
    unavailable(check(bytes([asset()],change)),"发布信息")
}
unavailable(check(bytes([asset()]),200,nil,"0.1.6","x86_64",URL(string:"https://example.com/")!))
unavailable(WorkbenchUpdate.evaluate(data:bytes([asset()]),response:URLResponse(url:WorkbenchUpdate.endpoint,mimeType:nil,
    expectedContentLength:0,textEncodingName:nil),error:nil,currentVersion:"0.1.6",architecture:"x86_64"))
let valid="https://github.com/NingCui29/story-skill/releases/tag/v0.6.18"
precondition(WorkbenchUpdate.githubURL(valid,prefix:"tag/") != nil)
for address in ["http://github.com/NingCui29/story-skill/releases/tag/v0.6.18",
    "https://github.com.evil.test/NingCui29/story-skill/releases/tag/v0.6.18",
    "https://user:pass@github.com/NingCui29/story-skill/releases/tag/v0.6.18",
    "https://github.com:443/NingCui29/story-skill/releases/tag/v0.6.18",
    "https://github.com/other/story-skill/releases/tag/v0.6.18", valid+"?redirect=https://evil.test",valid+"#another",
    "https://github.com/NingCui29/story-skill/releases/tag/../evil",
    "https://github.com/NingCui29/story-skill/releases/tag/%76%30.6.18"] {
    precondition(WorkbenchUpdate.githubURL(address,prefix:"tag/") == nil,address)
}
for address in ["https://evil.test/installer.zip","https://github.com/other/story-skill/releases/download/v0.6.18/installer.zip",
    "https://github.com/NingCui29/story-skill/releases/download/v0.6.19/story-workbench-0.1.10-macos-x86_64.zip",
    "http://github.com/NingCui29/story-skill/releases/download/v0.6.18/story-workbench-0.1.10-macos-x86_64.zip"] {
    unavailable(check(bytes([asset("0.1.10","x86_64","zip",["browser_download_url":address])])),"安装包信息")
}
for invalidSize:Int64 in [0,-1,WorkbenchUpdatePackage.maximumBytes+1] {
    unavailable(check(bytes([asset("0.1.10","x86_64","zip",["size":invalidSize])])),"大小")
}
for invalidDigest in ["", "sha256:abc", "sha512:"+String(repeating:"a",count:64), "sha256:"+String(repeating:"g",count:64)] {
    unavailable(check(bytes([asset("0.1.10","x86_64","zip",["digest":invalidDigest])])),"校验信息")
}
let noDigest=asset("0.1.10","x86_64","zip",["digest":NSNull()])
unavailable(check(bytes([noDigest])),"SHA256")
let sidecar=asset("0.1.10","x86_64","zip.sha256",["size":128])
switch check(bytes([noDigest,sidecar])) {
case .available(let package):
    precondition(package.digest == nil && package.checksumURL?.absoluteString == package.url.absoluteString+".sha256")
    precondition(package.checksumSize==128 && package.size==14)
default:fatalError("A pinned exact-name checksum companion should be supported")
}
for changes:[String:Any] in [["size":4097],["size":0],["state":"open"],
    ["browser_download_url":"https://github.com/NingCui29/story-skill/releases/download/v0.6.20/story-workbench-0.1.10-macos-x86_64.zip.sha256"]] {
    unavailable(check(bytes([noDigest,asset("0.1.10","x86_64","zip.sha256",changes)])),"SHA256")
}
switch check(bytes([asset("0.1.10","universal2","dmg"),asset("0.1.10","x86_64","dmg"),asset("0.1.10")])) {
case .available(let package):precondition(package.name == "story-workbench-0.1.10-macos-x86_64.dmg")
default:fatalError("Stable selection should prefer disk images and native architecture")
}
let session=URLSession(configuration:.ephemeral), task=session.dataTask(with:WorkbenchUpdate.request())
// A download manifest is authenticated by the selected release; ZIP then wins
// over DMG so stable per-file compressed data can be reused across updates.
let zipPlan=asset("0.1.10","x86_64","zip.download.json",["size":200])
let dmgPlan=asset("0.1.10","x86_64","dmg.download.json",["size":200])
switch check(bytes([asset(),asset("0.1.10","x86_64","dmg"),zipPlan,dmgPlan])) {
case .available(let package):precondition(package.name.hasSuffix(".zip") && package.planSize==200 && package.planDigest != nil)
default:fatalError("Verified plans should enable stable compressed ZIP preference")
}
unavailable(check(bytes([asset(),asset("0.1.10","x86_64","zip.download.json",["digest":"unverified"])])),"分块清单")
unavailable(check(bytes([asset(),zipPlan,zipPlan])),"分块清单")
let redirect=HTTPURLResponse(url:WorkbenchUpdate.endpoint,statusCode:302,httpVersion:nil,headerFields:nil)!
var redirectRejected=false
WorkbenchUpdateRedirectPolicy().urlSession(session,task:task,willPerformHTTPRedirection:redirect,
    newRequest:URLRequest(url:URL(string:"https://example.com/")!)) {redirectRejected = $0 == nil}
precondition(redirectRejected)
session.invalidateAndCancel()
print("native update fixtures passed")
'''
        with tempfile.TemporaryDirectory(prefix='story-mac-updates-') as raw:
            folder = Path(raw)
            swift = folder / 'update-fixtures.swift'
            executable = folder / 'update-fixtures'
            swift.write_text('import Foundation\n' + policy + checks)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-target',
                                    platform.machine() + '-apple-macosx13.0', str(swift), '-o', str(executable)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn('native update fixtures passed', ran.stdout)

    def test_download_progress_checksum_cancellation_retry_and_cleanup(self):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        source = SOURCE.read_text()
        policy = source[source.index('struct WorkbenchClientVersion:'):source.index('\n@MainActor')]
        checks = r'''
let name = "story-workbench-0.1.10-macos-x86_64.dmg"
let url = URL(string:"https://github.com/NingCui29/story-skill/releases/download/v0.6.19/"+name)!
let payload = Data(repeating: 65, count: 1_048_576)
let expected = SHA256.hash(data:payload).map { String(format:"%02x",$0) }.joined()
let sandbox = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true)
final class Fixture: URLProtocol, @unchecked Sendable {
    static var mode = "good"
    private let lock = NSLock()
    private var stopped = false
    override class func canInit(with request:URLRequest)->Bool { true }
    override class func canonicalRequest(for request:URLRequest)->URLRequest { request }
    override func startLoading() {
        guard let url = request.url else { fatalError() }
        if Self.mode == "timeout" {
            client?.urlProtocol(self,didFailWithError:NSError(domain:NSURLErrorDomain,code:NSURLErrorTimedOut)); return
        }
        let sidecar = url.path.hasSuffix(".sha256")
        let data = sidecar ? Data((expected+"  "+name+"\n").utf8) :
            (Self.mode == "corrupt" ? Data(repeating:66,count:payload.count) : payload)
        let length = Self.mode == "wrong-size" ? data.count+1 : data.count
        client?.urlProtocol(self,didReceive:HTTPURLResponse(url:url,statusCode:Self.mode == "http-error" ? 503 : 200,
            httpVersion:nil,headerFields:["Content-Length":String(length)])!,cacheStoragePolicy:.notAllowed)
        if Self.mode == "slow" {
            client?.urlProtocol(self,didLoad:Data(data.prefix(4096)))
            return // caller cancels deterministically after partial bytes arrive
        }
        if Self.mode == "short" { client?.urlProtocol(self,didLoad:Data(data.prefix(4096))) }
        else {
            for offset in stride(from:0,to:data.count,by:16_384) {
                lock.lock(); let active = !stopped; lock.unlock()
                if !active { return }
                client?.urlProtocol(self,didLoad:data.subdata(in:offset..<min(data.count,offset+16_384)))
            }
        }
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() { lock.lock(); stopped = true; lock.unlock() }
}
func package(sidecar:Bool=false)->WorkbenchUpdatePackage {
    let sidecarBytes = Int64(Data((expected+"  "+name+"\n").utf8).count)
    return WorkbenchUpdatePackage(version:"0.1.10",releaseURL:URL(string:"https://github.com/NingCui29/story-skill/releases/tag/v0.6.19")!,
        name:name,url:url,size:Int64(payload.count),digest:sidecar ? nil : expected,
        checksumURL:sidecar ? URL(string:url.absoluteString+".sha256") : nil,checksumSize:sidecar ? sidecarBytes : nil)
}
var attempts = 0
func attempt(_ mode:String, sidecar:Bool=false, cancellation:Bool=false)->String {
    Fixture.mode = mode
    let config = URLSessionConfiguration.ephemeral
    config.protocolClasses = [Fixture.self]
    var terminal = "", progress:[Double] = [], checking=false, verifying=false
    var transfer: WorkbenchUpdateDownload!
    transfer = WorkbenchUpdateDownload(package:package(sidecar:sidecar),configuration:config,stagingParent:sandbox) { event in
        switch event {
        case .stage:break
        case .preparingChecksum:checking=true
        case .progress(let sample):
            progress.append(sample.fraction)
            if cancellation && sample.received>0 { transfer.cancel() }
        case .verifying:verifying=true
        case .completed(let file,let digest):
            precondition(digest == expected && progress.first == 0 && progress.last == 1 && verifying)
            precondition((try? Data(contentsOf:file)) == payload)
            try! WorkbenchUpdateFile.verify(file,size:Int64(payload.count),digest:expected)
            // Rechecking on the actual saved bytes detects later corruption.
            try! FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
            try! Data(repeating:66,count:payload.count).write(to:file)
            var rejected=false
            do { try WorkbenchUpdateFile.verify(file,size:Int64(payload.count),digest:expected) } catch { rejected=true }
            precondition(rejected)
            try! FileManager.default.removeItem(at:file.deletingLastPathComponent())
            terminal="completed"
        case .cancelled:terminal="cancelled"
        case .failed(let message):precondition(!message.isEmpty);terminal="failed"
        }
    }
    transfer.begin()
    let deadline=Date().addingTimeInterval(10)
    while terminal.isEmpty && Date()<deadline { _ = RunLoop.main.run(mode:.default,before:Date().addingTimeInterval(0.01)) }
    precondition(!terminal.isEmpty,"fixture timed out: "+mode)
    precondition((try! FileManager.default.contentsOfDirectory(atPath:sandbox.path)).isEmpty,"partial files leaked")
    if sidecar && terminal=="completed" { precondition(checking) }
    attempts += 1
    return terminal
}
precondition(attempt("good") == "completed")
precondition(attempt("good",sidecar:true) == "completed")
for mode in ["corrupt","short","wrong-size","http-error","timeout"] { precondition(attempt(mode) == "failed",mode) }
precondition(attempt("slow",cancellation:true) == "cancelled")
precondition(attempt("good") == "completed") // retry is a fresh, successful transfer
let progress = WorkbenchUpdateProgress(received:25,total:100,elapsed:5)
precondition(progress.fraction==0.25 && progress.bytesPerSecond==5 && progress.remaining==15)
precondition(progress.detail.contains("25%"))
precondition(WorkbenchUpdatePackage.checksum(Data((expected+"  "+name+"\n").utf8),name:name)==expected)
precondition(WorkbenchUpdatePackage.checksum(Data((expected+" *"+name+"\n").utf8),name:name)==expected)
for invalid in [expected,expected+"  another.dmg",expected+"  "+name+"\nextra",String(repeating:"0",count:4097)] {
    precondition(WorkbenchUpdatePackage.checksum(Data(invalid.utf8),name:name)==nil)
}
let allowed=URL(string:"https://release-assets.githubusercontent.com/github-production-release-asset/123/file?sig=test")!
precondition(WorkbenchUpdatePackage.allowsDownloadURL(url,initial:url))
precondition(WorkbenchUpdatePackage.allowsDownloadURL(allowed,initial:url))
for invalid in ["http://release-assets.githubusercontent.com/github-production-release-asset/a",
    "https://release-assets.githubusercontent.com.evil.test/github-production-release-asset/a",
    "https://user@release-assets.githubusercontent.com/github-production-release-asset/a",
    "https://release-assets.githubusercontent.com:443/github-production-release-asset/a",
    "https://release-assets.githubusercontent.com/github-production-release-asset/%2e%2e/a",
    "https://release-assets.githubusercontent.com/github-production-release-asset/a%2fb",
    "https://release-assets.githubusercontent.com/github-production-release-asset/a#fragment",
    "https://github.com/NingCui29/other/releases/download/v0.6.19/a",
    "https://objects.githubusercontent.com/other-path/a"] {
    precondition(!WorkbenchUpdatePackage.allowsDownloadURL(URL(string:invalid)!,initial:url),invalid)
}
// The redirect delegate denies foreign addresses and strips inherited credentials.
let policy = WorkbenchUpdateDownload(package:package()) { _ in }
let session=URLSession(configuration:.ephemeral), task=session.dataTask(with:url)
let response=HTTPURLResponse(url:url,statusCode:302,httpVersion:nil,headerFields:nil)!
var accepted:URLRequest?
var request=URLRequest(url:allowed);request.setValue("Bearer secret-fixture",forHTTPHeaderField:"Authorization")
policy.urlSession(session,task:task,willPerformHTTPRedirection:response,newRequest:request){accepted=$0}
precondition(accepted?.url==allowed && accepted?.value(forHTTPHeaderField:"Authorization")==nil)
var rejected=false
policy.urlSession(session,task:task,willPerformHTTPRedirection:response,newRequest:URLRequest(url:URL(string:"https://evil.test/a")!)){rejected=$0==nil}
precondition(rejected)
session.invalidateAndCancel()
// A symlink cannot be accepted as a verified installation package.
let target=sandbox.appendingPathComponent("target"), link=sandbox.appendingPathComponent("link")
try! payload.write(to:target)
try! FileManager.default.createSymbolicLink(at:link,withDestinationURL:target)
var linkRejected=false
do { try WorkbenchUpdateFile.verify(link,size:Int64(payload.count),digest:expected) } catch {linkRejected=true}
precondition(linkRejected)
try! FileManager.default.removeItem(at:link);try! FileManager.default.removeItem(at:target)
print("native download fixtures passed: \(attempts) attempts; cancel cleanup, retry, SHA, disk corruption, redirects")
'''
        with tempfile.TemporaryDirectory(prefix='story-mac-transfer-') as raw:
            folder = Path(raw)
            staging = folder / 'staging'
            staging.mkdir()
            swift = folder / 'download-fixtures.swift'
            executable = folder / 'download-fixtures'
            swift.write_text('import Foundation\n' + policy + checks)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-target',
                                    platform.machine() + '-apple-macosx13.0', str(swift), '-o', str(executable)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable), str(staging)], capture_output=True, text=True, timeout=30)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn('native download fixtures passed: 9 attempts', ran.stdout)

    def test_progress_window_is_isolated_and_quit_waits_for_download_cleanup(self):
        source = SOURCE.read_text()
        smoke = source.split('private func collectUpdateSelfTest(', 1)[1].split('private func finishSelfTest(', 1)[0]
        self.assertIn('networkStarted', smoke)
        self.assertIn('synthetic', smoke)
        for mutation in ['startShelf(', 'showEditor(', '.begin()', 'NSWorkspace.shared.open']:
            self.assertNotIn(mutation, smoke)
        quitting = source.split('func applicationShouldTerminate(', 1)[1].split('private func confirmAll(', 1)[0]
        self.assertIn('confirmAll(controllers', quitting)
        self.assertIn('awaitingUpdateCancellationForQuit = true', quitting)
        self.assertIn('download.cancel()', quitting)
        terminal = source.split('private func showUpdateEvent(', 1)[1].split('@objc private func updateAction(', 1)[0]
        self.assertIn('awaitingUpdateCancellationForQuit, updateDownloader == nil', terminal)
        self.assertIn('NSApp.reply(toApplicationShouldTerminate: true)', terminal)
        checking = source.split('@objc private func checkForUpdates()', 1)[1].split('private func presentUpdate(', 1)[0]
        self.assertIn('updatePanel().makeKeyAndOrderFront(nil)', checking)
        self.assertIn('updateActionMode = "cancel-check"', checking)
        self.assertIn('updateProgress?.startAnimation(nil)', checking)

    def test_menu_check_keeps_page_and_backend_sessions_intact(self):
        source = SOURCE.read_text()
        check = source.split('@objc private func checkForUpdates()', 1)[1].split('func applicationShouldTerminate', 1)[0]
        self.assertIn('CFBundleShortVersionString', check)
        self.assertIn('URLSessionConfiguration.ephemeral', check)
        self.assertIn('timeoutIntervalForResource = 20', check)
        self.assertIn('DispatchQueue.main.async', check)
        self.assertIn('检查更新…', source)
        for mutation in ['launcher.', 'reloadPage(', 'webView.', 'showShelf(', 'startShelf(', 'terminate(']:
            self.assertNotIn(mutation, check)


if __name__ == '__main__':
    unittest.main()
