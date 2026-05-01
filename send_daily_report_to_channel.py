import requests
import os
import time
import sys
import pandas as pd
from datetime import datetime, timedelta


class SupersetClient:
    def __init__(self, url, username, password):
        self.url = url
        self.username = username
        self.password = password
        self._session = requests.Session()
        self._session.headers.update({"Referer": f"{url}/sqllab/", "User-Agent": "Mozilla/5.0"})

    def login(self):
        for attempt in range(1, 5):
            try:
                r = self._session.post(
                    f"{self.url}/api/v1/security/login",
                    json={"username": self.username, "password": self.password,
                          "provider": "db", "refresh": True},
                    timeout=60
                )
                tok = r.json()["access_token"]
                cr = self._session.get(
                    f"{self.url}/api/v1/security/csrf_token/",
                    headers={"Authorization": f"Bearer {tok}"}, timeout=30
                ).json()["result"]
                self._session.headers.update({
                    "Authorization": f"Bearer {tok}",
                    "X-CSRFToken": cr,
                    "Content-Type": "application/json"
                })
                print("[OK] Superset login successful")
                return
            except Exception as e:
                print(f"  [AUTH] attempt {attempt} failed: {e} -- retrying in 10s")
                time.sleep(10)
        raise RuntimeError("Could not authenticate after 4 attempts")

    def execute_sql(self, database_id, sql):
        r = self._session.post(
            f"{self.url}/api/v1/sqllab/execute/",
            json={"database_id": database_id, "sql": sql, "json": True, "queryLimit": 50000},
            timeout=300
        )
        d = r.json()
        if d.get("error") or d.get("errors"):
            raise RuntimeError(f"Query error: {d.get('error') or d.get('errors')}")
        return d


# --- CONFIGURATION ---
SUPERSET_URL = 'https://superset.bkosh.com'
SUPERSET_USERNAME = os.environ["SUPERSET_UN"]
SUPERSET_PASSWORD = os.environ["SUPERSET_PASS"]
DATABASE_ID = 21  # Kosh Analytics

API_URL = 'https://kosh.cluster.gksh.in/chat/v1/channel-message/create-system-msg/'
AUTH_TOKEN = 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc4NjQ5NzQ2LCJpYXQiOjE3NzYwNTc3NDYsImp0aSI6ImYwNWI2YjEzNjZjYjRjYjI4N2U5NTM5MTI3ZDhjMWMyIiwidXNlcl9pZCI6ImlkSnBaUVJRS2ZHOU00dVlRdXVwZ3giLCJwZXJtaXNzaW9ucyI6W10sImdyb3VwcyI6WyJtZW1iZXIiLCJrb3NoX3N1cGVyX2FkbWluIl19.pjyh3kK-Ady8QR4YbJGdt7iLGkWRHCOxLGhgZKiPU5l1iLLfQRQE3BufsDM2wy1HM7PvQYIXTqhT0-VMP3h09JvhYPgsT37SwyjiqIovlpG1uPNxeyHv0GWjdm_L4zLSPyrDWKZq4JyScKJ1vxriqmCNy55x1i4Ru9qq67kyqR4411qyAPJ_zwiEdVQBM88BrS9J5cfhjr-mHOW1wdNVtN9SqW0qF9HtggEkuzgpeFNESDNf5NYGcaX2hw9lK4dylMYQ7zbGB-XJHGR7xkOr5QEruoCwcCrmk1HpGpqw_kCwc-dDoSB-WJTFu-JIlhoH_x2lzI86pYMBUs6bO6trWA'
CHANNEL_ID = 'HUw1tY5eGk0QCHB9ZuNrPpI5ILVg3EyiAY7ROx63eaw~'

def get_base_query(target_date):
    return f"""
    WITH core AS (
        SELECT user_id, source, ifsc_code, acc_number
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
        SELECT
            a.loan_id,
            a.loanshare_id,
            a.share AS amount,
            b.source,
            a.approval_date::date AS approval_date,
            e.cx_name,
            e.crif_score,
            COALESCE(c.leader_name, d.leader_name) AS leader,
            COALESCE(c.ce_name, d.ce_name) AS ce_name,
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
        WHERE a.approval_date::date = '{target_date}'
    ),
    main_ AS (
        SELECT
            a.loan_id,
            a.loanshare_id,
            a.loanshare_amount AS amount,
            b.source,
            a.approval_date::date AS approval_date,
            a.name AS cx_name,
            a.crif_score,
            a.leader_name AS leader,
            a.ce_name,
            a.team
        FROM "dss_KOSHSUPERSET_acc_agg_fs_rs_st" a
        LEFT JOIN core b
            ON a.tenant_user_id = b.user_id
            AND (a.ifsc_code = b.ifsc_code OR RIGHT(b.acc_number, 4) = RIGHT(a.bank_account, 4))
        WHERE a.approval_date::date = '{target_date}'
    ),
    basic AS (
        SELECT * FROM main
        UNION ALL
        SELECT * FROM main_
    ),
    deduped AS (
        SELECT DISTINCT ON (loanshare_id) *
        FROM basic
        ORDER BY loanshare_id, source NULLS LAST, crif_score DESC
    )
    SELECT
        approval_date, loan_id, loanshare_id, cx_name, crif_score, team, leader, ce_name, source,
        CASE
            WHEN crif_score BETWEEN -100 AND 299 THEN 'NTC'
            ELSE 'NOT_NTC'
        END AS crif_status
    FROM deduped
    """

def calculate_pct(df):
    if df.empty: return 0
    match = df[(df['source'] == 'account_aggregator') | (df['crif_status'] == 'NOT_NTC')]
    return (len(match) / len(df)) * 100

def send_report_via_api(report_content, channel_id, file_paths):
    print("Attempting to send report and files via API...")
    headers = {'Authorization': AUTH_TOKEN}
    multipart_payload = [
        ('channel', (None, channel_id)),
        ('text', (None, report_content))
    ]
    opened_files = []
    try:
        for file_path in file_paths:
            if os.path.exists(file_path):
                try:
                    f = open(file_path, 'rb')
                    opened_files.append(f)
                    content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if file_path.endswith('.xlsx') else 'text/csv'
                    multipart_payload.append(('file', (os.path.basename(file_path), f, content_type)))
                except IOError as e:
                    print(f"Error opening file {file_path}: {e}")
        response = requests.post(API_URL, headers=headers, files=multipart_payload)
        response.raise_for_status()
        print("Report and file(s) sent successfully via API!")
        print(f"API Response: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending report and file(s) via API: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"API Error Response: {e.response.text}")
    finally:
        for f in opened_files:
            try: f.close()
            except: pass
        for file_path in file_paths:
            if os.path.exists(file_path):
                try: os.remove(file_path)
                except: pass

def main():
    if len(sys.argv) > 1:
        for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
            try:
                target_date = datetime.strptime(sys.argv[1], fmt)
                break
            except ValueError:
                continue
        else:
            print("Invalid date format. Please use YYYY-MM-DD or DD-MM-YYYY.")
            return
    else:
        target_date = datetime.now() - timedelta(days=1)

    target_str = target_date.strftime('%Y-%m-%d')
    display_date = target_date.strftime('%d-%m-%Y')
    print(f"Generating report and files for {display_date} to send to channel.")

    client = SupersetClient(SUPERSET_URL, SUPERSET_USERNAME, SUPERSET_PASSWORD)
    client.login()

    result = client.execute_sql(DATABASE_ID, get_base_query(target_str))
    if "data" not in result or not result["data"]:
        print(f"No data found for {target_str}")
        return

    df_all = pd.DataFrame(result["data"])
    filename = f"daily_aggregator_report_{target_str}.xlsx"
    file_paths = []
    try:
        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            df_all.to_excel(writer, sheet_name='daily_report', index=False)
        file_paths.append(filename)

        overall_pct = calculate_pct(df_all)
        team_stats = df_all.groupby('team').apply(calculate_pct, include_groups=False)
        leader_stats = df_all.groupby(['team', 'leader']).apply(calculate_pct, include_groups=False)

        fs_below_text = ", ".join([f"{name} ({val:.2f}%)" for name, val in leader_stats.get('field_sales', pd.Series()).items() if val < overall_pct])
        rs_below_text = ", ".join([f"{name} ({val:.2f}%)" for name, val in leader_stats.get('referral_sales', pd.Series()).items() if val < overall_pct])

        report_body = f"""Daily Account Aggregator Report - {display_date}

For {display_date}, the NON_NTC or aggregator % were {overall_pct:.2f}%  .
The teamwise account aggregator %
field_sales: {team_stats.get('field_sales', 0):.2f}%
referral_sales: {team_stats.get('referral_sales', 0):.2f}%

Members with agg% below average are -
FS: {fs_below_text if fs_below_text else 'None'}
RS: {rs_below_text if rs_below_text else 'None'}
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
