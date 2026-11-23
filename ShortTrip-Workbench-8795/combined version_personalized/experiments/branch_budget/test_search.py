"""Check suspension, sibling isolation and conservation of finite work."""

from collections import Counter
import unittest

from experiments.branch_budget.search import CHECKPOINT, Continuation, RoundRobin


class TinyAgent:
    def __init__(self):
        self.dfs_entries = 0
        self.visited = []
        self.active = None
        self.trace = []

    def path_state(self):
        return {"visited": self.visited}

    def switch_group(self, group):
        pass

    def subtree(self, name, mode, size):
        def walk():
            for index in range(size):
                yield CHECKPOINT
                if self.visited != [(name, mode, i) for i in range(index)]:
                    raise AssertionError("A suspended task lost its state")
                self.dfs_entries += 1
                event = (name, mode, index)
                self.trace.append(event)
                self.visited.append(event)

        return Continuation(name, {"visited": []}, walk())


class SchedulingTest(unittest.TestCase):
    def test_rounds_resume_each_transport_variant_and_admit_later_branches(self):
        agent = TinyAgent()
        children = [agent.subtree("A", "train", 7), agent.subtree("A", "walk", 3),
                    agent.subtree("B", "train", 6), agent.subtree("C", "walk", 3)]

        def discover():
            yield CHECKPOINT
            agent.dfs_entries += 1
            yield from children

        root = Continuation(None, {"visited": []}, discover())
        scheduler = RoundRobin(agent, quantum=2, initial_width=3)
        scheduler.run(root)

        self.assertEqual(scheduler.admissions, Counter({"A": 2, "B": 1, "C": 1}))
        self.assertEqual(scheduler.completed, scheduler.admissions)
        self.assertEqual(agent.dfs_entries, 20)
        self.assertEqual(len(set(agent.trace)), 19)
        self.assertEqual([name for name, _, _ in agent.trace[:4]], ["A", "A", "B", "B"])
        self.assertLess(agent.trace.index(("C", "walk", 0)), agent.trace.index(("A", "train", 6)))


if __name__ == "__main__":
    unittest.main()
