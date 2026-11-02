import re
from decimal import Decimal, InvalidOperation

# Word-to-number mapping
ONES_AND_TEENS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

SCALES = {
    "hundred": 100,
    "thousand": 1_000,
    "k": 1_000,
    "grand": 1_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
}


def words_to_number(text: str) -> Decimal | None:
    """Converts English number words like 'five thousand two hundred' or 'twenty' into Decimal.

    Returns None if text cannot be parsed as a number.
    """
    clean_words = [w.strip() for w in re.split(r"[\s-]+", text.lower()) if w.strip()]
    if not clean_words:
        return None

    # Handle special cases like 'half a million' or 'half million'
    if len(clean_words) >= 2 and clean_words[0] == "half":
        rest = " ".join(clean_words[1:])
        if rest in ["a million", "million", "m"]:
            return Decimal(500000)
        if rest in ["a billion", "billion", "b"]:
            return Decimal(500000000)
        if rest in ["a thousand", "thousand", "k"]:
            return Decimal(500)

    total = Decimal(0)
    current = Decimal(0)
    has_number = False

    for word in clean_words:
        if word in ["and", "a", "an"]:
            continue
        elif word in ONES_AND_TEENS:
            current += Decimal(ONES_AND_TEENS[word])
            has_number = True
        elif word in TENS:
            current += Decimal(TENS[word])
            has_number = True
        elif word == "hundred":
            if current == 0:
                current = Decimal(1)
            current *= Decimal(100)
            has_number = True
        elif word in ["thousand", "k", "grand"]:
            if current == 0:
                current = Decimal(1)
            total += current * Decimal(1000)
            current = Decimal(0)
            has_number = True
        elif word in ["million", "m"]:
            if current == 0:
                current = Decimal(1)
            total += current * Decimal(1000000)
            current = Decimal(0)
            has_number = True
        elif word in ["billion", "b"]:
            if current == 0:
                current = Decimal(1)
            total += current * Decimal(1000000000)
            current = Decimal(0)
            has_number = True
        else:
            # Check if this single word is already a numeric string, e.g. '20' or '5.5'
            try:
                num = Decimal(word)
                current += num
                has_number = True
            except InvalidOperation:
                return None

    if not has_number:
        return None

    return total + current


def parse_amount_to_kobo(raw_text: str) -> int | None:
    """Deterministically parses raw amount text into Nigerian kobo (1 Naira = 100 kobo).

    Handles:
    - Shorthand: '5k', '1.5k', '20k', '2m', '5 grand'
    - Currency symbols: '₦5000', 'N5000', 'NGN 5,000.50', '#5000'
    - English words: 'five thousand naira', 'twenty thousand five hundred naira'
    - Compound currency: 'five hundred naira fifty kobo', '75 kobo'
    - Fractions: 'half a million naira'

    Returns:
        Integer kobo count, or None if amount cannot be deterministically determined.
    """
    if not raw_text or not isinstance(raw_text, str):
        return None

    text = raw_text.strip().lower()
    # Normalize currency markers
    text = text.replace("₦", " ").replace("ngn", " ").replace("#", " ")
    # Replace leading 'n' when attached to digit, e.g. "n5000" -> "5000"
    text = re.sub(r"\bn(\d+)", r"\1", text)
    # Remove commas in numbers (e.g., 5,000 -> 5000)
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = text.strip()

    if not text:
        return None

    # If it's purely kobo (e.g. "50 kobo" or "fifty kobo")
    if (
        "kobo" in text
        and "naira" not in text
        and not re.search(r"\b(k|grand|million|m|billion|b)\b", text)
    ):
        kobo_part = re.sub(r"\s*kobo\b.*", "", text).strip()
        # Try direct number
        try:
            kobo_val = Decimal(kobo_part)
            return round(kobo_val)
        except InvalidOperation:
            word_num = words_to_number(kobo_part)
            if word_num is not None:
                return round(word_num)
            return None

    # If both naira and kobo are explicitly stated
    if "naira" in text and "kobo" in text:
        parts = text.split("naira")
        naira_part = parts[0].strip()
        kobo_part = parts[1].replace("kobo", "").replace("and", "").strip()

        naira_val = _parse_naira_portion(naira_part)
        if naira_val is None:
            return None

        try:
            kobo_val = Decimal(kobo_part) if kobo_part else Decimal(0)
        except InvalidOperation:
            kobo_val = words_to_number(kobo_part) or Decimal(0)

        total_kobo = (naira_val * Decimal(100)) + kobo_val
        return round(total_kobo)

    # Strip trailing "naira", "bucks", "n"
    cleaned = re.sub(r"\b(naira|nairas|bucks|ngn)\b", "", text).strip()

    naira_val = _parse_naira_portion(cleaned)
    if naira_val is not None:
        return round(naira_val * Decimal(100))

    return None


def _parse_naira_portion(text: str) -> Decimal | None:
    """Helper to parse a string representing a Naira amount into Decimal Naira."""
    text = text.strip()
    if not text:
        return None

    # Check for multiplier shorthand like '5k', '1.5k', '2.5m', '10b', '5 grand'
    shorthand_match = re.match(
        r"^(\d+(?:\.\d+)?)\s*(k|grand|m|million|b|billion)$", text, re.IGNORECASE
    )
    if shorthand_match:
        num = Decimal(shorthand_match.group(1))
        unit = shorthand_match.group(2).lower()
        if unit in ["k", "grand"]:
            return num * Decimal(1000)
        elif unit in ["m", "million"]:
            return num * Decimal(1000000)
        elif unit in ["b", "billion"]:
            return num * Decimal(1000000000)

    # Check for direct decimal/integer number
    try:
        return Decimal(text)
    except InvalidOperation:
        pass

    # Try word-to-number
    return words_to_number(text)


def verify_amount_agreement(amount_kobo: int, llm_estimate: float | None) -> bool:
    """Validates whether the deterministic parse matches the LLM's own numeric estimate.

    If LLM provided an estimate, it must agree within 0.01 Naira.
    If LLM provided None, deterministic parse is accepted.
    """
    if llm_estimate is None:
        return True

    naira_deterministic = Decimal(amount_kobo) / Decimal(100)
    llm_naira = Decimal(str(llm_estimate))

    # Must agree within 1 kobo (0.01 Naira)
    return abs(naira_deterministic - llm_naira) < Decimal("0.01")
