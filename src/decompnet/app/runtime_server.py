"""Start application cleanup before waiting for long-lived HTTP/MCP connections."""
import os
import signal
import threading

import uvicorn


class Server(uvicorn.Server):
    def __init__(self, config, quit_app):
        super().__init__(config)
        self.quit_app = quit_app
        self.shutdown_requested = False

    def handle_exit(self, sig, frame):
        # Uvicorn normally drains connections before returning to cli's finally.
        # MCP streams can stay open indefinitely, so start the app's bounded,
        # database-aware cleanup now. Never acquire a database/logging lock in a
        # signal handler, or replay SIGINT while cleanup still owns transactions.
        self.should_exit = True
        if self.shutdown_requested:
            message = '[shutdown] Shutdown is already in progress; cleanup is still running.\n'
        else:
            self.shutdown_requested = True
            reason = 'Ctrl-C' if sig == signal.SIGINT else 'Termination signal'
            message = f'[shutdown] {reason} received. Stopping workers and preserving saved progress...\n'
        try:
            os.write(2, message.encode())
        except OSError:
            pass  # A closed terminal must not prevent cleanup.
        if not hasattr(self, '_shutdown_thread'):
            self._shutdown_thread = threading.Thread(target=self.quit_app, daemon=True, name='app-shutdown')
            self._shutdown_thread.start()
