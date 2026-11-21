"""A supported two-arm relay with a physical, ordered success rule."""

import numpy as np

from .scoring import TaskScorer

PROMPT = "Use the left arm to put the apple on the transfer plate, then use the right arm to pick it up from the plate and place it in the bowl."


def setup_relay(env, offset=(0, 0)):
    import mujoco

    x, y = offset
    env.model.body("plate").pos[:2] = [y * .6, .19 + x * .6]
    env.model.body("bowl").pos[:2] = [-x * .5, .045 + y * .5]
    env.data.joint("apple_free").qpos[:2] += offset
    env.data.joint("orange_free").qpos[:2] = [.28, .08]
    env.data.joint("banana_free").qpos[:2] = [-.29, .10]
    mujoco.mj_forward(env.model, env.data)


class RelayScorer(TaskScorer):
    def __init__(self, env, initial_target_z):
        super().__init__(env, initial_target_z)
        self.left_pickup = False
        self.plate_dwell = 0
        self.transferred = False
        self.right_pickup = False
        self.last_supported_by_right = False

    def observe(self):
        env = self.env
        score = super().observe()
        apple = env.model.body("apple").id
        contacts = set()
        for index in range(env.data.ncon):
            contact = env.data.contact[index]
            first, second = (int(env.model.geom_bodyid[geom]) for geom in (contact.geom1, contact.geom2))
            if apple in (first, second):
                contacts.add(env.model.body(second if first == apple else first).name)
        left = any(name.startswith("left_") for name in contacts)
        right = any(name.startswith("right_") for name in contacts)
        lifted = score["target_position"][2] > self.initial_target_z + .045
        self.left_pickup |= bool(left and lifted)
        if self.left_pickup and "plate" in contacts and not left and not right:
            self.plate_dwell += 1
        else:
            self.plate_dwell = 0
        self.transferred |= self.plate_dwell >= 10
        self.right_pickup |= bool(self.transferred and right and lifted and "plate" not in contacts)
        if left or right:
            self.last_supported_by_right = right and not left
        passed = self.right_pickup and self.last_supported_by_right and score["verdict"] == "passed"
        score["verdict"] = "passed" if passed else None
        score["milestone"] = ("Right arm placed the apple in the bowl" if passed
                              else "Right arm picked up the apple" if self.right_pickup
                              else "Apple settled on the transfer plate" if self.transferred
                              else "Left arm picked up the apple" if self.left_pickup
                              else "Waiting for left pickup")
        score["checks"].update(left_pickup=self.left_pickup, transfer=self.transferred,
                               right_pickup=self.right_pickup, settled=passed)
        return score


def relay_reference(env, *, action_hold=2, on_action=None, target="apple", final_destination="bowl"):
    from mock_pickups import solve

    home = np.r_[np.zeros(6), 1.]
    for side, destination in (("left", "plate"), ("right", final_destination)):
        limits = np.asarray([env.model.joint(f"{side}_joint{i}").range for i in range(1, 7)])
        q = np.clip(env.state()[side][:6], *limits.T)
        start = env.data.body(target).xpos.copy()
        goal = env.data.body(destination).xpos.copy()
        rotation = np.diag([1., -1., -1.] if side == "left" else [-1., 1., -1.])

        def execute(commands, phase):
            for command in commands:
                left, right = (command, home) if side == "left" else (home, command)
                if on_action is not None:
                    on_action(left, right)
                for _ in range(action_hold):
                    env.step(left, right)
                    yield f"{side.title()} {phase}", False

        def move(position, gripper, samples, phase):
            nonlocal q
            end = solve(env, side, position, rotation, q)
            commands = (np.r_[joints, gripper] for joints in np.linspace(q, end, samples))
            yield from execute(commands, phase)
            q = end

        yield from move(start + [0, 0, .15 if target == "apple" else .115], 1, 30, "approach")
        yield from move(start + [0, 0, .003 if target == "apple" else -.009], 1, 30, "lower")
        yield from execute((np.r_[q, opening] for opening in np.linspace(1, 0, 17)), "grip")
        yield from move(start + [0, 0, .16 if side == "left" and target == "apple" else .13], 0, 35, "lift")
        held = env.data.body(target).xpos.copy()
        if held[2] - start[2] < .07:
            return
        offset = env.data.site(f"{side}_tcp").xpos - held
        above = goal + offset + [0, 0, .14]
        yield from move(above, 0, 45, "carry")
        release_height = {"plate": .05, "bowl": .10}.get(destination, .08)
        yield from move(goal + offset + [0, 0, release_height], 0, 25, "place")
        yield from execute([np.r_[q, 1.]] * (22 if final_destination == "bowl" else 30), "release")
        yield from move(above, 1, 24, "retreat")
        yield from execute((np.r_[joints, 1.] for joints in np.linspace(q, home[:6], 30)), "park")
        yield f"{side.title()} relay leg", True
