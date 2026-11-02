import json

def generate_dataset():
    data = []
    
    # 1. Fully specified transfers (Pidgin, Nigerian English, standard) - 60 examples
    names = ["David", "Chinedu", "Amaka", "Sarah", "Mama", "Emeka", "Ngozi", "Femi", "Alhaji", "Bola", 
             "Tunde", "Blessing", "Ibrahim", "Chinwe", "Kelechi", "Folake", "Yusuf", "Ada", "Obi", "Zainab"]
    
    transfers_templates = [
        ("abeg send {amt} give {name}", "{amt}", "{name}"),
        ("send {amt} to {name}", "{amt}", "{name}"),
        ("transfer {amt} to {name}", "{amt}", "{name}"),
        ("oya wire {amt} to {name}", "{amt}", "{name}"),
        ("help me transfer {amt} give {name}", "{amt}", "{name}"),
        ("dash {amt} give {name}", "{amt}", "{name}"),
        ("pay {amt} to {name}", "{amt}", "{name}"),
        ("please send {amt} to {name}", "{amt}", "{name}"),
        ("abeg wire {amt} give {name}", "{amt}", "{name}"),
        ("transfer {amt} give {name}", "{amt}", "{name}")
    ]
    
    amounts = [
        ("5k", 500000),
        ("10k", 1000000),
        ("20 grand", 2000000),
        ("1.5k", 150000),
        ("N5000", 500000),
        ("₦5,000", 500000),
        ("500 naira", 50000),
        ("2m naira", 200000000),
        ("twenty thousand naira", 2000000),
        ("100k", 10000000),
        ("50k", 5000000),
        ("2k", 200000),
        ("15k", 1500000),
        ("2.5k", 250000),
        ("N10000", 1000000),
        ("1000 naira", 100000),
        ("thirty thousand naira", 3000000),
        ("half a million naira", 50000000),
        ("500 naira 50 kobo", 50050),
        ("5 grand", 500000)
    ]
    
    for i in range(60):
        tmpl, _, _ = transfers_templates[i % len(transfers_templates)]
        name = names[i % len(names)]
        raw_amt, kobo = amounts[i % len(amounts)]
        utterance = tmpl.format(amt=raw_amt, name=name)
        data.append({
            "utterance": utterance,
            "expected_type": "action",
            "expected_intent": "transfer_money",
            "expected_recipient": name,
            "expected_kobo": kobo,
            "category": "valid_transfer"
        })
        
    # 2. Balance inquiries (Pidgin, Nigerian English, standard) - 35 examples
    balance_phrases = [
        "wetin be my balance",
        "how much dey inside my account",
        "check my account balance for me",
        "what is my account balance",
        "check balance",
        "how much remain for my account",
        "view my balance",
        "how much I get left",
        "show my account balance",
        "wetin dey my balance",
        "abeg check balance",
        "show me my balance",
        "balance please",
        "tell me how much is in my account",
        "what is my current balance",
        "oya check my account",
        "how much remain",
        "abeg how much dey my account",
        "I want to see my balance",
        "can you check my balance",
        "what do I have in my account",
        "check my money for me",
        "how much balance do I have",
        "wetin remain for inside",
        "show account balance",
        "abeg wetin be my balance",
        "give me my balance",
        "how much is left in my account",
        "account balance inquiry",
        "please check my balance",
        "how much money dey there",
        "check account balance",
        "look my balance for me",
        "balance check",
        "how much I get"
    ]
    
    for phrase in balance_phrases:
        data.append({
            "utterance": phrase,
            "expected_type": "action",
            "expected_intent": "get_balance",
            "category": "valid_balance"
        })
        
    # 3. Missing recipient transfers (must clarify, NEVER guess recipient) - 20 examples
    missing_recipients = [
        "send 5k",
        "abeg transfer 10 grand",
        "wire twenty thousand naira",
        "oya pay 500 naira",
        "transfer 2m",
        "send 1.5k",
        "wire N5000",
        "help me send 50k",
        "abeg dash 2k",
        "pay 30k",
        "send 100k",
        "transfer 5 grand",
        "wire 15k",
        "please pay 2500 naira",
        "abeg send 500 naira",
        "transfer N20000",
        "wire half a million",
        "dash 10k",
        "send twenty thousand",
        "help me wire 5k"
    ]
    
    for phrase in missing_recipients:
        data.append({
            "utterance": phrase,
            "expected_type": "clarification",
            "expected_reason": "missing_recipient",
            "category": "missing_recipient"
        })
        
    # 4. Missing amount transfers (must clarify, NEVER guess amount) - 20 examples
    missing_amounts = [
        "send money to David",
        "abeg transfer to Chinedu",
        "wire some money to Amaka",
        "oya pay Sarah",
        "help me dash Mama",
        "transfer give Emeka",
        "send cash to Ngozi",
        "wire money to Femi",
        "pay Alhaji money",
        "abeg send money to Bola",
        "transfer money to Tunde",
        "send cash to Blessing",
        "wire money give Ibrahim",
        "abeg transfer to Chinwe",
        "pay Kelechi",
        "dash money give Folake",
        "send money to Yusuf",
        "wire to Ada",
        "transfer to Obi",
        "oya pay Zainab"
    ]
    
    for phrase in missing_amounts:
        data.append({
            "utterance": phrase,
            "expected_type": "clarification",
            "expected_reason": "missing_amount",
            "category": "missing_amount"
        })
        
    # 5. Out of scope / unsupported queries - 15 examples
    out_of_scopes = [
        "book a flight to Abuja",
        "what is the weather in Lagos today",
        "play some afrobeat music",
        "tell me a joke",
        "call customer care",
        "order pizza for me",
        "who won the football match yesterday",
        "open google maps",
        "what time is it in London",
        "set an alarm for 6am tomorrow",
        "buy groceries online",
        "what is the capital of Nigeria",
        "read my text messages",
        "how far my guy",
        "take a selfie"
    ]
    
    for phrase in out_of_scopes:
        data.append({
            "utterance": phrase,
            "expected_type": "clarification",
            "expected_reason": "unknown_tool",
            "category": "out_of_scope"
        })
        
    return data

if __name__ == "__main__":
    dataset = generate_dataset()
    print(f"Generated {len(dataset)} evaluation items.")
    with open("tests/evals/dataset.json", "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2)
