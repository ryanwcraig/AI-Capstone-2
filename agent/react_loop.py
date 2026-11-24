"""ReAct reasoning loop for sequential coupon book processing."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, List

from .logger import get_logger
from .config import Config


@dataclass
class Thought:
    """A single step in the ReAct reasoning chain."""
    reason: str
    action: str
    observation: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "reason": self.reason,
            "action": self.action,
            "observation": self.observation,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Plan:
    """A complete ReAct plan with reasoning trace."""
    goal: str
    thoughts: List[Thought] = field(default_factory=list)
    status: str = "pending"  # pending, running, completed, failed

    def add_thought(self, reason: str, action: str, observation: Optional[str] = None):
        self.thoughts.append(Thought(reason=reason, action=action, observation=observation))

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "status": self.status,
            "thoughts": [t.to_dict() for t in self.thoughts],
        }


class ReActAgent:
    """Base class for agents using the ReAct reasoning loop.

    Implements Plan -> Execute -> Observe -> Correct pattern.
    """

    def __init__(self, name: str, config: Config = None):
        self.name = name
        self.config = config or Config()
        self.logger = get_logger(name.lower(), self.config.LOG_FILE)
        self.plan = None

    def plan(self, goal: str, context: dict = None) -> Plan:
        """Create a plan for achieving the goal."""
        raise NotImplementedError

    def execute(self, action: str, **kwargs) -> Any:
        """Execute an action and return results."""
        raise NotImplementedError

    def observe(self, result: Any) -> str:
        """Observe execution results and form assessment."""
        raise NotImplementedError

    def correct(self, assessment: str) -> Optional[str]:
        """Determine corrective action based on observation.

        Returns a new action to try or None if plan is complete.
        """
        return None

    def run(self, goal: str, context: dict = None, max_steps: int = 10) -> Plan:
        """Execute the ReAct loop until completion or max steps reached."""
        self.plan = self.plan(goal, context)
        self.plan.status = "running"
        self.logger.info(f"[{self.name}] Starting plan: {goal}")

        steps = 0
        while steps < max_steps and self.plan.status == "running":
            steps += 1

            # Reasoning about current step
            reason = self._next_reason()
            action = self._next_action()

            if action is None:
                self.plan.status = "completed"
                break

            thought = Thought(reason=reason, action=action)
            self.plan.add_thought(reason, action)
            self.logger.info(f"[{self.name}] Step {steps}: {reason}")
            self.logger.debug(f"[{self.name}] Action: {action}")

            # Execute the action
            try:
                result = self.execute(action, **kwargs)
                observation = self.observe(result)
            except Exception as e:
                observation = f"Error: {str(e)}"
                self.logger.error(f"[{self.name}] Execution error: {e}")

            thought.observation = observation
            self.logger.debug(f"[{self.name}] Observation: {observation[:100]}")

            # Check if correction needed
            next_action = self.correct(observation)
            if next_action is not None:
                self.logger.info(f"[{self.name}] Correcting course: {next_action}")

        return self.plan

    def _next_reason(self) -> str:
        """Generate reasoning for the current step. Override in subclasses."""
        return f"Working toward goal: {self.plan.goal}"

    def _next_action(self) -> Optional[str]:
        """Determine the next action to take. Returns None when complete."""
        raise NotImplementedError
