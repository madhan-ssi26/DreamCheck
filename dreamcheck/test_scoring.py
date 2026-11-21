"""Synthetic physics snapshots for checking the YAM task scorer."""

import unittest
from types import SimpleNamespace

import numpy as np

from .scoring import TaskScorer


class FakeEnv:
    def __init__(self, fruit_position, velocity=(0, 0, 0), contact_body=2, target="apple"):
        bodies = {"apple": 1, "bowl": 2, "right_gripper": 3, "orange": 4}
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


if __name__ == "__main__":
    unittest.main()
