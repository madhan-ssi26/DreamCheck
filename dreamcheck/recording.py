"""Summarize local LeRobot episode telemetry without assuming a robot model."""

import math


def analyze_parquet(payload):
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    file = pq.ParquetFile(pa.BufferReader(payload))
    names = set(file.schema_arrow.names)
    required = {"timestamp", "observation.state", "action"}
    if not required <= names:
        raise ValueError("The episode needs timestamp, observation.state and action columns.")
    if not 1 <= file.metadata.num_rows <= 100_000:
        raise ValueError("Select an episode with 1–100,000 frames.")
    columns = required | (names & {"rollout.chunk", "episode_index"})
    table = file.read(columns=sorted(columns))
    if "episode_index" in columns and len(set(table["episode_index"].to_pylist())) != 1:
        raise ValueError("Select a LeRobot v2.1 per-episode file. This parquet contains multiple episodes.")
    times = np.asarray(table["timestamp"].to_pylist(), dtype=float)
    state = np.asarray(table["observation.state"].to_pylist(), dtype=float)
    actions = np.asarray(table["action"].to_pylist(), dtype=float)
    if times.ndim != 1 or not np.isfinite(times).all() or np.any(np.diff(times) < 0):
        raise ValueError("Episode timestamps must be finite and ordered.")
    for vector in (state, actions):
        if vector.ndim != 2 or not 1 <= vector.shape[1] <= 256 or not np.isfinite(vector).all():
            raise ValueError("State and action must be finite vectors with at most 256 channels.")
    times -= times[0]
    delta = np.max(np.abs(np.diff(state, axis=0, prepend=state[:1])), axis=1)
    chunk = table["rollout.chunk"].to_pylist() if "rollout.chunk" in columns else None
    windows = []
    for indices in np.array_split(np.arange(len(times)), min(120, len(times))):
        i = int(indices[np.argmax(delta[indices])]); start = int(indices[0]); end = int(indices[-1])
        windows.append({"time": float(times[i]), "start": float(times[start]), "end": float(times[end]),
                        "motion": float(delta[i]), "state": state[i].tolist(), "action": actions[i].tolist(),
                        "chunk": chunk[i] if chunk is not None else None})
    if not all(math.isfinite(point["motion"]) for point in windows):
        raise ValueError("Recorded state changes exceed the supported numeric range.")
    return {"frames": len(times), "duration": float(times[-1]), "state_width": state.shape[1],
            "action_width": actions.shape[1], "windows": windows}
