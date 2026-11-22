"""Inference-time aids for two-arm relay checkpoints: a stage gate and a faster playback of each plan."""

import numpy as np

HOME = np.r_[np.zeros(6), 1.]
CLEARANCE_M = 0.11
RELEASE_TICKS = 160
PARK_TICKS = 40
MAX_STRIDE = 3


def validate_assist(assist, task, live):
    """Return the saved form of the requested aids, or None when the run uses the plain policy loop."""
    if assist is None:
        return None
    if not isinstance(assist, dict) or assist.keys() - {"stage_gate", "stride"}:
        raise ValueError("Choose a stage gate, a plan stride, or both.")
    gate, stride = assist.get("stage_gate", False), assist.get("stride", 1)
    if type(gate) is not bool or type(stride) is not int or not 1 <= stride <= MAX_STRIDE:
        raise ValueError(f"Choose a plan stride from 1 to {MAX_STRIDE}.")
    if not gate and stride == 1:
        return None
    if not live:
        raise ValueError("Run aids apply to live Dream-1 runs.")
    if gate and task not in ("relay", "sorting"):
        raise ValueError("The stage gate needs a two-arm relay or sorting task.")
    return {"stage_gate": gate, "stride": stride}


class StageGate:
    """Keep the arm that has no job in the current relay stage parked at home.

    Every demonstration holds the idle arm at its home pose, so both stages start from the same joint state.
    The gate reads the judge's plate-transfer check: until it registers, only the left arm follows the policy.
    Afterwards the left arm may finish its retreat, is parked along a straight joint path, and the right arm
    takes over. The stage signal comes from simulator contacts, so results with the gate are simulator-assisted.
    """

    def __init__(self, env, scorer):
        self.env = env
        self.scorer = scorer
        self.step = env.step
        self.since = 0
        self.start = None
        self.ramp = 0
        self.events = {}

    def install(self):
        self.env.step = self
        return self

    def left_clear(self):
        return self.env.data.site("left_tcp").xpos[2] - self.env.data.body("plate").xpos[2] > CLEARANCE_M

    def __call__(self, left, right):
        left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
        if not self.scorer.transferred:
            return self.step(left, HOME)
        self.since += 1
        self.events.setdefault("handoff_step", self.scorer.steps)
        if self.start is None:
            if self.since < RELEASE_TICKS and not self.left_clear():
                return self.step(np.r_[left[:6], 1.], HOME)
            self.start = np.array(self.env.state()["left"][:6], dtype=float)
        self.ramp = min(PARK_TICKS, self.ramp + 1)
        share = self.ramp / PARK_TICKS
        if share >= 1:
            self.events.setdefault("right_released_step", self.scorer.steps)
        return self.step(np.r_[self.start * (1 - share), 1.], right if share >= 1 else HOME)
