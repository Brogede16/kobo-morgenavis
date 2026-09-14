"""Isolated edition process; stdout carries a small result, logs use stderr."""
import json
import logging
import os
import signal
import sys

from morning_news import run_edition


def main():
    # Render runs Linux. Kill the child if Gunicorn exits during a deploy,
    # so an orphan cannot upload over an edition started by the new worker.
    if sys.platform == "linux":
        import ctypes
        parent = os.getppid()
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL) != 0:
            raise OSError(ctypes.get_errno(), "Could not bind edition lifetime to parent")
        if parent == 1 or os.getppid() != parent:
            raise RuntimeError("Edition parent exited during startup")
    logging.basicConfig(level=logging.INFO)
    request = json.load(sys.stdin)
    try:
        result = run_edition(**request)
        response = {key: result.get(key) for key in ("articles", "drive_file", "github_saved")}
    except Exception as exc:
        logging.exception("Edition failed")
        response = {"error": f"{type(exc).__name__}: {exc}"[:400]}
    print(json.dumps(response))


if __name__ == "__main__":
    main()
