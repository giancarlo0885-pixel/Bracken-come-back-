from pathlib import Path

from paper_broker import allocate_purchase, allocate_sale, build_account


def test_empty_stock_account_has_four_x_paper_buying_power():
    account = build_account(
        "cash",
        {
            "cash": 10_000_000,
            "starting_balance": 10_000_000,
            "margin_debt": 0,
            "leverage_limit": 4,
            "broker_profile": "institutional-paper",
        },
        [],
    )
    assert account.equity == 10_000_000
    assert account.buying_power == 40_000_000
    assert account.leverage_used == 0
    assert account.margin_call is False


def test_purchase_uses_cash_reserve_then_margin():
    new_cash, new_debt, cash_used, borrowed = allocate_purchase(
        cash=10_000_000,
        margin_debt=0,
        trade_value=20_000_000,
        cash_reserve=500_000,
    )
    assert new_cash == 500_000
    assert cash_used == 9_500_000
    assert borrowed == 10_500_000
    assert new_debt == 10_500_000


def test_sale_repays_margin_before_increasing_cash():
    new_cash, new_debt, repayment = allocate_sale(
        cash=500_000,
        margin_debt=10_500_000,
        sale_value=5_000_000,
    )
    assert new_cash == 500_000
    assert new_debt == 5_500_000
    assert repayment == 5_000_000


def test_leveraged_account_metrics_are_net_of_margin_debt():
    account = build_account(
        "cash",
        {
            "cash": 500_000,
            "starting_balance": 10_000_000,
            "margin_debt": 10_500_000,
            "leverage_limit": 4,
        },
        [{"quantity": 200_000, "current_price": 100}],
    )
    assert account.positions_value == 20_000_000
    assert account.equity == 10_000_000
    assert account.leverage_used == 2
    assert account.buying_power == 20_000_000


def test_v33_ui_and_runtime_expose_broker_metrics():
    app = Path("app.py").read_text()
    bot = Path("oracle_bot.py").read_text()
    variables = Path("railway_variables.example").read_text()
    assert "Institutional Paper Broker" in app
    assert "Available Buying Power" in app
    assert "paper_margin_reduction" in bot
    assert "STOCK_PAPER_LEVERAGE=4.0" in variables
    assert "CRYPTO_PAPER_LEVERAGE=2.0" in variables
