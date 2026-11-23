"""Resume the frozen C-group DFS at recursive entry points."""

import ast
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
import inspect
import sys
import textwrap
import time

from experiments.prefix_probe.probe import PROJECT

# The original experimental agent and runner use sibling imports.
sys.path.insert(0, str(PROJECT / "experiments/prefix_probe"))
from probe import PrefixProbeAgent, ProbeBudget
from experiment_agent import ExperimentAgent, dfs_snapshot
from chinatravel.soft_constraints.agent import CandidateDeadline


class SuspendCalls(ast.NodeTransformer):
    """Change only the two known DFS call forms into generator delegation."""

    def __init__(self):
        self.recursive = 0
        self.body = 0

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "dfs_poi":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                self.recursive += 1
            elif isinstance(node.func.value, ast.Name) and node.func.value.id == "dfs_snapshot":
                self.body += 1
                node.func = ast.Attribute(ast.Name("self", ast.Load()), "_resume_body", ast.Load())
                node.args = node.args[1:]
            else:
                raise ValueError("Unexpected DFS call form")
            return ast.copy_location(ast.YieldFrom(node), node)
        return node


@lru_cache(maxsize=1)
def resumable_functions():
    result = []
    for function, expected in ((ExperimentAgent.dfs_poi, (1, 1)), (dfs_snapshot.dfs_poi, (6, 0))):
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
        transform = SuspendCalls()
        transform.visit(tree)
        if (transform.recursive, transform.body) != expected:
            raise ValueError("Frozen DFS call sites changed")
        namespace = dict(function.__globals__)
        code = compile(ast.fix_missing_locations(tree), inspect.getsourcefile(function), "exec")
        exec(code, namespace)
        result.append(namespace["dfs_poi"])
    return tuple(result)


VISITING = ("restaurants_visiting", "attractions_visiting", "food_type_visiting",
            "spot_type_visiting", "attraction_names_visiting")
CHECKPOINT = object()


@dataclass
class Continuation:
    first: str | None
    state: dict
    iterator: object = None


@dataclass
class FirstAttraction:
    name: str
    tasks: deque = field(default_factory=deque)


class RoundRobin:
    """Give each admitted first attraction one slice per round."""

    def __init__(self, agent, quantum, initial_width):
        self.agent = agent
        self.quantum = quantum
        self.initial_width = initial_width
        self.groups = {}
        self.admissions = Counter()
        self.slices = Counter()
        self.completed = Counter()
        self.rounds = 0

    def advance(self, continuation, quota):
        agent = self.agent
        agent.active = continuation
        agent.switch_group(continuation.first)
        for name, value in continuation.state.items():
            setattr(agent, name, value)
        start = agent.dfs_entries
        if continuation.first is not None:
            self.slices[continuation.first] += 1
        try:
            while True:
                event = next(continuation.iterator)
                if isinstance(event, Continuation):
                    group = self.groups.setdefault(event.first, FirstAttraction(event.first))
                    group.tasks.append(event)
                    self.admissions[event.first] += 1
                    return False
                if agent.dfs_entries - start >= quota:
                    return False
        except StopIteration:
            if continuation.first is not None:
                self.completed[continuation.first] += 1
            return True
        finally:
            continuation.state = agent.path_state()
            agent.active = None
            agent.switch_group(None)

    def run(self, root):
        frontier_done = False
        while not frontier_done or self.groups:
            self.rounds += 1
            # The first few resumes admit actual legal outgoing choices. A
            # coordinator slice also bounds work before the first attraction.
            additions = self.initial_width if self.rounds == 1 else 1
            for _ in range(additions):
                if frontier_done:
                    break
                frontier_done = self.advance(root, self.quantum)
            for name, group in list(self.groups.items()):
                task = group.tasks.popleft()
                done = self.advance(task, self.quantum)
                if not done:
                    group.tasks.append(task)
                if not group.tasks:
                    del self.groups[name]

    def close(self, root):
        root.iterator.close()
        for group in self.groups.values():
            for task in group.tasks:
                task.iterator.close()


class BudgetAgent(PrefixProbeAgent):
    def __init__(self, *, order, method, ranking="rank_fixed", **kwargs):
        super().__init__(variant=ranking, activity_policy="rules", **kwargs)
        self.order = order
        self.method = method
        self.first_order = None
        self.active = None
        self.branch_entries = Counter()
        self.branch_seconds = Counter()
        self.branch_routes = Counter()
        self.branch_validations = Counter()
        self.branch_candidates = Counter()
        self._group = None
        self._group_since = time.monotonic()
        self._resume_entry, self._resume_body = (f.__get__(self) for f in resumable_functions())

    def path_state(self):
        return {name: getattr(self, name, None) for name in (*VISITING, "_context", "_path")}

    def switch_group(self, group):
        now = time.monotonic()
        if self._group is not None:
            self.branch_seconds[self._group] += now - self._group_since
        self._group, self._group_since = group, now

    def wall_check(self):
        if time.monotonic() - self.started >= self.wall_limit_seconds:
            raise CandidateDeadline()

    def _deadline_check(self):
        self.wall_check()
        if self.dfs_entries >= self.dfs_budget:
            raise ProbeBudget("dfs_budget_reached")

    def collect_innercity_transport(self, *args, **kwargs):
        self.wall_check()
        self.branch_routes[self._group or ""] += 1
        result = super().collect_innercity_transport(*args, **kwargs)
        self.wall_check()
        return result

    def constraints_validation(self, query, plan, poi_plan):
        self.wall_check()
        self.branch_validations[self._group or ""] += 1
        return super().constraints_validation(query, plan, poi_plan)

    def reranking_attractions_with_constraints(self, plan, poi_type, day, at,
                                               position, info, query, ranking):
        indices = super().reranking_attractions_with_constraints(
            plan, poi_type, day, at, position, info, query, ranking)
        if not self.attractions_visiting:
            if len(indices) >= 3:
                indices = [indices[i] for i in self.order] + indices[3:]
            if self.first_order is None:
                self.first_order = [str(info.iloc[int(i)]["name"]) for i in indices[:3]]
        return indices

    def dfs_poi(self, query, poi_plan, plan, current_time, current_position, current_day=0):
        first = self.attraction_names_visiting[0] if self.attraction_names_visiting else None
        args = (query, poi_plan, plan, current_time, current_position, current_day)
        if first is not None and self.method == "round_robin" and self.active.first is None:
            # The coordinator backtracks as usual. The copied child owns its
            # own mutable plan and visiting lists from this point onward.
            state = {name: deepcopy(getattr(self, name)) for name in VISITING}
            state.update(_context=None, _path=[])
            child = Continuation(str(first), state)
            child.iterator = self.dfs_poi(query, poi_plan, deepcopy(plan),
                                         current_time, current_position, current_day)
            yield child
            return False, plan
        yield CHECKPOINT
        self._deadline_check()
        self.dfs_entries += 1
        self.branch_entries[first or ""] += 1
        parent, old_path = self._group, self._path
        self.switch_group(first)
        self._path = plan
        try:
            return (yield from self._resume_entry(*args))
        finally:
            self._path = old_path
            self.switch_group(parent)

    def search(self, state, quantum, width):
        root = Continuation(None, self.path_state(), self.dfs_poi(**state))
        scheduler = RoundRobin(self, quantum, width)
        try:
            if self.method == "dfs":
                scheduler.advance(root, sys.maxsize)
            else:
                scheduler.run(root)
        finally:
            self.switch_group(None)
            scheduler.close(root)
            self.scheduler = scheduler
