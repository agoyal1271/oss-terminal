import { useState } from "react";
import type { AnnualRow, FinancialsResponse } from "../api/client";
import { fmtPercent, fmtUsdCompact } from "../format";

type RowSpec = { key: string; label: string; kind: "metric" | "derived"; bold?: boolean; percent?: boolean };

const SECTIONS: { title: string; rows: RowSpec[] }[] = [
  {
    title: "Income statement",
    rows: [
      { key: "revenue", label: "Revenue", kind: "metric", bold: true },
      { key: "cost_of_revenue", label: "Cost of revenue", kind: "metric" },
      { key: "gross_profit", label: "Gross profit", kind: "metric" },
      { key: "gross_margin", label: "Gross margin", kind: "derived", percent: true },
      { key: "research_development_expense", label: "R&D expense", kind: "metric" },
      { key: "sga_expense", label: "SG&A expense", kind: "metric" },
      { key: "operating_income", label: "Operating income", kind: "metric" },
      { key: "operating_margin", label: "Operating margin", kind: "derived", percent: true },
      { key: "net_income", label: "Net income", kind: "metric", bold: true },
      { key: "net_margin", label: "Net margin", kind: "derived", percent: true },
      { key: "eps_diluted", label: "EPS (diluted)", kind: "metric" },
    ],
  },
  {
    title: "Balance sheet",
    rows: [
      { key: "total_assets", label: "Total assets", kind: "metric", bold: true },
      { key: "total_liabilities", label: "Total liabilities", kind: "metric" },
      { key: "stockholders_equity", label: "Stockholders' equity", kind: "metric", bold: true },
      { key: "cash_and_equivalents", label: "Cash & equivalents", kind: "metric" },
      { key: "long_term_debt", label: "Long-term debt", kind: "metric" },
      { key: "current_ratio", label: "Current ratio", kind: "derived" },
      { key: "debt_to_equity", label: "Debt / equity", kind: "derived" },
      { key: "return_on_equity", label: "Return on equity", kind: "derived", percent: true },
    ],
  },
  {
    title: "Cash flow",
    rows: [
      { key: "operating_cash_flow", label: "Operating cash flow", kind: "metric", bold: true },
      { key: "capital_expenditures", label: "Capital expenditures", kind: "metric" },
      { key: "free_cash_flow", label: "Free cash flow", kind: "derived", bold: true },
      { key: "dividends_paid", label: "Dividends paid", kind: "metric" },
      { key: "share_repurchases", label: "Share repurchases", kind: "metric" },
    ],
  },
];

function cellValue(row: AnnualRow, spec: RowSpec): number | null {
  const source = spec.kind === "metric" ? row.metrics : row.derived;
  return source[spec.key] ?? null;
}

function fmtCell(value: number | null, spec: RowSpec): string {
  if (value === null) return "—";
  if (spec.percent) return fmtPercent(value);
  if (spec.key === "eps_diluted") return `$${value.toFixed(2)}`;
  if (spec.key === "current_ratio" || spec.key === "debt_to_equity") return value.toFixed(2);
  return fmtUsdCompact(value);
}

export function FinancialsTable({ data }: { data: FinancialsResponse }) {
  const [showSources, setShowSources] = useState(false);
  const years = [...data.annual].sort((a, b) => b.fy - a.fy).slice(0, 8);

  return (
    <div className="financials-table-wrap">
      {SECTIONS.map((section) => (
        <div key={section.title} className="statement-section">
          <h4>{section.title}</h4>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th className="row-label">&nbsp;</th>
                  {years.map((y) => (
                    <th key={y.fiscal_year_end} title={`Period ended ${y.fiscal_year_end}, filed ${y.filed}`}>
                      FY{y.fy}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {section.rows.map((spec) => (
                  <tr key={spec.key} className={spec.bold ? "bold-row" : ""}>
                    <td className="row-label">
                      {spec.label}
                      {showSources && spec.kind === "metric" && data.metric_sources[spec.key] && (
                        <span className="tag-source"> [{data.metric_sources[spec.key]}]</span>
                      )}
                    </td>
                    {years.map((y) => (
                      <td key={y.fiscal_year_end}>{fmtCell(cellValue(y, spec), spec)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
      <button className="link-btn" onClick={() => setShowSources((s) => !s)}>
        {showSources ? "Hide" : "Show"} XBRL source tags (which reported line item each row is normalized from)
      </button>
    </div>
  );
}
