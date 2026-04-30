import os
import sys
import requests
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from superset_sqllab import SupersetClient

# --- CONFIGURATION ---
SUPERSET_URL = 'https://superset.bkosh.com'
SUPERSET_USERNAME = 'aditya_'
SUPERSET_PASSWORD = 'Kosh@123'
WA_DATABASE_ID = 21  # Kosh Analytics
PAY_DATABASE_ID = 1   # Main Database

# API settings for sending messages
API_URL = 'https://kosh.bkosh.com/chat/v1/channel-message/create-system-msg/'
AUTH_TOKEN = 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5OTYyMzE4LCJpYXQiOjE3NzczNzAzMTgsImp0aSI6IjlkMzcwZjJmODk1YjQzZTVhM2JjYjIzNWU4NTdmMWIzIiwidXNlcl9pZCI6ImlkSnBaUVJRS2ZHOU00dVlRdXVwZ3giLCJwZXJtaXNzaW9ucyI6W10sImdyb3VwcyI6WyJtZW1iZXIiLCJrb3NoX3N1cGVyX2FkbWluIl19.uK4l5IySINAbiukU5xXlqKWBUPcM2s56Isb52Prw8x28JXqMnMcjQE85g4mZb8voL9PQ1rchYA4tKsazYellOenjeDZ30vf2cpVomVXB0KzO3vtam4J7iWXtlAOnsAX7L9wmafqtXigfSqqqPcuV01cLokAvO6UAT7vDW1GT-Opu1AtJGX92qyycLQr1hcq0o750Vc8R1PCQgharNDE0OXl3Llu5aErYuyxmujdBPTqVxC_gZKwMoYWNlVQN5_YevOrzee8oszu2y0DDbK2wiSBPdYQ6BRCD2JWJ8R8D0RIemKjlMx9VclsvOdZiXClwlhHwreeuXkTtgeCiVMCaBg'

# Trying the long ID without the kosh/private/ prefix
CHANNEL_ID = '_dJx4_nTzakYlg2N8esNENAxf9q_Uc3joW1XPljU__IovU5uu_GC-21unS44Vp4H' 
CREATOR = 'CuGh8L3b79Zc2ctpuWgMjd'

def get_whatsapp_query(target_date):
    # Updated to use created_at for date filtering as status_timestamp gave higher counts
    return f"""
    SELECT count(DISTINCT wa_message_id) AS total_messages
    FROM (
        SELECT b.wa_message_id, (b.created_at AT TIME ZONE 'Asia/Calcutta')::date as message_created_at
        FROM "dss_KOSHSUPERSET_whatsapp_loan_approved_" b
        JOIN "dss_KOSHSUPERSET_loan_wise_user_list_" a ON a.tenant_user_id = b.receiver_user_id
        WHERE b.message_type = 'template' AND b.status IN ('sent', 'read', 'delivered')
    ) AS virtual_table
    WHERE message_created_at = '{target_date}'
    """

def get_payment_query(target_date):
    return f"""
    SELECT payment_kosh_source, count(DISTINCT loanshare_id) AS total_count, sum(received_amount) AS total_amount
    FROM (
        SELECT c.id as loanshare_id, a.amount as received_amount, a.success_time::date as success_date, a.payment_kosh_source
        FROM "Payment_payment" a 
        LEFT JOIN loan_loanrepayment b ON a.id = b.payment_id 
        LEFT JOIN loan_loanshare c ON c.id = b.loanshare_id 
        WHERE a.payment_kosh_source IN ('ca_team_early', 'chat_action', 'reminder_msg', 'reminder_sms', 'reminder_whatsapp', 'app')
    ) AS virtual_table 
    WHERE success_date = '{target_date}'
    GROUP BY payment_kosh_source
    ORDER BY total_count DESC
    """

def create_report_image(wa_count, pay_df, display_date):
    """Generates an image with a Big Number for WhatsApp and a Table for Payments."""
    plt.figure(figsize=(10, 10))
    
    # 1. WhatsApp Big Number Chart
    ax1 = plt.subplot(2, 1, 1)
    ax1.text(0.5, 0.6, str(wa_count), fontsize=100, ha='center', va='center', fontweight='bold', color='#128C7E')
    ax1.text(0.5, 0.3, "WhatsApp Messages Sent", fontsize=25, ha='center', va='center', color='#075E54')
    ax1.set_title(f"WhatsApp Report - {display_date}", fontsize=20, pad=20)
    ax1.axis('off')
    
    # 2. Payments Pivot Table Chart
    ax2 = plt.subplot(2, 1, 2)
    ax2.axis('off')
    
    if pay_df.empty:
        ax2.text(0.5, 0.5, "No Payment Data Found", ha='center', fontsize=20)
    else:
        table_data = []
        for _, row in pay_df.iterrows():
            table_data.append([row['payment_kosh_source'], row['total_count'], f"₹{row['total_amount']:,.2f}"])
        
        total_count = pay_df['total_count'].sum()
        total_amount = pay_df['total_amount'].sum()
        table_data.append(["Total", total_count, f"₹{total_amount:,.2f}"])
        
        columns = ("Source", "Count", "Amount")
        table = ax2.table(cellText=table_data, colLabels=columns, loc='center', cellLoc='center')
        
        table.auto_set_font_size(False)
        table.set_fontsize(14)
        table.scale(1.2, 2.5)
        
        for (row, col), cell in table.get_celld().items():
            if row == 0:
                cell.set_text_props(weight='bold', color='white')
                cell.set_facecolor('#4CAF50')
            if row == len(table_data): 
                cell.set_text_props(weight='bold')
                cell.set_facecolor('#E8F5E9')

        ax2.set_title("Payments Detailed Report", fontsize=20, pad=10)

    img_path = f"daily_report_{display_date}.png"
    plt.tight_layout(pad=5.0)
    plt.savefig(img_path, dpi=100)
    plt.close()
    return img_path

def send_report_via_api(report_content, image_path):
    """Sends the report content and generated image to the chat channel."""
    print("Attempting to send report and image via API...")
    headers = {
        'Authorization': AUTH_TOKEN,
    }
    
    multipart_payload = [
        ('channel', (None, CHANNEL_ID)),
        ('text', (None, report_content)),
        ('creator', (None, CREATOR))
    ]

    opened_files = []
    try:
        if image_path and os.path.exists(image_path):
            f = open(image_path, 'rb')
            opened_files.append(f)
            multipart_payload.append(('file', (os.path.basename(image_path), f, 'image/png')))
        
        response = requests.post(API_URL, headers=headers, files=multipart_payload)
        response.raise_for_status()
        print("Report and image sent successfully via API!")
        print(f"API Response: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Error sending report via API: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"API Error Response: {e.response.text}")
    finally:
        for f in opened_files:
            f.close()
        if image_path and os.path.exists(image_path):
            try:
                os.remove(image_path)
            except:
                pass

def main():
    # 1. Setup Dates
    if len(sys.argv) > 1:
        try:
            target_date = datetime.strptime(sys.argv[1], '%Y-%m-%d')
        except ValueError:
            print("Invalid date format. Use YYYY-MM-DD")
            return
    else:
        # Default to yesterday (1 day less than today)
        target_date = datetime.now() - timedelta(days=1)
        
    target_str = target_date.strftime('%Y-%m-%d')
    display_date = target_date.strftime('%d-%m-%Y')
    
    print(f"Generating WhatsApp & Payment report for: {display_date}")
    
    # 2. Initialize Superset Client
    client = SupersetClient(SUPERSET_URL, SUPERSET_USERNAME, SUPERSET_PASSWORD)
    client.login()
    
    # 3. Execute WhatsApp Query
    wa_query = get_whatsapp_query(target_str)
    wa_result = client.execute_sql(WA_DATABASE_ID, wa_query)
    wa_count = wa_result["data"][0].get('total_messages', 0) if wa_result.get("data") else 0
    
    # 4. Execute Payment Query
    pay_query = get_payment_query(target_str)
    pay_result = client.execute_sql(PAY_DATABASE_ID, pay_query)
    pay_df = pd.DataFrame(pay_result["data"]) if pay_result.get("data") else pd.DataFrame()
    
    # 5. Create the Image Report
    image_path = create_report_image(wa_count, pay_df, display_date)
    
    # 6. Format the text message
    report_body = f"Hi team, this is the count of WhatsApp messages sent on {display_date} and the payment details received from different sources."
    
    # 7. Send via API
    send_report_via_api(report_body, image_path)

if __name__ == "__main__":
    main()
