// A stable loopback origin keeps IndexedDB ROMs and saves across launches. No interface on the LAN is opened.
import Foundation
import Network

final class LocalServer {
    static let port = 18643
    static let url = URL(string: "http://localhost:\(port)/")!
    private let queue = DispatchQueue(label: "game.bundled-files")
    private var listener: NWListener?
    private let root: URL
    private let port: UInt16

    init(root: URL = Bundle.main.resourceURL!.appendingPathComponent("Web"), port: UInt16 = UInt16(LocalServer.port)) {
        self.root = root.resolvingSymlinksInPath()
        self.port = port
    }

    func start(completion: @escaping (Error?) -> Void) {
        queue.async { [self] in
            if let listener = self.listener, case .ready = listener.state {
                DispatchQueue.main.async { completion(nil) }
                return
            }
            self.listener?.cancel()
            do {
                let parameters = NWParameters.tcp
                parameters.requiredLocalEndpoint = .hostPort(host: .ipv4(.loopback),
                                                             port: NWEndpoint.Port(rawValue: self.port)!)
                let listener = try NWListener(using: parameters)
                self.listener = listener
                var reported = false
                listener.stateUpdateHandler = { state in
                    guard !reported else { return }
                    switch state {
                    case .ready:
                        reported = true
                        DispatchQueue.main.async { completion(nil) }
                    case .failed(let error), .waiting(let error):
                        reported = true
                        listener.cancel()
                        DispatchQueue.main.async { completion(error) }
                    default: break
                    }
                }
                listener.newConnectionHandler = { [weak self] connection in self?.accept(connection) }
                listener.start(queue: self.queue)
            } catch { DispatchQueue.main.async { completion(error) } }
        }
    }

    private func accept(_ connection: NWConnection) {
        connection.start(queue: queue)
        // A stalled client must not keep a connection alive indefinitely.
        queue.asyncAfter(deadline: .now() + 15) { connection.cancel() }
        receive(connection, header: Data())
    }

    private func receive(_ connection: NWConnection, header: Data) {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 8192) { [weak self] data, _, ended, error in
            guard let self = self else { connection.cancel(); return }
            var header = header
            if let data = data { header.append(data) }
            guard header.count <= 16384 else { connection.cancel(); return }
            if header.range(of: Data("\r\n\r\n".utf8)) != nil {
                self.respond(connection, request: String(decoding: header, as: UTF8.self))
            } else if ended || error != nil { connection.cancel() }
            else { self.receive(connection, header: header) }
        }
    }

    private func respond(_ connection: NWConnection, request: String) {
        let parts = request.components(separatedBy: "\r\n")[0].split(separator: " ")
        guard parts.count == 3, parts[0] == "GET",
              let components = URLComponents(string: String(parts[1])), components.host == nil,
              components.scheme == nil, components.path.hasPrefix("/") else {
            send(connection, status: "400 Bad Request", type: "text/plain", body: Data())
            return
        }
        let path = components.path == "/" ? "index.html" : String(components.path.dropFirst())
        let file = root.appendingPathComponent(path).standardizedFileURL.resolvingSymlinksInPath()
        guard file.path.hasPrefix(root.path + "/"),
              let data = try? Data(contentsOf: file, options: .mappedIfSafe) else {
            send(connection, status: "404 Not Found", type: "text/plain", body: Data())
            return
        }
        let types = ["html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8",
                     "css": "text/css; charset=utf-8", "json": "application/json", "wasm": "application/wasm"]
        send(connection, status: "200 OK", type: types[file.pathExtension] ?? "application/octet-stream", body: data)
    }

    private func send(_ connection: NWConnection, status: String, type: String, body: Data) {
        var response = Data(("HTTP/1.1 \(status)\r\nContent-Type: \(type)\r\nContent-Length: \(body.count)\r\n"
            + "Cache-Control: no-cache\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n").utf8)
        response.append(body)
        connection.send(content: response, completion: .contentProcessed { _ in connection.cancel() })
    }
}
