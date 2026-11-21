"""Deterministic contact-only control runs for fruit into the bowl."""

import numpy as np


def apple_to_bowl(env, *, action_hold=1, on_action=None):
    yield from fruit_to_bowl(env, "apple", action_hold=action_hold, on_action=on_action)


def fruit_to_bowl(env, target, *, action_hold=1, on_action=None):
    """Execute a scripted pick, carry and release through the YAM joint servos."""
    from mock_pickups import solve

    home = np.r_[np.zeros(6), 1.0]
    rotation = np.diag([1.0, -1.0, -1.0])
    fruit = env.data.body(target).xpos.copy()
    bowl = env.data.body("bowl").xpos.copy()
    side, idle = "left", "right"
    bounds = {arm: np.array([env.model.joint(f"{arm}_joint{i}").range for i in range(1, 7)])
              for arm in (side, idle)}

    def bounded(command, arm):
        limits = bounds[arm]
        return np.clip(command, np.r_[limits[:, 0], 0], np.r_[limits[:, 1], 1])

    q = bounded(env.state()[side], side)[:6]
    idle_home = bounded(home, idle)
    lift = 0.15 if target == "apple" else 0.10

    def execute(commands, phase):
        for index, command in enumerate(commands, 1):
            arms = {side: bounded(command, side), idle: idle_home}
            if on_action is not None:
                on_action(arms["left"], arms["right"])
            for tick in range(action_hold):
                env.step(arms["left"], arms["right"])
                yield phase, index == len(commands) and tick == action_hold - 1

    def move(target, opening, steps, phase):
        nonlocal q
        end = solve(env, side, np.asarray(target), rotation, q)
        commands = [np.r_[joints, opening] for joints in np.linspace(q, end, -(-steps // action_hold))]
        yield from execute(commands, phase)
        q = end

    yield from move(fruit + [0, 0, lift - 0.01], 1, 50, "Approach")
    yield from move(fruit + [0, 0, 0.003 if target == "apple" else -0.009], 1, 50, "Lower")
    yield from execute([np.r_[q, opening] for opening in np.linspace(1, 0, 28 // action_hold)], "Grip")
    yield from move(fruit + [0, 0, lift], 0, 55, "Lift")

    offset = env.data.site(f"{side}_tcp").xpos - env.data.body(target).xpos
    release = bowl + offset + [0, 0, 0.10]
    yield from move(bowl + [0.06, 0.035, 0.16 if target == "apple" else 0.13],
                    0, 40, "Carry toward bowl")
    yield from move(release + [0, 0, 0.02], 0, 35, "Move over bowl")
    yield from move(release, 0, 28, "Lower over bowl")
    yield from execute([np.r_[q, 1.0]] * (50 // action_hold), "Release and settle")
