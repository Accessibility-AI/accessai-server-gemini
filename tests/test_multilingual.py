import pytest

from app.parsers.amount_parser import parse_amount_to_kobo
from app.parsers.language import detect_language
from app.providers.mock_provider import MockLLMProvider
from app.services.confirmation import is_strict_confirmation
from app.services.interpreter import InterpreterService

MANIFEST = [
  {
    "id": "get_balance",
    "name": "get_balance",
    "description": "Check account balance",
    "params": [],
  },
  {
    "id": "transfer_money",
    "name": "transfer_money",
    "description": "Transfer money to a recipient",
    "params": [
      {
        "name": "recipient",
        "type": "string",
        "required": True,
      },
      {
        "name": "amount_minor",
        "type": "amount_minor",
        "required": True,
      },
    ],
  },
]


@pytest.fixture
def service():
  provider = MockLLMProvider()
  return InterpreterService(provider=provider)


def test_language_detection():
  assert detect_language("ẹ jọọ ẹ fi 5k ranṣẹ si David") == "yo"
  assert detect_language("biko ziga 5k nye David") == "ig"
  assert detect_language("don allah tura 5k zuwa ga David") == "ha"
  assert detect_language("s'il vous plaît envoyez 5k à David") == "fr"
  assert detect_language("bitte überweise 5k an David") == "de"
  assert detect_language("por favor envía 5k a David") == "es"
  assert detect_language("abeg send 5k give David") == "pcm"
  assert detect_language("please transfer 5000 to David") == "en"


def test_multilingual_amount_parsing():
  # Yoruba words
  assert parse_amount_to_kobo("ẹgbẹrún kan") == 100_000
  assert parse_amount_to_kobo("ẹgbẹrún márùn-ún") == 500_000

  # Igbo words
  assert parse_amount_to_kobo("puku ise") == 500_000
  assert parse_amount_to_kobo("puku iri") == 1_000_000

  # Hausa words
  assert parse_amount_to_kobo("dubu biyar") == 500_000
  assert parse_amount_to_kobo("dubu ashirin") == 2_000_000

  # French words
  assert parse_amount_to_kobo("cinq mille nairas") == 500_000
  assert parse_amount_to_kobo("dix mille nairas") == 1_000_000

  # German words
  assert parse_amount_to_kobo("fünftausend naira") == 500_000
  assert parse_amount_to_kobo("zehntausend naira") == 1_000_000

  # Spanish words
  assert parse_amount_to_kobo("cinco mil nairas") == 500_000
  assert parse_amount_to_kobo("diez mil nairas") == 1_000_000


def test_multilingual_strict_confirmation():
  # Yoruba
  assert is_strict_confirmation("jẹ́rìí") is True
  assert is_strict_confirmation("mo fọwọsi") is True

  # Igbo
  assert is_strict_confirmation("kwado") is True
  assert is_strict_confirmation("akwadom") is True

  # Hausa
  assert is_strict_confirmation("tabbatar") is True
  assert is_strict_confirmation("na tabbatar") is True

  # French
  assert is_strict_confirmation("confirmer") is True
  assert is_strict_confirmation("je confirme") is True

  # German
  assert is_strict_confirmation("bestätigen") is True
  assert is_strict_confirmation("ich bestätige") is True

  # Spanish
  assert is_strict_confirmation("confirmar") is True
  assert is_strict_confirmation("confirmo") is True

  # Rejections
  assert is_strict_confirmation("oui") is False
  assert is_strict_confirmation("ja") is False
  assert is_strict_confirmation("sí") is False
  assert is_strict_confirmation("si") is False
  assert is_strict_confirmation("yes") is False
  assert is_strict_confirmation("d'accord") is False


@pytest.mark.asyncio
async def test_yoruba_transfer_and_balance(service):
  # Yoruba transfer
  res = await service.interpret_utterance("Ẹ jọọ ẹ fi 5k ranṣẹ si David", MANIFEST)
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "David"
  assert res.params["amount_minor"] == 500_000
  assert "amount_kobo" not in res.params
  assert res.language == "yo"
  assert "Firanṣẹ" in res.spoken_prompt

  # Yoruba balance
  res_bal = await service.interpret_utterance("Kí ni balance mi", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "yo"


@pytest.mark.asyncio
async def test_igbo_transfer_and_balance(service):
  res = await service.interpret_utterance("Biko ziga 10k nye Amaka", MANIFEST)
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "Amaka"
  assert res.params["amount_minor"] == 1_000_000
  assert "amount_kobo" not in res.params
  assert res.language == "ig"
  assert "Ziga" in res.spoken_prompt

  # Igbo balance
  res_bal = await service.interpret_utterance("Kedu balance m", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "ig"


@pytest.mark.asyncio
async def test_hausa_transfer_and_balance(service):
  res = await service.interpret_utterance(
    "Don Allah tura 20k zuwa ga Chinedu", MANIFEST
  )
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "Chinedu"
  assert res.params["amount_minor"] == 2_000_000
  assert "amount_kobo" not in res.params
  assert res.language == "ha"
  assert "Tura" in res.spoken_prompt

  # Hausa balance
  res_bal = await service.interpret_utterance("Nawa ne kudi na", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "ha"


@pytest.mark.asyncio
async def test_french_transfer_and_balance(service):
  res = await service.interpret_utterance(
    "S'il vous plaît envoyez 5k à David", MANIFEST
  )
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "David"
  assert res.params["amount_minor"] == 500_000
  assert "amount_kobo" not in res.params
  assert res.language == "fr"
  assert "Transférer" in res.spoken_prompt

  # French balance
  res_bal = await service.interpret_utterance("Quel est mon solde", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "fr"


@pytest.mark.asyncio
async def test_german_transfer_and_balance(service):
  res = await service.interpret_utterance("Bitte überweise 5k an David", MANIFEST)
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "David"
  assert res.params["amount_minor"] == 500_000
  assert "amount_kobo" not in res.params
  assert res.language == "de"
  assert "Überweise" in res.spoken_prompt

  # German balance
  res_bal = await service.interpret_utterance("Wie ist mein Kontostand", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "de"


@pytest.mark.asyncio
async def test_spanish_transfer_and_balance(service):
  res = await service.interpret_utterance("Por favor envía 5k a David", MANIFEST)
  assert res.type == "action"
  assert res.intent == "transfer_money"
  assert res.params["recipient"] == "David"
  assert res.params["amount_minor"] == 500_000
  assert "amount_kobo" not in res.params
  assert res.language == "es"
  assert "Transferir" in res.spoken_prompt

  # Spanish balance
  res_bal = await service.interpret_utterance("Cuál es mi saldo", MANIFEST)
  assert res_bal.type == "action"
  assert res_bal.intent == "get_balance"
  assert res_bal.language == "es"
