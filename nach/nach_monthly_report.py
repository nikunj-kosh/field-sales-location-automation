"""
NACH Monthly Activation Report
--------------------------------
Checks active loanshares with EMI due in a given month,
breaks down NACH activation by provider, shows EMI demand,
and saves a bar chart to the Desktop.

Usage:
  python nach_monthly_report.py              # current month
  python nach_monthly_report.py 2026-04      # specific month (YYYY-MM)
"""

import sys
import os
import requests
from datetime import date, datetime

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL  = "https://superset.bkosh.com"
USERNAME  = "ayushd"
PASSWORD  = "Getkosh101"
DATABASE_ID = int(os.environ.get("SUPERSET_DATABASE_ID", "21"))
DESKTOP   = os.path.join(os.path.expanduser("~"), "Desktop")

# ── Auth ──────────────────────────────────────────────────────────────────────
def get_auth():
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/v1/security/login",
        json={"username": USERNAME, "password": PASSWORD, "provider": "db", "refresh": True},
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    csrf_token = session.get(f"{BASE_URL}/api/v1/security/csrf_token/", headers=headers).json()["result"]
    return session, access_token, csrf_token, DATABASE_ID


def run_query(session, token, csrf, db_id, sql):
    headers = {
        "Authorization": f"Bearer {token}",
        "X-CSRFToken": csrf,
        "Content-Type": "application/json",
        "Referer": f"{BASE_URL}/sqllab/",
    }
    resp = session.post(
        f"{BASE_URL}/api/v1/sqllab/execute/",
        json={"database_id": db_id, "sql": sql},
        headers=headers,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Query failed: {resp.text}")
    result = resp.json()
    if "data" not in result:
        raise RuntimeError(f"Unexpected response: {result}")
    return result["data"]


# ── Query ─────────────────────────────────────────────────────────────────────
def fetch_nach_report(session, token, csrf, db_id, month_start: str, month_end: str):
    sql = f"""
WITH active_emis AS (
    -- One row per active loanshare with EMI due in the target month
    SELECT e.loanshare_id, SUM(e.amount) AS emi_amount
    FROM "dss_KOSHSUPERSET_kosh_all_emis_" e
    JOIN "dss_KOSHSUPERSET_kosh_loans_portfolio_latest_" p
      ON e.loanshare_id = p.loanshare_id
    WHERE e.due_date >= '{month_start}'
      AND e.due_date <  '{month_end}'
      AND e.status = 'loan_disbursed'
      AND p.status = 'loan_disbursed'
    GROUP BY e.loanshare_id
),
nach_latest AS (
    -- Most recent NACH status per loanshare per provider
    SELECT DISTINCT ON (loanshare_id, nach_type)
        loanshare_id,
        nach_type,
        final_nach_status
    FROM "dss_KOSHSUPERSET_final_nach_status_"
    ORDER BY loanshare_id, nach_type, date_charge DESC
),
best_nach AS (
    -- Per loanshare: prefer an activated provider if multiple exist
    SELECT DISTINCT ON (loanshare_id)
        loanshare_id,
        nach_type,
        final_nach_status,
        CASE
            WHEN nach_type = 'setu upi'   AND final_nach_status = 'success'          THEN 1
            WHEN nach_type = 'lotuspay'   AND final_nach_status = 'confirmed'         THEN 1
            WHEN nach_type = 'digionach'  AND final_nach_status = 'accepted_spo_bank' THEN 1
            ELSE 0
        END AS is_activated
    FROM nach_latest
    ORDER BY loanshare_id,
        CASE
            WHEN nach_type = 'setu upi'   AND final_nach_status = 'success'          THEN 0
            WHEN nach_type = 'lotuspay'   AND final_nach_status = 'confirmed'         THEN 0
            WHEN nach_type = 'digionach'  AND final_nach_status = 'accepted_spo_bank' THEN 0
            ELSE 1
        END
)
SELECT
    COALESCE(n.nach_type, 'no nach')           AS nach_provider,
    SUM(CASE WHEN n.is_activated = 1 THEN 1 ELSE 0 END)                          AS activated,
    SUM(CASE WHEN n.is_activated = 0 AND n.nach_type IS NOT NULL THEN 1 ELSE 0 END) AS not_activated,
    SUM(CASE WHEN n.nach_type IS NULL THEN 1 ELSE 0 END)                          AS no_nach_count,
    COUNT(a.loanshare_id)                      AS total_loanshares,
    SUM(a.emi_amount)                          AS total_emi_demand
FROM active_emis a
LEFT JOIN best_nach n ON a.loanshare_id = n.loanshare_id
GROUP BY n.nach_type
ORDER BY total_loanshares DESC
"""
    return run_query(session, token, csrf, db_id, sql)


# ── Chart ─────────────────────────────────────────────────────────────────────
def save_chart(rows, month_label: str, out_path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import numpy as np

    providers    = [r["nach_provider"] for r in rows]
    activated    = [r["activated"]     for r in rows]
    not_activ    = [r["not_activated"] for r in rows]
    no_nach      = [r["no_nach_count"] for r in rows]

    x     = np.arange(len(providers))
    width = 0.25

    fig, ax = plt.subplots(figsize=(10, 6))
    b1 = ax.bar(x - width, activated, width, label="Activated",     color="#2ecc71")
    b2 = ax.bar(x,         not_activ, width, label="Not Activated",  color="#e74c3c")
    b3 = ax.bar(x + width, no_nach,   width, label="No NACH",        color="#95a5a6")

    def add_labels(bars):
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h + 50, f"{int(h):,}",
                    ha="center", va="bottom", fontsize=8
                )

    for b in (b1, b2, b3):
        add_labels(b)

    ax.set_title(f"NACH Activation — {month_label}", fontsize=14, fontweight="bold")
    ax.set_xlabel("NACH Provider")
    ax.set_ylabel("Loanshares")
    ax.set_xticks(x)
    ax.set_xticklabels([p.title() for p in providers])
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.legend()
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"Chart saved -> {out_path}")


# ── Report printer ────────────────────────────────────────────────────────────
def print_report(rows, month_label: str):
    print(f"\n{'='*72}")
    print(f"  NACH Monthly Activation Report - {month_label}")
    print(f"{'='*72}")
    print(f"{'Provider':<15} {'Activated':>10} {'Not Activ':>10} {'No NACH':>8} {'Total LS':>10} {'EMI Demand (Rs)':>16}")
    print(f"{'-'*72}")

    g_act = g_nact = g_nonach = g_total = g_emi = 0
    for r in rows:
        prov      = r["nach_provider"]
        act       = r["activated"]
        nact      = r["not_activated"]
        nonach    = r["no_nach_count"]
        total     = r["total_loanshares"]
        emi       = r["total_emi_demand"] or 0
        act_pct   = f"({act/total*100:.1f}%)" if total else ""
        print(f"{prov:<15} {act:>7,} {act_pct:>5} {nact:>10,} {nonach:>8,} {total:>10,} {emi:>16,.0f}")
        g_act += act; g_nact += nact; g_nonach += nonach
        g_total += total; g_emi += emi

    print(f"{'-'*72}")
    g_pct = f"({g_act/g_total*100:.1f}%)" if g_total else ""
    print(f"{'TOTAL':<15} {g_act:>7,} {g_pct:>5} {g_nact:>10,} {g_nonach:>8,} {g_total:>10,} {g_emi:>16,.0f}")
    print(f"\n  Total EMI Demand : Rs {g_emi:,.0f}")
    coverage = g_act / g_total * 100 if g_total else 0
    print(f"  NACH Coverage    : {coverage:.1f}% activated of {g_total:,} active loanshares")
    print(f"{'='*72}\n")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # Determine month
    if len(sys.argv) > 1:
        target = datetime.strptime(sys.argv[1], "%Y-%m")
    else:
        today  = date.today()
        target = datetime(today.year, today.month, 1)

    year, month = target.year, target.month
    month_start = f"{year}-{month:02d}-01"
    next_month  = date(year + (month // 12), (month % 12) + 1, 1)
    month_end   = next_month.strftime("%Y-%m-%d")
    month_label = target.strftime("%B %Y")

    print(f"Authenticating to {BASE_URL} …")
    session, token, csrf, db_id = get_auth()
    print(f"Auth OK  |  DB ID: {db_id}  |  Month: {month_label}")

    print("Running NACH report query …")
    rows = fetch_nach_report(session, token, csrf, db_id, month_start, month_end)

    print_report(rows, month_label)

    chart_path = os.path.join(DESKTOP, f"nach_report_{year}_{month:02d}.png")
    save_chart(rows, month_label, chart_path)


if __name__ == "__main__":
    main()
