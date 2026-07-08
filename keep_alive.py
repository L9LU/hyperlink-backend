# keep_alive.py
# Run this on demo day to prevent Render cold starts 
# python keep_alive.py

import requests
import time

def ping():
    url = 'https://hyperlink-backend-up65.onrender.com/health'
    while True:
        try:
            response = requests.get(url, timeout = 10)
            print(f'Pinged successfully - status: {response.status_code}')
        except Exception as e:
            print(f'Ping failed: {e}')
        time.sleep(840)  # every 14 minutes

if __name__ == '__main__':
    print('HyperLink keep-alive started...')
    ping()                