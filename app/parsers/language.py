import re

SUPPORTED_LANGUAGES = {
  "en": "English",
  "pcm": "Broken English / Nigerian Pidgin",
  "yo": "Yoruba",
  "ig": "Igbo",
  "ha": "Hausa",
  "fr": "French",
  "de": "German",
  "es": "Spanish",
}

# Detection keyword patterns
LANGUAGE_PATTERNS = [
  # Yoruba
  (
    "yo",
    [
      r"\b(ẹ jọọ|e joo|ẹ joo|e jọọ|ranṣẹ|ránṣẹ́|ranse|ṣayẹwo|sayewo|ẹgbẹrún|egberun|kí ni|ki ni|ọgọrún|ogorun|owo|owó|àkọọ́lẹ̀|akoolo|akọọlẹ|jẹ́rìí|fọwọsi)\b"
    ],
  ),
  # Igbo
  (
    "ig",
    [
      r"\b(biko|ziga|nye|ego|akaụntụ|akawntu|fọdụrụ|foduru|kedu|kwado|akwadom|puku|narị|nari|nde|otu|abụọ|atọ|ise|iri)\b"
    ],
  ),
  # Hausa
  (
    "ha",
    [
      r"\b(don allah|tura|zuwa|ga|kudi|kuɗi|asusu|asusunka|nawa|dubu|dari|miliyan|tabbatar|biyar|goma|ashirin)\b"
    ],
  ),
  # French
  (
    "fr",
    [
      r"\b(s'il vous plaît|sil vous plait|envoyez|virer|solde|combien|compte|nairas|dix|vingt|mille|cent|confirmer|annuler)\b"
    ],
  ),
  # German
  (
    "de",
    [
      r"\b(bitte|überweise|sende|kontostand|guthaben|wieviel|tausend|hundert|bestätigen|abbrechen)\b"
    ],
  ),
  # Spanish
  (
    "es",
    [
      r"\b(por favor|envía|envia|transfiere|saldo|cuál|cual|cuánto|cuanto|cuenta|mil|ciento|confirmar|cancelar)\b"
    ],
  ),
  # Nigerian Pidgin / Broken English
  (
    "pcm",
    [
      r"\b(abeg|wetin|dey|give|dash|oya|wire|remain|for inside|how much i get|how much remain)\b"
    ],
  ),
]


def detect_language(utterance: str, locale: str | None = None) -> str:
  """Detects whether utterance is Yoruba, Igbo, Hausa, French, German, Spanish,

  Nigerian Pidgin (Broken English), or Standard English. Defaults to 'en' or supplied locale.
  """
  if not utterance:
    if locale:
      norm_loc = locale.split("-")[0].lower()
      if norm_loc in SUPPORTED_LANGUAGES:
        return norm_loc
    return "en"

  text = utterance.strip().lower()

  for lang, patterns in LANGUAGE_PATTERNS:
    for pat in patterns:
      if re.search(pat, text, re.IGNORECASE):
        return lang

  if locale:
    norm_loc = locale.split("-")[0].lower()
    if norm_loc in SUPPORTED_LANGUAGES:
      return norm_loc

  return "en"
