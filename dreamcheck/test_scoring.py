"""Synthetic physics snapshots for checking the YAM task scorer."""

import unittest
from types import SimpleNamespace

import numpy as np

from .scoring import TaskScorer
from .relay import RelayScorer


class FakeEnv:
    def __init__(self, fruit_position, velocity=(0, 0, 0), contact_body=2, target="apple"):
        bodies = {"apple": 1, "bowl": 2, "right_gripper": 3, "orange": 4, "left_gripper": 5, "plate": 6}
        names = {index: name for name, index in bodies.items()}
        self.model = SimpleNamespace(
            body=lambda name: SimpleNamespace(id=bodies[name]) if isinstance(name, str) else SimpleNamespace(name=names[name]),
            geom_bodyid=[bodies[target], contact_body],
        )
        self.data = SimpleNamespace(
            body=lambda name: SimpleNamespace(
                xpos=np.array(fruit_position) if name == target else np.array((0, -0.04, 0.76)),
                cvel=(0, 0, 0, *velocity) if name == target else (0, 0, 0, 0, 0, 0),
            ),
            ncon=1,
            contact=[SimpleNamespace(geom1=0, geom2=1)],
        )


class ScorerFixtures(unittest.TestCase):
    def test_settled_apple_in_bowl_passes_only_after_ten_steps(self):
        scorer = TaskScorer(FakeEnv((0, -0.04, 0.80)), 0.79)
        verdicts = [scorer.observe()["verdict"] for _ in range(10)]
        self.assertEqual(verdicts[:9], [None] * 9)
        self.assertEqual(verdicts[-1], "passed")

    def test_apple_outside_bowl_does_not_pass(self):
        scorer = TaskScorer(FakeEnv((0.2, 0.1, 0.80)), 0.79)
        for _ in range(20):
            self.assertIsNone(scorer.observe()["verdict"])

    def test_apple_held_above_bowl_does_not_pass(self):
        scorer = TaskScorer(FakeEnv((0, -0.04, 0.82), contact_body=3), 0.79)
        for _ in range(20):
            self.assertIsNone(scorer.observe()["verdict"])
        self.assertEqual(scorer.observe()["milestone"], "Apple reached the bowl")

    def test_brief_contact_does_not_pass(self):
        env = FakeEnv((0, -0.04, 0.80))
        scorer = TaskScorer(env, 0.79)
        for _ in range(5):
            scorer.observe()
        env.data.body = lambda name: SimpleNamespace(
            xpos=np.array((0.2, 0.1, 0.80)) if name == "apple" else np.array((0, -0.04, 0.76)),
            cvel=(0, 0, 0, 0, 0, 0),
        )
        self.assertIsNone(scorer.observe()["verdict"])

    def test_only_selected_fruit_passes(self):
        env = FakeEnv((0, -0.04, 0.80), target="orange")
        apple = TaskScorer(env, 0.79)
        orange = TaskScorer(env, 0.79, "orange")
        for _ in range(10):
            self.assertIsNone(apple.observe()["verdict"])
            result = orange.observe()
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual(result["milestone"], "Orange settled in the bowl")


class RelayFixtures(unittest.TestCase):
    def relay_verdict(self, final_gripper):
        env = FakeEnv((0, -0.04, .85), contact_body=5)
        scorer = RelayScorer(env, .79)

        def observe(position, contact, ticks):
            env.model.geom_bodyid[1] = contact
            env.data.body = FakeEnv(position).data.body
            return [scorer.observe() for _ in range(ticks)][-1]

        observe((.1, .1, .88), 5, 1)
        observe((0, .19, .80), 6, 10)
        observe((0, .19, .88), 3, 1)
        observe((0, -.04, .88), final_gripper, 1)
        return observe((0, -.04, .80), 2, 10)

    def test_requires_right_arm_to_finish_relay(self):
        self.assertEqual(self.relay_verdict(3)["verdict"], "passed")
        self.assertIsNone(self.relay_verdict(5)["verdict"])

    def test_direct_bowl_placement_cannot_pass(self):
        scorer = RelayScorer(FakeEnv((0, -.04, .80)), .79)
        for _ in range(12):
            self.assertIsNone(scorer.observe()["verdict"])


if __name__ == "__main__":
    unittest.main()
