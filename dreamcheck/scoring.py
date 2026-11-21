"""Physical scoring for fruit placed in the YAM bowl."""

import numpy as np

TARGETS = ("apple", "orange")


class TaskScorer:
    def __init__(self, env, initial_target_z, target="apple"):
        self.env = env
        self.initial_target_z = float(initial_target_z)
        self.target = target
        self.stable_steps = 0
        self.outside_steps = 0
        self.lifted = False
        self.above_bowl = False
        self.steps = 0

    def observe(self):
        env = self.env
        target_id = env.model.body(self.target).id
        bowl_id = env.model.body("bowl").id
        fruit = env.data.body(self.target).xpos.copy()
        bowl = env.data.body("bowl").xpos.copy()
        velocity = env.data.body(self.target).cvel[3:]
        self.steps += 1
        self.lifted |= fruit[2] - self.initial_target_z > 0.045
        horizontal = np.linalg.norm(fruit[:2] - bowl[:2]) < 0.047
        above = horizontal and bowl[2] + 0.035 < fruit[2] < bowl[2] + 0.095
        self.above_bowl |= above
        bowl_contact = False
        robot_contact = False
        for index in range(env.data.ncon):
            contact = env.data.contact[index]
            first = int(env.model.geom_bodyid[contact.geom1])
            second = int(env.model.geom_bodyid[contact.geom2])
            if target_id not in (first, second):
                continue
            other = second if first == target_id else first
            bowl_contact |= other == bowl_id
            if other not in (bowl_id, 0):
                robot_contact |= env.model.body(other).name.startswith(("left_", "right_"))
        inside = horizontal and bowl[2] + 0.025 < fruit[2] < bowl[2] + 0.090
        settled = np.linalg.norm(velocity) < 0.035
        self.stable_steps = self.stable_steps + 1 if inside and bowl_contact and not robot_contact and settled else 0
        back_on_table = settled and not horizontal and not robot_contact and abs(fruit[2] - self.initial_target_z) < 0.012
        self.outside_steps = self.outside_steps + 1 if back_on_table else 0
        if self.stable_steps >= 10:
            verdict = "passed"
            milestone = f"{self.target.title()} settled in the bowl"
        elif self.above_bowl:
            verdict = None
            milestone = f"{self.target.title()} reached the bowl"
        elif self.lifted:
            verdict = None
            milestone = f"{self.target.title()} lifted from the table"
        else:
            verdict = None
            milestone = "No pickup observed yet"
        return {
            "verdict": verdict, "milestone": milestone, "target_position": fruit.tolist(),
            "stable_steps": self.stable_steps,
            "checks": {"lifted": bool(self.lifted), "reached_bowl": bool(self.above_bowl), "settled": verdict == "passed"},
            "end_state": {"arm_contact": bool(robot_contact), "resting_outside": self.outside_steps >= 10},
        }


def fixture_cases():
    """Expected scorer labels for review when local scene setup is available."""
    return {
        "settled_in_bowl": "passed",
        "outside_bowl": "task_timeout",
        "held_over_bowl": "task_timeout",
        "brief_bowl_contact": "task_timeout",
    }
