import requests as req
from alive_progress import alive_it
import time
from openai import OpenAI
import streamlit as st

token = "" # input browser session id
url = "https://api.openai.com/v1/threads"
client = OpenAI(api_key=st.secrets.openai_api_key)

headers = {
    "Authorization": f"Bearer {token}", 
    "Openai-Organization": st.secrets.organization_id, 
    "OpenAI-Project" : st.secrets.project_id
}
params = {"limit": 10}
resp = req.get(url, headers=headers, params=params)
ids = [t['id'] for t in resp.json()['data']]

while len(ids) > 0:
    for tid in alive_it(ids, force_tty=True):
        client.beta.threads.delete(tid)
        time.sleep(1)
    resp = req.get(url, headers=headers, params=params)
    ids = [t['id'] for t in resp.json()['data']]