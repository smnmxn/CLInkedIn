import json

from clinkedin.connections import format_json, format_table


SAMPLE = [
    {"name": "Ada Lovelace", "jobtitle": "Analyst at Analytical Engine Co", "location": "London"},
    {"name": "Grace Hopper", "jobtitle": "Rear Admiral", "location": "Arlington, VA"},
    {"name": "No Headline", "jobtitle": "", "location": ""},
]


def test_format_table_renders_rows():
    out = format_table(SAMPLE)
    lines = out.splitlines()
    assert lines[0] == "Ada Lovelace · Analyst at Analytical Engine Co · London"
    assert lines[1] == "Grace Hopper · Rear Admiral · Arlington, VA"
    assert lines[2] == "No Headline"


def test_format_table_empty():
    assert format_table([]) == "No connections found."


def test_format_json_roundtrip():
    out = format_json(SAMPLE)
    assert json.loads(out) == SAMPLE
