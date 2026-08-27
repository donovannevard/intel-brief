"""GPU contention guard: lets analyze() pause itself when something else
(games, etc.) is actively using the GPU for graphics work, rather than
competing with interactive use -- the exact problem that tanked a Steam
game's framerate twice before this existed.

Uses `intel_gpu_top -J` to see real per-engine GPU utilization. Requires
CAP_PERFMON on the binary (granted via `setcap cap_perfmon=ep
$(which intel_gpu_top)` on the host -- not root, not interactive pkexec,
works from an unprivileged service). If intel_gpu_top isn't available or the
check fails for any reason, this fails OPEN (assumes not busy) rather than
blocking the pipeline indefinitely on a monitoring failure.

Checks the Render/3D engine specifically -- that's what games/graphics use.
Our own LLM inference load shows up on the Compute engine instead, so this
naturally doesn't mistake our own work for "someone else is using the GPU."

GPU_BUSY_THRESHOLD_PERCENT is an initial estimate (30%), not independently
validated against real gameplay -- same caveat as the clustering threshold
in pipeline/cluster.py. May need tuning based on observed false positives/
negatives once this has run against real gaming sessions."""

import json
import logging
import shutil
import subprocess

from intel_brief.config import settings

log = logging.getLogger("intel_brief.gpu_guard")

GPU_BUSY_THRESHOLD_PERCENT = 30.0
SAMPLE_COUNT = 3
SAMPLE_INTERVAL_MS = 500


def _enabled() -> tuple[bool, str]:
    """GPU_GUARD: auto (use it when intel_gpu_top exists) | on | off.

    The guard is Intel-specific -- it reads per-engine utilisation from
    `intel_gpu_top`. On any other GPU that binary does not exist, and while
    the check already fails open, silently doing nothing would leave someone
    believing they had contention protection they do not have. "auto" is
    honest about it and the Status page can say which.
    """
    setting = settings.gpu_guard.strip().lower()
    if setting == "off":
        return False, "GPU_GUARD=off"
    if shutil.which("intel_gpu_top") is None:
        if setting == "on":
            return False, "GPU_GUARD=on but intel_gpu_top is not installed"
        return False, "no intel_gpu_top on this machine (GPU_GUARD=auto)"
    return True, "intel_gpu_top"


def guard_status() -> tuple[bool, str]:
    """(active, reason) -- for the Status page."""
    return _enabled()


def check_gpu_busy_with_other_work() -> tuple[bool, str]:
    """Returns (is_busy, reason)."""
    active, why = _enabled()
    if not active:
        return False, why
    try:
        result = subprocess.run(
            ["intel_gpu_top", "-J", "-s", str(SAMPLE_INTERVAL_MS), "-n", str(SAMPLE_COUNT)],
            capture_output=True, text=True,
            timeout=(SAMPLE_INTERVAL_MS / 1000 * SAMPLE_COUNT) + 5,
        )
        samples = json.loads(result.stdout)
        if not samples:
            return False, "no samples captured"

        # Skip the first sample (often a startup artifact), average the rest.
        usable = samples[1:] or samples
        readings = [float(s["engines"]["Render/3D"]["busy"]) for s in usable]
        avg_busy = sum(readings) / len(readings)

        if avg_busy > GPU_BUSY_THRESHOLD_PERCENT:
            return True, f"Render/3D engine at {avg_busy:.1f}% (threshold {GPU_BUSY_THRESHOLD_PERCENT}%)"
        return False, f"clear ({avg_busy:.1f}%)"
    except Exception as e:
        log.warning("GPU busy check failed (%s: %s), assuming clear", type(e).__name__, e)
        return False, f"check failed: {e}"
