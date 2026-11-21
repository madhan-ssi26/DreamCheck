"""Fixed training and evaluation layouts for the YAM apple task."""

TRAIN = {
    "train-stock": (0.0, 0.0),
    "train-left": (-0.008, 0.0),
    "train-right": (0.008, 0.0),
    "train-near": (0.0, 0.008),
    "train-far": (0.0, -0.008),
    "train-diagonal": (0.006, -0.006),
}
HOLDOUT = {
    "holdout-left": (-0.013, -0.006),
    "holdout-right": (0.013, 0.005),
    "holdout-far": (-0.004, -0.014),
}
WORKSHOP = {"workshop": (0.0, 0.0), "workshop-left": (-0.013, -0.006), "workshop-right": (0.013, 0.005)}
RELAY_TRAIN = {f"relay-train-{index:02}": (x / 1000, y / 1000)
               for index, (x, y) in enumerate((x, y) for x in range(-6, 7, 2) for y in range(-6, 7, 2))}
RELAY_HOLDOUT = {f"relay-holdout-{index:02}": (x / 1000, y / 1000)
                 for index, (x, y) in enumerate([(-7, -3), (-5, 1), (-3, 5), (-1, -5), (1, -1),
                                                  (3, 3), (5, -3), (7, 1), (-5, -7), (5, 7)])}
LAYOUTS = {"stock": (0.0, 0.0), **WORKSHOP, **TRAIN, **HOLDOUT, **RELAY_TRAIN, **RELAY_HOLDOUT}


def scene_for_layout(layout):
    return "workshop" if layout in WORKSHOP else "stock"


def place_target(env, layout, target="apple"):
    import mujoco

    joint = env.data.joint(f"{target}_free")
    joint.qpos[:2] += LAYOUTS[layout]
    joint.qvel[:] = 0
    mujoco.mj_forward(env.model, env.data)


def place_apple(env, layout):
    place_target(env, layout)
