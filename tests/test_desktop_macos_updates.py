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
    var record:[String:Any] = ["name":name,"state":"uploaded",
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
case .available(let version,let page):
    precondition(version == "0.1.10" && page.absoluteString == "https://github.com/NingCui29/story-skill/releases/tag/v0.6.18")
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
case .available(let version,_):precondition(version == "0.1.7")
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
let session=URLSession(configuration:.ephemeral), task=session.dataTask(with:WorkbenchUpdate.request())
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
