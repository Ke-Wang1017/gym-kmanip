from functools import partial
import time

from dm_control import mujoco
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

import gym_kmanip as k

"""
based on this inverse kinematics example from deepmind
https://github.com/google-deepmind/mujoco/blob/main/python/least_squares.ipynb

uses scipy least squares
https://docs.scipy.org/doc/scipy-1.13.0/reference/generated/scipy.optimize.least_squares.html
"""


def ik_res(
    q_pos: NDArray,
    physics: mujoco.Physics = None,
    goal_pos: NDArray = None,
    goal_orn: NDArray = None,
    q_mask: NDArray = None,
    q_home: NDArray = None,
    ee_site: str = None,
    rad: float = k.IK_RES_RAD,
    reg: float = k.IK_RES_REG,
) -> NDArray:
    # forward kinematics
    physics.data.qpos[q_mask] = q_pos
    mujoco.mj_kinematics(physics.model.ptr, physics.data.ptr)
    # position residual
    ee_pos: NDArray = physics.data.site(ee_site).xpos
    res_pos: NDArray = ee_pos - goal_pos
    # orientation residual
    curr_quat = np.empty(4)
    mujoco.mju_mat2Quat(curr_quat, physics.data.site(ee_site).xmat)
    res_quat = np.empty(3)
    mujoco.mju_subQuat(res_quat, goal_orn.flatten(), curr_quat)
    res_quat *= rad
    # regularization residual
    res_reg = reg * (q_pos - q_home)
    return np.hstack((res_pos.flatten(), res_quat, res_reg))


def ik_jac(
    q_pos: NDArray,
    physics: mujoco.Physics = None,
    goal_orn: NDArray = None,
    q_mask: NDArray = None,
    ee_site: str = None,
    rad: float = k.IK_JAC_RAD,
    reg: float = k.IK_JAC_REG,
) -> NDArray:
    # analytic jacobian
    # forward kinematics
    physics.data.qpos[q_mask] = q_pos
    mujoco.mj_kinematics(physics.model.ptr, physics.data.ptr)
    mujoco.mj_comPos(physics.model.ptr, physics.data.ptr)
    # position jacobian
    # TODO: nv is all joints, perhaps speedup by using q_mask?
    jac_pos: NDArray = np.empty((3, physics.model.nv))
    jac_quat: NDArray = np.empty((3, physics.model.nv))
    mujoco.mj_jacSite(
        physics.model.ptr,
        physics.data.ptr,
        jac_pos,
        jac_quat,
        physics.data.site(ee_site).id,
    )
    # orientation jacobian
    ee_orn: NDArray = np.empty(4)
    mujoco.mju_mat2Quat(ee_orn, physics.data.site(ee_site).xmat)
    Dtarget: NDArray = np.empty((9, 1))
    D_ee: NDArray = np.empty((9, 1))
    mujoco.mjd_subQuat(goal_orn, ee_orn, Dtarget, D_ee)
    D_ee = D_ee.reshape(3, 3)
    target_mat = physics.data.site(ee_site).xmat.reshape(3, 3)
    mat = rad * D_ee.T @ target_mat.T
    jac_quat = mat @ jac_quat
    # regularization jacobian
    jac_reg = reg * np.eye(physics.model.nv)
    # filter using q_mask
    jac_pos = jac_pos[:, q_mask]
    jac_quat = jac_quat[:, q_mask]
    jac_reg = jac_reg[q_mask, :][:, q_mask]
    return np.vstack((jac_pos, jac_quat, jac_reg))


def ik(
    physics: mujoco.Physics,
    goal_pos: NDArray = None,
    goal_orn: NDArray = None,
    q_mask: NDArray = None,
    q_home: NDArray = None,
    ee_site: str = None,
) -> NDArray:
    start_time = time.time()
    q_pos: NDArray = physics.data.qpos[q_mask]
    # TODO: debug why bounds causes issues
    # bounds: Tuple[NDArray] = [
    #     physics.model.jnt_range[q_mask, 0],
    #     physics.model.jnt_range[q_mask, 1],
    # ]
    ik_func = partial(
        ik_res,
        physics=physics,
        goal_pos=goal_pos,
        goal_orn=goal_orn,
        q_home=q_home[q_mask],
        q_mask=q_mask,
        ee_site=ee_site,
    )
    ik_jac_func = partial(
        ik_jac,
        physics=physics,
        goal_orn=goal_orn,
        q_mask=q_mask,
        ee_site=ee_site,
    )
    try:
        result = least_squares(
            ik_func,
            q_pos,
            jac=ik_jac_func,
            # bounds=bounds,
            verbose=0,
        )
        q_pos = result.x
    except ValueError as e:
        print(e)
    total_time = time.time() - start_time
    print(f"IK took {total_time*1000}ms")
    return q_pos