"""Chat and extract model ids. README and /api/about read this module."""

CHAT_MODELS = {
    "groq": "openai/gpt-oss-120b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-opus-5-5",
}

ANTHROPIC_MODEL_FALLBACKS = (
    "claude-opus-5-5",
    "claude-sonnet-4-5",
    "claude-sonnet-4-20250514",
)

# Degraded drawings were read once in Cursor. No API vision model id was recorded.
VISION_MODEL = None
VISION_NOTE = (
    "Degraded pages were read once with Cursor vision during extract. "
    "No API vision model id was recorded. Runtime does not call vision."
)
