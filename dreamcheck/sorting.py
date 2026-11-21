"""Offline reference and ordered physics checks for instruction-conditioned sorting."""

import argparse
import json
import xml.etree.ElementTree as ET

import numpy as np

from .relay import relay_reference
from .runner import sim_imports

BAYS = ("near_bay", "far_bay")
OBJECTS = ("apple", "orange")


def sorting_scene(offset=(0, 0)):
    import mujoco

    sim_imports()
    from yam_env import YamEnv, build_xml

    root = ET.fromstring(build_xml())
    root.find("size").set("memory", "256M")
    world = root.find("worldbody")
    world.find("body[@name='plate']").set("pos", "0 .19 .764")
    world.remove(world.find("body[@name='bowl']"))
    for name, x, color in zip(BAYS, (-.065, -.195), (".30 .54 .65 1", ".77 .52 .26 1")):
        bay = ET.SubElement(world, "body", name=name, pos=f"{x} .048 .765")
        ET.SubElement(bay, "geom", type="box", size=".054 .066 .005", rgba=color)
        for side in (-1, 1):
            ET.SubElement(bay, "geom", type="box", pos=f"{side * .053} 0 .015", size=".006 .066 .016", rgba=color)
            ET.SubElement(bay, "geom", type="box", pos=f"0 {side * .061} .015", size=".054 .006 .016", rgba=color)
    env = YamEnv.__new__(YamEnv)
    env.model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"))
    env.data = mujoco.MjData(env.model)
    env.renderer = None
    for body, location in (("apple", [.15, .105]), ("orange", [.225, .102]), ("banana", [-.31, .12])):
        env.data.joint(f"{body}_free").qpos[:2] = np.asarray(location) + offset
    home = np.r_[np.zeros(6), 1.]
    for _ in range(30):
        env.step(home, home)
    return env


class SortingScorer:
    def __init__(self, env, target, bay):
        self.env = env
        self.target = env.model.body(target).id
        self.other = env.model.body(next(name for name in OBJECTS if name != target)).id
        self.bay = bay
        self.start_height = env.data.xpos[self.target, 2]
        self.other_start = env.data.xpos[self.other].copy()
        self.left_pickup = self.right_pickup = self.last_right = False
        self.transferred = False
        self.plate_ticks = self.bay_ticks = 0

    def observe(self):
        env = self.env
        contacts = set()
        for contact in env.data.contact:
            bodies = [int(env.model.geom_bodyid[index]) for index in (contact.geom1, contact.geom2)]
            if self.target in bodies:
                contacts.add(env.model.body(bodies[1] if bodies[0] == self.target else bodies[0]).name)
        left = any(name.startswith("left_") for name in contacts)
        right = any(name.startswith("right_") for name in contacts)
        lifted = env.data.xpos[self.target, 2] > self.start_height + .045
        self.left_pickup |= bool(left and lifted)
        self.plate_ticks = self.plate_ticks + 1 if self.left_pickup and "plate" in contacts and not (left or right) else 0
        self.transferred |= self.plate_ticks >= 10
        self.right_pickup |= bool(self.transferred and right and lifted and "plate" not in contacts)
        if left or right:
            self.last_right = right and not left
        position = env.data.xpos[self.target] - env.data.body(self.bay).xpos
        settled = np.linalg.norm(env.data.cvel[self.target, 3:]) < .035
        inside = abs(position[0]) < .038 and abs(position[1]) < .042
        released = self.right_pickup and self.last_right and not (left or right) and settled
        self.bay_ticks = self.bay_ticks + 1 if released and inside and self.bay in contacts else 0
        if np.linalg.norm(env.data.xpos[self.other] - self.other_start) > .025:
            return "wrong_object_moved"
        if released and any(bay in contacts for bay in BAYS if bay != self.bay):
            return "wrong_bay"
        return "passed" if self.bay_ticks >= 10 else None

    def incomplete_stage(self):
        return next(stage for stage, complete in (("left_pickup", self.left_pickup), ("plate_transfer", self.transferred),
                                                  ("right_pickup", self.right_pickup), ("placement", False)) if not complete)


def trial(target, bay, offset=(0, 0)):
    env = sorting_scene(offset)
    scorer = SortingScorer(env, target, bay)
    try:
        for _ in relay_reference(env, target=target, final_destination=bay):
            if verdict := scorer.observe():
                return {"target": target, "bay": bay, "offset": offset, "verdict": verdict}
        return {"target": target, "bay": bay, "offset": offset, "verdict": scorer.incomplete_stage()}
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=OBJECTS, required=True)
    parser.add_argument("--bay", choices=BAYS, required=True)
    args = parser.parse_args()
    result = trial(args.target, args.bay)
    print(json.dumps(result))
    raise SystemExit(result["verdict"] != "passed")


if __name__ == "__main__":
    main()
