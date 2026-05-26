"""Minimal prompt template loader.

Extracts content between '## ===== PROMPT START =====' and '## ===== PROMPT END ====='
in a markdown template, then replaces {{VAR}} placeholders.
"""
from pathlib import Path


PROMPT_START = "## ===== PROMPT START ====="
PROMPT_END = "## ===== PROMPT END ====="


def load_prompt(template_path: str | Path, **vars) -> str:
    text = Path(template_path).read_text(encoding="utf-8")

    start_idx = text.index(PROMPT_START) + len(PROMPT_START)
    end_idx = text.index(PROMPT_END)
    body = text[start_idx:end_idx].strip()

    for key, value in vars.items():
        body = body.replace("{{" + key + "}}", str(value))

    return body
