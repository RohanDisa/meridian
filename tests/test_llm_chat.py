from meridian.chat import (
    SYSTEM_PROMPT,
    answer_question,
    corpus_roster,
    _compact_tool_result,
    _history_messages,
    _provider_tool_choice,
    _speak_notes,
    _strip_markdown_emphasis,
    _system_prompt,
    _wants_opened_visual,
)
from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import link_drawings_to_bom
from meridian.paths import BOM_CSV, DRAWINGS_DIR, MANIFEST_JSON
from meridian.reconstruct import build_all, register_reconstructions


def _ready(db_path, tmp_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    build_all(tmp_path)
    register_reconstructions(conn, tmp_path)
    return conn


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Call:
    def __init__(self, ident, name, arguments):
        self.id = ident
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def test_llm_uses_tools_then_explains_a_misspelled_part(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    turns = []

    def fake_complete(messages, tools, tool_choice="auto"):
        turns.append(messages[-1])
        if len(turns) == 1:
            return _Message(
                tool_calls=[_Call("c1", "find_part", '{"query":"left side box"}')]
            )
        if len(turns) == 2:
            return _Message(
                content=(
                    "Left Side Box is in the Box family. "
                    "It interfaces with Print Platform, Top Side, Front Side Box, and Back Box."
                )
            )
        return _Message(content="done")

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    result = answer_question(conn, "tell me abt the left side boxx", llm=True)
    assert "Left Side Box" in result["text"]
    assert any(item["tool"] == "find_part" for item in result["tool_results"])
    assert result["uncertainty"] != "unsupported"


def test_llm_can_open_a_drawing_without_3d(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    turns = []

    def fake_complete(messages, tools, tool_choice="auto"):
        turns.append(True)
        if len(turns) == 1:
            return _Message(
                tool_calls=[_Call("c1", "find_part", '{"query":"print platform"}')]
            )
        if len(turns) == 2:
            found = messages[-1]["content"]
            drawing_id = "D-003" if "D-003" in found else "D-003"
            return _Message(
                tool_calls=[
                    _Call("c2", "open_visual", '{"drawing_id":"%s"}' % drawing_id)
                ]
            )
        return _Message(
            content="Opening the Print Platform drawing. There is no 3D reconstruction for it."
        )

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    result = answer_question(conn, "open the print platfrm drawing", llm=True)
    assert result["attachments"]
    assert result["attachments"][0]["drawing_id"]
    assert not result["attachments"][0].get("reconstruction")


def test_planner_path_still_used_when_llm_disabled(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "What does the Front Door interface with?", llm=False)
    assert "Front Side Box" in result["text"]


def test_missing_api_key_does_not_answer(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("meridian.chat.load_dotenv", lambda *args, **kwargs: None)
    result = answer_question(conn, "Tell me about the build plate")
    assert result["uncertainty"] == "no_api_key"
    assert result["attachments"] == []
    assert "No API key" in result["text"]


def test_groq_key_enables_llm_outside_pytest(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MERIDIAN_CHAT_PROVIDER", "groq")
    monkeypatch.setenv("MERIDIAN_GROQ_MODEL", "openai/gpt-oss-120b")
    from meridian.chat import _chat_client_config, _use_llm

    assert _use_llm(None) is True
    cfg = _chat_client_config("groq")
    assert cfg["base_url"] == "https://api.groq.com/openai/v1"
    assert cfg["model"] == "openai/gpt-oss-120b"


def test_provider_order_is_groq_then_anthropic_then_openai(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MERIDIAN_CHAT_PROVIDER", "anthropic")
    monkeypatch.setenv("MERIDIAN_ANTHROPIC_MODEL", "claude-opus-5-5")
    from meridian.chat import _available_providers, _chat_client_config, _to_anthropic_messages, _use_llm

    assert _use_llm(None) is True
    assert _available_providers() == ["groq", "anthropic", "openai"]
    cfg = _chat_client_config("anthropic")
    assert cfg["provider"] == "anthropic"
    assert cfg["model"] == "claude-opus-5-5"
    system, converted = _to_anthropic_messages(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "left side boxx"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "find_part", "arguments": '{"query":"left side box"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "find_part", "content": "{}"},
        ]
    )
    assert system == "rules"
    assert converted[0]["role"] == "user"
    assert converted[1]["content"][0]["type"] == "tool_use"
    assert converted[2]["role"] == "user"
    assert converted[2]["content"][0]["type"] == "tool_result"


def test_system_prompt_includes_roster_and_bans_bold(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    roster = corpus_roster(conn)
    prompt = _system_prompt(conn)
    assert "GLAMS" in roster
    assert "Print Platform" in roster
    assert "D-026" in roster
    assert roster in prompt
    assert "Never use markdown bold" in prompt
    assert "Do not use the characters ** anywhere" in prompt
    assert "Never say identity_conflict" in prompt
    assert "Bad: The system flags an identity_conflict" in prompt
    assert "GLAMS is a BOM name, not clamps" in prompt


def test_llm_system_message_carries_the_roster(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    seen = []

    def fake_complete(messages, tools, tool_choice="auto"):
        seen.append(messages[0]["content"])
        return _Message(content="Print Platform has no drawing.")

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    answer_question(conn, "anything interface with the print platfrm", llm=True)
    assert seen
    assert "GLAMS" in seen[0]
    assert "Print Platform" in seen[0]
    assert "Never use markdown bold" in seen[0]
    assert "Do not use the characters ** anywhere" in seen[0]


def test_recent_turns_are_sent_before_the_new_question(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    seen = []

    def fake_complete(messages, tools, tool_choice="auto"):
        seen.append(messages)
        return _Message(content="Recoater head is now EN-AW 5005.")

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    answer_question(
        conn,
        "What material is the recoater plate?",
        llm=True,
        history=[
            {"role": "user", "text": "Correct the Recoater head material to EN-AW 5005"},
            {"role": "assistant", "text": "A correction has been proposed."},
        ],
    )
    roles = [item["role"] for item in seen[0]]
    assert roles[:4] == ["system", "user", "assistant", "user"]
    assert seen[0][1]["content"] == "Correct the Recoater head material to EN-AW 5005"
    assert seen[0][-1]["content"] == "What material is the recoater plate?"


def test_compact_tool_result_adds_plain_speak_notes_instead_of_flags():
    result = {
        "identity_conflict": True,
        "material_relation": "conflict",
        "conflict": True,
        "bom": {"id": 27, "name": "Recoater head", "material": "Aluminium"},
        "drawing": {"id": "D-013", "material": "3.3315 (EN-AW"},
        "linked_bom": [
            {"name": "Recoater head", "csv_line": 28, "link_status": "conflict"},
            {"name": "Recoater stage plate", "csv_line": 31, "link_status": "conflict"},
        ],
        "citations": [],
    }
    notes = _speak_notes(result)
    compact = _compact_tool_result(result)
    assert any("Recoater head on CSV line 28" in note for note in notes)
    assert any("cut off" in note for note in notes)
    assert compact["speak_notes"] == notes
    assert "identity_conflict" not in compact
    assert "material_relation" not in compact


def test_accepted_correction_is_spoken_as_the_current_material():
    notes = _speak_notes(
        {
            "conflict": True,
            "material_relation": "conflict",
            "current": {"material": "AISI 316 Stainless Steel"},
            "bom": {"id": 47, "name": "Build Plate", "material": "Stainless steel,Steel"},
            "drawing": {"id": "D-026", "material": "AISI 316 Stainless Steel Sheet (SS)"},
            "linked_bom": [],
            "citations": [],
        }
    )
    text = " ".join(notes)
    assert "AISI 316 Stainless Steel" in text
    assert "first sentence" in text
    assert "undecided" not in text
    assert "pending" in text
    assert "stored strings disagree" not in text


def test_history_note_lists_a_correction_accepted_since_an_old_reply(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    from meridian.tools import knowledge_version, propose_correction, review_correction

    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title block",
    )
    review_correction(conn, proposal["id"], "accepted", reviewer="test")
    current = knowledge_version(conn)
    stale = _history_messages(
        [{"role": "assistant", "text": "The material is Aluminium.", "knowledge_version": current - 1}],
        conn,
    )
    note = " ".join(item["content"] for item in stale)
    assert "Recoater head" in note
    assert "material" in note
    assert "Aluminium" in note
    assert "EN-AW 5005" in note
    assert "outdated" in note
    fresh = _history_messages(
        [{"role": "assistant", "text": "The material is EN-AW 5005.", "knowledge_version": current}],
        conn,
    )
    assert all("Knowledge changed" not in item["content"] for item in fresh)


def test_style_example_uses_no_dataset_part_names(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    example = SYSTEM_PROMPT.split("Example of tone", 1)[1]
    names = [
        row["name"]
        for row in conn.execute("SELECT name FROM bom_rows WHERE name IS NOT NULL")
    ]
    for name in names:
        assert name not in example
    for number in range(1, 31):
        assert f"D-{number:03d}" not in example
    assert "Sample Bracket" in example
    assert "D-900" in example


def test_first_step_requires_a_tool_and_later_steps_do_not(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    choices = []

    def fake_complete(messages, tools, tool_choice="auto"):
        choices.append(tool_choice)
        if len(choices) == 1:
            return _Message(tool_calls=[_Call("c1", "find_part", '{"query":"glams"}')])
        return _Message(content="GLAMS is a BOM part.")

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    answer_question(conn, "what is glams", llm=True)
    assert choices == ["required", "auto"]
    assert _provider_tool_choice("groq", "required") == "required"
    assert _provider_tool_choice("openai", "required") == "required"
    assert _provider_tool_choice("anthropic", "required") == {"type": "any"}
    assert _provider_tool_choice("anthropic", "auto") == {"type": "auto"}


def test_strips_markdown_bold_markers():
    assert (
        _strip_markdown_emphasis("The BOM says **Aluminium** and flags **identity conflict**.")
        == "The BOM says Aluminium and flags identity conflict."
    )


def test_does_not_auto_open_drawing_on_is_there_a_drawing_question():
    assert _wants_opened_visual("open the CAD for the build plate")
    assert _wants_opened_visual("show me the silicon wipr sheet")
    assert not _wants_opened_visual(
        "how does the left side box meet the print platfrm and is there a drawing for either"
    )
    assert not _wants_opened_visual("who mentions glams")


def test_failed_provider_falls_through_to_the_next_key(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("meridian.chat.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("MERIDIAN_CHAT_PROVIDER", "groq")
    seen = []

    def fake_complete(messages, tools, tool_choice="auto"):
        from meridian import chat as chat_mod

        seen.append(chat_mod._active_provider)
        if chat_mod._active_provider != "openai":
            raise RuntimeError("429 too many requests")
        return _Message(content="The Build Plate material is recorded as Stainless steel,Steel.")

    monkeypatch.setattr("meridian.chat.complete_chat", fake_complete)
    result = answer_question(conn, "What is the build plate made of?", llm=True)
    assert seen == ["groq", "anthropic", "openai"]
    assert "Stainless steel,Steel" in result["text"]
    assert result.get("uncertainty") != "model_unavailable"


def test_every_provider_failing_says_none_of_the_keys_worked(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr("meridian.chat.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def boom(messages, tools, tool_choice="auto"):
        raise RuntimeError("429 too many requests")

    monkeypatch.setattr("meridian.chat.complete_chat", boom)
    result = answer_question(conn, "What does the Front Door interface with?", llm=True)
    assert result["uncertainty"] == "model_unavailable"
    assert "None of the API keys worked" in result["text"]
    assert "Groq" in result["text"]
    assert "Anthropic" in result["text"]
    assert "rate limit" in result["text"]
    assert "Front Side Box" not in result["text"]


def test_llm_quota_error_does_not_use_the_keyword_checker(db_path, tmp_path, monkeypatch):
    conn = _ready(db_path, tmp_path)

    def boom(messages, tools, tool_choice="auto"):
        raise RuntimeError("You have no credits remaining. credit_balance_exhausted")

    monkeypatch.setattr("meridian.chat.complete_chat", boom)
    result = answer_question(conn, "What does the Front Door interface with?", llm=True)
    assert "no credits" in result["text"].lower()
    assert "Front Side Box" not in result["text"]
    assert result["uncertainty"] == "model_unavailable"
