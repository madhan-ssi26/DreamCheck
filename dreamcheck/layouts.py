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
LAYOUTS = {"stock": (0.0, 0.0), **WORKSHOP, **TRAIN, **HOLDOUT}


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
