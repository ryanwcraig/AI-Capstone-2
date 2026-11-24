"""Costco Coupon Agent - Personal Assistant for tracking Costco coupon books.

This agent automates the retrieval, parsing, classification, and scheduling
of Costco coupon book data from costcoinsider.com, with rate-limit protections
and intelligent caching.
"""

__version__ = "1.0.0"

from .config import Config
from .react_loop import ReActAgent, Plan
from .agents.supervisor import SupervisorAgent
from .agents.retrieval_agent import RetrievalAgent
from .agents.parsing_agent import ParsingAgent
from .agents.structuring_agent import StructuringAgent

__all__ = [
    "Config",
    "ReActAgent",
    "Plan",
    "SupervisorAgent",
    "RetrievalAgent",
    "ParsingAgent",
    "StructuringAgent",
]
