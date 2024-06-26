import asyncio
import time
from typing import Dict

import gymnasium as gym
import numpy as np
from numpy.typing import NDArray
from scipy.spatial.transform import Rotation as R
from vuer import Vuer, VuerSession
from vuer.schemas import Box, Capsule, Hands, Plane, PointLight, Urdf

import gym_kmanip as k

# choose your environment
# ENV_NAME: str = "KManipSoloArm"
# ENV_NAME: str = "KManipSoloArmQPos"
# ENV_NAME: str = "KManipSoloArmVision"
# ENV_NAME: str = "KManipDualArm"
# ENV_NAME: str = "KManipDualArmVision"
ENV_NAME: str = "KManipTorso"
# ENV_NAME: str = "KManipTorsoVision"
env = gym.make(ENV_NAME)
mj_data = env.unwrapped.mj_env.physics.data
mj_model = env.unwrapped.mj_env.physics.model
env.reset()

# is this environment bimanual?
BIMANUAL: bool = True
if "Solo" in ENV_NAME:
    BIMANUAL = False

# is this environment vision enabled?
VISUAL: bool = False
if "Vision" in ENV_NAME:
    VISUAL = True

# pick the appropriate urdf
if "SoloArm" in ENV_NAME:
    URDF_NAME: str = "stompy_tiny_solo_arm_glb"
if "DualArm" in ENV_NAME:
    URDF_NAME: str = "stompy_dual_arm_tiny_glb"
if "Torso" in ENV_NAME:
    URDF_NAME: str = "stompy_tiny_glb"
# Vuer requires a web link to the urdf for the headset
URDF_LINK: str = (
    f"https://raw.githubusercontent.com/kscalelabs/webstompy/master/urdf/{URDF_NAME}/robot.urdf"
)

# global variables get updated by various async functions
async_lock = asyncio.Lock()
q: Dict[str, float] = env.unwrapped.q_dict

# gobal variables for hand pose and grip
hr_pos: NDArray = mj_data.mocap_pos[k.MOCAP_ID_R].copy()
hr_orn: NDArray = mj_data.mocap_quat[k.MOCAP_ID_R].copy()
hr_size: NDArray = mj_model.site("hand_r_orn").size
grip_r: float = 0.0
if BIMANUAL:
    hl_pos: NDArray = mj_data.mocap_pos[k.MOCAP_ID_L].copy()
    hl_orn: NDArray = mj_data.mocap_quat[k.MOCAP_ID_L].copy()
    hl_size: NDArray = mj_model.site("hand_l_orn").size
    grip_l: float = 0.0
# NOTE: these are not .copy() and will be updated by mujoco in the background
cube_pos: NDArray = mj_data.body("cube").xpos
cube_orn: NDArray = mj_data.body("cube").xquat
cube_size: NDArray = mj_model.geom("cube").size
robot_pos: NDArray = mj_data.body("robot_root").xpos
robot_orn: NDArray = mj_data.body("robot_root").xquat
# table is easier to construct from base vuer plane primitize than load from stl
table_pos: NDArray = mj_data.body("table").xpos
TABLE_SIZE: NDArray = np.array([0.4, 0.8])
TABLE_ROT: NDArray = (
    R.from_euler("z", np.pi / 2) * R.from_euler("x", -np.pi / 2)
).as_euler("xyz")


async def run_env() -> None:
    start_time = time.time()
    action = env.action_space.sample()
    async with async_lock:
        global hr_pos, hr_orn, grip_r
        action["eer_pos"] = hr_pos
        action["eer_orn"] = hr_orn
        action["grip_r"] = grip_r
        if BIMANUAL:
            global hl_pos, hl_orn, grip_l
            action["eel_pos"] = hl_pos
            action["eel_orn"] = hl_orn
            action["grip_l"] = grip_l
        _q = mj_data.qpos[: env.unwrapped.q_len]
        global q
        for i, val in enumerate(_q):
            joint_name = env.unwrapped.q_keys[i]
            q[joint_name] = val
    _, reward, terminated, _, _ = env.step(action)
    print(f"env step took {(time.time() - start_time) * 1000:.2f}ms")


# Vuer rendering params
MAX_FPS: int = 60
VUER_LIGHT_POS: NDArray = np.array([0, 2, 2])
VUER_LIGHT_INTENSITY: float = 10.0

# Vuer hand tracking and pinch detection params
HAND_FPS: int = 30
FINGER_INDEX: int = 9
FINGER_THUMB: int = 4
FINGER_MIDLE: int = 14
PINCH_OPEN: float = 0.10  # 10cm
PINCH_CLOSE: float = 0.01  # 1cm

app = Vuer()


@app.add_handler("HAND_MOVE")
async def hand_handler(event, _):
    # right hand
    rindex_pos: NDArray = np.array(event.value["rightLandmarks"][FINGER_INDEX])
    rthumb_pos: NDArray = np.array(event.value["rightLandmarks"][FINGER_THUMB])
    rpinch_dist: NDArray = np.linalg.norm(rindex_pos - rthumb_pos)
    # index finger to thumb pinch turns on tracking
    if rpinch_dist < PINCH_CLOSE:
        print("Pinch detected in right hand")
        # pinching with middle finger controls gripper
        rmiddl_pos: NDArray = np.array(event.value["rightLandmarks"][FINGER_MIDLE])
        rgrip_dist: float = np.linalg.norm(rthumb_pos - rmiddl_pos) / PINCH_OPEN
        # orientation is calculated from wrist rotation matrix
        wrist_rotation: NDArray = np.array(event.value["rightHand"])
        wrist_rotation = wrist_rotation.reshape(4, 4)[:3, :3]
        wrist_rotation = R.from_matrix(wrist_rotation)
        async with async_lock:
            global hr_pos, hr_orn, grip_r
            hr_pos = k.vuer2mj_pos(rthumb_pos)
            print(f"goal_pos_eer {hr_pos}")
            hr_orn = k.vuer2mj_orn(wrist_rotation)
            print(f"goal_orn_eer {hr_orn}")
            grip_r = rgrip_dist
            print(f"right gripper at {grip_r}")
    if BIMANUAL:
        # left hand
        lindex_pos: NDArray = np.array(event.value["leftLandmarks"][FINGER_INDEX])
        lthumb_pos: NDArray = np.array(event.value["leftLandmarks"][FINGER_THUMB])
        lpinch_dist: NDArray = np.linalg.norm(lindex_pos - lthumb_pos)
        # index finger to thumb pinch turns on tracking
        if lpinch_dist < PINCH_CLOSE:
            print("Pinch detected in left hand")
            # pinching with middle finger controls gripper
            lmiddl_pos: NDArray = np.array(event.value["leftLandmarks"][FINGER_MIDLE])
            lgrip_dist: float = np.linalg.norm(lthumb_pos - lmiddl_pos) / PINCH_OPEN
            # orientation is calculated from wrist rotation matrix
            wrist_rotation: NDArray = np.array(event.value["leftHand"])
            wrist_rotation = wrist_rotation.reshape(4, 4)[:3, :3]
            wrist_rotation = R.from_matrix(wrist_rotation)
            async with async_lock:
                global hl_pos, hl_orn, grip_l
                hl_pos = k.vuer2mj_pos(lthumb_pos)
                print(f"goal_pos_eel {hl_pos}")
                hl_orn = k.vuer2mj_orn(wrist_rotation)
                print(f"goal_orn_eel {hl_orn}")
                grip_l = lgrip_dist
                print(f"left gripper at {grip_l}")


@app.spawn(start=True)
async def main(session: VuerSession):
    global q
    global cube_pos, cube_orn
    global hr_pos, hr_orn
    global hl_pos, hl_orn
    session.upsert @ PointLight(intensity=VUER_LIGHT_INTENSITY, position=VUER_LIGHT_POS)
    session.upsert @ Hands(fps=HAND_FPS, stream=True, key="hands")
    await asyncio.sleep(0.1)
    session.upsert @ Urdf(
        src=URDF_LINK,
        jointValues=env.unwrapped.q_dict,
        position=k.mj2vuer_pos(robot_pos),
        rotation=k.mj2vuer_orn(robot_orn),
        key="robot",
    )
    session.upsert @ Box(
        args=cube_size,
        position=k.mj2vuer_pos(cube_pos),
        rotation=k.mj2vuer_orn(cube_orn),
        materialType="standard",
        material=dict(color="#ff0000"),
        key="cube",
    )
    session.upsert @ Plane(
        args=TABLE_SIZE,
        position=k.mj2vuer_pos(table_pos),
        rotation=TABLE_ROT,
        materialType="standard",
        material=dict(color="#cbc1ae"),
        key="table",
    )
    session.upsert @ Capsule(
        args=hr_size,
        position=k.mj2vuer_pos(hr_pos),
        rotation=k.mj2vuer_orn(hr_orn),
        materialType="standard",
        material=dict(color="#0000ff"),
        key="hr",
    )
    if BIMANUAL:
        session.upsert @ Capsule(
            args=hl_size,
            position=k.mj2vuer_pos(hl_pos),
            rotation=k.mj2vuer_orn(hl_orn),
            materialType="standard",
            material=dict(color="#ff0000"),
            key="hl",
        )
    while True:
        await asyncio.gather(
            run_env(),  # ~1ms
            # update_image(),  # ~10ms
            asyncio.sleep(1 / MAX_FPS),  # ~16ms @ 60fps
        )
        async with async_lock:
            session.upsert @ Urdf(
                jointValues=q,
                position=k.mj2vuer_pos(robot_pos),
                rotation=k.mj2vuer_orn(robot_orn),
                key="robot",
            )
            session.upsert @ Box(
                position=k.mj2vuer_pos(cube_pos),
                rotation=k.mj2vuer_orn(cube_orn),
                key="cube",
            )
            session.upsert @ Capsule(
                position=k.mj2vuer_pos(hr_pos),
                rotation=k.mj2vuer_orn(hr_orn),
                key="hr",
            )
            if BIMANUAL:
                session.upsert @ Capsule(
                    position=k.mj2vuer_pos(hl_pos),
                    rotation=k.mj2vuer_orn(hl_orn),
                    key="hl",
                )
