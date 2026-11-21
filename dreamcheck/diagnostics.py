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
