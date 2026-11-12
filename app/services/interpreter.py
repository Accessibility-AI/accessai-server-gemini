import json
import re
import uuid
from typing import Any

from app.api.schemas import InterpretResponse
from app.config import settings
from app.parsers.amount_parser import parse_amount_to_kobo
from app.parsers.language import detect_language
from app.providers.base import BaseLLMProvider, sanitize_tool_name
from app.templates.speech import (
  build_action_confirmation_prompt,
  build_balance_prompt,
  build_clarification_prompt,
  build_transactions_prompt,
)

RE_10_PLUS_DIGITS = re.compile(r"\d{10,}")
RE_VALUE_ATTR = re.compile(r'"value"\s*:\s*"(?!\[value\])[^"]+"', re.IGNORECASE)

CONTROL_COMMANDS = {
  "cancel": "cancel",
  "stop": "stop",
  "repeat": "repeat",
  "next": "next",
  "previous": "previous",
  "back": "previous",
  "help": "help",
  "describe screen": "describe_screen",
  "describe_screen": "describe_screen",
  "what's on my screen": "describe_screen",
  "what is on screen": "describe_screen",
}


def _contains_10_plus_digits(obj: Any) -> bool:
  if isinstance(obj, str):
    # Allow filtered placeholders like [number removed]
    clean_str = re.sub(r"\[number removed\]", "", obj, flags=re.IGNORECASE)
    return bool(RE_10_PLUS_DIGITS.search(clean_str))
  if isinstance(obj, dict):
    return any(_contains_10_plus_digits(v) for v in obj.values())
  if isinstance(obj, list):
    return any(_contains_10_plus_digits(item) for item in obj)
  return False


def _has_element_value(obj: Any) -> bool:
  if isinstance(obj, dict):
    if "value" in obj and obj["value"] not in (None, ""):
      return True
    return any(_has_element_value(v) for v in obj.values())
  if isinstance(obj, list):
    return any(_has_element_value(item) for item in obj)
  return False


def validate_defence_in_depth(
  utterance: str,
  screen: Any = None,
  screen_descriptor: str | None = None,
  dialog_state: dict[str, Any] | None = None,
) -> tuple[bool, str]:
  """Validates v1.1 defence-in-depth rules:

  1. Rejects any 10+ digit runs in utterance, screen, screen descriptor, or dialog state.
  2. Rejects any screen element containing a value.
  """
  if _contains_10_plus_digits(utterance):
    return (
      False,
      "10+ digit run detected in utterance; potential PIN, password, OTP, or account number leak",
    )

  if screen_descriptor and _contains_10_plus_digits(screen_descriptor):
    return False, "10+ digit run detected in screen descriptor"

  if screen and _contains_10_plus_digits(screen):
    return False, "10+ digit run detected in screen payload"

  if dialog_state and _contains_10_plus_digits(dialog_state):
    return False, "10+ digit run detected in dialog state"

  if screen and _has_element_value(screen):
    return (
      False,
      "Screen element contains a value; screen values must be filtered on device",
    )

  if screen_descriptor:
    try:
      parsed = json.loads(screen_descriptor)
      if _has_element_value(parsed):
        return (
          False,
          "Screen element contains a value; screen values must be filtered on device",
        )
    except (json.JSONDecodeError, ValueError, TypeError):
      if RE_VALUE_ATTR.search(screen_descriptor):
        return (
          False,
          "Screen element contains a value; screen values must be filtered on device",
        )

  return True, ""


_SENSITIVE_DIALOG_KEYS = {
  "balance",
  "beneficiaries",
  "account_number",
  "pin",
  "bvn",
  "password",
  "last_system_message",
  "value",
}


def _sanitize_dialog_state(dialog_state: Any) -> dict[str, Any] | list[Any] | None:
  """Strips sensitive fields from dialog state regardless of whether it is a dict or list."""
  if isinstance(dialog_state, dict):
    return {k: v for k, v in dialog_state.items() if k not in _SENSITIVE_DIALOG_KEYS}
  if isinstance(dialog_state, list):
    return [
      {k: v for k, v in item.items() if k not in _SENSITIVE_DIALOG_KEYS}
      if isinstance(item, dict)
      else item
      for item in dialog_state
    ]
  return None


# Names used before manifests carried an explicit `amount_minor` type
LEGACY_AMOUNT_NAMES = ("amount_raw", "amount_numeric_estimate", "kobo", "sum")
RECIPIENT_NAMES = ("recipient", "beneficiary")


class ParamRoles:
  """Which declared manifest params hold the amount and the recipient for an action."""

  def __init__(self, action: dict[str, Any]):
    manifest_params = action.get("params")
    if not manifest_params and isinstance(action.get("parameters"), dict):
      props = action["parameters"].get("properties", {})
      req_set = set(action["parameters"].get("required") or [])
      manifest_params = [
        {"name": k, "type": v.get("type", "string"), "required": k in req_set}
        for k, v in props.items()
      ]

    self.declared: dict[str, dict[str, Any]] = {}
    self.required: list[str] = []
    if isinstance(manifest_params, list):
      for p in manifest_params:
        if isinstance(p, dict) and p.get("name"):
          self.declared[p["name"]] = p
          if p.get("required"):
            self.required.append(p["name"])

    # The manifest type is authoritative; fall back to name matching only for
    # legacy manifests that declare no `amount_minor` param.
    typed = [
      n
      for n, p in self.declared.items()
      if str(p.get("type", "")).lower() == "amount_minor"
    ]
    self.amount: list[str] = typed or [
      n
      for n in self.declared
      if "amount" in n.lower() or n.lower() in LEGACY_AMOUNT_NAMES
    ]

    # Prefer an exact `recipient`/`beneficiary` name over a substring match like
    # `recipient_bank`; the first match wins.
    exact = [n for n in self.declared if n.lower() in RECIPIENT_NAMES]
    partial = [
      n
      for n in self.declared
      if n not in self.amount and any(r in n.lower() for r in RECIPIENT_NAMES)
    ]
    self.recipient: str | None = (exact or partial or [None])[0]

  def is_money_or_recipient(self, name: str) -> bool:
    p_lower = name.lower()
    return (
      name in self.amount
      or name == self.recipient
      or "amount" in p_lower
      or p_lower in LEGACY_AMOUNT_NAMES
      or any(r in p_lower for r in RECIPIENT_NAMES)
    )

  def amount_values(
    self, amount_kobo: int, raw_model_val: Any = None, utterance: str = ""
  ) -> dict[str, Any]:
    """Grounded amount written into each declared amount param, in its declared type."""
    raw_grounded = bool(
      raw_model_val and str(raw_model_val).lower() in utterance.lower()
    )
    out: dict[str, Any] = {}
    for p_name in self.amount:
      p_type = str(self.declared[p_name].get("type", "")).lower()
      if p_name == "amount_raw" or p_type == "string":
        out[p_name] = str(raw_model_val) if raw_grounded else f"{amount_kobo // 100}"
      elif p_name == "amount_numeric_estimate" or p_type in ("number", "float"):
        out[p_name] = float(amount_kobo / 100)
      else:
        out[p_name] = amount_kobo
    return out


class InterpreterService:
  def __init__(self, provider: BaseLLMProvider):
    self.provider = provider

  def _build_response(
    self,
    outcome: str,
    confidence: float,
    action_id: str | None = None,
    params: dict[str, Any] | None = None,
    missing_params: list[str] | None = None,
    control: str | None = None,
    message: str | None = None,
    spoken_prompt: str | None = None,
    clarification_reason: str | None = None,
    language: str = "en",
  ) -> InterpretResponse:
    provider_name = settings.LLM_PROVIDER
    region = settings.LLM_REGION
    notice = (
      f"Processed by {provider_name} ({region}) pursuant to "
      f"Nigeria Data Protection Act 2023 (NDPA) cross-border guidelines."
    )
    req_id = f"r_{uuid.uuid4().hex[:12]}"
    safe_params = params or {}
    safe_missing = missing_params or []
    prompt = spoken_prompt or message or ""

    # Map outcome to legacy type: if there are missing params, legacy clients see "clarification",
    # while v1.1 contract sees outcome="action" with missing_params populated.
    legacy_type = (
      "clarification"
      if safe_missing
      else ("action" if outcome == "action" else "clarification")
    )

    return InterpretResponse(
      request_id=req_id,
      outcome=outcome,
      action_id=action_id,
      params=safe_params,
      missing_params=safe_missing,
      control=control,
      message=message,
      confidence=confidence,
      type=legacy_type,
      intent=action_id,
      spoken_prompt=prompt,
      clarification_reason=clarification_reason,
      language=language,
      provider=provider_name,
      processing_region=region,
      data_processing_notice=notice,
    )

  def _build_action_outcome(
    self,
    action_id: str,
    roles: ParamRoles,
    params: dict[str, Any],
    amount_kobo: int | None,
    confidence: float,
    lang: str,
  ) -> InterpretResponse:
    """`action` response with only declared params; lists `missing_params` if required ones are unfilled.

    `amount_kobo` is the grounded amount, passed separately so prompts never depend
    on which name the manifest gave the amount param.
    """
    params = {k: v for k, v in params.items() if k in roles.declared}
    recipient = params.get(roles.recipient) if roles.recipient else None
    missing_params = [p for p in roles.required if p not in params]

    if missing_params:
      missing_recip = roles.recipient in missing_params
      missing_amt = any(p in missing_params for p in roles.amount)
      if missing_recip and missing_amt:
        c_reason = "missing_amount_and_recipient"
        prompt = build_clarification_prompt(c_reason, lang=lang)
      elif missing_recip:
        c_reason = "missing_recipient"
        prompt = build_clarification_prompt(c_reason, lang=lang)
      elif missing_amt:
        c_reason = "missing_amount"
        prompt = build_clarification_prompt(
          "missing_amount" if recipient else "missing_payment_amount",
          recipient=recipient,
          lang=lang,
        )
      else:
        prompt = f"Please specify {', '.join(missing_params)}."
        c_reason = f"missing_{'_and_'.join(missing_params)}"

      return self._build_response(
        outcome="action",
        action_id=action_id,
        params=params,
        missing_params=missing_params,
        confidence=confidence,
        message=prompt,
        spoken_prompt=prompt,
        clarification_reason=c_reason,
        language=lang,
      )

    spoken_text = build_action_confirmation_prompt(
      action_id, amount_kobo or 0, recipient, lang=lang
    )
    return self._build_response(
      outcome="action",
      action_id=action_id,
      params=params,
      missing_params=[],
      confidence=confidence,
      message=None,
      spoken_prompt=spoken_text,
      language=lang,
    )

  async def interpret_utterance(
    self,
    utterance: str,
    manifest: list[dict[str, Any]],
    screen_descriptor: str | None = None,
    screen: Any = None,
    dialog_state: dict[str, Any] | list[Any] | None = None,
    locale: str | None = None,
  ) -> InterpretResponse:
    """Interprets a user utterance against the allowed actions list across 8 languages.

    Conforms strictly to SDK 0.2.0 / Contract v1.1.
    Never guesses on money or recipients.
    """
    # Validate defence-in-depth rules
    is_valid, reason = validate_defence_in_depth(
      utterance=utterance,
      screen=screen,
      screen_descriptor=screen_descriptor,
      dialog_state=dialog_state,
    )
    if not is_valid:
      raise ValueError(reason)

    clean_text = utterance.strip().lower()

    # Check for control commands (cancel, stop, repeat, next, previous, help, describe_screen)
    # Note: Do not return "confirm" as a control. Confirmation never goes through the model.
    if clean_text in CONTROL_COMMANDS:
      ctrl = CONTROL_COMMANDS[clean_text]
      return self._build_response(
        outcome="control",
        confidence=1.0,
        control=ctrl,
        message=f"Command {ctrl} received.",
      )

    # Detect utterance language with optional client locale hint
    lang = detect_language(utterance, locale=locale)

    # Build action maps
    actions_by_id = {}
    actions_by_name = {}
    # Providers expose actions under sanitized tool names ("send money" -> "send_money")
    actions_by_tool_name = {}
    for action in manifest:
      aid = action.get("id") or action.get("name")
      aname = action.get("name") or aid
      if aid:
        actions_by_id[aid] = action
        actions_by_tool_name.setdefault(sanitize_tool_name(str(aid)), action)
      if aname:
        actions_by_name[aname] = action

    # Handle dialog continuation if pending_action_id is set
    if isinstance(dialog_state, dict) and dialog_state.get("pending_action_id"):
      pending_id = dialog_state["pending_action_id"]
      pending_action = (
        actions_by_id.get(pending_id)
        or actions_by_name.get(pending_id)
        or actions_by_tool_name.get(pending_id)
      )
      if pending_action:
        roles = ParamRoles(pending_action)
        canonical_pending_id = (
          pending_action.get("id") or pending_action.get("name") or pending_id
        )
        # Only short-circuit when the pending action takes an amount and this
        # utterance supplies one; otherwise let the model handle the turn.
        parsed_kobo = parse_amount_to_kobo(utterance) if roles.amount else None
        if parsed_kobo is not None and parsed_kobo > 0:
          raw_filled = dialog_state.get("filled_params")
          filled = raw_filled if isinstance(raw_filled, dict) else {}
          # Keep only declared, non-amount params from earlier turns; the amount
          # always comes from this utterance.
          merged_params = {
            k: v
            for k, v in filled.items()
            if k in roles.declared and k not in roles.amount
          }
          merged_params.update(roles.amount_values(parsed_kobo))
          return self._build_action_outcome(
            action_id=canonical_pending_id,
            roles=roles,
            params=merged_params,
            amount_kobo=parsed_kobo,
            confidence=0.95,
            lang=lang,
          )

    # Sanitize dialog context: Never pass balances, beneficiary lists, passwords, or system messages
    sanitized_dialog_state = _sanitize_dialog_state(dialog_state)

    # Resolve screen descriptor
    desc = screen_descriptor
    if not desc and screen:
      if isinstance(screen, str):
        desc = screen
      else:
        desc = json.dumps(screen)

    llm_result = await self.provider.interpret(
      utterance=utterance,
      manifest=manifest,
      screen_descriptor=desc,
      dialog_state=sanitized_dialog_state,
    )

    # 1. Check confidence threshold
    if llm_result.confidence < settings.CONFIDENCE_THRESHOLD:
      msg = build_clarification_prompt("low_confidence", lang=lang)
      return self._build_response(
        outcome="clarify",
        confidence=llm_result.confidence,
        message=msg,
        spoken_prompt=msg,
        clarification_reason="low_confidence",
        language=lang,
      )

    # 2. Check tool call count
    if len(llm_result.tool_calls) == 0:
      msg = build_clarification_prompt("unknown_tool", lang=lang)
      return self._build_response(
        outcome="unsupported",
        confidence=llm_result.confidence,
        message=msg,
        spoken_prompt=msg,
        clarification_reason="unknown_tool",
        language=lang,
      )

    if len(llm_result.tool_calls) > 1:
      msg = build_clarification_prompt("multiple_tools", lang=lang)
      return self._build_response(
        outcome="clarify",
        confidence=llm_result.confidence,
        message=msg,
        spoken_prompt=msg,
        clarification_reason="multiple_tools",
        language=lang,
      )

    tool_call = llm_result.tool_calls[0]
    tool_name = tool_call.name
    tool_args = tool_call.arguments or {}

    # 3. Check if tool is allowed in actions
    action_match = (
      actions_by_id.get(tool_name)
      or actions_by_name.get(tool_name)
      or actions_by_tool_name.get(tool_name)
    )
    if not action_match:
      msg = build_clarification_prompt("unknown_tool", lang=lang)
      return self._build_response(
        outcome="unsupported",
        confidence=llm_result.confidence,
        message=msg,
        spoken_prompt=msg,
        clarification_reason="unknown_tool",
        language=lang,
      )

    canonical_action_id = (
      action_match.get("id") or action_match.get("name") or tool_name
    )

    # 4. Handle get_balance
    if canonical_action_id in ("get_balance", "check_balance", "balance"):
      spoken_text = build_balance_prompt(lang=lang)
      return self._build_response(
        outcome="action",
        action_id=canonical_action_id,
        params={},
        missing_params=[],
        confidence=llm_result.confidence,
        message=None,
        spoken_prompt=spoken_text,
        language=lang,
      )

    # 5. Handle get_transactions / history
    if canonical_action_id in ("get_transactions", "get_transaction_history"):
      spoken_text = build_transactions_prompt(lang=lang)
      return self._build_response(
        outcome="action",
        action_id=canonical_action_id,
        params={},
        missing_params=[],
        confidence=llm_result.confidence,
        message=None,
        spoken_prompt=spoken_text,
        language=lang,
      )

    # 6. Manifest-driven parameter extraction and grounding for all actions
    # (Fixes H2, bill payments, and partial requests). Only declared params are returned.
    roles = ParamRoles(action_match)
    verified_params: dict[str, Any] = {}
    amount_kobo: int | None = None

    # 6a. Ground and parse amount (H2: Never trust hallucinated amounts)
    if roles.amount:
      # Deterministic parse strictly from user utterance
      parsed_from_utt = parse_amount_to_kobo(utterance)
      raw_model_val = tool_args.get("amount_raw")
      # Only consider amount_raw if it is actually substring in user utterance
      if raw_model_val and str(raw_model_val).lower() in utterance.lower():
        parsed_from_raw = parse_amount_to_kobo(str(raw_model_val))
        amount_kobo = parsed_from_raw or parsed_from_utt
      else:
        amount_kobo = parsed_from_utt

      if amount_kobo is not None and amount_kobo > 0:
        verified_params.update(
          roles.amount_values(amount_kobo, raw_model_val, utterance)
        )

    # 6b. Ground recipient in utterance (H2: Never trust invented recipients)
    if roles.recipient:
      cand_recip = (
        tool_args.get(roles.recipient)
        or tool_args.get("recipient")
        or tool_args.get("beneficiary")
      )
      if cand_recip:
        cand_str = str(cand_recip).strip()
        # Grounding check: recipient MUST be present in user utterance and not a placeholder!
        if (
          cand_str.lower()
          not in [
            "someone",
            "person",
            "recipient",
            "anybody",
            "user",
            "none",
            "quelqu'un",
            "jemand",
            "alguien",
          ]
          and cand_str.lower() in utterance.lower()
        ):
          verified_params[roles.recipient] = cand_str

    # 6c. Ground any other declared params from tool_args
    for p_name in roles.declared:
      # Never copy ungrounded amount or recipient params from tool_args (H2 fix)
      if p_name in verified_params or roles.is_money_or_recipient(p_name):
        continue
      val = tool_args.get(p_name)
      if val is not None and (
        not isinstance(val, str)
        or (isinstance(val, str) and val.lower() in utterance.lower())
      ):
        verified_params[p_name] = val

    # 6d. Missing required params keep outcome "action" with missing_params
    # (Contract v1.1 / SDK DialogEngine standard)
    return self._build_action_outcome(
      action_id=canonical_action_id,
      roles=roles,
      params=verified_params,
      amount_kobo=amount_kobo,
      confidence=llm_result.confidence,
      lang=lang,
    )
