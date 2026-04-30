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
PAY_DATABASE_ID = 1   # Main Database

API_URL = 'https://kosh.bkosh.com/chat/v1/channel-message/create-system-msg/'
AUTH_TOKEN = 'Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzc5OTYyMzE4LCJpYXQiOjE3NzczNzAzMTgsImp0aSI6IjlkMzcwZjJmODk1YjQzZTVhM2JjYjIzNWU4NTdmMWIzIiwidXNlcl9pZCI6ImlkSnBaUVJRS2ZHOU00dVlRdXVwZ3giLCJwZXJtaXNzaW9ucyI6W10sImdyb3VwcyI6WyJtZW1iZXIiLCJrb3NoX3N1cGVyX2FkbWluIl19.uK4l5IySINAbiukU5xXlqKWBUPcM2s56Isb52Prw8x28JXqMnMcjQE85g4mZb8voL9PQ1rchYA4tKsazYellOenjeDZ30vf2cpVomVXB0KzO3vtam4J7iWXtlAOnsAX7L9wmafqtXigfSqqqPcuV01cLokAvO6UAT7vDW1GT-Opu1AtJGX92qyycLQr1hcq0o750Vc8R1PCQgharNDE0OXl3Llu5aErYuyxmujdBPTqVxC_gZKwMoYWNlVQN5_YevOrzee8oszu2y0DDbK2wiSBPdYQ6BRCD2JWJ8R8D0RIemKjlMx9VclsvOdZiXClwlhHwreeuXkTtgeCiVMCaBg'

# Updated with the "fixed query path" and "creator"
CHANNEL_ID = '_dJx4_nTzakYlg2N8esNENAxf9q_Uc3joW1XPljU__IovU5uu_GC-21unS44Vp4H'
CREATOR = 'CuGh8L3b79Zc2ctpuWgMjd'

def get_comparison_query():
    return """
SELECT payment_month AS payment_month, payment_kosh_source AS payment_kosh_source, count(DISTINCT loanshare_id) AS "count", sum(received_amount) AS "amount" 
FROM (SELECT  
    e.id AS loan_id, 
    c.id AS loanshare_id, 
    a.amount AS received_amount, 
    a.success_time::date AS success_date,
    a.payment_kosh_source, 
    to_char(a.success_time::date, 'yyyy-mm') AS payment_month, 
    a.source
FROM "Payment_payment" a 
LEFT JOIN loan_loanrepayment b ON a.id = b.payment_id 
LEFT JOIN loan_loanshare c ON c.id = b.loanshare_id 
LEFT JOIN loan_loanapp d ON d.id = c.loanapp_id 
LEFT JOIN loan_loan e ON e.id = d.loan_id 
WHERE 
    (
        (
            DATE_TRUNC('month', a.success_time::date) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 day')
            AND EXTRACT(DAY FROM a.success_time::date) <= EXTRACT(DAY FROM CURRENT_DATE - INTERVAL '1 day')
        )
        OR
        (
            DATE_TRUNC('month', a.success_time::date) = DATE_TRUNC('month', CURRENT_DATE - INTERVAL '1 month' - INTERVAL '1 day')
            AND EXTRACT(DAY FROM a.success_time::date) <= EXTRACT(DAY FROM CURRENT_DATE - INTERVAL '1 day')
        )
    )
    AND a.payment_type='repayment'
) AS virtual_table 
WHERE (payment_kosh_source NOT IN ('', 'website')) 
GROUP BY payment_month, payment_kosh_source 
ORDER BY payment_month DESC, "count" DESC
"""

def create_comparison_image(df, display_date):
    plt.figure(figsize=(12, 12))
    
    # 1. Bar Chart Comparison
    ax1 = plt.subplot(2, 1, 1)
    pivot_df = df.pivot(index='payment_kosh_source', columns='payment_month', values='count').fillna(0)
    pivot_df.plot(kind='barh', ax=ax1, color=['#A5D6A7', '#4CAF50'])
    ax1.set_title(f"Month-over-Month Comparison (MTD up to {display_date})", fontsize=18, pad=15)
    ax1.set_xlabel("Loan Count")
    ax1.set_ylabel("Source")
    ax1.legend(title="Month")
    
    # 2. Detailed Data Table (Pivoted for side-by-side comparison)
    ax2 = plt.subplot(2, 1, 2)
    ax2.axis('off')
    
    # Pivot the data for the table: Index=Source, Columns=Month, Values=[count, amount]
    pivot_table = df.pivot(index='payment_kosh_source', columns='payment_month', values=['count', 'amount']).fillna(0)
    
    # Sort months so previous month is first
    months = sorted(df['payment_month'].unique())
    
    # Flatten and reorder columns for better readability: Count M1, Count M2, Amount M1, Amount M2
    final_table_data = []
    headers = ['Source']
    for m in months:
        headers.append(f"Count\n({m})")
    for m in months:
        headers.append(f"Amount\n({m})")

    for source, row in pivot_table.iterrows():
        row_data = [source]
        # Add counts for all months
        for m in months:
            row_data.append(int(row[('count', m)]))
        # Add amounts for all months
        for m in months:
            row_data.append(f"₹{row[('amount', m)]:,.0f}")
        final_table_data.append(row_data)

    # Add Grand Total row
    total_row = ['**Total**']
    for m in months:
        total_row.append(int(pivot_table[('count', m)].sum()))
    for m in months:
        total_row.append(f"₹{pivot_table[('amount', m)].sum():,.0f}")
    final_table_data.append(total_row)
    
    table = ax2.table(cellText=final_table_data, colLabels=headers, loc='center', cellLoc='center')
    
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1.2, 2.5)
    
    # Style Header
    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(weight='bold', color='white')
            cell.set_facecolor('#2E7D32')
        if row == len(final_table_data): # Total row
            cell.set_text_props(weight='bold')
            cell.set_facecolor('#E8F5E9')

    img_path = "monthly_comparison_report.png"
    plt.tight_layout(pad=4.0)
    plt.savefig(img_path, dpi=120)
    plt.close()
    return img_path

def send_report_via_api(report_content, image_path):
    print("Sending Monthly Comparison Report...")
    headers = {'Authorization': AUTH_TOKEN}
    multipart_payload = [
        ('channel', (None, CHANNEL_ID)),
        ('text', (None, report_content)),
        ('creator', (None, CREATOR))
    ]
    
    opened_files = []
    try:
        if os.path.exists(image_path):
            f = open(image_path, 'rb')
            opened_files.append(f)
            multipart_payload.append(('file', (os.path.basename(image_path), f, 'image/png')))
        
        response = requests.post(API_URL, headers=headers, files=multipart_payload)
        response.raise_for_status()
        print("Success!")
    finally:
        for f in opened_files: f.close()
        if os.path.exists(image_path): os.remove(image_path)

def main():
    client = SupersetClient(SUPERSET_URL, SUPERSET_USERNAME, SUPERSET_PASSWORD)
    client.login()
    
    # Calculate yesterday's date for the display
    yesterday = datetime.now() - timedelta(days=1)
    display_date = yesterday.strftime('%d-%m-%Y')
    
    print(f"Fetching comparison data up to {display_date}...")
    result = client.execute_sql(PAY_DATABASE_ID, get_comparison_query())
    
    if not result.get("data"):
        print("No data found for comparison.")
        return

    df = pd.DataFrame(result["data"])
    image_path = create_comparison_image(df, display_date)
    
    report_body = f"Hi team, here is the MTD comparison of repayment sources between the current month and the previous month (up to {display_date})."
    send_report_via_api(report_body, image_path)

if __name__ == "__main__":
    main()
