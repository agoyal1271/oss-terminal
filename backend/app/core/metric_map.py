"""Canonical financial-statement line items mapped onto candidate us-gaap XBRL tags.

Different filers (and the same filer across years, after adopting new
accounting standards) report the "same" line item under different XBRL
tags. This table lists candidate tags in priority order per canonical
metric; the normalizer takes the first tag that has usable data for a
given company. This is the same problem every fundamentals vendor solves
with a proprietary mapping table -- this one is small, transparent, and
meant to be extended as gaps are found in the wild.
"""

# unit -> ("USD" for dollar amounts, "USD/shares" for per-share, "shares" for counts)

INCOME_STATEMENT: dict[str, tuple[str, list[str]]] = {
    "revenue": ("USD", [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ]),
    "cost_of_revenue": ("USD", [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfServices",
    ]),
    "gross_profit": ("USD", ["GrossProfit"]),
    "research_development_expense": ("USD", ["ResearchAndDevelopmentExpense"]),
    "sga_expense": ("USD", ["SellingGeneralAndAdministrativeExpense"]),
    "operating_income": ("USD", ["OperatingIncomeLoss"]),
    "interest_expense": ("USD", ["InterestExpense", "InterestExpenseDebt"]),
    "income_tax_expense": ("USD", ["IncomeTaxExpenseBenefit"]),
    "net_income": ("USD", [
        "NetIncomeLoss",
        "ProfitLoss",
        "NetIncomeLossAvailableToCommonStockholdersBasic",
    ]),
    "eps_basic": ("USD/shares", ["EarningsPerShareBasic"]),
    "eps_diluted": ("USD/shares", ["EarningsPerShareDiluted"]),
    "weighted_avg_shares_diluted": ("shares", [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
    ]),
}

BALANCE_SHEET: dict[str, tuple[str, list[str]]] = {
    "total_assets": ("USD", ["Assets"]),
    "current_assets": ("USD", ["AssetsCurrent"]),
    "cash_and_equivalents": ("USD", [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ]),
    "total_liabilities": ("USD", ["Liabilities"]),
    "current_liabilities": ("USD", ["LiabilitiesCurrent"]),
    "long_term_debt": ("USD", ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtNoncurrentIncludingCurrentMaturities"]),
    "short_term_debt": ("USD", ["LongTermDebtCurrent", "ShortTermBorrowings", "DebtCurrent"]),
    "stockholders_equity": ("USD", [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ]),
    "retained_earnings": ("USD", ["RetainedEarningsAccumulatedDeficit"]),
    "shares_outstanding": ("shares", ["CommonStockSharesOutstanding"]),
}

CASH_FLOW: dict[str, tuple[str, list[str]]] = {
    "operating_cash_flow": ("USD", [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ]),
    "investing_cash_flow": ("USD", [
        "NetCashProvidedByUsedInInvestingActivities",
        "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations",
    ]),
    "financing_cash_flow": ("USD", [
        "NetCashProvidedByUsedInFinancingActivities",
        "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations",
    ]),
    "capital_expenditures": ("USD", [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
    ]),
    "dividends_paid": ("USD", ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"]),
    "share_repurchases": ("USD", ["PaymentsForRepurchaseOfCommonStock"]),
    "depreciation_amortization": ("USD", [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
    ]),
}

ALL_METRICS: dict[str, tuple[str, list[str]]] = {
    **INCOME_STATEMENT,
    **BALANCE_SHEET,
    **CASH_FLOW,
}

# Metrics that represent a flow over a fiscal period (need start+end, ~1yr duration)
# vs. a point-in-time balance (need only end date).
FLOW_METRICS = set(INCOME_STATEMENT) | set(CASH_FLOW)
POINT_IN_TIME_METRICS = set(BALANCE_SHEET)
