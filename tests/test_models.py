"""Tests for the task-graph model: ordering, level grouping, and guardrails."""

import unittest

from agentic_sdlc.models import Task, TaskGraph


class TaskGraphTests(unittest.TestCase):
    def _graph(self):
        return TaskGraph(tasks=[
            Task("a", "A", "", depends_on=[]),
            Task("b", "B", "", depends_on=["a"]),
            Task("c", "C", "", depends_on=["a"]),
            Task("d", "D", "", depends_on=["b", "c"]),
        ])

    def test_topological_levels(self):
        levels = self._graph().topological_levels()
        self.assertEqual([sorted(t.id for t in lvl) for lvl in levels],
                         [["a"], ["b", "c"], ["d"]])

    def test_cycle_detected(self):
        graph = TaskGraph(tasks=[
            Task("a", "A", "", depends_on=["b"]),
            Task("b", "B", "", depends_on=["a"]),
        ])
        with self.assertRaises(ValueError):
            graph.validate_acyclic()

    def test_dangling_dependency_detected(self):
        graph = TaskGraph(tasks=[Task("a", "A", "", depends_on=["missing"])])
        with self.assertRaises(ValueError):
            graph.validate_acyclic()


if __name__ == "__main__":
    unittest.main()
