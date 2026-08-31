"""LLM invocation abstraction with automatic GitHub Copilot API fallback.

Primary path: Claude CLI (`claude -p ...` with structured JSON output), using
the logged-in subscription (--safe-mode, NOT --bare).
Fallback path: GitHub Copilot chat completions API via httpx, authenticated
with `gh auth token`. Triggered on any Claude failure (non-zero exit, timeout,
parse error). Both paths return the same structured dict.

Ported near-verbatim from israel-news-digest/automation/llm_runner.py.
"""
import json
import logging
import subprocess
from pathlib import Path

import httpx

import config

log = logging.getLogger(__name__)


def _gh_token() -> str:
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh auth token failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _call_copilot(system_msg: str, user_msg: str, model: str) -> str:
    token = _gh_token()
    resp = httpx.post(
        f"{config.COPILOT_API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Copilot-Integration-Id": config.COPILOT_INTEGRATION_ID,
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _try_claude(
    prompt: str,
    schema: dict,
    model: str,
    max_budget: str,
    effort: str | None = None,
) -> dict:
    args = [
        "claude", "-p", prompt,
        "--model", model,
        "--safe-mode",
        "--allowedTools", "Read",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--max-budget-usd", max_budget,
    ]
    if effort:
        args += ["--effort", effort]
    result = subprocess.run(args, capture_output=True, text=True, timeout=240)
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr[:500]}")
    envelope = json.loads(result.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude reported an error: {envelope}")
    return envelope["structured_output"]


def run_search_with_schema(
    prompt: str,
    schema: dict,
    model: str,
    max_budget: str,
    effort: str | None = None,
) -> dict:
    """Runs a Claude CLI call with WebSearch + WebFetch allowed and a JSON
    schema on the output. No Copilot fallback (that path has no web access).
    Raises on failure — callers must degrade gracefully.
    """
    args = [
        "claude", "-p", prompt,
        "--model", model,
        "--safe-mode",
        "--allowedTools", "WebSearch", "WebFetch",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--max-budget-usd", max_budget,
    ]
    if effort:
        args += ["--effort", effort]
    result = subprocess.run(args, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"claude search exited {result.returncode}: {result.stderr[:500]}")
    envelope = json.loads(result.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude search reported an error: {envelope}")
    return envelope["structured_output"]


def _try_copilot(instructions: str, input_path: Path, schema: dict, fallback_model: str) -> dict:
    file_content = input_path.read_text(encoding="utf-8")
    schema_text = json.dumps(schema, ensure_ascii=False, indent=2)
    system_msg = (
        f"{instructions}\n\n"
        "IMPORTANT: Return ONLY a valid JSON object matching this exact schema. "
        "Do not include any explanation, markdown, or text before or after the JSON.\n\n"
        f"Required JSON schema:\n{schema_text}"
    )
    raw = _call_copilot(system_msg, file_content, fallback_model).strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()
    return json.loads(raw)


def run_with_schema(
    instructions: str,
    input_path: Path,
    schema: dict,
    claude_model: str,
    claude_max_budget: str,
    copilot_fallback_model: str,
    claude_effort: str | None = None,
) -> dict:
    """Returns the structured output dict. Tries Claude CLI first, then Copilot.

    Raises only if both backends fail — callers must degrade gracefully.
    """
    prompt = f"{instructions}\n\nRead this file: {input_path}"

    try:
        structured = _try_claude(prompt, schema, claude_model, claude_max_budget, claude_effort)
        log.info("llm_runner: used Claude CLI (%s)", claude_model)
        return structured
    except Exception as claude_exc:  # noqa: BLE001
        log.warning("llm_runner: Claude failed (%s), trying Copilot API fallback", claude_exc)

    structured = _try_copilot(instructions, input_path, schema, copilot_fallback_model)
    log.info("llm_runner: used Copilot API fallback (%s)", copilot_fallback_model)
    return structured
