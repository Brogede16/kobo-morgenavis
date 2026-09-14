"""Bound the entire pipeline, including native code and external API calls."""
import json
import subprocess
import sys
from pathlib import Path


def run_isolated(settings, use_web_search=None, deliver_failure=True):
    request = json.dumps(dict(settings=settings, use_web_search=use_web_search,
                              deliver_failure=deliver_failure))
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("edition_worker.py"))],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        output, _ = process.communicate(request, timeout=int(settings.get("collection", {}).get("max_seconds", 1800)))
    except BaseException:
        process.kill()
        process.wait()  # Do not release the run lock until the old process is dead.
        raise
    if process.returncode:
        raise RuntimeError(f"Avisprocessen stoppede med kode {process.returncode}")
    result = json.loads(output)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result
