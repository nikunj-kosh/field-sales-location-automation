import requests
import pandas as pd
import os
import urllib3
import base64
import json
import ssl
import uuid
import time
import paho.mqtt.client as mqtt

from datetime import datetime, timedelta, timezone

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ───────────────── CONFIG ─────────────────

SUPERSET_URL = 'https://superset.bkosh.com'

USERNAME = 'nikunj'
PASSWORD = 'Kosh@123'

DATABASE_ID = 21

AUTH_TOKEN_VALUE       =  'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgwNjM4NTgyLCJpYXQiOjE3NzgwNDY1ODIsImp0aSI6IjM2N2M3MDdhYjRhMjRkM2FiYzFjNjg1ODg0Nzc2ZDM3IiwidXNlcl9pZCI6ImYyVlJzTE1jTXA5bzRlRGdwV0xWRzciLCJwZXJtaXNzaW9ucyI6W10sImdyb3VwcyI6WyJtZW1iZXIiXX0.Wq0kJZP22Xy1DNsGZOSG3VYrtysm8aVRU16pAvF4I2x5zpr7v-6BMmQGdBHAsxNAf0j0Kx_1vw8NWD6bUlK8DgYMri9gQ0vPTlYs5DWdVsk8qI8cqfWfz7Ttf9zZQkDZg_gJjXWN3umIYQgv5Z-4x6JxmmiaLZMxP7wY_SyszECG6YKosOZOavFEYLd8kJDGxarEpy1N0PSiapq48emCVTXaga2Ef7LI-f0kCUZiTlH9Actn0wpNAe9RIbFLNPbSDJpNrNbTDJhtGmTRDq1tTX75awzFmHXmlzbb61UuLp7ZGfUrrTTqMJQBGzRAyVLLyffXtn26XuVOiJxDQKhd1w'

CHANNEL_ID = 't7NHtmu7EPIUZqZ8jabuP1JbooV6jXFpJ4nZ7x6ykDE~'

MQTT_TOPIC = f'kosh/private/{CHANNEL_ID}'

MQTT_HOST = 'emq.gksh.in'
MQTT_PORT = 9001
MQTT_WS_PATH = '/mqtt'

SENDER_NAME = "Nikunj"

IST = timezone(timedelta(hours=5, minutes=30))

# ───────────────── FILES ─────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CSV_FILE = os.path.join(
    BASE_DIR,
    "location_history.csv"
)

# ───────────────── SESSION ─────────────────

session = requests.Session()

session.headers.update({
    "Referer": f"{SUPERSET_URL}/sqllab/",
    "User-Agent": "Mozilla/5.0"
})

# ───────────────── LOGIN ─────────────────

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

# ───────────────── QUERY ─────────────────

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

        raise RuntimeError(
            d.get("error") or d.get("errors")
        )

    return pd.DataFrame(d.get("data", []))

# ───────────────── MQTT ─────────────────

def decode_user_id_from_token(token):

    token = token.removeprefix("Bearer ").strip()

    payload = token.split(".")[1]

    payload += "=" * (-len(payload) % 4)

    decoded = json.loads(
        base64.urlsafe_b64decode(
            payload.encode("ascii")
        )
    )

    return decoded["user_id"]

def get_mqtt_credentials():

    response = requests.get(
        "https://kosh.bkosh.com/chat/v1/acl/chat-password/get-password/",
        params={"password_type": "bchat"},
        headers={
            "Authorization": AUTH_TOKEN_VALUE,
            "Accept": "application/json",
            "Referer": "https://kosh.bkosh.com/"
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    return (
        data["client_id"],
        data["password"],
        decode_user_id_from_token(
            AUTH_TOKEN_VALUE
        )
    )

# ───────────────── SEND MESSAGE ─────────────────

def send_message(text):

    client_id, password, user_id = get_mqtt_credentials()

    topic = f"{MQTT_TOPIC}/write"

    payload = {

        "msg": {

            "_id": str(uuid.uuid4()),

            "topic": topic,

            "createdAt": datetime.now(
                timezone.utc
            ).isoformat(),

            "system": False,

            "text": text,

            "user": {
                "_id": user_id,
                "name": SENDER_NAME
            }
        },

        "type": "NEW_MESSAGE",
    }

    published = {"done": False}

    def on_connect(client, userdata, flags, reason_code, properties=None):

        print("✅ MQTT Connected")

        client.publish(
            topic,
            json.dumps(payload),
            qos=1
        )

    def on_publish(client, userdata, mid, reason_code=None, properties=None):

        print("✅ Message Sent")

        published["done"] = True

        client.disconnect()

    client = mqtt.Client(

        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,

        client_id=client_id,

        clean_session=False,

        transport="websockets",
    )

    client.username_pw_set(user_id, password)

    client.ws_set_options(path=MQTT_WS_PATH)

    client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    client.on_connect = on_connect

    client.on_publish = on_publish

    client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

    start = time.time()

    while (
        not published["done"]
        and time.time() - start < 30
    ):

        client.loop(timeout=1.0)

# ───────────────── MAIN ─────────────────

def main():

    now = datetime.now(IST)

    today_str = now.strftime('%Y-%m-%d')

    report_time = now.strftime('%H:%M:%S')

    current_hour = now.hour
    current_min = now.minute

    print(f"\n⏳ Running snapshot at {report_time}")

    # ───────────────── SQL ─────────────────

    sql = f"""
    SELECT

        a.manager_name,

        a.name,

        a.username,

        CASE

            WHEN c.max_time_in_IST IS NOT NULL

                 AND (
                     current_timestamp
                     AT TIME ZONE 'Asia/Kolkata'
                 ) - (
                     c.max_time_in_IST::timestamp
                 ) < INTERVAL '30 minutes'

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

    # ───────────────── FETCH ─────────────────

    df = run_query(sql)

    print(f"✅ Rows fetched: {len(df)}")

    if df.empty:

        print("❌ No data fetched")

        return

    # ───────────────── SNAPSHOT ─────────────────

    df['timestamp'] = report_time

    snapshot = df[
        [
            'manager_name',
            'name',
            'username',
            'location_on',
            'timestamp'
        ]
    ]

    if not os.path.exists(CSV_FILE):

        snapshot.to_csv(CSV_FILE, index=False)

    else:

        snapshot.to_csv(
            CSV_FILE,
            mode='a',
            header=False,
            index=False
        )

    print("✅ Snapshot saved")

    # ───────────────── LIVE REPORT ─────────────────

    live_msg = (
        f"📊 Field Sales Live Location Report\n"
        f"Time: {report_time}\n\n"
    )

    live_msg += (
        df[
            [
                'name',
                'username',
                'location_on'
            ]
        ]
        .to_string(index=False)
    )

    send_message(live_msg)

    print("✅ Live report sent")

    # ───────────────── DAILY SUMMARY ─────────────────

    if current_hour == 18:

        print("\n📊 Generating Daily Summary")

        hist = pd.read_csv(CSV_FILE)

        matrix = hist.pivot(
            index=[
                'manager_name',
                'name',
                'username'
            ],
            columns='timestamp',
            values='location_on'
        ).reset_index()

        cols = list(matrix.columns)

        fixed_cols = cols[:3]

        time_cols = sorted(cols[3:])

        matrix = matrix[
            fixed_cols + time_cols
        ]

        excel_path = os.path.join(
            BASE_DIR,
            f"location_matrix_{today_str}.xlsx"
        )

        matrix.to_excel(
            excel_path,
            index=False
        )

        print(f"✅ Excel Saved: {excel_path}")

        # ───────────────── OUTLIERS ─────────────────

        off_counts = (

            hist.groupby(
                [
                    'manager_name',
                    'name',
                    'username'
                ]
            )['location_on']

            .apply(
                lambda x: (x == 'OFF').sum()
            )

            .reset_index(name='off_count')

        )

        bottom_5 = (

            off_counts

            .sort_values(
                'off_count',
                ascending=False
            )

            .head(5)

        )

        outlier_msg = (
            "🚨 Bottom 5 Location Outliers\n\n"
        )

        for _, row in bottom_5.iterrows():

            outlier_msg += (
                f"Manager: {row['manager_name']}\n"
                f"Name: {row['name']}\n"
                f"Phone: {row['username']}\n"
                f"OFF Count: {row['off_count']}\n\n"
            )

        send_message(outlier_msg)

        print("✅ Outlier report sent")

        # ───────────────── RESET CSV ─────────────────

        pd.DataFrame(
            columns=hist.columns
        ).to_csv(CSV_FILE, index=False)

        print("✅ CSV Reset Complete")

# ───────────────── RUN ─────────────────

if __name__ == "__main__":

    main()