from app.parsers.amount_parser import (
  parse_amount_to_kobo,
  verify_amount_agreement,
  words_to_number,
)


def test_words_to_number():
  assert words_to_number("five") == 5
  assert words_to_number("twenty") == 20
  assert words_to_number("twenty five") == 25
  assert words_to_number("one hundred") == 100
  assert words_to_number("one hundred and fifty") == 150
  assert words_to_number("five thousand") == 5000
  assert words_to_number("twenty thousand five hundred") == 20500
  assert words_to_number("half a million") == 500000


def test_parse_shorthand_amounts():
  # 1 Naira = 100 Kobo
  assert parse_amount_to_kobo("5k") == 500_000
  assert parse_amount_to_kobo("5 K") == 500_000
  assert parse_amount_to_kobo("1.5k") == 150_000
  assert parse_amount_to_kobo("20k") == 2_000_000
  assert parse_amount_to_kobo("5 grand") == 500_000
  assert parse_amount_to_kobo("1m") == 100_000_000
  assert parse_amount_to_kobo("2m naira") == 200_000_000


def test_parse_comma_thousands_inside_utterance():
  """Regression: '5,000' without naira/₦ used to parse as ₦5 (500 kobo)."""
  assert parse_amount_to_kobo("send 5,000 to David") == 500_000
  assert parse_amount_to_kobo("transfer 1,500 to Amaka") == 150_000
  assert parse_amount_to_kobo("give Amaka 1,500") == 150_000
  assert parse_amount_to_kobo("send 15,000 to David") == 1_500_000
  assert parse_amount_to_kobo("send 250,000 to David") == 25_000_000
  assert parse_amount_to_kobo("send 1,250,000 naira") == 125_000_000
  assert parse_amount_to_kobo("send ₦1,500 to Amaka") == 150_000
  assert parse_amount_to_kobo("send N1,500 to Amaka") == 150_000


def test_parse_currency_symbols():
  assert parse_amount_to_kobo("N5000") == 500_000
  assert parse_amount_to_kobo("₦5000") == 500_000
  assert parse_amount_to_kobo("₦5,000") == 500_000
  assert parse_amount_to_kobo("#5000") == 500_000
  assert parse_amount_to_kobo("NGN 5,000.50") == 500_050
  assert parse_amount_to_kobo("5000 naira") == 500_000


def test_parse_word_amounts():
  assert parse_amount_to_kobo("five thousand naira") == 500_000
  assert parse_amount_to_kobo("twenty thousand naira") == 2_000_000
  assert parse_amount_to_kobo("twenty thousand five hundred naira") == 2_050_000
  assert parse_amount_to_kobo("fifty naira") == 5_000
  assert parse_amount_to_kobo("one hundred naira") == 10_000


def test_parse_kobo_and_compound():
  assert parse_amount_to_kobo("50 kobo") == 50
  assert parse_amount_to_kobo("fifty kobo") == 50
  assert parse_amount_to_kobo("500 naira 50 kobo") == 50_050
  assert parse_amount_to_kobo("five hundred naira and fifty kobo") == 50_050


def test_parse_invalid_amounts():
  assert parse_amount_to_kobo("") is None
  assert parse_amount_to_kobo("some money") is None
  assert parse_amount_to_kobo("naira") is None
  assert parse_amount_to_kobo("david") is None


def test_amount_agreement_verification():
  # 500,000 kobo = 5,000 Naira
  assert verify_amount_agreement(500_000, 5000.0) is True
  assert verify_amount_agreement(500_000, 5000) is True
  assert verify_amount_agreement(500_000, None) is True
  # Disagreements must return False
  assert verify_amount_agreement(500_000, 50.0) is False
  assert verify_amount_agreement(500_000, 500.0) is False
  assert verify_amount_agreement(500_000, 50000.0) is False
