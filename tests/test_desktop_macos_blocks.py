"""Compile and exercise the real Mac downloader with isolated HTTP/cache fixtures."""
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MacBlockDownloadTests(unittest.TestCase):
    def test_range_resume_corrupt_cache_fallback_manifest_and_final_identity(self):
        if sys.platform != 'darwin' or not shutil.which('xcrun'):
            self.skipTest('macOS Swift compiler required')
        source = (ROOT / 'desktop/macos/StoryWorkbench.swift').read_text()
        policy = source[source.index('struct WorkbenchClientVersion:'):source.index('\n@MainActor')]
        fixture = r'''
var payload = Data(count:524_305)
for index in 0..<payload.count { payload[index] = UInt8((index * 31 + index / 65_536) % 251) }
let name = "story-workbench-0.2.0-macos-x86_64.zip"
let address = URL(string:"https://github.com/NingCui29/story-skill/releases/download/v-test/"+name)!
let root = URL(fileURLWithPath:CommandLine.arguments[1],isDirectory:true).resolvingSymlinksInPath()
let stage = root.appendingPathComponent("stage"), cache = root.appendingPathComponent("cache")
try! FileManager.default.createDirectory(at:stage,withIntermediateDirectories:true)
let digest = WorkbenchDownloadPlan.hash(payload)
let records:[[String:Any]] = stride(from:0,to:payload.count,by:65_536).map { offset in
    let data=payload.subdata(in:offset..<min(payload.count,offset+65_536))
    return ["offset":offset,"size":data.count,"sha256":WorkbenchDownloadPlan.hash(data)]
}
let planData=try! JSONSerialization.data(withJSONObject:["schema":1,"file":name,"size":payload.count,"sha256":digest,"chunks":records])
var package=WorkbenchUpdatePackage(version:"0.2.0",releaseURL:URL(string:"https://github.com/NingCui29/story-skill/releases/tag/v-test")!,name:name,
    url:address,size:Int64(payload.count),digest:digest,checksumURL:nil,checksumSize:nil)
package.planURL=URL(string:address.absoluteString+".download.json")!
package.planSize=Int64(planData.count);package.planDigest=WorkbenchDownloadPlan.hash(planData)
let plan=try! WorkbenchDownloadPlan.parse(planData,package:package,digest:digest)
final class Fixture:URLProtocol,@unchecked Sendable {
    static var mode="good",requests:[String]=[]
    static let mutex=NSLock()
    private let mutex=NSLock()
    private var stopped=false
    override class func canInit(with request:URLRequest)->Bool {true}
    override class func canonicalRequest(for request:URLRequest)->URLRequest {request}
    override func startLoading() {
        let mode=Self.mode,request=self.request
        DispatchQueue.global().async { [self] in
            guard let url=request.url else {fatalError()}
            let range=request.value(forHTTPHeaderField:"Range")
            Self.mutex.lock();Self.requests.append(range ?? "full");Self.mutex.unlock()
            if url.path.hasSuffix(".json") {
                let data=mode == "manifest" ? Data(repeating:32,count:planData.count) : planData
                client?.urlProtocol(self,didReceive:HTTPURLResponse(url:url,statusCode:200,httpVersion:nil,headerFields:["Content-Length":String(data.count)])!,cacheStoragePolicy:.notAllowed)
                client?.urlProtocol(self,didLoad:data);client?.urlProtocolDidFinishLoading(self);return
            }
            var data=payload,status=200,headers=["Content-Length":String(payload.count)]
            if let range,mode != "unsupported" {
                let parts=range.dropFirst(6).split(separator:"-").map{Int($0)!},from=parts[0],to=parts[1]
                data=payload.subdata(in:from..<(to+1));status=206
                headers=["Content-Length":String(data.count),"Content-Range":"bytes \(mode == "range" ? from+1:from)-\(to)/\(payload.count)"]
                if mode == "corrupt" {data[0] ^= 1}
                if mode == "gzip" {headers["Content-Encoding"]="gzip"}
            }
            if mode == "http" {status=503}
            client?.urlProtocol(self,didReceive:HTTPURLResponse(url:url,statusCode:status,httpVersion:nil,headerFields:headers)!,cacheStoragePolicy:.notAllowed)
            if mode == "slow" {client?.urlProtocol(self,didLoad:Data(data.prefix(4096)));return}
            Thread.sleep(forTimeInterval:0.005)
            mutex.lock();let active = !stopped;mutex.unlock();if !active {return}
            client?.urlProtocol(self,didLoad:mode == "short" ? Data(data.prefix(4096)):data)
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {mutex.lock();stopped=true;mutex.unlock()}
}
var attempts=0
func attempt(_ mode:String,_ selected:WorkbenchUpdatePackage=package,cancel:Bool=false)->[String] {
    Fixture.mode=mode;Fixture.requests=[]
    let config=URLSessionConfiguration.ephemeral;config.protocolClasses=[Fixture.self]
    var terminal="",reused:Int64=0
    var downloader:WorkbenchUpdateDownload!
    downloader=WorkbenchUpdateDownload(package:selected,configuration:config,stagingParent:stage,cacheParent:cache) { event in
        switch event {
        case .preparingChecksum,.stage,.verifying:break
        case .progress(let value):
            precondition(value.received<=value.total)
            reused=max(reused,value.reused)
            if cancel && value.received>value.reused {downloader.cancel()}
        case .completed(let file,let actual):
            precondition(actual==digest && (try! Data(contentsOf:file))==payload)
            try! WorkbenchUpdateFile.verify(file,size:Int64(payload.count),digest:digest)
            try! FileManager.default.removeItem(at:file.deletingLastPathComponent());terminal="completed"
        case .cancelled:terminal="cancelled"
        case .failed(let error):precondition(!error.isEmpty);terminal="failed"
        }
    }
    downloader.begin();let deadline=Date().addingTimeInterval(15)
    while terminal.isEmpty && Date()<deadline {_=RunLoop.main.run(mode:.default,before:Date().addingTimeInterval(0.01))}
    precondition(!terminal.isEmpty,"timed out "+mode)
    precondition((try! FileManager.default.contentsOfDirectory(atPath:stage.path)).isEmpty)
    let expected=cancel ? "cancelled" : ["good","unsupported"].contains(mode) ? "completed":"failed"
    precondition(terminal==expected,"unexpected "+mode+" "+terminal)
    if cancel {precondition(reused>0)}
    attempts += 1;return Fixture.requests
}
// Pause a transfer with one previously verified block. Partial bytes are not cached.
do {let store=try! WorkbenchBlockCache(cache);let block=plan.chunks[0];try! store.save(block,data:payload.subdata(in:0..<block.size))}
_=attempt("slow",cancel:true)
let resumed=attempt("good")
precondition(!resumed.contains("bytes=0-65535"))
let repeated=attempt("good")
precondition(!repeated.contains(where:{$0.hasPrefix("bytes=")}),"cache-only repeat downloaded blocks")
// Corrupt an existing block; only that block is fetched again.
let first=plan.chunks[0]
try! Data(repeating:0,count:first.size).write(to:cache.appendingPathComponent(first.sha256+".chunk"))
let repaired=attempt("good")
precondition(repaired.filter{$0.hasPrefix("bytes=")}.count==1)
// Every failure uses a clean cache so the server behavior is actually observed.
for mode in ["unsupported","corrupt","range","gzip","http","short","manifest"] {
    try! FileManager.default.removeItem(at:cache)
    let requests=attempt(mode)
    if mode=="unsupported" {precondition(requests.filter{$0=="full"}.count==2)} // manifest + full installer
}
// A cached symlink is rejected, and the external target remains unchanged.
if FileManager.default.fileExists(atPath:cache.path) {try! FileManager.default.removeItem(at:cache)}
let target=root.appendingPathComponent("external")
try! Data("untouched".utf8).write(to:target)
do {let store=try! WorkbenchBlockCache(cache);_ = store}
try! FileManager.default.createSymbolicLink(at:cache.appendingPathComponent(first.sha256+".chunk"),withDestinationURL:target)
_=attempt("good")
precondition((try! String(contentsOf:target,encoding:.utf8))=="untouched")
print("verified block fixtures passed: \(attempts)")
'''
        with tempfile.TemporaryDirectory(prefix='workbench-blocks-') as raw:
            folder = Path(raw)
            program = folder / 'fixture.swift'
            executable = folder / 'fixture'
            program.write_text('import Foundation\n' + policy + fixture)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', '-target',
                                    platform.machine() + '-apple-macosx13.0', str(program), '-o', str(executable)],
                                   capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable), str(folder)], capture_output=True, text=True, timeout=90)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn('verified block fixtures passed: 12', ran.stdout)
