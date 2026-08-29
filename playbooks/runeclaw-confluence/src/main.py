"""RUNECLAW Confluence — sandbox entry point (``python -m src.main``).

Routes on the platform-injected evaluation mode. Historical runs never import
live trading code; live runs never import the backtest runner.
"""
from getagent import runtime


def run() -> None:
    if runtime.is_historical():
        from . import main_backtest

        main_backtest.run()
        return
    if runtime.is_live():
        from . import main_live

        main_live.run()
        return
    raise ValueError(f"unsupported evaluation_mode={runtime.evaluation_mode!r}")


if __name__ == "__main__":
    run()
