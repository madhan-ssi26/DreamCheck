"""The stage gate parks the idle arm and hands over only after the left arm is clear."""

import unittest
from types import SimpleNamespace

import numpy as np

from .assist import HOME, PARK_TICKS, StageGate, validate_assist
from .training import probe_metrics


class FakeEnv:
    def __init__(self):
        self.commands = []
        self.left = np.full(7, .5)
        self.tcp_height = .02
        self.data = SimpleNamespace(site=lambda name: SimpleNamespace(xpos=np.array([0, 0, self.tcp_height])),
                                    body=lambda name: SimpleNamespace(xpos=np.zeros(3)))

    def state(self):
        return {"left": self.left, "right": HOME}

    def step(self, left, right):
        self.commands.append((np.array(left), np.array(right)))


class StageGateTests(unittest.TestCase):
    def test_right_arm_waits_for_transfer_and_a_parked_left_arm(self):
        env = FakeEnv()
        scorer = SimpleNamespace(transferred=False, steps=0)
        StageGate(env, scorer).install()
        moving = np.r_[np.full(6, .3), 0.]
        env.step(moving, moving)
        np.testing.assert_array_equal(env.commands[-1][0], moving)
        np.testing.assert_array_equal(env.commands[-1][1], HOME)
        scorer.transferred = True
        env.step(moving, moving)
        self.assertEqual(env.commands[-1][0][6], 1)
        np.testing.assert_array_equal(env.commands[-1][1], HOME)
        env.tcp_height = .2
        for _ in range(PARK_TICKS):
            env.step(moving, moving)
        np.testing.assert_allclose(env.commands[-1][0], HOME)
        np.testing.assert_array_equal(env.commands[-1][1], moving)
        np.testing.assert_array_equal(env.commands[-2][1], HOME)

    def test_settings_are_checked_before_a_run_starts(self):
        self.assertIsNone(validate_assist({"stage_gate": False, "stride": 1}, "apple", True))
        self.assertEqual(validate_assist({"stride": 2}, "apple", True), {"stage_gate": False, "stride": 2})
        with self.assertRaises(ValueError):
            validate_assist({"stage_gate": True}, "apple", True)
        with self.assertRaises(ValueError):
            validate_assist({"stride": 2}, "relay", False)
        with self.assertRaises(ValueError):
            validate_assist({"stride": 9}, "relay", True)


class ProbeTests(unittest.TestCase):
    def test_a_plan_that_moves_the_idle_arm_is_flagged(self):
        home = np.r_[np.zeros(6), 1., np.zeros(6), 1.]
        expert = np.tile(home, (48, 1))
        expert[:, 1] = np.linspace(0, 1.2, 48)
        wrong = np.tile(home, (48, 1))
        wrong[:, 8] = np.linspace(0, .5, 48)
        flagged = probe_metrics(expert, wrong, home)
        self.assertEqual(flagged["active_arm"], "left")
        self.assertTrue(flagged["idle_arm_moves"] and flagged["active_arm_waits"])
        slow = np.tile(home, (48, 1))
        slow[:, 1] = np.linspace(0, .6, 48)
        matched = probe_metrics(expert, slow, home)
        self.assertFalse(matched["idle_arm_moves"] or matched["active_arm_waits"])
        self.assertAlmostEqual(matched["pace"], .5, places=1)


if __name__ == "__main__":
    unittest.main()
