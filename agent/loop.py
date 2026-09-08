"""The chat loop: read user input, let the model reason + call tools, reply.

This is the whole agent. No framework — just the OpenAI chat API, a message
history, and a tool registry we dispatch to by hand.
"""

from __future__ import annotations

from pathlib import Path

from .auth import check_login
from .client import MODEL, build_client
from .prompts import authenticated_greeting, authenticated_prompt
from .render import to_terminal
from .tools import ToolRegistry
from .validator import validate_prompt

# Guard against a pathological loop of tool calls in a single turn.
MAX_TOOL_ROUNDS = 5


def _complete(client, messages, tools):
    return client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=tools,
        temperature=0.7,  # a little warmth for the brand voice
    )


def run_turn(client, registry: ToolRegistry, messages: list[dict]) -> str:
    """Run one user turn to completion, resolving any tool calls in between."""
    for _ in range(MAX_TOOL_ROUNDS):
        response = _complete(client, messages, registry.schemas)
        message = response.choices[0].message

        # No tool call -> we have the final assistant reply.
        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content})
            return message.content or ""

        # Record the assistant's tool-call turn, then answer each call.
        messages.append(message.model_dump(exclude_none=True))
        # print(f"Message to the model {messages}")
        for call in message.tool_calls:
            result = registry.run(call.function.name, call.function.arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                }
            )

    return "I'm having trouble completing that request — let's try again. 🧭"


def _login(orders) -> str | None:
    """Prompt for credentials until they check out. None if the user quits.

    Same stub as the web app (see agent/auth.py): any known customer email plus
    password "1234". Signing in here is what binds the agent to an identity, so
    the CLI never has to ask for an email mid-conversation either.
    """
    print("🏔️  Summit Outfitters — please sign in. (type 'quit' to exit)")
    while True:
        try:
            email = input("Email: ").strip()
            if email.lower() in {"quit", "exit"}:
                return None
            password = input("Password: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if check_login(orders, email, password):
            return email
        print("Unknown email or wrong password — try again.\n")


def chat_loop(orders_path: str | Path, catalog_path: str | Path) -> None:
    from .stores import OrderStore, ProductCatalog

    client = build_client()
    orders = OrderStore(orders_path)
    catalog = ProductCatalog(catalog_path)

    email = _login(orders)
    if email is None:
        print("\nOnward into the unknown! 🏔️")
        return

    registry = ToolRegistry(orders, catalog, identity=email)
    messages: list[dict] = [{"role": "system", "content": authenticated_prompt(email)}]

    print("\n" + authenticated_greeting(email))
    print("(type 'quit' to exit)")
    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\nOnward into the unknown! 🏔️")
            return

        if user_input.lower() in {"quit", "exit"}:
            print("\nOnward into the unknown! 🏔️")
            return
        if not user_input:
            continue

        # Scope check before the main agent runs. An out-of-scope prompt is
        # answered here and never enters the message history.
        rejection = validate_prompt(client, user_input)
        if rejection is not None:
            print(f"\nSummit: {to_terminal(rejection)}")
            continue

        messages.append({"role": "user", "content": user_input})
        reply = run_turn(client, registry, messages)
        # Render only at the display boundary — the message history keeps the
        # model's original text so its own context stays consistent.
        print(f"\nSummit: {to_terminal(reply)}")
