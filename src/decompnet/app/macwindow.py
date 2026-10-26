"""Native macOS WKWebView, launched separately by window.py with the app's Python.

Cocoa/WebKit are macOS-only dependencies. Import them inside create_window() so navigation policy can also be tested on Linux.
The per-launch secret travels only in the private web view's user agent, never in an external browser's URL.
"""

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

TOKEN_AGENT = "decompdecomp-window/"
ASK_PAGE = 'typeof decompClose === "function" ? String(decompClose()) : "close"'


def navigation_kind(origin: str, url: str) -> str:
    """Same-origin pages stay private; only HTTP(S) links may leave for the user's browser."""
    target, source = urlsplit(url), urlsplit(origin)
    if target.scheme not in ("http", "https"):
        return "reject"
    if (target.scheme, target.netloc) == (source.scheme, source.netloc):
        return "internal"
    return "external"


def export_url(origin: str, url: str) -> bool:
    source = urlsplit(origin)
    return navigation_kind(origin, url) == "internal" or url.startswith(f"blob:{source.scheme}://{source.netloc}/")


def create_window(args):
    import objc
    from AppKit import (
        NSAlert,
        NSAlertFirstButtonReturn,
        NSApplication,
        NSEvent,
        NSEventTypeApplicationDefined,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSModalResponseOK,
        NSOpenPanel,
        NSSavePanel,
        NSTerminateCancel,
        NSTextField,
        NSWindow,
    )
    from Foundation import NSMakeRect, NSObject, NSProcessInfo
    from WebKit import WKWebView, WKWebViewConfiguration

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(0)  # regular Dock app, including when invoked from a terminal
    NSProcessInfo.processInfo().setProcessName_(args.title)
    if args.icon:
        icon = NSImage.alloc().initWithContentsOfFile_(args.icon)
        if icon:
            app.setApplicationIconImage_(icon)

    class MacWindowDelegate(NSObject):
        @objc.typedSelector(objc._C_NSBOOL + b"@:@")
        def windowShouldClose_(self, _window):
            if not self.asking:
                self.asking = True
                def answered(answer, error):
                    self.asking = False
                    if error or answer == "close":
                        self.close()
                self.view.evaluateJavaScript_completionHandler_(ASK_PAGE, answered)
            return False

        @objc.typedSelector(b"Q@:@")
        def applicationShouldTerminate_(self, _application):
            self.windowShouldClose_(self.window)
            return NSTerminateCancel

        @objc.typedSelector(objc._C_NSBOOL + b"@:@")
        def applicationShouldTerminateAfterLastWindowClosed_(self, _application):
            return True

        def close(self):
            self.window.orderOut_(None)
            app.stop_(None)
            # stop: alone leaves nextEventMatchingMask waiting when close came from a timer or JS callback.
            wake = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
            app.postEvent_atStart_(wake, True)

        def present(self):
            self.window.makeKeyAndOrderFront_(None)
            app.activateIgnoringOtherApps_(True)

        def reload_(self, _sender):
            self.view.reload()

        def quit_(self, _sender):
            self.windowShouldClose_(self.window)

        def webView_decidePolicyForNavigationAction_decisionHandler_(self, _view, action, decide):
            url = str(action.request().URL().absoluteString())
            kind = navigation_kind(args.url, url)
            if kind == "external":
                webbrowser.open(url)
            # Downloads, including same-origin blob URLs, are exports rather than pages with our secret.
            if action.shouldPerformDownload() and export_url(args.url, url):
                decide(2)  # WKNavigationActionPolicyDownload
            elif kind == "internal" and action.targetFrame() is None:
                decide(0)
                self.view.loadRequest_(action.request())
            else:
                decide(1 if kind == "internal" else 0)

        def webView_decidePolicyForNavigationResponse_decisionHandler_(self, _view, response, decide):
            headers = response.response().allHeaderFields() if hasattr(response.response(), "allHeaderFields") else {}
            attachment = any(str(k).lower() == "content-disposition" and "attachment" in str(v).lower()
                             for k, v in headers.items())
            decide(2 if attachment or not response.canShowMIMEType() else 1)

        def webView_navigationAction_didBecomeDownload_(self, _view, _action, download):
            download.setDelegate_(self)

        def webView_navigationResponse_didBecomeDownload_(self, _view, _response, download):
            download.setDelegate_(self)

        def download_decideDestinationUsingResponse_suggestedFilename_completionHandler_(self, _download, _response, name, done):
            panel = NSSavePanel.savePanel()
            panel.setNameFieldStringValue_(Path(str(name)).name)
            panel.setCanCreateDirectories_(True)
            done(panel.URL() if panel.runModal() == NSModalResponseOK else None)

        def webView_runOpenPanelWithParameters_initiatedByFrame_completionHandler_(self, _view, parameters, _frame, done):
            panel = NSOpenPanel.openPanel()
            panel.setAllowsMultipleSelection_(parameters.allowsMultipleSelection())
            panel.setCanChooseDirectories_(parameters.allowsDirectories())
            panel.setCanChooseFiles_(True)
            done(panel.URLs() if panel.runModal() == NSModalResponseOK else None)

        def webView_runJavaScriptAlertPanelWithMessage_initiatedByFrame_completionHandler_(self, _view, message, _frame, done):
            alert = NSAlert.alloc().init()
            alert.setMessageText_(message)
            alert.runModal()
            done()

        def webView_runJavaScriptConfirmPanelWithMessage_initiatedByFrame_completionHandler_(self, _view, message, _frame, done):
            alert = NSAlert.alloc().init()
            alert.setMessageText_(message)
            alert.addButtonWithTitle_("Continue")
            alert.addButtonWithTitle_("Cancel")
            done(alert.runModal() == NSAlertFirstButtonReturn)  # NSAlertFirstButtonReturn = 1000

        def webView_runJavaScriptTextInputPanelWithPrompt_defaultText_initiatedByFrame_completionHandler_(self, _view, prompt, text, _frame, done):
            alert = NSAlert.alloc().init()
            alert.setMessageText_(prompt)
            alert.addButtonWithTitle_("OK")
            alert.addButtonWithTitle_("Cancel")
            field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 360, 24))
            field.setStringValue_(text or "")
            alert.setAccessoryView_(field)
            done(field.stringValue() if alert.runModal() == NSAlertFirstButtonReturn else None)

    delegate = MacWindowDelegate.alloc().init()
    delegate.asking = False
    config = WKWebViewConfiguration.alloc().init()
    delegate.view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 1440, 920), config)
    delegate.view.setCustomUserAgent_("Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 "
                                      + TOKEN_AGENT + os.environ.get("DECOMP_WINDOW_TOKEN", ""))
    delegate.view.setNavigationDelegate_(delegate)
    delegate.view.setUIDelegate_(delegate)
    if args.dev and hasattr(delegate.view, "setInspectable_"):
        delegate.view.setInspectable_(True)
    delegate.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(NSMakeRect(0, 0, 1440, 920), 15, 2, False)
    delegate.window.setTitle_(args.title)
    delegate.window.setReleasedWhenClosed_(False)
    delegate.window.setContentView_(delegate.view)
    delegate.window.setDelegate_(delegate)
    delegate.window.center()
    app.setDelegate_(delegate)

    menu = NSMenu.alloc().init()
    for title, entries in [(args.title, [("Quit " + args.title, "quit:", "q")]),
                           ("Edit", [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                                     ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")]),
                           ("View", [("Reload", "reload:", "r")])]:
        parent = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        submenu = NSMenu.alloc().initWithTitle_(title)
        for label, action, key in entries:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, action, key)
            if action in ("quit:", "reload:"):
                item.setTarget_(delegate)
            submenu.addItem_(item)
        parent.setSubmenu_(submenu)
        menu.addItem_(parent)
    app.setMainMenu_(menu)

    return app, delegate


def run(args) -> int:
    from Foundation import NSURL, NSOperationQueue, NSURLRequest

    app, delegate = create_window(args)

    def commands():
        for line in sys.stdin:
            if line.strip() == "present":
                NSOperationQueue.mainQueue().addOperationWithBlock_(delegate.present)
            elif line.strip() == "close":
                break
        NSOperationQueue.mainQueue().addOperationWithBlock_(delegate.close)

    threading.Thread(target=commands, daemon=True, name="window-commands").start()
    delegate.view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(args.url)))
    delegate.present()
    app.run()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", default="decompdecomp")
    parser.add_argument("--window-class", default="decompdecomp")
    parser.add_argument("--icon", default="")
    parser.add_argument("--dev", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
