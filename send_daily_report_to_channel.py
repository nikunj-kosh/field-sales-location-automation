import requests
import os
import time
import sys
import urllib3
import pandas as pd
from datetime import datetime, timedelta

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- CONFIGURATION ---
SUPERSET_URL      = 'https://superset.bkosh.com'
SUPERSET_USERNAME = os.environ["SUPERSET_UN"]
SUPERSET_PASSWORD = os.environ["SUPERSET_PASS"]
DATABASE_ID       = 21
API_URL           = 'https://kosh.cluster.gksh.in/chat/v1/channel-message/create-system-msg/'
AUTH_TOKEN        = f'Bearer {os.environ["CHANNEL_AUTH_TOKEN"]}'
CHANNEL_ID        = 'HUw1tY5eGk0QCHB9ZuNrPpI5ILVg3EyiAY7ROx63eaw~'

# ── Superset auth ─────────────────────────────────────────────────────────────
_session = requests.Session()
_session.headers.update({"Referer": f"{SUPERSET_URL}/sqllab/", "User-Agent": "Mozilla/5.0"})

def _login():
    for attempt in range(1, 5):
        try:
            r = _session.post(f"{SUPERSET_URL}/api/v1/security/login",
                json={"username": SUPERSET_USERNAME, "password": SUPERSET_PASSWORD,
                      "provider": "db", "refresh": True}, timeout=60)
            tok = r.json()["access_token"]
            cr = _session.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/",
                headers={"Authorization": f"Bearer {tok}"}, timeout=30).json()["result"]
            _session.headers.update({
                "Authorization": f"Bearer {tok}",
                "X-CSRFToken": cr,
                "Content-Type": "application/json"
            })
            print("[OK] Superset login successful")
            return
        except Exception as e:
            print(f"  [AUTH] attempt {attempt} failed: {e} — retrying in 10s")
            time.sleep(10)
    raise RuntimeError("Could not authenticate after 4 attempts")

def run_query(sql):
    _login()
    r = _session.post(f"{SUPERSET_URL}/api/v1/sqllab/execute/",
        json={"database_id": DATABASE_ID, "sql": sql, "json": True, "queryLimit": 50000},
        timeout=300)
    d = r.json()
    if d.get("error") or d.get("errors"):
        raise RuntimeError(f"Query error: {d.get('error') or d.get('errors')}")
    return pd.DataFrame(d.get("data", []))

# ── Query ─────────────────────────────────────────────────────────────────────
def get_base_query(target_date):
    return f"""
    WITH core AS (
        SELECT *
        FROM "dss_KOSHSUPERSET_acc_agg_finark_details_st"
        WHERE source IN ('account_aggregator')
    ),
    field_sales AS (
        SELECT
            b.manager_name AS leader_name,
            b.name as ce_name,
            b.leader_mobile,
            a.leader_id
        FROM "dss_KOSHSUPERSET_acc_agg_leaders_st" a
        LEFT JOIN "dss_KOSHSUPERSET_field_sales_leaders_st" b
            ON a.leader_id = b.tenant_user_id
    ),
    referral_sales AS (
        SELECT
            b.manager_name AS leader_name,
            b.name as ce_name,
            b.leader_mobile,
            a._customer_engagement_lead_id
        FROM "dss_KOSHSUPERSET_acc_agg_leaders_st" a
        LEFT JOIN "dss_KOSHSUPERSET_field_sales_leaders_st" b
            ON a._customer_engagement_lead_id = b.tenant_user_id
    ),
    main AS (
        SELECT DISTINCT
            a.loan_id,
            a.loanshare_id,
            a.share AS amount,
            b.source,
            a.approval_date::date AS approval_date,
            e.cx_name,
            e.crif_score,
            COALESCE(c.leader_name, d.leader_name) AS leader,
            COALESCE(c.ce_name, d.ce_name) AS ce_name,
            DATE_TRUNC('week', approval_date::date)::date AS approval_week,
            DATE_TRUNC('month', approval_date::date)::date AS approval_month,
            CASE
                WHEN a._customer_engagement_lead_id IS NOT NULL THEN 'referral_sales'
                WHEN a._customer_engagement_lead_id IS NULL AND a.leader_id IS NOT NULL THEN 'field_sales'
                ELSE 'Others'
            END AS team
        FROM "dss_KOSHSUPERSET_acc_agg_leaders_st" a
        LEFT JOIN core b ON b.user_id = a.cx_user_id
        LEFT JOIN field_sales c ON c.leader_id = a.leader_id
        LEFT JOIN referral_sales d ON d._customer_engagement_lead_id = a._customer_engagement_lead_id
        LEFT JOIN "dss_KOSHSUPERSET_crif_details_acc_agg_st" e ON a.cx_user_id = e.tenant_user_id
    ),
    core_ AS (
        SELECT * FROM "dss_KOSHSUPERSET_acc_agg_finark_details_st" WHERE source IN ('account_aggregator')
    ),
    main_ AS (
        SELECT DISTINCT
            a.loan_id,
            a.loanshare_id,
            a.loanshare_amount AS amount,
            b.source,
            a.approval_date::date AS approval_date,
            a.name AS cx_name,
            a.crif_score,
            a.leader_name AS leader,
            a.ce_name,
            DATE_TRUNC('week', TO_DATE(a.approval_date, 'YYYY-MM-DD'))::date AS approval_week,
            DATE_TRUNC('month', TO_DATE(a.approval_date, 'YYYY-MM-DD'))::date AS approval_month,
            a.team
        FROM "dss_KOSHSUPERSET_acc_agg_fs_rs_st" a
        LEFT JOIN core_ b
            ON a.tenant_user_id = b.user_id
            AND (a.ifsc_code = b.ifsc_code OR RIGHT(b.acc_number, 4) = RIGHT(a.bank_account, 4))
    ),
    basic AS (SELECT * FROM main UNION SELECT * FROM main_),
    deduped AS (
        SELECT DISTINCT ON (loanshare_id) *
        FROM basic
        ORDER BY loanshare_id, source NULLS LAST, crif_score DESC
    )
    SELECT
        approval_date, loan_id, loanshare_id, cx_name, crif_score, team, leader, ce_name, source,
        CASE WHEN crif_score BETWEEN -100 AND 299 THEN 'NTC' ELSE 'NOT_NTC' END AS crif_status
    FROM deduped
    WHERE approval_date = '{target_date}'
    """

# ── Metrics ───────────────────────────────────────────────────────────────────
def calculate_pct(df):
    if df.empty: return 0
    match = df[(df['source'] == 'account_aggregator') | (df['crif_status'] == 'NOT_NTC')]
    return (len(match) / len(df)) * 100

# ── Channel send ──────────────────────────────────────────────────────────────
def send_report_via_api(report_content, channel_id, file_paths):
    print("Sending report to channel...")
    headers = {'Authorization': AUTH_TOKEN}
    multipart_payload = [
        ('channel', (None, channel_id)),
        ('text',    (None, report_content))
    ]
    opened_files = []
    try:
        for file_path in file_paths:
            if os.path.exists(file_path):
                f = open(file_path, 'rb')
                opened_files.append(f)
                multipart_payload.append(('file', (os.path.basename(file_path), f, 'text/csv')))

        response = requests.post(API_URL, headers=headers, files=multipart_payload)
        response.raise_for_status()
        print(f"[OK] Sent successfully | API response: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] API call failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"API error body: {e.response.text}")
    finally:
        for f in opened_files:
            try: f.close()
            except: pass
        for file_path in file_paths:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except: pass

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        try:
            target_date = datetime.strptime(sys.argv[1], '%d-%m-%Y')
        except ValueError:
            print("Invalid date format. Use DD-MM-YYYY.")
            return
    else:
        target_date = datetime.now() - timedelta(days=1)

    target_str   = target_date.strftime('%Y-%m-%d')
    display_date = target_date.strftime('%d-%m-%Y')
    print(f"Generating report for {display_date}")

    df_all = run_query(get_base_query(target_str))
    if df_all.empty:
        print(f"No data found for {target_str}")
        return

    filename   = f"acc_agg_or_not_ntc_{target_str}.csv"
    file_paths = []

    try:
        df_report = df_all[(df_all['source'] == 'account_aggregator') | (df_all['crif_score'] > 299)]
        with open(filename, "w", newline="", encoding='utf-8') as f:
            df_report.to_csv(f, index=False)
        file_paths.append(filename)

        overall_pct  = calculate_pct(df_all)
        team_stats   = df_all.groupby('team').apply(calculate_pct, include_groups=False)
        leader_stats = df_all.groupby(['team', 'leader']).apply(calculate_pct, include_groups=False)

        fs_below = ", ".join([
            f"{name} ({val:.2f}%)"
            for name, val in leader_stats.get('field_sales', pd.Series()).items()
            if val < overall_pct
        ])
        rs_below = ", ".join([
            f"{name} ({val:.2f}%)"
            for name, val in leader_stats.get('referral_sales', pd.Series()).items()
            if val < overall_pct
        ])

        report_body = f"""Daily Account Aggregator Report - {display_date}

For {display_date}, the NON_NTC or aggregator % were {overall_pct:.2f}%.
The teamwise account aggregator %
field_sales: {team_stats.get('field_sales', 0):.2f}%
referral_sales: {team_stats.get('referral_sales', 0):.2f}%

Members with agg% below average are -
FS: {fs_below if fs_below else 'None'}
RS: {rs_below if rs_below else 'None'}
"""
        send_report_via_api(report_body, CHANNEL_ID, file_paths)

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        for file_path in file_paths:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except: pass

if __name__ == "__main__":
    main()
