"""Thin wrapper around the OpenAI SDK so the loop stays readable.

The SDK talks to any OpenAI-compatible endpoint, so the provider is env-driven.
Defaults target Groq (fast, generous free tier, good tool calling on Llama 3.3);
point LLM_BASE_URL / LLM_MODEL elsewhere for OpenAI, Together, a local Ollama, etc.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# gpt-oss-120b is an open-weight MoE model, fast on Groq and strong at tool
# calling. Override with LLM_MODEL ("openai/gpt-oss-20b" for speed, "qwen/qwen3.8-27b",
# or a GPT / local model when pointing LLM_BASE_URL elsewhere). Run
# `curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $LLM_API_KEY"`
# to see what your account can use.
MODEL = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")

# OpenAI-compatible base URL. Groq by default; set it blank to fall back to the
# SDK's own default (api.openai.com) if you'd rather use OpenAI.
BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")


def build_client() -> OpenAI:
    # LLM_API_KEY is the provider-agnostic name. GROQ_API_KEY / OPENAI_API_KEY
    # are accepted only when BASE_URL actually points at that provider, so a
    # stray OPENAI_API_KEY in the shell can't get sent to Groq (401).
    api_key = os.environ.get("LLM_API_KEY")
    if not api_key and "groq.com" in BASE_URL:
        api_key = os.environ.get("GROQ_API_KEY")
    if not api_key and ("openai.com" in BASE_URL or not BASE_URL):
        api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No API key found. Copy .env.example to .env and set LLM_API_KEY "
            "(a Groq key from https://console.groq.com/keys by default)."
        )
    return OpenAI(api_key=api_key, base_url=BASE_URL or None)
