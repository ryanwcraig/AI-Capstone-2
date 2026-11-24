"""Configuration for the Costco Coupon Agent."""

from datetime import datetime
from pathlib import Path


class Config:
    """Central configuration for the agent system."""

    # Paths
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    OUTPUT_BASE = PROJECT_ROOT / "coupons"
    CACHE_DIR = PROJECT_ROOT / "cache"
    DB_PATH = PROJECT_ROOT / "coupons.db"
    LOG_FILE = PROJECT_ROOT / "agent.log"

    # Time scope constraint
    MIN_START_DATE = datetime(2026, 1, 1)

    # Rate limiting
    DELAY_MIN = 2.0
    DELAY_MAX = 3.0
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2.0
    REQUEST_TIMEOUT = 30

    # Allowed domains
    ALLOWED_DOMAINS = {"costcoinsider.com", "www.costcoinsider.com"}

    # OCR configuration
    OCR_LANGUAGE = "en-US"

    # Classification departments
    DEPARTMENTS = [
        "Grocery",
        "Health & Beauty",
        "Home & Kitchen",
        "Electronics",
        "Apparel",
        "Automotive",
        "Seasonal",
        "Dining",
        "Travel",
        "General",
    ]

    # Keyword rules for classification (department -> keywords)
    CLASSIFICATION_RULES = {
        "Grocery": [
            "food", "snack", "breakfast", "coffee", "tea", "cereal", "pasta",
            "rice", "spice", "condiment", "sauce", "dairy", "cheese", "yogurt",
            "meat", "chicken", "beef", "fish", "seafood", "fruit", "vegetable",
            "juice", "drink", "water", "soda", "energy drink", "protein bar",
        ],
        "Health & Beauty": [
            "vitamin", "supplement", "medicine", "pain relief", "allergy",
            "shampoo", "conditioner", "lotion", "cream", "soap", "toothpaste",
            "deodorant", "sunscreen", "makeup", "perfume", "razor", "floss",
        ],
        "Home & Kitchen": [
            "kitchen", "cookware", "utensil", "pan", "pot", "knife", "spatula",
            "cleaning", "detergent", "paper towel", "tissue", "trash bag",
            "furniture", "decor", "lighting", "linen", "towel", "pillow",
        ],
        "Electronics": [
            "phone", "laptop", "computer", "tv", "headphone", "speaker",
            "camera", "tablet", "charger", "battery", "cable", "gadget",
            "smart home", "security system",
        ],
        "Apparel": [
            "shirt", "pants", "dress", "jacket", "sweater", "hat", "sock",
            "shoe", "boot", "glove", "scarf", "underwear", "swimwear",
        ],
        "Automotive": [
            "oil", "tire", "wiper", "car wash", "fuel", "motor", "battery",
            "antifreeze", "floor mat", "seat cover",
        ],
        "Seasonal": [
            "christmas", "holiday", "halloween", "easter", "valentine",
            "thanksgiving", "new year", "summer", "winter", "spring", "fall",
        ],
        "Dining": [
            "restaurant", "dinner", "lunch", "breakfast place", "coffee shop",
            "ice cream", "pizza", "burger", "steakhouse", "brunch",
        ],
        "Travel": [
            "hotel", "resort", "airline", "cruise", "vacation", "rental car",
            "national park", "museum", "theme park",
        ],
    }

    # User preferences (can be updated via feedback)
    USER_PREFERENCES = {
        "favorite_departments": ["Grocery", "Health & Beauty"],
        "excluded_categories": [],
        "price_threshold": None,  # Only show deals above this value
    }
