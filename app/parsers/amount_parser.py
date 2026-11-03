import re
from decimal import Decimal, InvalidOperation

# Multilingual Word-to-number mapping (English, Yoruba, Igbo, Hausa, French, German, Spanish)
ONES_AND_TEENS = {
    # English
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
    # Yoruba
    "kan": 1,
    "meji": 2,
    "mẹta": 3,
    "meta": 3,
    "mẹrin": 4,
    "merin": 4,
    "marun": 5,
    "márùn": 5,
    "márùn-ún": 5,
    "mẹfa": 6,
    "meje": 7,
    "mẹjọ": 8,
    "mẹsan": 9,
    "mẹwa": 10,
    "mẹ́wàá": 10,
    # Igbo
    "otu": 1,
    "abụọ": 2,
    "abuo": 2,
    "atọ": 3,
    "ato": 3,
    "anọ": 4,
    "ano": 4,
    "ise": 5,
    "isii": 6,
    "asaa": 7,
    "asatọ": 8,
    "asato": 8,
    "itoolu": 9,
    "iri": 10,
    # Hausa
    "daya": 1,
    "ɗaya": 1,
    "biyu": 2,
    "uku": 3,
    "hudu": 4,
    "huɗu": 4,
    "biyar": 5,
    "shida": 6,
    "bakwai": 7,
    "takwas": 8,
    "tara": 9,
    "goma": 10,
    # French
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    # German
    "eins": 1,
    "ein": 1,
    "eine": 1,
    "zwei": 2,
    "drei": 3,
    "vier": 4,
    "fünf": 5,
    "funf": 5,
    "sechs": 6,
    "sieben": 7,
    "acht": 8,
    "neun": 9,
    "zehn": 10,
    # Spanish
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
}

TENS = {
    # English
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    # Yoruba
    "ogun": 20,
    "ọgbọ̀n": 30,
    "ogbon": 30,
    "ogoji": 40,
    "aadọta": 50,
    # Igbo
    "iri abụọ": 20,
    "iri ato": 30,
    # Hausa
    "ashirin": 20,
    "talatin": 30,
    "arba'in": 40,
    "hamsin": 50,
    # French
    "vingt": 20,
    "trente": 30,
    "quarante": 40,
    "cinquante": 50,
    # German
    "zwanzig": 20,
    "dreißig": 30,
    "dreissig": 30,
    "vierzig": 40,
    "fünfzig": 50,
    # Spanish
    "veinte": 20,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
}

SCALES = {
    # Hundreds
    "hundred": 100,
    "ọgọrún": 100,
    "ogorun": 100,
    "narị": 100,
    "nari": 100,
    "dari": 100,
    "cent": 100,
    "hundert": 100,
    "cien": 100,
    "ciento": 100,
    # Thousands
    "thousand": 1_000,
    "k": 1_000,
    "grand": 1_000,
    "ẹgbẹrún": 1_000,
    "egberun": 1_000,
    "puku": 1_000,
    "dubu": 1_000,
    "mille": 1_000,
    "tausend": 1_000,
    "mil": 1_000,
    # Compound thousands (e.g. German fünftausend)
    "fünftausend": 5_000,
    "zehntausend": 10_000,
    "zwanzigtausend": 20_000,
    # Millions
    "million": 1_000_000,
    "m": 1_000_000,
    "mílíọ̀nù": 1_000_000,
    "miliọnu": 1_000_000,
    "nde": 1_000_000,
    "miliyan": 1_000_000,
    "millón": 1_000_000,
    "millon": 1_000_000,
    # Billions
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
}


def words_to_number(text: str) -> Decimal | None:
    """Converts multilingual number words (English, Yoruba, Igbo, Hausa, French, German, Spanish)

    into Decimal. Returns None if text cannot be parsed as a number.
    """
    clean_words = [w.strip() for w in re.split(r"[\s-]+", text.lower()) if w.strip()]
    if not clean_words:
        return None

    # Handle special phrases like 'half a million'
    if len(clean_words) >= 2 and clean_words[0] in [
        "half",
        "moitié",
        "hälfte",
        "mitad",
    ]:
        rest = " ".join(clean_words[1:])
        if rest in [
            "a million",
            "million",
            "m",
            "d'un million",
            "einer million",
            "un millón",
        ]:
            return Decimal(500000)
        if rest in ["a thousand", "thousand", "k"]:
            return Decimal(500)

    # Check direct single-token scales (e.g., 'fünftausend' = 5000)
    joined = " ".join(clean_words)
    if joined in SCALES:
        return Decimal(SCALES[joined])

    # Handle African languages where scale precedes multiplier (e.g. "ẹgbẹrún márùn-ún", "dubu biyar", "puku ise")
    african_scales = {
        "ẹgbẹrún": 1_000,
        "egberun": 1_000,
        "puku": 1_000,
        "dubu": 1_000,
        "ọgọrún": 100,
        "ogorun": 100,
        "dari": 100,
        "narị": 100,
        "nari": 100,
        "mílíọ̀nù": 1_000_000,
        "miliọnu": 1_000_000,
        "nde": 1_000_000,
        "miliyan": 1_000_000,
    }
    if clean_words and clean_words[0] in african_scales:
        scale_mult = Decimal(african_scales[clean_words[0]])
        if len(clean_words) == 1:
            return scale_mult
        rest_val = words_to_number(" ".join(clean_words[1:]))
        if rest_val is not None:
            return scale_mult * rest_val

    total = Decimal(0)
    current = Decimal(0)
    has_number = False

    for word in clean_words:
        if word in ["and", "a", "an", "et", "und", "y", "de", "d'", "un", "ún"]:
            continue
        elif word in ONES_AND_TEENS:
            current += Decimal(ONES_AND_TEENS[word])
            has_number = True
        elif word in TENS:
            current += Decimal(TENS[word])
            has_number = True
        elif word in SCALES:
            scale_val = Decimal(SCALES[word])
            if scale_val == 100:
                if current == 0:
                    current = Decimal(1)
                current *= scale_val
            elif scale_val >= 1000:
                if current == 0:
                    current = Decimal(1)
                total += current * scale_val
                current = Decimal(0)
            has_number = True
        else:
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

    Handles English, Pidgin, Yoruba, Igbo, Hausa, French, German, and Spanish:
    - Shorthand: '5k', '1.5k', '20k', '2m', '5 grand'
    - Currency symbols: '₦5000', 'N5000', 'NGN 5,000.50', '#5000'
    - Multilingual words: 'five thousand naira', 'ẹgbẹrún márùn-ún', 'puku ise', 'dubu biyar', 'cinq mille', 'fünftausend', 'cinco mil'
    - Compound currency: 'five hundred naira fifty kobo', '75 kobo'
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

    # Strip currency naming in multiple languages (naira, nairas)
    cleaned = re.sub(
        r"\b(naira|nairas|bucks|ngn|owó|owo|ego|kudi|kuɗi)\b", "", text
    ).strip()

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

    # Try multilingual word-to-number
    return words_to_number(text)


def verify_amount_agreement(amount_kobo: int, llm_estimate: float | None) -> bool:
    """Validates whether the deterministic parse matches the LLM's own numeric estimate."""
    if llm_estimate is None:
        return True

    naira_deterministic = Decimal(amount_kobo) / Decimal(100)
    llm_naira = Decimal(str(llm_estimate))

    return abs(naira_deterministic - llm_naira) < Decimal("0.01")
