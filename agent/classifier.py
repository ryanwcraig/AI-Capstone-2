"""Hybrid deal classification: keyword rules with LLM fallback."""

import json
from pathlib import Path
from typing import Optional, Tuple

from .config import Config
from .logger import get_logger


class DealClassifier:
    """Classify coupon deals into departments using rules + LLM fallback."""

    def __init__(self, config: Config = None):
        self.config = config or Config()
        self.logger = get_logger("classifier", self.config.LOG_FILE)
        self.feedback_path = self.config.PROJECT_ROOT / "classification_feedback.json"
        self.custom_rules = self._load_custom_rules()

    def _load_custom_rules(self) -> dict:
        """Load user-provided classification overrides from feedback file."""
        if not self.feedback_path.exists():
            return {}
        try:
            return json.loads(self.feedback_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.logger.warning(f"Could not load custom rules: {e}")
            return {}

    def _save_custom_rules(self):
        """Persist user-provided classification overrides."""
        try:
            self.feedback_path.write_text(
                json.dumps(self.custom_rules, indent=2), encoding="utf-8"
            )
        except Exception as e:
            self.logger.warning(f"Could not save custom rules: {e}")

    def classify_by_rules(self, deal_text: str) -> Optional[str]:
        """Classify using keyword matching. Returns department or None."""
        text = deal_text.lower()

        # Check user-provided overrides first
        for phrase, department in self.custom_rules.items():
            if phrase.lower() in text:
                return department

        # Score-based keyword matching
        scores = {dept: 0 for dept in self.config.DEPARTMENTS}
        for department, keywords in self.config.CLASSIFICATION_RULES.items():
            for keyword in keywords:
                if keyword.lower() in text:
                    scores[department] += len(keyword)  # Longer matches weigh more

        best_department = max(scores, key=scores.get)
        if scores[best_department] > 0:
            return best_department

        return None

    def classify_with_llm(self, deal_text: str) -> str:
        """Classify using LLM fallback when rules don't match."""
        # Placeholder for LLM-based classification
        # In production, this would call an LLM API with the deal description
        self.logger.debug(f"LLM fallback needed for: {deal_text[:50]}...")

        # Simple heuristic fallback for now
        return "General"

    def classify(self, deal_text: str) -> Tuple[str, bool]:
        """Classify a deal into a department.

        Returns (department, used_llm_fallback).
        """
        department = self.classify_by_rules(deal_text)
        if department is not None:
            return department, False

        # Fall back to LLM classification
        department = self.classify_with_llm(deal_text)
        return department, True

    def record_feedback(self, deal_text: str, correct_department: str):
        """Record user feedback to improve future classifications."""
        # Extract a representative phrase from the deal text
        words = deal_text.split()
        if len(words) >= 2:
            phrase = " ".join(words[:3])  # First few words as key phrase
            self.custom_rules[phrase] = correct_department
            self._save_custom_rules()
            self.logger.info(f"Recorded feedback: '{phrase}' -> {correct_department}")
