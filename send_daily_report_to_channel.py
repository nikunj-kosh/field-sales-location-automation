import requests
import os
import time
import sys
import pandas as pd
from datetime import datetime, timedelta
from superset_sqllab import SupersetClient

# --- CONFIGURATION ---
SUPERSET_URL = 'https://superset.bkosh.com'
SUPERSET_USERNAME = 'aditya_'
SUPERSET_PASSWORD = 'Kosh@123'
DATABASE_ID = 21  # Kosh Analytics

# API settings for sending messages to the chat channel
API_URL = 'https://kosh.cluster.gksh.in/chat/v1/channel-message/create-system-msg/'
# Reverting to the original token as the other one had permission issues
AUTH_TOKEN = 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc4NjQ5NzQ2LCJpYXQiOjE3NzYwNTc3NDYsImp0aSI6ImYwNWI2YjEzNjZjYjRjYjI4N2U5NTM5MTI3ZDhjMWMyIiwidXNlcl9pZCI6ImlkSnBaUVJRS2ZHOU00dVlRdXVwZ3giLCJwZXJtaXNzaW9ucyI6W10sImdyb3VwcyI6WyJtZW1iZXIiLCJrb3NoX3N1cGVyX2FkbWluIl19.pjyh3kK-Ady8QR4YbJGdt7iLGkWRHCOxLGhgZKiPU5l1iLLfQRQE3BufsDM2wy1HM7PvQYIXTqhT0-VMP3h09JvhYPgsT37SwyjiqIovlpG1uPNxeyHv0GWjdm_L4zLSPyrDWKZq4JyScKJ1vxriqmCNy55x1i4Ru9qq67kyqR4411qyAPJ_zwiEdVQBM88BrS9J5cfhjr-mHOW1wdNVtN9SqW0qF9HtggEkuzgpeFNESDNf5NYGcaX2hw9lK4dylMYQ7zbGB-XJHGR7xkOr5QEruoCwcCrmk1HpGpqw_kCwc-dDoSB-WJTFu-JIlhoH_x2lzI86pYMBUs6bO6trWA'
CHANNEL_ID = 'HUw1tY5eGk0QCHB9ZuNrPpI5ILVg3EyiAY7ROx63eaw~'

def get_base_query(target_date):
    """Fetches all data for the date without the final filters to allow for metric calculation."""
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
        approval_date, 
        loan_id, 
        loanshare_id, 
        cx_name,
        crif_score, 
        team, 
        leader, 
        ce_name, 
        source, 
        CASE 
            WHEN crif_score BETWEEN -100 AND 299 THEN 'NTC'
            ELSE 'NOT_NTC'
        END AS crif_status
    FROM deduped
    """

def calculate_pct(df):
    """Calculates (Aggregator or NOT_NTC) / Total %"""
    if df.empty: return 0
    match = df[(df['source'] == 'account_aggregator') | (df['crif_status'] == 'NOT_NTC')]
    return (len(match) / len(df)) * 100

def send_report_via_api(report_content, channel_id, file_paths):
    """Sends the report content and files to the chat channel using the provided API."""
    print("Attempting to send report and files via API...")
    
    headers = {
        'Authorization': AUTH_TOKEN,
    }
    
    # Consolidating everything into the 'files' parameter list for a clean multipart/form-data request.
    multipart_payload = [
        ('channel', (None, channel_id)),
        ('text', (None, report_content))
    ]

    opened_files = []
    try:
        if file_paths:
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
            try:
                f.close()
            except:
                pass
        
        for file_path in file_paths:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

def main():
    # 1. Setup Dates
    if len(sys.argv) > 1:
        # Try YYYY-MM-DD first, then fallback to DD-MM-YYYY
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
    
    # 2. Fetch Base Data
    client = SupersetClient(SUPERSET_URL, SUPERSET_USERNAME, SUPERSET_PASSWORD)
    client.login()
    
    query = get_base_query(target_str)
    result = client.execute_sql(DATABASE_ID, query)
    
    if "data" not in result or not result["data"]:
        print(f"No data found for {target_str}")
        return

    df_all = pd.DataFrame(result["data"])
    
    # 3. Create Excel file with multiple tabs
    filename = f"daily_aggregator_report_{target_str}.xlsx"
    
    file_paths = [] 
    try:
        # Tab 1: (source == 'account_aggregator') OR (crif_score > 299)
        df_report1 = df_all[(df_all['source'] == 'account_aggregator') | (df_all['crif_score'] > 299)]
        
        # Tab 2: (source != 'account_aggregator') OR (crif_score between -100 and 299)
        df_report2 = df_all[
            ((df_all['source'].notnull()) & (df_all['source'] != 'account_aggregator')) | 
            (df_all['crif_score'].between(-100, 299))
        ]

        with pd.ExcelWriter(filename, engine='openpyxl') as writer:
            df_report1.to_excel(writer, sheet_name='acc_agg_or_not_ntc', index=False)
            df_report2.to_excel(writer, sheet_name='acc_no_agg_or_ntc', index=False)
        
        file_paths.append(filename) 
        
        # 4. Calculate Metrics for Text Report
        overall_pct = calculate_pct(df_all)
        team_stats = df_all.groupby('team').apply(calculate_pct, include_groups=False)
        leader_stats = df_all.groupby(['team', 'leader']).apply(calculate_pct, include_groups=False)
        
        fs_below_text = ", ".join([f"{name} ({val:.2f}%)" for name, val in leader_stats.get('field_sales', pd.Series()).items() if val < overall_pct])
        rs_below_text = ", ".join([f"{name} ({val:.2f}%)" for name, val in leader_stats.get('referral_sales', pd.Series()).items() if val < overall_pct])

        # 5. Format Report Body
        report_body = f"""Daily Account Aggregator Report - {display_date}

For {display_date}, the NON_NTC or aggregator % were {overall_pct:.2f}%  . 
The teamwise account aggregator %   
field_sales: {team_stats.get('field_sales', 0):.2f}%
referral_sales: {team_stats.get('referral_sales', 0):.2f}%

Members with agg% below average are - 
FS: {fs_below_text if fs_below_text else 'None'}
RS: {rs_below_text if rs_below_text else 'None'}
"""
        
        # 6. Send Report and Files via API
        send_report_via_api(report_body, CHANNEL_ID, file_paths)

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        for file_path in file_paths:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass

if __name__ == "__main__":
    main()
