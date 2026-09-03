"""Thin wrapper around the OpenAI client so the loop stays readable."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# gpt-4o-mini is fast + cheap and handles tool calling well. Bump to gpt-4o
# for richer brand voice if desired.
MODEL = "gpt-4o-mini"


def build_client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return OpenAI(api_key=api_key)
