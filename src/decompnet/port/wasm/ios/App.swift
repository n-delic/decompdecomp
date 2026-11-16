// The exported game uses only bundled web content. Device signing is chosen in Xcode by its owner.
import UIKit
import WebKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = GameController()
        window.makeKeyAndVisible()
        self.window = window
        return true
    }
}

final class GameController: UIViewController, WKNavigationDelegate {
    private let server = LocalServer()
    private var web: WKWebView!
    private let message = UILabel()
    private let retry = UIButton(type: .system)

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.allowsInlineMediaPlayback = true
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.scrollView.contentInsetAdjustmentBehavior = .never
        message.numberOfLines = 0
        message.textAlignment = .center
        retry.setTitle("Try again", for: .normal)
        retry.addTarget(self, action: #selector(start), for: .touchUpInside)
        for item in [web!, message, retry] {
            item.translatesAutoresizingMaskIntoConstraints = false
            view.addSubview(item)
        }
        let safe = view.safeAreaLayoutGuide
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: safe.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: safe.trailingAnchor),
            web.topAnchor.constraint(equalTo: safe.topAnchor),
            web.bottomAnchor.constraint(equalTo: safe.bottomAnchor),
            message.leadingAnchor.constraint(equalTo: safe.leadingAnchor, constant: 24),
            message.trailingAnchor.constraint(equalTo: safe.trailingAnchor, constant: -24),
            message.centerYAnchor.constraint(equalTo: safe.centerYAnchor),
            retry.topAnchor.constraint(equalTo: message.bottomAnchor, constant: 16),
            retry.centerXAnchor.constraint(equalTo: safe.centerXAnchor)
        ])
        start()
    }

    @objc private func start() {
        web.isHidden = true
        retry.isHidden = true
        message.isHidden = false
        message.text = "Opening the game…"
        server.start { [weak self] error in
            guard let self = self else { return }
            if let error = error { self.failed(error); return }
            self.web.load(URLRequest(url: LocalServer.url))
        }
    }

    private func failed(_ error: Error) {
        web.isHidden = true
        message.isHidden = false
        message.text = "The game could not open. \(error.localizedDescription) Close and reopen the app, or try again."
        retry.isHidden = false
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        message.isHidden = true
        retry.isHidden = true
        web.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        failed(error)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed(error) }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        failed(NSError(domain: "Game", code: 1, userInfo: [NSLocalizedDescriptionKey:
            "The game stopped. Try a lower rendering resolution; this device may not have enough memory."]))
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        decisionHandler(url?.scheme == "http" && url?.host == "localhost" && url?.port == LocalServer.port
                        ? .allow : .cancel)
    }
}
