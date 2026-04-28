"""
AUM & Disbursement MIS — 8 cuts, time-series from 2022-01-01 to latest snapshot
- Each sheet shows all monthly snapshots (1st of each month) from 2022-01-01 onwards
- AUM  : SUM(principal_outstanding) per snapshot
- Disb : SUM(amount * adj_factor) for loans disbursed since FY_FROM, present in that snapshot
         adj_factor: 2026-01 x1.29 | 2026-02 x1.12 | 2026-03 x1.12 | else x1.0
- Snapshot filter : date_created::date >= '2022-01-01' AND EXTRACT(day FROM date_created) = 1
- FY filter       : db_month >= '2022-01'
"""
import os, sys, time, requests
import pandas as pd
import urllib3
from datetime import datetime
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding='utf-8')

BASE      = "https://superset.bkosh.com"
UN        = os.environ["SUPERSET_UN"]
PW        = os.environ["SUPERSET_PASS"]
DB_ID     = 21
TBL       = 'public."dss_KOSHSUPERSET_all_cohorts_updated_locations_kosh"'
FY_FROM   = '2022-01'
SNAP_FROM = '2022-01-01'

# ── Auth ──────────────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update({"Referer": f"{BASE}/sqllab/", "User-Agent": "Mozilla/5.0"})

def _do_login():
    for attempt in range(1, 5):
        try:
            r = session.post(f"{BASE}/api/v1/security/login",
                json={"username": UN, "password": PW, "provider": "db", "refresh": True},
                timeout=60)
            tok = r.json()["access_token"]
            cr = session.get(f"{BASE}/api/v1/security/csrf_token/",
                headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()["result"]
            session.headers.update({
                "Authorization": f"Bearer {tok}",
                "X-CSRFToken": cr,
                "Content-Type": "application/json"
            })
            return
        except Exception as e:
            print(f"  [AUTH] Login attempt {attempt} failed: {e} — retrying in 10s")
            time.sleep(10)
    raise RuntimeError("Could not authenticate after 4 attempts")

_do_login()
print(f"[OK] Logged in | Snapshots from: {SNAP_FROM} | FY from: {FY_FROM}")

def refresh_auth():
    _do_login()
    print("  [AUTH] Re-authenticated")

def run(sql, label="", _retry=True):
    try:
        r = session.post(f"{BASE}/api/v1/sqllab/execute/",
            json={"database_id": DB_ID, "sql": sql, "json": True, "queryLimit": 20000},
            timeout=300)
    except Exception as e:
        print(f"  [ERR] {label}: request failed — {e}")
        return pd.DataFrame()

    if not r.content:
        print(f"  [ERR] {label}: empty response (server timeout)")
        return pd.DataFrame()

    if r.status_code == 404 and _retry:
        refresh_auth()
        return run(sql, label, _retry=False)

    try:
        d = r.json()
    except Exception:
        print(f"  [ERR] {label}: bad JSON — HTTP {r.status_code} — {r.text[:200]}")
        return pd.DataFrame()

    errs = d.get("error") or d.get("errors")
    if errs:
        print(f"  [ERR] {label}: {str(errs)[:200]}")
        refresh_auth()
        return pd.DataFrame()

    rows = d.get("data", [])
    if not rows:
        print(f"  [WARN] No data: {label}")
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    print(f"  [OK] {label} -- {len(df)} rows across {df['snapshot_date'].nunique() if 'snapshot_date' in df.columns else '?'} snapshots")
    return df


YEARS = [2022, 2023, 2024, 2025, 2026]

def run_batched(sql_template, label):
    parts = []
    for yr in YEARS:
        year_sql = sql_template.replace(
            "{year_filter}",
            f"AND date_created::date >= '{yr}-01-01' AND date_created::date < '{yr+1}-01-01'"
        )
        chunk = run(year_sql, f"{label} ({yr})")
        if not chunk.empty:
            parts.append(chunk)
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    print(f"  [COMBINED] {label} -- {len(df)} total rows")
    return df

# ── Shared filter & expressions ───────────────────────────────────────────────
AUM_WHERE = f"""
    date_created::date >= '{SNAP_FROM}'
    AND EXTRACT(day FROM date_created) = 1
    AND amount > 0
"""

ADJ = """amount::numeric * CASE
        WHEN db_month = '2026-01' THEN 1.29
        WHEN db_month = '2026-02' THEN 1.12
        WHEN db_month = '2026-03' THEN 1.12
        ELSE 1.0
    END"""

FY_CASE = f"CASE WHEN db_month >= '{FY_FROM}' THEN {ADJ} ELSE 0 END"
FY_CNT  = f"CASE WHEN db_month >= '{FY_FROM}' THEN loanshare_id ELSE NULL END"

# ── Sanity check ──────────────────────────────────────────────────────────────
print("\n[Sanity Check — available snapshots]")
run(f"""
SELECT
    date_created::date           AS snapshot_date,
    COUNT(DISTINCT loanshare_id) AS active_loans,
    SUM(principal_outstanding)::bigint AS total_aum
FROM {TBL}
WHERE {AUM_WHERE}
GROUP BY 1
ORDER BY 1
""", "Snapshots")

# ── Template ──────────────────────────────────────────────────────────────────
def cut(dim_expr, label, order_by='"AUM" DESC'):
    tmpl = f"""
SELECT
    date_created::date             AS snapshot_date,
    {dim_expr}                     AS dimension,
    SUM(principal_outstanding)::bigint AS "AUM",
    SUM({FY_CASE})::bigint         AS disbursal
FROM {TBL}
WHERE {AUM_WHERE}
  {{year_filter}}
GROUP BY 1, 2
ORDER BY snapshot_date, {order_by}
"""
    return run_batched(tmpl, label)

# ── 8 cuts ────────────────────────────────────────────────────────────────────
print("\n[1] By Cluster")
df1 = cut("COALESCE(cluster_name, 'Unknown')", "1. By Cluster")

print("\n[2] By Ticket Size")
df2 = run_batched(f"""
SELECT
    date_created::date AS snapshot_date,
    CASE
        WHEN amount <= 25000  THEN '1. Upto 25K'
        WHEN amount <= 50000  THEN '2. 25K - 50K'
        WHEN amount <= 100000 THEN '3. 50K - 1L'
        WHEN amount <= 200000 THEN '4. 1L - 2L'
        WHEN amount <= 500000 THEN '5. 2L - 5L'
        ELSE                       '6. Above 5L'
    END                    AS dimension,
    SUM(principal_outstanding)::bigint AS "AUM",
    SUM({FY_CASE})::bigint AS disbursal
FROM {TBL}
WHERE {AUM_WHERE}
  {{year_filter}}
GROUP BY 1, 2
ORDER BY snapshot_date, dimension
""", "2. By Ticket Size")

print("\n[3] By Sourcing Mix")
df3 = cut("""CASE sales_channel
        WHEN 'fso' THEN 'Direct (Field Sales)'
        WHEN 'ref' THEN 'DSA / Referral'
        WHEN 'lsp' THEN 'Partnerships (LSP)'
        ELSE COALESCE(sales_channel, 'Unknown')
    END""", "3. By Sourcing Mix")

print("\n[4] By Income Level")
df4 = run_batched(f"""
SELECT
    date_created::date AS snapshot_date,
    CASE
        WHEN annual_salary IS NULL    THEN '0. Not Available'
        WHEN annual_salary <= 120000  THEN '1. Upto 10K/month'
        WHEN annual_salary <= 240000  THEN '2. 10K - 20K/month'
        WHEN annual_salary <= 360000  THEN '3. 20K - 30K/month'
        WHEN annual_salary <= 600000  THEN '4. 30K - 50K/month'
        ELSE                               '5. Above 50K/month'
    END                    AS dimension,
    SUM(principal_outstanding)::bigint AS "AUM",
    SUM({FY_CASE})::bigint AS disbursal
FROM {TBL}
WHERE {AUM_WHERE}
  {{year_filter}}
GROUP BY 1, 2
ORDER BY snapshot_date, dimension
""", "4. By Income Level")

print("\n[5] By Employment Type")
df5 = cut("""CASE employment_type
        WHEN 'salaried'      THEN 'Salaried'
        WHEN 'self_employed' THEN 'Self Employed'
        ELSE COALESCE(employment_type, 'Unknown')
    END""", "5. By Employment Type")

print("\n[6] By Lender Count")
df6 = run_batched(f"""
SELECT
    date_created::date AS snapshot_date,
    CASE
        WHEN ranking IS NULL OR ranking::int = 1 THEN '1. Exclusive to Kosh'
        WHEN ranking::int = 2                    THEN '2. With 1 other lender'
        WHEN ranking::int = 3                    THEN '3. With 2 other lenders'
        ELSE                                          '4. With >2 lenders'
    END                    AS dimension,
    SUM(principal_outstanding)::bigint AS "AUM",
    SUM({FY_CASE})::bigint AS disbursal
FROM {TBL}
WHERE {AUM_WHERE}
  {{year_filter}}
GROUP BY 1, 2
ORDER BY snapshot_date, dimension
""", "6. By Lender Count")

print("\n[7] By Book Type")
df7 = cut("""CASE
        WHEN lender IN ('light_nfcpl_colending','light_colending',
                        'light_colending_new','hindon_colending',
                        'janasha_colending','fintree_colending') THEN 'Co-lending'
        WHEN lender = 'narendra_finance'                         THEN 'BC Model'
        WHEN lender IS NULL                                      THEN 'Own Book'
        ELSE lender
    END""", "7. By Book Type")

print("\n[8] New vs Repeat")
df8 = run_batched(f"""
SELECT
    date_created::date AS snapshot_date,
    CASE cx_type
        WHEN '1_time_cx'    THEN 'New (1st loan)'
        WHEN 'recurring_cx' THEN 'Repeat (2nd+ loan)'
        ELSE COALESCE(cx_type, 'Unknown')
    END                    AS dimension,
    COUNT(DISTINCT loanshare_id) AS active_borrowers
FROM {TBL}
WHERE {AUM_WHERE}
  {{year_filter}}
GROUP BY 1, 2
ORDER BY snapshot_date, dimension
""", "8. New vs Repeat")

datasets = {
    "1. By Cluster":         df1,
    "2. By Ticket Size":     df2,
    "3. By Sourcing Mix":    df3,
    "4. By Income Level":    df4,
    "5. By Employment Type": df5,
    "6. By Lender Count":    df6,
    "7. By Book Type":       df7,
    "8. New vs Repeat":      df8,
}

# ── Styles ────────────────────────────────────────────────────────────────────
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference

TITLE_FILL = PatternFill("solid", fgColor="17324D")
TITLE_FONT = Font(color="FFFFFF", bold=True, size=11)
DATE_FILL  = PatternFill("solid", fgColor="1F4E79")
DATE_FONT  = Font(color="FFFFFF", bold=True, size=8)
DIM_FILL   = PatternFill("solid", fgColor="D6E4F0")
DIM_FONT   = Font(bold=True, size=9, color="17324D")
EVEN_FILL  = PatternFill("solid", fgColor="FFFFFF")
ODD_FILL   = PatternFill("solid", fgColor="EBF3FB")
BORDER     = Border(
    left=Side(style="thin", color="BDC3C7"),
    right=Side(style="thin", color="BDC3C7"),
    top=Side(style="thin", color="BDC3C7"),
    bottom=Side(style="thin", color="BDC3C7"),
)
MONEY_FMT = "#,##0"

def _border_cell(cell, fill, font=None, align=None, fmt=None):
    cell.fill   = fill
    cell.border = BORDER
    if font:  cell.font           = font
    if align: cell.alignment      = align
    if fmt:   cell.number_format  = fmt

def write_pivot_block(ws, pivot, start_row, label, fmt=MONEY_FMT):
    n_dims, n_cols = pivot.shape
    hdr_row        = start_row + 1
    first_data_row = start_row + 2
    last_data_row  = start_row + 1 + n_dims

    ws.merge_cells(start_row=start_row, start_column=1,
                   end_row=start_row,   end_column=n_cols + 1)
    c = ws.cell(row=start_row, column=1, value=label)
    c.fill      = TITLE_FILL
    c.font      = TITLE_FONT
    c.border    = BORDER
    c.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[start_row].height = 22

    ws.row_dimensions[hdr_row].height = 64
    corner = ws.cell(row=hdr_row, column=1, value="")
    _border_cell(corner, DATE_FILL)
    for i, d in enumerate(pivot.columns, start=2):
        c = ws.cell(row=hdr_row, column=i, value=d)
        _border_cell(c, DATE_FILL, font=DATE_FONT,
                     align=Alignment(horizontal="center", vertical="bottom",
                                     text_rotation=90, wrap_text=False))

    for r_off, (dim, row_data) in enumerate(pivot.iterrows()):
        row      = first_data_row + r_off
        row_fill = ODD_FILL if r_off % 2 == 1 else EVEN_FILL
        ws.row_dimensions[row].height = 16

        dim_c = ws.cell(row=row, column=1, value=dim)
        _border_cell(dim_c, DIM_FILL, font=DIM_FONT,
                     align=Alignment(horizontal="left", vertical="center", indent=1))

        for c_off, val in enumerate(row_data, start=2):
            v  = int(val) if pd.notna(val) else 0
            dc = ws.cell(row=row, column=c_off, value=v)
            _border_cell(dc, row_fill,
                         align=Alignment(horizontal="right", vertical="center"),
                         fmt=fmt)

    ws.column_dimensions["A"].width = 24
    for i in range(2, n_cols + 2):
        ws.column_dimensions[get_column_letter(i)].width = 8

    return last_data_row, hdr_row, first_data_row, n_cols


def make_line_chart(ws, first_data_row, last_data_row, hdr_row, n_cols, title):
    chart               = LineChart()
    chart.title         = title
    chart.style         = 10
    chart.width         = 30
    chart.height        = 15
    chart.y_axis.numFmt = "#,##0"
    chart.y_axis.title  = "INR"
    chart.x_axis.title  = "Date"

    data = Reference(ws, min_col=1, max_col=n_cols + 1,
                     min_row=first_data_row, max_row=last_data_row)
    chart.add_data(data, from_rows=True, titles_from_data=True)

    cats = Reference(ws, min_col=2, max_col=n_cols + 1,
                     min_row=hdr_row, max_row=hdr_row)
    chart.set_categories(cats)
    return chart


# ── Build workbook ────────────────────────────────────────────────────────────
OUT_FILE = f"AUM_MIS_TimeSeries_{datetime.today().strftime('%Y%m%d_%H%M')}.xlsx"
wb = Workbook()
wb.remove(wb.active)

for name, df in datasets.items():
    ws = wb.create_sheet(title=name[:31])
    ws.sheet_view.showGridLines = False

    if df.empty:
        ws.cell(row=1, column=1, value="No data")
        continue

    is_new_repeat = "active_borrowers" in df.columns
    df = df.copy()
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])

    if is_new_repeat:
        piv = df.pivot_table(index="dimension", columns="snapshot_date",
                             values="active_borrowers", aggfunc="sum")
        piv = piv.sort_index(axis=1)
        piv.columns = [c.strftime("%b %Y") for c in piv.columns]
        piv = piv.reindex(piv.iloc[:, -1].sort_values(ascending=False).index)

        last, hdr, fdr, nc = write_pivot_block(ws, piv, start_row=1,
                                                label="Active Borrowers", fmt="#,##0")
        chart = make_line_chart(ws, fdr, last, hdr, nc, "Active Borrowers Over Time")
        chart.y_axis.title = "Count"
        ws.add_chart(chart, f"{get_column_letter(nc + 3)}1")
        ws.freeze_panes = "B3"

    else:
        aum_piv  = df.pivot_table(index="dimension", columns="snapshot_date",
                                  values="AUM", aggfunc="sum")
        disb_piv = df.pivot_table(index="dimension", columns="snapshot_date",
                                  values="disbursal", aggfunc="sum")
        aum_piv  = aum_piv.sort_index(axis=1)
        disb_piv = disb_piv.sort_index(axis=1)
        date_labels      = [c.strftime("%b %Y") for c in aum_piv.columns]
        aum_piv.columns  = date_labels
        disb_piv.columns = date_labels

        row_order = aum_piv.iloc[:, -1].sort_values(ascending=False).index
        aum_piv  = aum_piv.reindex(row_order)
        disb_piv = disb_piv.reindex(row_order)

        nc        = len(date_labels)
        chart_col = get_column_letter(nc + 3)

        aum_last, aum_hdr, aum_fdr, _ = write_pivot_block(
            ws, aum_piv, start_row=1,
            label="AUM — Principal Outstanding (INR)")
        ws.add_chart(
            make_line_chart(ws, aum_fdr, aum_last, aum_hdr, nc, "AUM — Principal Outstanding"),
            f"{chart_col}1")

        disb_start = aum_last + 3
        disb_last, disb_hdr, disb_fdr, _ = write_pivot_block(
            ws, disb_piv, start_row=disb_start,
            label="Total Disbursal (INR)")
        ws.add_chart(
            make_line_chart(ws, disb_fdr, disb_last, disb_hdr, nc, "Total Disbursal"),
            f"{chart_col}{disb_start}")

        ws.freeze_panes = "B3"

wb.save(OUT_FILE)
print(f"\n[DONE] Saved: {OUT_FILE}")
print(f"  Sheets: {', '.join(datasets.keys())}")
