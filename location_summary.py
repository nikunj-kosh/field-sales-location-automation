import requests
import pandas as pd
import os
import urllib3
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── CONFIG ─────────────────────────────────────
SUPERSET_URL = 'https://superset.bkosh.com'
USERNAME = 'nikunj'
PASSWORD = 'Kosh@123'
DATABASE_ID = 21

IST = timezone(timedelta(hours=5, minutes=30))

# Save CSV on Desktop
FILE = os.path.join(
    os.path.expanduser("~"),
    "Desktop",
    "location_history.csv"
)

# ── SESSION ────────────────────────────────────
session = requests.Session()
session.headers.update({
    "Referer": f"{SUPERSET_URL}/sqllab/"
})

def login():
    r = session.post(
        f"{SUPERSET_URL}/api/v1/security/login",
        json={
            "username": USERNAME,
            "password": PASSWORD,
            "provider": "db",
            "refresh": True
        }
    )

    r.raise_for_status()

    token = r.json()["access_token"]

    csrf = session.get(
        f"{SUPERSET_URL}/api/v1/security/csrf_token/",
        headers={"Authorization": f"Bearer {token}"}
    ).json()["result"]

    session.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRFToken": csrf,
        "Content-Type": "application/json"
    })

def run_query(sql):
    login()

    r = session.post(
        f"{SUPERSET_URL}/api/v1/sqllab/execute/",
        json={
            "database_id": DATABASE_ID,
            "sql": sql,
            "json": True
        }
    )

    r.raise_for_status()

    d = r.json()

    if d.get("error") or d.get("errors"):
        raise RuntimeError(d.get("error") or d.get("errors"))

    return pd.DataFrame(d.get("data", []))

# ── MAIN ───────────────────────────────────────
def main():

    now = datetime.now(IST)

    today_str = now.strftime('%Y-%m-%d')

    # Use seconds to avoid duplicate columns
    report_time = now.strftime('%H:%M:%S')

    print(f"\n⏳ Running snapshot at {report_time}")

    # ── QUERY ──────────────────────────────────
    sql = f"""
    SELECT
        a.name,
        a.username,

        CASE
            WHEN c.max_time_in_IST IS NOT NULL
                 AND (
                     current_timestamp AT TIME ZONE 'Asia/Kolkata'
                 ) - (c.max_time_in_IST::timestamp)
                 < INTERVAL '30 minutes'
            THEN 'ON'
            ELSE 'OFF'
        END AS location_on

    FROM "dss_KOSHSUPERSET_fso_details_static_copy" a

    LEFT JOIN (
        SELECT
            user_id,
            MAX(max_time_in_IST) AS max_time_in_IST
        FROM "dss_KOSHSUPERSET_fso_location_time_t2_static_copy"
        GROUP BY user_id
    ) c
        ON a.tenant_user_id = c.user_id

    WHERE a.report_date::date = '{today_str}'
    """

    # ── FETCH DATA ─────────────────────────────
    df = run_query(sql)

    print(f"✅ Rows fetched: {len(df)}")

    if df.empty:
        print("❌ No data fetched")
        return

    # ── SAVE SNAPSHOT ──────────────────────────
    df['timestamp'] = report_time

    snapshot = df[
        ['name', 'username', 'location_on', 'timestamp']
    ]

    if not os.path.exists(FILE):
        snapshot.to_csv(FILE, index=False)
    else:
        snapshot.to_csv(
            FILE,
            mode='a',
            header=False,
            index=False
        )

    print("✅ Snapshot saved")

    # ── LOAD HISTORY ───────────────────────────
    hist = pd.read_csv(FILE)

    # Remove duplicates
    hist = hist.drop_duplicates(
        subset=['name', 'username', 'timestamp'],
        keep='last'
    )

    # ── BUILD MATRIX ───────────────────────────
    matrix = hist.pivot(
        index=['name', 'username'],
        columns='timestamp',
        values='location_on'
    ).reset_index()

    # Sort time columns
    cols = list(matrix.columns)

    fixed_cols = cols[:2]

    time_cols = sorted(cols[2:])

    matrix = matrix[fixed_cols + time_cols]

    # ── PRINT MATRIX ───────────────────────────
    print("\n📊 LOCATION MATRIX:\n")

    print(matrix.to_string(index=False))

    # ── SAVE EXCEL TO DESKTOP ──────────────────
    try:

        excel_path = os.path.join(
            os.path.expanduser("~"),
            "Desktop",
            "location_matrix.xlsx"
        )

        matrix.to_excel(excel_path, index=False)

        print(f"\n✅ Excel saved to:\n{excel_path}")

    except Exception as e:

        print(f"\n⚠️ Excel save failed: {e}")                                       

        print("\n👉 Install openpyxl using:")
        print("pip install openpyxl")

# ── RUN ───────────────────────────────────────
if __name__ == "__main__":
    main()




