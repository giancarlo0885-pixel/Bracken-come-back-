from __future__ import annotations

import runpy

from paper_entry_edge_challenger_shadow import install_paper_entry_edge_challenger_shadow
from paper_aeve_generation_controller import install_aeve_generation_controller
from paper_kelly_challenger_shadow import install_paper_kelly_shadow


def main() -> None:
    # Forward-only measurement challengers. These persist their own durable state
    # and cannot alter Council decisions, sizing, execution, broker submission, or live state.
    install_paper_entry_edge_challenger_shadow()
    install_aeve_generation_controller()
    # Research-only Kelly uses immutable prior Council fills and AEVE generation identity.
    # It cannot affect trade decisions, allocation, orders, or live execution.
    install_paper_kelly_shadow()
    runpy.run_module("crypto_worker", run_name="__main__")


if __name__ == "__main__":
    main()
