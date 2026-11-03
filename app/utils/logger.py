import logging
import re

# PII Masking regular expressions
RE_10_PLUS_DIGITS = re.compile(r"\b\d{10,}\b")
RE_AMOUNTS = re.compile(
    r"(?:₦|NGN|\$|€|£)\s*[\d,.]+|\b\d+(?:,\d{3})*(?:\.\d{1,2})?\s*(?:naira|kobo|ngn|k)\b",
    re.IGNORECASE,
)


def mask_pii(text: str) -> str:
    """Masks 10+ digit account/phone numbers and financial amounts for safe logging."""
    if not text:
        return ""
    masked = RE_10_PLUS_DIGITS.sub("[NUMBER MASKED]", text)
    masked = RE_AMOUNTS.sub("[AMOUNT MASKED]", masked)
    return masked


def mask_utterance(utterance: str) -> str:
    """Masks spoken utterance text in logs to prevent speech content retention."""
    if not utterance:
        return "[EMPTY]"
    return f"[UTTERANCE MASKED (len={len(utterance)})]"


def get_logger(name: str = "accessai") -> logging.Logger:
    """Configures a standard privacy-compliant logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
