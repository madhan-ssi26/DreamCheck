"""Load a local, explicitly mapped bimanual MuJoCo scene package."""

import io
import json
import re
import stat
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath

import numpy as np

SCENES_DIR = Path(__file__).resolve().parents[1] / ".dreamcheck" / "scenes"
SCENE_ID = re.compile(r"imported-[0-9a-f]{12}")
MAX_PACKAGE = 48 * 1024 * 1024
SIDES = ("left", "right")


def scene_path(scene_id):
    if not isinstance(scene_id, str) or not SCENE_ID.fullmatch(scene_id):
        raise ValueError("Invalid imported scene ID.")
    path = SCENES_DIR / f"{scene_id}.zip"
    if not path.is_file():
        raise ValueError("The imported scene package is missing.")
    return path


def scene_metadata(path):
    with zipfile.ZipFile(path) as archive:
        config = json.loads(archive.read("dreamcheck.json"))
        world = ET.fromstring(archive.read("scene.xml")).find("worldbody")
    objects, destinations = [], []

    def collect(node, fixed):
        joints = [child for child in node if child.tag in {"joint", "freejoint"}]
        fixed = fixed and not joints
        name = node.get("name")
        if node.tag == "body" and name:
            if any(joint.tag == "freejoint" or joint.get("type") == "free" for joint in joints):
                objects.append(name)
            if fixed:
                destinations.append(f"body:{name}")
        if fixed:
            destinations.extend(f"site:{site.get('name')}" for site in node.findall("site") if site.get("name"))
        for child in node.findall("body"):
            collect(child, fixed)

    if world is not None:
        collect(world, True)
    return {"id": path.stem, "name": str(config.get("name", "Imported scene"))[:100],
            "cameras": list(config["cameras"]), "preview": path.with_suffix(".jpg").is_file(),
            "objects": objects, "destinations": destinations}


def saved_scenes():
    entries = []
    for path in sorted(SCENES_DIR.glob("imported-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True):
        if not SCENE_ID.fullmatch(path.stem):
            continue
        try:
            entries.append(scene_metadata(path))
        except (ValueError, KeyError, OSError, zipfile.BadZipFile, ET.ParseError):
            continue
    return entries


def validate_goal(goal, scene):
    if goal is None:
        return None
    if (not isinstance(goal, dict) or goal.get("object") not in scene["objects"]
            or goal.get("destination") not in scene["destinations"]):
        raise ValueError("Choose a movable object and a fixed destination from this scene.")
    size = goal.get("size_cm")
    dwell = goal.get("dwell_ticks")
    if (not isinstance(size, list) or len(size) != 3
            or any(type(value) is not int or not 1 <= value <= 100 for value in size)
            or type(dwell) is not int or not 2 <= dwell <= 60):
        raise ValueError("Use goal dimensions of 1–100 cm and a dwell of 2–60 ticks.")
    return {"object": goal["object"], "destination": goal["destination"],
            "size_cm": size, "dwell_ticks": dwell}


class RegionGoal:
    """Require entry and consecutive 50 ms ticks inside a fixed world-axis box."""

    def __init__(self, scene, goal):
        kind, name = goal["destination"].split(":", 1)
        self.object = scene.data.body(goal["object"])
        self.center = (scene.data.site(name) if kind == "site" else scene.data.body(name)).xpos.copy()
        self.half_size = np.asarray(goal["size_cm"]) / 200
        self.required = goal["dwell_ticks"]
        self.dwell = 0
        self.entered_from_outside = not self.inside()

    def inside(self):
        return bool(np.all(np.abs(self.object.xpos - self.center) <= self.half_size))

    def observe(self):
        inside = self.inside()
        if not inside:
            self.entered_from_outside = True
        self.dwell = self.dwell + 1 if inside and self.entered_from_outside else 0
        return {"inside": inside, "dwell_ticks": self.dwell, "required_ticks": self.required,
                "center_m": self.center.tolist(), "position_m": self.object.xpos.tolist()}


def read_package(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        total = 0
        files = {}
        for entry in archive.infolist():
            name = entry.filename
            path = PurePosixPath(name)
            if path.is_absolute() or re.search(r"[\\:]", name) or ".." in path.parts or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("Packages must use local, relative asset paths.")
            if entry.is_dir():
                continue
            if name in files or len(files) >= 300 or entry.file_size > MAX_PACKAGE:
                raise ValueError("Too many files, duplicate paths, or an oversized asset.")
            total += entry.file_size
            if total > 2 * MAX_PACKAGE:
                raise ValueError("The expanded scene exceeds 96 MB.")
            files[name] = archive.read(entry)
    if not {"scene.xml", "dreamcheck.json"} <= files.keys():
        raise ValueError("Put scene.xml and dreamcheck.json at the ZIP root.")
    for name, content in files.items():
        if not name.lower().endswith(".xml"):
            continue
        for node in ET.fromstring(content).iter():
            if node.tag in {"plugin", "extension", "include", "attach"}:
                raise ValueError("Export a flattened MJCF scene without includes, attached models, or plugins.")
            for attr, value in node.attrib.items():
                if attr in {"meshdir", "texturedir", "assetdir"}:
                    raise ValueError("Use ZIP-relative file paths directly instead of MJCF asset directories.")
                if attr.startswith("file") and value not in files:
                    raise ValueError(f"Missing packaged asset: {value}")
    return files, json.loads(files["dreamcheck.json"])


class ImportedScene:
    def __init__(self, payload):
        import mujoco

        files, config = read_package(payload)
        self.config = config
        root = ET.fromstring(files["scene.xml"])
        assets = {}
        aliases = {}
        for node in root.iter():
            for key, value in node.attrib.copy().items():
                if key.startswith("file"):
                    if value not in aliases:
                        alias = f"asset-{len(assets)}{PurePosixPath(value).suffix}"
                        aliases[value] = alias
                        assets[alias] = files[value]
                    node.set(key, aliases[value])
        self.model = mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"), assets)
        self.data = mujoco.MjData(self.model)
        self.renderer = None
        if not 0 < self.model.opt.timestep <= 0.05:
            raise ValueError("The scene timestep must be between zero and 0.05 seconds.")
        self.substeps = round(0.05 / self.model.opt.timestep)
        if self.substeps > 100 or not np.isclose(self.substeps * self.model.opt.timestep, .05):
            raise ValueError("The physics timestep must divide the 50 ms controller tick (up to 100 substeps).")
        self.cameras = config["cameras"]
        for role in ("top", "left", "right"):
            self.model.camera(self.cameras[role])
        self.model.camera(self.cameras.get("overview", self.cameras["top"]))
        self.arms = {}
        mapped_joints, mapped_actuators = set(), set()
        for side in SIDES:
            arm = config["arms"][side]
            joints = [self.model.joint(name) for name in arm["joints"]]
            actuators = [self.model.actuator(name) for name in arm["actuators"]]
            grip = self.model.joint(arm["gripper_joint"])
            driver = self.model.actuator(arm["gripper_actuator"])
            closed, opened = map(float, arm["gripper_range"])
            commands = [*actuators, driver]
            sensors = [*joints, grip]
            if (len(joints) != 6 or len(actuators) != 6
                    or any(int(j.type[0]) != mujoco.mjtJoint.mjJNT_HINGE or not j.limited[0] for j in joints)
                    or int(grip.type[0]) not in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE)
                    or not np.isfinite([closed, opened]).all() or closed == opened
                    or len({j.id for j in sensors}) != 7 or mapped_joints.intersection(j.id for j in sensors)
                    or len({a.id for a in commands}) != 7 or mapped_actuators.intersection(a.id for a in commands)):
                raise ValueError("Each arm needs six distinct limited hinge joints and one mapped gripper.")
            for joint, actuator in zip(sensors, commands):
                if int(actuator.trntype[0]) != mujoco.mjtTrn.mjTRN_JOINT or int(actuator.trnid[0]) != joint.id:
                    raise ValueError("Each actuator must address its mapped joint directly.")
                if (int(actuator.biastype[0]) != mujoco.mjtBias.mjBIAS_AFFINE or actuator.gainprm[0] <= 0
                        or not np.isclose(actuator.biasprm[1], -actuator.gainprm[0])
                        or not np.isclose(actuator.gear[0], 1)):
                    raise ValueError("Use direct position servos with unit gear for the mapped actuators.")
                bounds = sorted((closed, opened)) if joint.id == grip.id else joint.range
                if actuator.ctrllimited[0] and (bounds[0] < actuator.ctrlrange[0] or bounds[1] > actuator.ctrlrange[1]):
                    raise ValueError("Actuator control range must cover the mapped joint range.")
            mapped_joints.update(j.id for j in sensors)
            mapped_actuators.update(a.id for a in commands)
            followers = []
            for follower in arm.get("gripper_followers", []):
                actuator = self.model.actuator(follower["actuator"])
                follower_closed, follower_open = map(float, follower["range"])
                if actuator.id in mapped_actuators or not np.isfinite([follower_closed, follower_open]).all():
                    raise ValueError("Invalid secondary gripper actuator mapping.")
                if actuator.ctrllimited[0] and (min(follower_closed, follower_open) < actuator.ctrlrange[0]
                                               or max(follower_closed, follower_open) > actuator.ctrlrange[1]):
                    raise ValueError("Secondary gripper targets exceed its control range.")
                mapped_actuators.add(actuator.id)
                followers.append((actuator, follower_closed, follower_open))
            self.arms[side] = (joints, commands, grip, closed, opened, followers)
        if config.get("keyframe") is not None:
            mujoco.mj_resetDataKeyframe(self.model, self.data, self.model.key(config["keyframe"]).id)
        mujoco.mj_forward(self.model, self.data)

    def joint_limits(self, side):
        return np.asarray([joint.range for joint in self.arms[side][0]])

    def state(self):
        return {side: np.r_[[self.data.qpos[j.qposadr[0]] for j in joints],
                            np.clip((self.data.qpos[grip.qposadr[0]] - closed) / (opened - closed), 0, 1)]
                for side, (joints, _, grip, closed, opened, _) in self.arms.items()}

    def step(self, left, right):
        import mujoco

        for side, vector in zip(SIDES, (left, right)):
            _, commands, _, closed, opened, followers = self.arms[side]
            targets = np.r_[np.clip(vector[:6], *self.joint_limits(side).T), closed + vector[6] * (opened - closed)]
            for actuator, value in zip(commands, targets):
                self.data.ctrl[actuator.id] = value
            for actuator, start, end in followers:
                self.data.ctrl[actuator.id] = start + vector[6] * (end - start)
        mujoco.mj_step(self.model, self.data, nstep=self.substeps)
        mujoco.mj_forward(self.model, self.data)

    def image(self, role):
        import mujoco

        if self.renderer is None:
            # Imported scenes may declare a smaller default framebuffer.
            self.model.vis.global_.offwidth = max(640, self.model.vis.global_.offwidth)
            self.model.vis.global_.offheight = max(480, self.model.vis.global_.offheight)
            self.renderer = mujoco.Renderer(self.model, height=480, width=640)
        self.renderer.update_scene(self.data, camera=self.cameras.get(role, self.cameras["top"]))
        return self.renderer.render()[60:420].copy()

    def images(self):
        return {role: self.image(role) for role in ("top", "left", "right")}

    def close(self):
        if self.renderer is not None:
            self.renderer.close()


def import_scene(payload):
    from .runner import jpeg

    scene = ImportedScene(payload)
    try:
        state = scene.state()
        scene.step(state["left"], state["right"])
        scene.images()
        preview = jpeg(scene.image("overview"))
    finally:
        scene.close()
    SCENES_DIR.mkdir(parents=True, exist_ok=True)
    scene_id = f"imported-{uuid.uuid4().hex[:12]}"
    path = SCENES_DIR / f"{scene_id}.zip"
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(payload)
    path.with_suffix(".jpg").write_bytes(preview)
    temporary.replace(path)
    return scene_metadata(path)


def example_package():
    from .runner import sim_imports

    sim_imports()
    from yam_env import ASSETS, build_xml

    root = ET.fromstring(build_xml("workshop"))
    root.find("compiler").attrib.pop("meshdir", None)
    arms = {}
    for side in SIDES:
        joints = [f"{side}_joint{index}" for index in range(1, 7)]
        grip = f"{side}_finger_1"
        arms[side] = {"joints": joints, "actuators": joints, "gripper_joint": grip,
                      "gripper_actuator": grip, "gripper_range": [0, .0475],
                      "gripper_followers": [{"actuator": f"{side}_finger_-1", "range": [0, .0475]}]}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        seen = set()
        for node in root.iter():
            if "file" not in node.attrib:
                continue
            source = (ASSETS / node.attrib["file"]).resolve()
            relative = source.relative_to(ASSETS).as_posix()
            node.set("file", relative)
            if relative not in seen:
                archive.write(source, relative)
                seen.add(relative)
        archive.writestr("scene.xml", ET.tostring(root, encoding="unicode"))
        archive.writestr("dreamcheck.json", json.dumps({"name": "Portable Workshop", "arms": arms,
                         "cameras": {role: role for role in ("top", "left", "right", "overview")}}, indent=2))
    return buffer.getvalue()
