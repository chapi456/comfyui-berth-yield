"""
comfyui-berth-yield - ComfyUI nodes "Berth Yield" / "Berth Resume": a cooperative GPU yield around an LLM call.
Unofficial companion to Berth (https://github.com/T-Crypt/berth, docs/leasing.md "A yield"). Apache-2.0.
Standard library only. Configuration from the environment, never from the graph:
  BERTH_URL       Berth gateway (default http://127.0.0.1:9799)
  BERTH_API_KEY   the node's key, or BERTH_KEY_FILE (default ~/.config/berth/api.key, where Berth writes it)
  BERTH_ENGINE    ComfyUI's entry name in berth.toml (default comfyui)
"""

import http.client
import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("berth_yield")
NET_ERRORS = (OSError, http.client.HTTPException)   # unreachable, refused, timeout, cut answer
_state = {"yielded": False}                         # ComfyUI runs one job at a time: Resume ends only the yield we took


class AnyType(str):
    """Matches every ComfyUI type (the usual wildcard trick)."""

    def __ne__(self, other):
        return False


ANY = AnyType("*")


def _key():
    key = os.environ.get("BERTH_API_KEY", "").strip()
    if key:
        return key
    path = Path(os.environ.get("BERTH_KEY_FILE") or Path.home() / ".config" / "berth" / "api.key")
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _engine():
    return os.environ.get("BERTH_ENGINE", "comfyui")


def _call(key, path, body=None, timeout=10.0):
    """(status, text) of a Berth gateway call ; an HTTP error is a status, not an exception."""
    url = os.environ.get("BERTH_URL", "http://127.0.0.1:9799").rstrip("/") + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _free_models():
    import comfy.model_management as mm  # ComfyUI only: /free waits for the job boundary, this does not
    mm.unload_all_models()
    mm.soft_empty_cache()


def yield_card(seconds, free=None):
    """True when Berth took the yield. Any failure is a warning: the workflow goes on as without Berth."""
    _state["yielded"] = False
    key = _key()
    if not key:
        log.warning("[berth_yield] no Berth key (BERTH_API_KEY, BERTH_KEY_FILE, ~/.config/berth/api.key): pass-through")
        return False
    try:
        status, text = _call(key, "/v1/berth/board", timeout=3.0)
        if status != 200:
            log.warning(f"[berth_yield] Berth board answered {status}: pass-through ({text[:200]})")
            return False
        try:
            (free or _free_models)()              # before the yield: the park that follows must find the card free
        except Exception as e:                    # a failed free must not end the job
            log.warning(f"[berth_yield] freeing ComfyUI's models failed: pass-through without yield ({e})")
            return False
        status, text = _call(key, "/v1/berth/yield", {"engine": _engine(), "seconds": int(seconds)}, timeout=60.0)
    except NET_ERRORS as e:
        log.warning(f"[berth_yield] Berth unreachable: pass-through ({e})")
        return False
    if status != 200:
        log.warning(f"[berth_yield] yield answered {status}: pass-through ({text[:200]})")
        return False
    log.info(f"[berth_yield] card yielded for {int(seconds)} s")
    _state["yielded"] = True
    return True


def resume_card(timeout=900.0):
    """True once ComfyUI holds the card again (Berth answers only then). No yield of ours: nothing is called."""
    key = _key()
    if not key or not _state["yielded"]:
        return False
    _state["yielded"] = False
    try:
        status, text = _call(key, "/v1/berth/yield", {"engine": _engine(), "end": True}, timeout=timeout)
    except NET_ERRORS as e:
        log.warning(f"[berth_yield] Berth unreachable at resume: pass-through ({e})")
        return False
    if status != 200:
        log.warning(f"[berth_yield] end of yield answered {status}: pass-through ({text[:200]})")
        return False
    log.info("[berth_yield] card back")
    return True


class BerthYield:
    CATEGORY = "utils/berth"
    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)
    FUNCTION = "run"
    DESCRIPTION = "Frees ComfyUI's models and yields the card to Berth, so the LLM node wired after it gets the GPU."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": (ANY, {"tooltip": "Anything: passed through unchanged to the LLM node."}),
                             "seconds": ("INT", {"default": 120, "min": 10, "max": 3600,
                                                 "tooltip": "Yield length; a bit more than the longest LLM call."})}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")                       # never cached: a cached yield would leave the LLM call without one

    def run(self, value, seconds):
        yield_card(seconds)
        return (value,)


class BerthResume:
    CATEGORY = "utils/berth"
    RETURN_TYPES = (ANY,)
    RETURN_NAMES = ("value",)
    FUNCTION = "run"
    DESCRIPTION = "Ends the yield: returns once ComfyUI holds the card again; the next nodes reload their models."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"value": (ANY, {"tooltip": "The LLM node's output, passed through unchanged."})}}

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def run(self, value):
        resume_card()
        return (value,)


NODE_CLASS_MAPPINGS = {"BerthYield": BerthYield, "BerthResume": BerthResume}
NODE_DISPLAY_NAME_MAPPINGS = {"BerthYield": "Berth Yield (before LLM)", "BerthResume": "Berth Resume (after LLM)"}
