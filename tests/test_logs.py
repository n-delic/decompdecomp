"""The app's log is always on and holds what went wrong (app/support.py file_log): the app once quit at start
without a word, and finding out why meant guessing."""

import logging
import threading

from decompnet.app.support import file_log


def test_the_log_holds_debug_messages_other_libraries_and_crashes_in_threads(tmp_path):
    path = tmp_path / "logs" / "decomp.log"
    log = file_log(path)
    log.debug("our own debug line")
    logging.getLogger("uvicorn.error").error("the web server's error")
    worker = threading.Thread(target=lambda: 1 / 0, name="doomed")
    worker.start()
    worker.join()
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text()
    assert "our own debug line" in text and "the web server's error" in text
    assert "uncaught error in thread doomed" in text and "ZeroDivisionError" in text
    assert file_log(path) is log  # once: a second call adds no second handler
    assert sum(getattr(h, "baseFilename", None) == str(path) for h in logging.getLogger().handlers) == 1


def test_the_web_servers_errors_reach_the_log(tmp_path):
    """uvicorn's own logging setup kept its errors in a terminal of their own, never in the app's log."""
    from decompnet.app import cli

    path = tmp_path / "logs" / "decomp.log"
    file_log(path)
    cli.server_config(lambda *a: None)  # what the app runs the server with (it sets logging up as it's made)
    logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    logging.getLogger("uvicorn.access").info("GET /api/live 200")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = path.read_text()
    assert "Exception in ASGI application" in text and "GET /api/live" not in text
