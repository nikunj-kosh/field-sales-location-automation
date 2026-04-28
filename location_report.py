import requests
import os
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

IST = timezone(timedelta(hours=5, minutes=30))

# manager_name (as it appears in DB) → bchat channel_id
MANAGER_CHANNELS = {
    'ASHOK RANA':               'JKcpTUdkp-UzqrSULse7BM_F4-BUPuukFWGsz-mahmkX1Dedzw1_186AeVXgSce9',
    'Himanshu':                 'aHQzwmGYmPtJ1crVLF3p2A~~',
    'RAHUL KUMAR':              'YtouXAvzbRwfiHzsQOF6wj07oRcLpxivk5xxsCy1CblMuq-BwG1ao8ZqFq_L5pOw',
    'Sanam Kumar':              'WDwFq6EF8-aK1QM1xm4jYg5Jmlpsm05WAh9EeLKjZPEUnCmaIKUQmsDAYTj87t-w',
    'SHAILENDRA SINGH RATHORE': 'dKs3mhc57CaRxVxPm2rjZWCd77YT-EiGmc15hJS-0byOy8-7b5JNQ5460ps_M1Rr',
    'Shivam Raina':             '0dyueHfF0uq9LjwVhdswr5x7EyqSfB3OQc5mBdbi-X4~',
}

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

def send_message(channel_id, text):
    headers = {'Authorization': AUTH_TOKEN}
    payload = [('channel', (None, channel_id)), ('text', (None, text))]
    resp = requests.post(API_URL, headers=headers, files=payload, timeout=30)
    resp.raise_for_status()
    return resp

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    now_ist = datetime.now(IST)

    if not (9 <= now_ist.hour < 18):
        print(f"Outside window ({now_ist.strftime('%H:%M')} IST) — skipping")
        return

    today_str   = now_ist.strftime('%Y-%m-%d')
    report_time = now_ist.strftime('%d-%m-%Y %H:%M IST')
    print(f"Generating location report for {today_str} at {report_time}")

    sql = f"""
    SELECT manager_name, name, username, location_on
    FROM "dss_KOSHSUPERSET_live_fso_live_locations_copy"
    WHERE date IS NULL
      AND report_date = '{today_str}'
    """
    df = run_query(sql)

    if df.empty:
        print(f"No live location data found for {today_str}")
        return

    for manager_name, channel_id in MANAGER_CHANNELS.items():
        team = df[df['manager_name'] == manager_name]
        if team.empty:
            print(f"No data for {manager_name} — skipping")
            continue

        total     = len(team)
        on_count  = int((team['location_on'] == 'Yes').sum())
        off_count = total - on_count

        off_rows = team[team['location_on'] != 'Yes'][['name', 'username']]
        off_list = "\n".join(
            f"  {r['name']} — {r['username']}" for _, r in off_rows.iterrows()
        )

        msg = (
            f"Field Sales Live Location Report — {report_time}\n\n"
            f"Total Members: {total}\n"
            f"Location ON:   {on_count}\n"
            f"Location OFF:  {off_count}\n\n"
            f"Members with Location OFF:\n"
            f"{off_list if off_list else 'None'}"
        )

        try:
            send_message(channel_id, msg)
            print(f"[OK] {manager_name}: ON={on_count}, OFF={off_count}")
        except Exception as e:
            print(f"[ERR] {manager_name}: {e}")

if __name__ == "__main__":
    main()
