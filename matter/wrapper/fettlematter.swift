// FettleMatter.app main executable: spawns the venv matter-server (or any
// passed command) as a child and forwards its exit. Exists so macOS TCC
// attributes the child's Local Network traffic to THIS bundle — giving the
// Matter stack a stable identity that can be granted Local Network access
// (raw python under tmux/launchd gets silently denied with no way to allow).
import Foundation

let args = Array(CommandLine.arguments.dropFirst())
guard !args.isEmpty else {
    FileHandle.standardError.write("usage: fettlematter <program> [args...]\n".data(using: .utf8)!)
    exit(64)
}

let p = Process()
p.executableURL = URL(fileURLWithPath: args[0])
p.arguments = Array(args.dropFirst())
p.standardOutput = FileHandle.standardOutput
p.standardError = FileHandle.standardError

signal(SIGTERM, SIG_IGN)
let src = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
src.setEventHandler { p.terminate() }
src.resume()

do {
    try p.run()
} catch {
    FileHandle.standardError.write("spawn failed: \(error)\n".data(using: .utf8)!)
    exit(71)
}
p.waitUntilExit()
exit(p.terminationStatus)
