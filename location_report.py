import requests
import os
import sys
import time
import urllib3
import pandas as pd
from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SUPERSET_URL      = 'https://superset.bkosh.com'
SUPERSET_USERNAME = os.environ["SUPERSET_UN"]
SUPERSET_PASSWORD = os.environ["SUPERSET_PASS"]
DATABASE_ID       = 21
API_URL           = 'https://kosh.cluster.gksh.in/chat/v1/channel-message/create-system-msg/'
AUTH_TOKEN        = f'Bearer {os.environ["CHANNEL_AUTH_TOKEN"]}'
CHANNEL_ID        = 't7NHtmu7EPIUZqZ8jabuP1JbooV6jXFpJ4nZ7x6ykDE~'

IST = timezone(timedelta(hours=5, minutes=30))

SKIP_MANAGERS = {'KARUN KAKAR', 'Partik Pannu'}

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

def send_message(text):
    headers = {'Authorization': AUTH_TOKEN}
    payload = [('channel', (None, CHANNEL_ID)), ('text', (None, text))]
    resp = requests.post(API_URL, headers=headers, files=payload, timeout=30)
    print(f"  API response: {resp.status_code} — {resp.text[:200]}")
    resp.raise_for_status()
    return resp

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now_ist = datetime.now(IST)

    force = '--force' in sys.argv
    if not force and not (9 <= now_ist.hour < 18):
        print(f"Outside window ({now_ist.strftime('%H:%M')} IST) — skipping. Use --force to override.")
        return

    today_str   = now_ist.strftime('%Y-%m-%d')
    report_time = now_ist.strftime('%d-%m-%Y %H:%M IST')
    print(f"Generating location report for {today_str} at {report_time}")

    sql = f"""
    SELECT
        a.manager_name,
        a.name,
        a.username,
        CASE
            WHEN (current_timestamp AT TIME ZONE 'Asia/Kolkata')
                 - (c.max_time_in_IST::timestamp) < INTERVAL '30 minutes'
            THEN 'Yes'
            ELSE 'No'
        END AS location_on
    FROM "dss_KOSHSUPERSET_fso_details_static_copy" a
    LEFT JOIN "dss_KOSHSUPERSET_fso_distance_details_final_static_copy" b
        ON a.tenant_user_id = b.user_id AND a.report_date = b.date
    LEFT JOIN "dss_KOSHSUPERSET_fso_location_time_t2_static_copy" c
        ON a.tenant_user_id = c.user_id AND b.date = c.date
    WHERE a.report_date::date = '{today_str}'
    """
    df = run_query(sql)

    if df.empty:
        print(f"No live location data found for {today_str}")
        return

    sections = []
    for manager_name, team in df.groupby('manager_name'):
        if manager_name in SKIP_MANAGERS:
            continue

        total     = len(team)
        on_count  = int((team['location_on'] == 'Yes').sum())
        off_count = total - on_count

        off_rows = team[team['location_on'] != 'Yes'][['name', 'username']]
        off_list = "\n".join(
            f"  {row['name']} — {row['username']}" for _, row in off_rows.iterrows()
        )

        sections.append(
            f"{manager_name}\n"
            f"Total: {total}  |  ON: {on_count}  |  OFF: {off_count}\n"
            f"Location OFF:\n{off_list if off_list else '  None'}"
        )
        print(f"  {manager_name}: total={total}, ON={on_count}, OFF={off_count}")

    if not sections:
        print("No data for any manager after filtering")
        return

    msg = f"Field Sales Live Location Report — {report_time}\n\n" + "\n\n".join(sections)

    print(f"Sending combined report to channel...")
    try:
        send_message(msg)
        print("[OK] Report sent successfully")
    except Exception as e:
        print(f"[ERR] Failed to send: {e}")

if __name__ == "__main__":
    main()
