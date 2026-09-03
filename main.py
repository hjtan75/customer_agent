"""Entrypoint for the Sierra Outfitters agent chat loop.

Usage:
    python main.py
"""

from pathlib import Path

from agent.loop import chat_loop

DATA_DIR = Path(__file__).parent / "data"


def main() -> None:
    chat_loop(
        orders_path=DATA_DIR / "CustomerOrders.json",
        catalog_path=DATA_DIR / "ProductCatalog.json",
    )


if __name__ == "__main__":
    main()
