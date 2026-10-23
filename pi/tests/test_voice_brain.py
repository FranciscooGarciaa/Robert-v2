from robert.voice.brain import Turn, describe_state, parse_turn


def test_parse_valid_turn():
    t = parse_turn('{"heard": " avanza ", "reply": "Allá voy.", "command": "forward"}')
    assert t == Turn("avanza", "Allá voy.", "forward")


def test_unknown_command_becomes_none():
    assert parse_turn('{"heard": "explora", "reply": "Desde la app.", "command": "explore"}').command == "none"


def test_garbage_becomes_empty_turn():
    for bad in ("no es json", "[1, 2]", None, '{"command": 3}'):
        t = parse_turn(bad)
        assert t.command == "none" and t.heard == ""


def test_describe_state_uses_real_facts_only():
    state = {"esp32": {"connected": True}, "lidar": {"connected": True, "nearest_mm": 830},
             "nav": {"clearance": {"front": 0.62, "back": None}}, "motion": {"mode": "idle"}}
    text = describe_state(state)
    assert "0.8 m" in text and "delante: 0.6" in text and "detrás" not in text and "parado" in text
    assert describe_state(None) == "Estado del robot: desconocido."
