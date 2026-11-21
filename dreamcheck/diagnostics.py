"""Compare policy requests with bounded targets for steps the simulator ran."""

import numpy as np


def summarize_chunk(reply, applied, executed, rtt):
    joints = []
    clipped_rows = np.zeros(executed, dtype=bool)
    for side, key, targets in zip(("left", "right"), ("q_l_chunk", "q_r_chunk"), applied):
        requested = np.asarray(reply[key], dtype=float)[:executed]
        bounded = targets[:executed]
        difference = np.abs(requested - bounded)
        clipped_rows |= np.any(difference > 0, axis=1)
        for column, count in enumerate(np.count_nonzero(difference, axis=0)):
            if count == 0:
                continue
            worst = int(np.argmax(difference[:, column]))
            joints.append({
                "name": f"{side.title()} joint {column + 1}" if column < 6 else f"{side.title()} gripper",
                "unit": "rad" if column < 6 else "",
                "clipped": int(count),
                "correction": float(difference[worst, column]),
                "requested": float(requested[worst, column]),
                "applied": float(bounded[worst, column]),
            })
    return {
        "rtt_ms": round(rtt * 1000),
        "proposed_steps": len(reply["q_l_chunk"]),
        "executed_steps": executed,
        "clipped_steps": int(clipped_rows.sum()),
        "joints": sorted(joints, key=lambda joint: (-joint["clipped"], joint["name"])),
    }


def api_failure(error):
    """Translate structured SDK errors without exposing request data or credentials."""
    reason = getattr(error, "reason", None)
    code = getattr(error, "code", None)
    name = type(error).__name__
    messages = {
        "over_cap": "The account reached its spending limit. Robot commands stopped. Check Billing before retrying.",
        "payment_required": "The account has insufficient credits. Robot commands stopped. Check Billing before retrying.",
        "pool_warming": "Dream Labs is warming this checkpoint. Retry when it is serving.",
        "rate_limited": "Dream Labs rate-limited this request. Wait before retrying or use more steps per response.",
        "pool_full": "Dream Labs has no available capacity for this checkpoint. Retry shortly.",
        "queue_timeout": "Dream Labs did not free capacity before the request timed out.",
        "not_entitled": "This account does not have access to the selected checkpoint.",
        "unknown_checkpoint": "Dream Labs did not recognize the checkpoint ID.",
        "too_large": "Dream Labs rejected the camera payload as too large.",
        "malformed": "Dream Labs rejected the observation format. Check the camera keys and checkpoint embodiment.",
    }
    fallback = {
        "DreamAuthError": "Dream-1 rejected the configured API key.",
        "DreamTimeout": "Dream-1 did not reply before the request deadline.",
        "DreamProtocolError": "The SDK could not reach Dream Labs or read its response.",
        "DreamObservationError": "The SDK rejected the observation before sending it. Check camera keys and the checkpoint embodiment.",
        "DreamActionError": "The SDK rejected the returned actions. Check the checkpoint embodiment.",
    }
    detail = messages.get(reason) or messages.get(code) or fallback.get(name) or f"Dream-1 request stopped ({name})."
    trace = {"type": name}
    for key, value in (("code", code), ("reason", reason)):
        if isinstance(value, str) and 0 < len(value) <= 40 and all(char.isascii() and (char.isalnum() or char in "_-") for char in value):
            trace[key] = value
    return detail, trace
